"""Semantischer Fallback ueber ein lokales LLM (Ollama), siehe SPEC-v1.md §7f.

Ergaenzt die Template-Extraktion, ersetzt sie nicht: `apply_template()`
bleibt die primaere Quelle. Dieser Layer greift nur bei Feldern, die dort
fehlen oder schwach sind, und bekommt das bisher erkannte Template-Ergebnis
als *Hinweis* mit -- nicht als Zwang. Damit toleriert er Layout-Abweichungen,
die eine starre Anker-Position verpasst, ohne dass ein Lieferant deshalb neu
angelernt werden muss.

Zweite Rolle (Stufe 2, ``extract_full``): ohne Template -- unbekannter
Lieferant oder noch nicht angelernt -- liest das Modell die komplette
Rechnung strukturiert aus (alle STANDARD_FIELDS, Lieferantenkennungen,
optional Positionen), als JSON mit Schema-Pruefung.

Gegen Halluzination gilt fuer beide Rollen: jeder Modellwert muss im PDF-Text
wiederzufinden sein (dieselbe Normalisierung wie beim Vorlagen-Lernen,
``textnorm.same_value``), sonst wird er verworfen und als ``rejected``
gemeldet. Konfidenz bleibt gedeckelt (unter CONF_MIN), die Bestaetigungs-
pflicht gilt unveraendert.

Standardmaessig AUS (``RECHNUNG_LLM_ENABLED``). Ohne das laeuft das Programm
exakt wie zuvor -- kein Netzwerkzugriff, keine neue Abhaengigkeit (reines
stdlib ``urllib``). Backend: lokales Ollama oder ein OpenAI-kompatibler
Endpoint auf eigener Hardware (vLLM/llama.cpp) -- kein Cloud-Dienst fest
eingebaut.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .config import llm as _llm_cfg
from .template import STANDARD_FIELDS, FieldResult, ki_felder
from .textnorm import coerce, compact, find_ustids, iban_valid, norm, value_in_text

# Typen, die als einfacher String zurueckkommen -- taxblock/Positionen sind
# strukturiert und bewusst (noch) nicht Teil dieses Fallbacks (v1 von §7f).
# ---- Extraktionsvarianten (Testsatz, Entscheidung Aaron 10.10.2026) --------
# Geplanter Vergleich: A = eine grosse KI-Anfrage ueber die ganze Rechnung
# (heutiges Verhalten), B = mehrere Block-Anfragen, C = Ueberblick + Bloecke.
# Gebaut ist nur A; B/C sind hier als Namen vorgesehen, damit der Testsatz
# (``rechnung testsatz --variante``) sie spaeter ohne Umbau messen kann. Die
# Variante wird von ``pipeline.process`` bis zur KI-Stelle (``_llm_full``)
# durchgereicht -- eine unbekannte oder noch nicht gebaute Variante ist ein
# Fehler, nie ein stiller Rueckfall auf A (kein Raten).
VARIANTE_A = "A"
VARIANTEN = {"A": "eine KI-Anfrage ueber die ganze Rechnung",
             "B": "KI-Anfragen je Block (noch nicht gebaut)",
             "C": "Ueberblick + Bloecke (noch nicht gebaut)"}
VARIANTEN_GEBAUT = frozenset({VARIANTE_A})


def pruefe_variante(variante: str | None) -> str:
    """Normalisiert die Variante ("a" -> "A"); ``ValueError`` bei
    unbekannter, ``NotImplementedError`` bei vorgesehener, aber noch nicht
    gebauter Variante."""
    v = (variante or VARIANTE_A).strip().upper()
    if v not in VARIANTEN:
        raise ValueError(f"Unbekannte Extraktionsvariante {variante!r} "
                         f"(bekannt: {', '.join(VARIANTEN)})")
    if v not in VARIANTEN_GEBAUT:
        raise NotImplementedError(f"Extraktionsvariante {v} ist vorgesehen, aber noch "
                                  f"nicht gebaut ({VARIANTEN[v]})")
    return v


_SIMPLE_TYPES = {"text", "number", "date", "amount"}

# Zeichenbudget fuer den Rechnungstext im Prompt. Kopf- und Fussbereich einer
# Rechnung sind fuer die gesuchten Felder am ergiebigsten (Rechnungsnummer/
# Datum/Lieferant oben, Summen/USt-Aufstellung unten) -- der Mittelteil
# (Positionszeilen) ist fuer diesen simplen Fallback am wenigsten wichtig,
# da Positionen (noch) nicht extrahiert werden (siehe _SIMPLE_TYPES).
_TEXT_BUDGET = 6000
_TAIL_SHARE = 0.4  # Anteil des Budgets, der vom Ende der Rechnung kommt


def _head_and_tail(text: str, budget: int = _TEXT_BUDGET, tail_share: float = _TAIL_SHARE) -> str:
    """Kappt lange Rechnungen auf Kopf + Fuss statt nur den Anfang -- sonst
    sieht das Modell bei mehrseitigen Rechnungen die Summenzeile auf der
    letzten Seite nie (gemessen: bei 10 Seiten realer Testrechnungen lag sie
    weit hinter einer reinen Anfangs-Kappung)."""
    if len(text) <= budget:
        return text
    tail_len = int(budget * tail_share)
    head_len = budget - tail_len
    return f"{text[:head_len]}\n[...gekuerzt...]\n{text[-tail_len:]}"


# ---- Gezielte Textausschnitte (SPEC Effizienz-Baustein 1) ------------------
# Statt dem Modell immer Kopf+Fuss der ganzen Rechnung zu schicken, nur die
# Umgebung von Schluesselwoertern je angefragtem Feld -- deutlich weniger
# Prompt-Text = schneller auf einem CPU-Laptop, kleinerer noetiger Kontext
# (siehe _dynamic_num_ctx). Arbeitet auf Wort-Token statt auf Zeilen: der
# Volltext einer Rechnung (``Document.text``) ist pro SEITE eine einzige
# leerzeichengetrennte Zeile, Zeilenumbrueche sagen hier also nichts ueber
# das Layout aus. Verglichen wird ausschliesslich gegen den VOLLEN Text
# (``_check_value``/``verify_in_text`` bekommen immer den Original-Text, nie
# den Ausschnitt) -- ein gekuerzter Prompt kann also nie dazu fuehren, dass
# ein tatsaechlich belegter Wert faelschlich verworfen wird.
_FIELD_KEYWORDS: dict[str, tuple[str, ...]] = {
    "rechnungsnummer": ("rechnungsnummer", "rechnungsnr", "rechnungs-nr", "rechnung",
                        "belegnummer", "beleg-nr", "invoice"),
    "rechnungsdatum": ("rechnungsdatum", "datum", "invoice-date"),
    "netto": ("netto", "nettobetrag", "nettosumme", "zwischensumme", "warenwert", "subtotal"),
    "ust_betrag": ("umsatzsteuer", "mehrwertsteuer", "ust", "mwst", "vat", "steuerbetrag"),
    "ust_satz": ("umsatzsteuer", "mehrwertsteuer", "mwst", "vat", "steuersatz"),
    "brutto": ("brutto", "bruttobetrag", "gesamtbetrag", "rechnungsbetrag", "endbetrag",
              "gesamtsumme", "zahlbetrag", "total"),
    "bestellnummer": ("bestellnummer", "bestellnr", "auftragsnummer", "order", "po"),
    "kostenstelle": ("kostenstelle", "kst"),
}
# Bank-/Zahlungsblock -- fuer lieferant_iban/lieferant_ustid (Stufe 2) wichtig,
# stehen aber nicht in _FIELD_KEYWORDS (kein eigenes STANDARD_FIELDS-Feld).
_BANK_KEYWORDS = ("iban", "bic", "bankverbindung", "konto", "ustid", "ust-idnr", "steuernummer")
# Positionsbereich (nur fuer extract_full mit line_items) -- grob, da die
# Tabellenspalten je Lieferant unterschiedlich heissen.
_LINE_KEYWORDS = ("pos", "menge", "beschreibung", "bezeichnung", "artikel", "einzelpreis")

_GAP_BUDGET = 1500     # Stufe-1-Luecken-Fuellung: wenige, meist klar benannte Felder
_FULL_EXCERPT_BUDGET = 3000  # Stufe 2: ganze Rechnung, aber Kopf+Summen+Bank+Positionen
_HEAD_TOKENS = 60      # Kopfbereich (Rechnungsnummer/-datum, Lieferant) in Wort-Token
_TAIL_TOKENS = 60      # Fuss-/Summenbereich
_WINDOW = 8            # Woerter vor/nach einem Schluesselwort-Treffer


def _targeted_excerpt(text: str, field_names: list[str], extra_keywords: tuple[str, ...] = (),
                      budget: int = _GAP_BUDGET) -> str:
    """Nur Kopf, Fuss (Summenblock) und die Umgebung von Schluesselwoertern
    der angefragten Felder -- statt der ganzen Rechnung. Bleibt der Ausschnitt
    trotzdem laenger als ``budget`` (z.B. sehr dichte Treffer) oder gibt es
    gar keine Schluesselwoerter zum Suchen, faellt die Funktion auf den
    bewaehrten Kopf+Fuss-Zeichenschnitt (``_head_and_tail``) zurueck -- nie
    auf mehr Text als vorher, aber auch nie weniger robust."""
    toks = text.split()
    n = len(toks)
    if n <= _HEAD_TOKENS + _TAIL_TOKENS:
        return text
    keep: set[int] = set(range(min(_HEAD_TOKENS, n))) | set(range(max(0, n - _TAIL_TOKENS), n))
    keywords = [norm(kw) for name in field_names for kw in _FIELD_KEYWORDS.get(name, ())]
    keywords += [norm(kw) for kw in extra_keywords]
    if keywords:
        for i, t in enumerate(toks):
            nt = norm(t)
            if nt and any(kw in nt for kw in keywords):
                keep.update(range(max(0, i - _WINDOW), min(n, i + _WINDOW + 1)))
    ordered = sorted(keep)
    out: list[str] = []
    prev = None
    for i in ordered:
        if prev is not None and i != prev + 1:
            out.append("[...]")
        out.append(toks[i])
        prev = i
    excerpt = " ".join(out)
    if not keywords or len(excerpt) > budget:
        return _head_and_tail(text, budget=max(budget, _TEXT_BUDGET))
    return excerpt

_PROMPT = """Du liest den Text einer deutschen Geschaeftsrechnung und gibst NUR ein JSON-Objekt zurueck, keinen Fliesstext, keine Erklaerung.

Gesuchte Felder (nur zurueckgeben, was tatsaechlich im Text steht -- nichts raten oder erfinden):
{fields}

Bereits vermutete Werte aus einer frueheren Rechnung desselben Lieferanten (nur Hinweis, nicht bindend -- widerspricht der Text, gilt der Text):
{hints}

Rechnungstext:
---
{text}
---

Antwort ausschliesslich als JSON-Objekt: {{"feldname": "wert", ...}}. Fehlt ein Feld im Text, lass den Schluessel weg."""

_avail_cache: tuple[float, bool] | None = None
_AVAIL_TTL = 30.0


def available() -> bool:
    """Ob der Fallback aktiv UND das konfigurierte Ollama erreichbar ist.

    Kurzzeitig gecacht, damit ein Batch-Lauf ueber viele Rechnungen bei
    abgeschaltetem/nicht erreichbarem Ollama nicht pro Datei erneut auf den
    Timeout wartet."""
    global _avail_cache
    cfg = _llm_cfg()
    if not cfg.enabled:
        return False
    now = time.monotonic()
    if _avail_cache is not None and now - _avail_cache[0] < _AVAIL_TTL:
        return _avail_cache[1]
    try:
        if cfg.backend == "openai":
            urllib.request.urlopen(urllib.request.Request(
                f"{cfg.base_url}/models", headers=_auth_headers(cfg)), timeout=1.5)
        else:
            urllib.request.urlopen(f"{cfg.base_url}/api/version", timeout=1.5)
        ok = True
    except (urllib.error.URLError, OSError, ValueError):
        ok = False
    _avail_cache = (now, ok)
    return ok


def release_model() -> None:
    """Modell nach einem KI-Stapel aus dem RAM entladen (SPEC Effizienz-
    Baustein 5: waehrend eines KI-Stapels geladen halten -- ``keep_alive``
    bei jedem Aufruf --, zwischen Stapeln freigeben). Ollama laedt es beim
    naechsten Aufruf einfach neu; nur fuer Ollama relevant (OpenAI-kompatible
    Server verwalten ihr Modell selbst). Reine Aufraeum-Operation -- kein
    Fehler, wenn das Modell aus/nicht erreichbar ist."""
    cfg = _llm_cfg()
    if not cfg.enabled or cfg.backend != "ollama":
        return
    body = json.dumps({"model": cfg.model, "keep_alive": 0}).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg.base_url}/api/generate", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except (urllib.error.URLError, OSError, ValueError):
        pass


def _dynamic_num_ctx(prompt: str, cap: int) -> int:
    """Kontextgroesse an die tatsaechliche Promptlaenge anpassen (SPEC
    Effizienz-Baustein 1) statt immer das Maximum zu reservieren --
    ``RECHNUNG_LLM_CTX`` ist damit eine Obergrenze (Laptop-RAM), kein fester
    Wert. Grobe Schaetzung: ~4 Zeichen/Token (Deutsch) plus Luft fuer die
    Antwort, auf die naechste gaengige Ollama-Kontextgroesse aufgerundet."""
    # ~2 Zeichen/Token statt 4: deutsche Rechnungstexte bestehen zu einem
    # grossen Teil aus Zahlen, Datumsangaben, IBANs und Satzzeichen, die je
    # 1-2 Zeichen ein Token kosten. Mit /4 lag ein typischer Prompt (2.800
    # Zeichen = 1.077 Token) knapp ueber 1024 -- Ollama schneidet dann still
    # den ANFANG ab (Anweisungen, Rechnungskopf, Summen) und die Antwort ist
    # unbrauchbar; danach folgte ein zweiter Volltext-Aufruf (Befund 09.10.2026:
    # Scan auf dem Laptop > 120 s). Lieber eine Stufe zu gross als abgeschnitten.
    needed = len(prompt) // 2 + 512
    for step in (512, 1024, 2048, 4096, 8192, 16384):
        if needed <= step:
            return min(step, cap)
    return cap


def _post_ollama(cfg, body: dict) -> str | None:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg.base_url}/api/generate", data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
            parsed = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    return parsed.get("response")


def _call_ollama(cfg, prompt: str, schema: dict | None = None) -> str | None:
    """SPEC Effizienz-Baustein 2: ein echtes JSON-Schema im ``format``-Feld
    statt nur ``"json"`` (Ollama >= 0.5) -- weniger kaputte/wiederholte
    Antworten. Scheitert der Aufruf mit Schema (z.B. aeltere Ollama-Version),
    einmal mit dem bisherigen ``"json"`` nachfassen statt leer zu bleiben."""
    body = {
        "model": cfg.model,
        "prompt": prompt,
        "stream": False,
        "format": schema if schema is not None else "json",
        "keep_alive": cfg.keep_alive,
        "options": {"temperature": 0.0, "num_ctx": _dynamic_num_ctx(prompt, cfg.num_ctx)},
    }
    raw = _post_ollama(cfg, body)
    if raw is None and schema is not None:
        body["format"] = "json"
        raw = _post_ollama(cfg, body)
    return raw


def _auth_headers(cfg) -> dict[str, str]:
    if not cfg.api_key:
        return {}
    if not cfg.base_url.startswith("https://") and not cfg.base_url.startswith(
            ("http://127.0.0.1", "http://localhost")):
        print("WARNUNG: RECHNUNG_LLM_API_KEY wird ueber eine nicht-https-URL "
              f"geschickt ({cfg.base_url}).", file=sys.stderr)
    return {"Authorization": f"Bearer {cfg.api_key}"}


def _post_openai(cfg, prompt: str, response_format: dict) -> str | None:
    body = json.dumps({
        "model": cfg.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "response_format": response_format,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg.base_url}/chat/completions", data=body,
        headers={"Content-Type": "application/json", **_auth_headers(cfg)},
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError,
            KeyError, IndexError, TypeError):
        return None


def _call_openai(cfg, prompt: str, schema: dict | None = None) -> str | None:
    """OpenAI-kompatibles /chat/completions (vLLM, llama.cpp-Server, ...).
    SPEC Effizienz-Baustein 2: ``response_format: json_schema`` wo ein Schema
    vorliegt, sonst (und als Rueckfallebene, falls der Server json_schema
    nicht kennt) das bisherige ``json_object``."""
    if schema is not None:
        raw = _post_openai(cfg, prompt, {
            "type": "json_schema",
            "json_schema": {"name": "extraktion", "schema": schema, "strict": True},
        })
        if raw is not None:
            return raw
    return _post_openai(cfg, prompt, {"type": "json_object"})


# Antwort-Cache je (Modell, Prompt): /api/invoice zeigt dem Menschen den
# Vorschlag, /api/confirm laeuft die Pipeline danach erneut -- ohne Cache
# koennte ein zweiter Modellaufruf (trotz temperature 0) minimal anders
# ausfallen, und bestaetigt waere etwas anderes als das Gesehene.
_resp_cache: dict[str, str] = {}
_RESP_CACHE_MAX = 64
# Zusaetzlich auf der Platte (``set_cache_dir``, von App/CLI gesetzt): eine
# Rechnung wird damit nur EINMAL vom Modell gelesen -- auch ueber Neustarts
# hinweg und auch, wenn die Hintergrund-Vorverarbeitung sie schon gelesen hat.
# Auf einem Laptop ohne Grafikkarte kostet ein Aufruf 30-90 s.
_cache_dir: Path | None = None


def set_cache_dir(path) -> None:
    global _cache_dir
    _cache_dir = Path(path) if path else None


def _disk_get(key: str) -> str | None:
    if _cache_dir is None:
        return None
    try:
        return (_cache_dir / f"{key}.json").read_text("utf-8")
    except OSError:
        return None


def _disk_put(key: str, raw: str) -> None:
    if _cache_dir is None:
        return
    try:
        _cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = _cache_dir / f"{key}.part"
        tmp.write_text(raw, "utf-8")
        os.replace(tmp, _cache_dir / f"{key}.json")
    except OSError:
        pass


def _call(cfg, prompt: str, schema: dict | None = None) -> str | None:
    schema_part = json.dumps(schema, sort_keys=True) if schema is not None else ""
    key = hashlib.sha256(f"{cfg.backend}|{cfg.model}|{schema_part}|{prompt}".encode("utf-8")).hexdigest()
    if key in _resp_cache:
        return _resp_cache[key]
    raw = _disk_get(key)
    if raw is None:
        raw = (_call_openai(cfg, prompt, schema) if cfg.backend == "openai"
               else _call_ollama(cfg, prompt, schema))
        if raw:
            _disk_put(key, raw)
    if raw:
        if len(_resp_cache) >= _RESP_CACHE_MAX:
            _resp_cache.pop(next(iter(_resp_cache)))
        _resp_cache[key] = raw
    return raw


def _parse_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):  # manche Modelle packen JSON in einen Codeblock
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def verify_in_text(text: str, value, value_type: str) -> bool:
    """Steht der (bereits geparste) Modellwert tatsaechlich im PDF-Text?
    IBAN/USt-IdNr stehen oft in Vierergruppen ueber mehr Woerter verteilt als
    ein Wortfenster fasst -- dort reicht der kompakte Teilstring (Pruefziffer
    bzw. Laenderformat machen Zufallstreffer praktisch unmoeglich)."""
    if value_type == "iban":
        return bool(value) and compact(value) in compact(text)
    if value_type == "ustid":
        return bool(value) and compact(value) in find_ustids(text)
    if value_type == "name":
        return bool(norm(str(value))) and norm(str(value)) in norm(text)
    return value_in_text(text, value, value_type)


def _check_value(text: str, name: str, val, vtype: str) -> tuple[object, str]:
    """-> (geparster Wert, "") oder (None, Grund fuer das Verwerfen)."""
    if isinstance(val, bool) or not isinstance(val, (str, int, float)):
        return None, "Schema: kein Text/Zahl"
    value, ok = coerce(str(val), vtype)
    if not ok:
        return None, "nicht als " + vtype + " lesbar"
    if vtype == "number" and compact(value) != compact(val):
        # coerce() hat nur einen Teil uebernommen ("RE 2026 1" -> "RE")
        return None, "Nummer nicht eindeutig lesbar"
    if not verify_in_text(text, value, vtype):
        return None, "nicht im PDF gefunden"
    return value, ""


def _gap_schema(field_names: list[str]) -> dict:
    """Schlankes JSON-Schema fuer die gezielte Luecken-Fuellung (nur die
    angefragten Felder, SPEC Effizienz-Baustein 2)."""
    props = {n: {"type": ["string", "number", "null"]} for n in field_names}
    return {"type": "object", "properties": props, "additionalProperties": False}


def extract(text: str, field_names: list[str], hints: dict[str, str],
            rejected: dict[str, str] | None = None) -> dict[str, FieldResult]:
    """Liefert je angefragtem, tatsaechlich gefundenem Feld ein FieldResult
    mit ``source="llm"``. Leer, wenn der Fallback aus/nicht erreichbar ist,
    keine Felder angefragt wurden, oder die Antwort nicht parsbar war.
    Werte, die nicht im Text stehen, landen statt im Ergebnis in
    ``rejected`` (Feld -> Grund).

    SPEC Effizienz-Baustein 1: die KI bekommt nur die Textumgebung der
    angefragten Felder (``_targeted_excerpt``), nicht die ganze Rechnung --
    belegt wird trotzdem immer gegen den vollen ``text`` (siehe
    ``_check_value``)."""
    cfg = _llm_cfg()
    sichtbar = ki_felder()
    field_names = [n for n in field_names if sichtbar.get(n, ("",))[0] in _SIMPLE_TYPES]
    if not cfg.enabled or not field_names or not available():
        return {}

    fields_desc = "\n".join(
        f"- {n} ({STANDARD_FIELDS.get(n, ('text',))[0]})" for n in field_names
    )
    hints_desc = "\n".join(f"- {k}: {v}" for k, v in hints.items() if v) or "(keine)"
    excerpt = _targeted_excerpt(text, field_names)
    prompt = _PROMPT.format(fields=fields_desc, hints=hints_desc, text=excerpt)

    parsed = _parse_json(_call(cfg, prompt, _gap_schema(field_names)))
    if parsed is None:
        return {}

    out: dict[str, FieldResult] = {}
    for name in field_names:
        val = parsed.get(name)
        if val in (None, ""):
            continue
        vtype = STANDARD_FIELDS.get(name, ("text", False))[0]
        value, why = _check_value(text, name, val, vtype)
        if why:
            if rejected is not None:
                rejected[name] = f"{why}: {val}"
            continue
        conf = min(cfg.max_confidence, 0.6)
        out[name] = FieldResult(name, value, str(val), round(conf, 2), "llm")
    return out


# ---- Stufe 2: komplette Rechnung ohne Template --------------------------
# Lieferantenkennungen kommen mit eigenem Praefix zurueck, damit sie nicht mit
# den Stammdaten-Feldern (iban/ustid aus dem Lieferanten-Datensatz)
# verwechselt werden.
_SUPPLIER_KEYS = {"lieferant_name": "name", "lieferant_iban": "iban",
                  "lieferant_ustid": "ustid"}
# Rechnungsempfaenger (an wen die Rechnung adressiert ist) -- nur Vorschlag
# fuer eine noch unbekannte Rechnungsadresse (Abschnitt "1 Rechnungsadresse",
# 09.10.2026), nie Identitaet; angelegt wird erst per Klick.
_RECIPIENT_KEYS = {"empfaenger_name": "name", "empfaenger_anschrift": "anschrift"}
# Weitere Stammdaten des Rechnungsstellers (Anschrift, Bank, Steuernummer,
# Kontakt) -- fuellen nur leere Felder eines Lieferanten-ENTWURFS vor
# (Wunsch Aaron 09.10.2026), nie Identitaet, gleiche Beleg-Pflicht.
_SUPPLIER_DETAIL_KEYS = {"lieferant_anschrift": "address_lines", "lieferant_bank": "bank_name",
                         "lieferant_bic": "bic", "lieferant_steuernummer": "creditor_id",
                         "lieferant_kontakt": "contact"}
_LINE_KEYS = {"beschreibung": "text", "menge": "amount", "einzelpreis": "amount",
              "netto": "amount", "ust_satz": "amount"}


def _schema(with_lines: bool) -> dict:
    """JSON-Schema der erwarteten Antwort -- geht als Vorgabe in den Prompt und
    ist Grundlage der Pruefung in ``_validate``."""
    scalar = {"type": ["string", "number", "null"]}
    props = {n: scalar for n, (t, _r) in ki_felder().items() if t in _SIMPLE_TYPES}
    props.update({k: {"type": ["string", "null"]} for k in _SUPPLIER_KEYS})
    props.update({k: {"type": ["string", "null"]} for k in _RECIPIENT_KEYS})
    props.update({k: {"type": ["string", "null"]} for k in _SUPPLIER_DETAIL_KEYS})
    if with_lines:
        props["positionen"] = {"type": "array", "items": {
            "type": "object", "properties": {k: scalar for k in _LINE_KEYS}}}
    return {"type": "object", "properties": props, "additionalProperties": False}


def _type_ok(val, types) -> bool:
    types = [types] if isinstance(types, str) else types
    py = {"string": str, "number": (int, float), "null": type(None),
          "array": list, "object": dict}
    return any(isinstance(val, py[t]) and not (t == "number" and isinstance(val, bool))
               for t in types)


def _validate(parsed: dict, schema: dict, rejected: dict[str, str]) -> dict:
    """Schluessel ausserhalb des Schemas ignorieren, falsch typisierte Werte
    verwerfen (mit Grund in ``rejected``) -- nie die ganze Antwort, nur das
    betroffene Feld."""
    out = {}
    for key, val in parsed.items():
        spec = schema["properties"].get(key)
        if spec is None:
            continue
        if not _type_ok(val, spec["type"]):
            rejected[key] = "Schema: falscher Typ"
            continue
        if spec["type"] == "array":
            item_props = spec["items"]["properties"]
            val = [{k: v for k, v in it.items() if k in item_props
                    and _type_ok(v, item_props[k]["type"])}
                   for it in val if isinstance(it, dict)]
        out[key] = val
    return out


@dataclass
class FullExtraction:
    fields: dict[str, FieldResult] = field(default_factory=dict)
    supplier: dict[str, str] = field(default_factory=dict)  # name/iban/ustid, belegt
    empfaenger: dict[str, object] = field(default_factory=dict)  # name/lines, belegt
    # address_lines (Liste)/bank_name/bic/creditor_id/contact, belegt
    lieferant_details: dict[str, object] = field(default_factory=dict)
    line_items: list[dict] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)  # Feld -> Grund


_PROMPT_FULL = """Du liest den Text einer Geschaeftsrechnung (Deutschland, Schweiz oder EU) und gibst NUR ein JSON-Objekt zurueck, keinen Fliesstext.

Gib jeden Wert GENAU so wieder, wie er im Text steht (gleiche Schreibweise, keine Umrechnung) -- jeder Wert wird anschliessend im Text gesucht und verworfen, wenn er dort nicht vorkommt. Nichts raten oder erfinden; unbekannte Felder auf null setzen.

Felder:
- rechnungsnummer, rechnungsdatum, netto, ust_betrag, ust_satz (Prozent), brutto, bestellnummer, kostenstelle
- lieferant_name, lieferant_iban, lieferant_ustid: Rechnungssteller (NICHT der Rechnungsempfaenger). Der Rechnungssteller steht im Briefkopf/Logo, in der Fusszeile und bei der Bankverbindung ("Zahlbar an", "Konto"); der Empfaenger steht im Adressfeld ("Zahlbar durch").
- Steuersaetze stehen auf der Rechnung (Deutschland z.B. 19/7, Schweiz MWST 8,1/2,6/3,8) -- nie einen Satz annehmen, der nicht im Text steht.
- lieferant_anschrift (Strasse, PLZ Ort, Land mit Komma getrennt), lieferant_bank (Name der Bank), lieferant_bic, lieferant_steuernummer, lieferant_kontakt (Telefon/E-Mail): ebenfalls vom Rechnungssteller
- empfaenger_name, empfaenger_anschrift: Rechnungsempfaenger (an wen die Rechnung adressiert ist); Anschrift als Strasse, PLZ Ort mit Komma getrennt
{lines}
JSON-Schema der Antwort:
{schema}

Rechnungstext:
---
{text}
---"""


def _recipient(data: dict, verify_text: str, res: "FullExtraction") -> None:
    """Rechnungsempfaenger mit Beleg-Pflicht: Name muss im Text stehen, von
    der Anschrift bleiben nur die Teile, die im Text stehen."""
    name = str(data.get("empfaenger_name") or "").strip()
    if name:
        if verify_in_text(verify_text, name, "name"):
            res.empfaenger["name"] = name
        else:
            res.rejected["empfaenger_name"] = f"nicht im PDF gefunden: {name}"
    raw = data.get("empfaenger_anschrift")
    if isinstance(raw, str) and raw.strip():
        parts = [x.strip() for x in re.split(r"[,\n]", raw) if x.strip()]
        ok = [x for x in parts if verify_in_text(verify_text, x, "name")]
        if ok:
            res.empfaenger["lines"] = ok
        if len(ok) < len(parts):
            res.rejected["empfaenger_anschrift"] = f"teilweise nicht im PDF gefunden: {raw}"


def _split_verified(raw, verify_text: str) -> tuple[list[str], bool]:
    """Kommagetrennte Angabe in Teile zerlegen, nur im Text belegte behalten
    -> (belegte Teile, alle belegt?)."""
    parts = [x.strip() for x in re.split(r"[,\n;]", str(raw)) if x.strip()]
    ok = [x for x in parts if verify_in_text(verify_text, x, "name")]
    return ok, len(ok) == len(parts)


def _supplier_details(data: dict, verify_text: str, res: "FullExtraction") -> None:
    """Anschrift/Bank/BIC/Steuernummer/Kontakt des Rechnungsstellers mit
    Beleg-Pflicht (Anschrift und Kontakt teilweise, Rest ganz oder gar nicht)."""
    for key, kind in _SUPPLIER_DETAIL_KEYS.items():
        raw = data.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        if kind in ("address_lines", "contact"):
            ok, alle = _split_verified(raw, verify_text)
            if ok:
                res.lieferant_details[kind] = ok if kind == "address_lines" else ", ".join(ok)
            if not alle:
                res.rejected[key] = f"teilweise nicht im PDF gefunden: {raw}"
            continue
        val = raw.strip()
        if verify_in_text(verify_text, val, "name"):
            res.lieferant_details[kind] = val.upper().replace(" ", "") if kind == "bic" else val
        else:
            res.rejected[key] = f"nicht im PDF gefunden: {val}"


def _extract_full_once(prompt_text: str, verify_text: str, cfg, schema: dict,
                       want_lines: bool) -> FullExtraction:
    """Ein Modellaufruf: ``prompt_text`` geht an das Modell, belegt wird
    immer gegen ``verify_text`` (SPEC Effizienz-Baustein 1: bei der gezielten
    Variante ist das der VOLLE Rechnungstext, auch wenn nur ein Ausschnitt im
    Prompt stand -- ein Modellwert aus dem Ausschnitt muss trotzdem irgendwo
    im ganzen Beleg stehen, sonst gilt er als nicht belegt). ``want_lines``:
    Positionen ueberhaupt erfragen -- bei Supplier.positionsmodus
    'eine_position' (Teil B) immer ``False``: die Positionen werden dort
    ausschliesslich aus der USt-Aufstellung gebildet, keine KI-Zeitersparnis
    sonst bei 10+ Seiten Einzelpositionen."""
    res = FullExtraction()
    lines = ("- positionen: Liste der Rechnungspositionen mit beschreibung, menge, "
             "einzelpreis, netto (Positionssumme), ust_satz\n" if want_lines else "")
    prompt = _PROMPT_FULL.format(lines=lines, schema=json.dumps(schema, ensure_ascii=False),
                                 text=prompt_text)
    parsed = _parse_json(_call(cfg, prompt, schema))
    if parsed is None:
        return res
    data = _validate(parsed, schema, res.rejected)
    conf = round(min(cfg.max_confidence, 0.6), 2)

    for name, (vtype, _req) in ki_felder().items():
        val = data.get(name)
        if vtype not in _SIMPLE_TYPES or val in (None, ""):
            continue
        value, why = _check_value(verify_text, name, val, vtype)
        if why:
            res.rejected[name] = f"{why}: {val}"
            continue
        res.fields[name] = FieldResult(name, value, str(val), conf, "llm")

    for key, kind in _SUPPLIER_KEYS.items():
        val = data.get(key)
        if not val:
            continue
        if kind == "iban":
            value, ok = coerce(val, "iban")
            ok = ok and iban_valid(value)
        elif kind == "ustid":
            value, ok = coerce(val, "ustid")
        else:
            value, ok = str(val).strip(), True
        if not ok or not verify_in_text(verify_text, value, kind):
            res.rejected[key] = f"nicht im PDF gefunden/ungueltig: {val}"
            continue
        res.supplier[kind] = value
    _recipient(data, verify_text, res)
    _supplier_details(data, verify_text, res)
    for kind in ("iban", "ustid"):
        if kind in res.supplier:
            res.fields[kind] = FieldResult(kind, res.supplier[kind], res.supplier[kind],
                                           conf, "llm")

    dropped = 0
    for it in data.get("positionen") or []:
        item: dict = {}
        for k, vtype in _LINE_KEYS.items():
            if it.get(k) in (None, ""):
                continue
            item[k], ok = coerce(str(it[k]), vtype)
            if not ok:
                item.pop(k)
        # Position nur behalten, wenn ihr Betrag im Text belegt ist
        if item.get("netto") is None or not value_in_text(verify_text, item["netto"], "amount"):
            dropped += 1
            continue
        item["_quelle"] = "llm"
        res.line_items.append(item)
    if dropped:
        res.rejected["positionen"] = f"{dropped} Position(en) ohne belegten Betrag verworfen"
    elif want_lines and not res.line_items:
        # sichtbar machen statt still leer (Befund Aaron 12.10.2026)
        res.rejected["positionen"] = "KI hat keine Positionen geliefert"
    return res


def extract_full(text: str, want_line_items: bool | None = None) -> FullExtraction:
    """Komplette Rechnung strukturiert auslesen (Stufe 2). Leer, wenn aus,
    nicht erreichbar oder unbrauchbare Antwort. Jedes Feld ist im Text belegt
    -- alles andere steht mit Grund in ``rejected``.

    SPEC Effizienz-Baustein 1: die Volllesung bekommt zuerst nur einen
    gekuerzten Ausschnitt (Kopf + Summen-/Bankbloecke + ggf. Positionsbereich,
    ``_targeted_excerpt``). Fehlt danach noch ein PFLICHTFELD (nicht nur ein
    optionales), fasst genau einmal mit dem vollen Text nach, statt zu raten
    -- der Ausschnitt kann ein Feld verpassen, das keinem der bekannten
    Schluesselwoerter nahe steht.

    ``want_line_items``: ueberschreibt ``config.llm().line_items`` nach
    unten (Teil B, Supplier.positionsmodus 'eine_position') -- die KI wird
    dann gar nicht erst nach Positionen gefragt, egal was die Konfiguration
    sagt. ``None`` (Standard) uebernimmt die Konfiguration unveraendert."""
    cfg = _llm_cfg()
    res = FullExtraction()
    if not cfg.enabled or not cfg.full_extraction or not available():
        return res
    line_items = cfg.line_items if want_line_items is None else (want_line_items and cfg.line_items)
    schema = _schema(line_items)
    extra = _BANK_KEYWORDS + (_LINE_KEYWORDS if line_items else ())
    excerpt = _targeted_excerpt(text, list(ki_felder()), extra_keywords=extra,
                               budget=_FULL_EXCERPT_BUDGET)
    res = _extract_full_once(excerpt, text, cfg, schema, line_items)

    missing_required = [n for n, (vtype, req) in ki_felder().items()
                        if req and vtype in _SIMPLE_TYPES
                        and n not in res.fields and n not in res.rejected]
    if missing_required and excerpt != text:
        retry = _extract_full_once(text, text, cfg, schema, line_items)
        for n, fr in retry.fields.items():
            res.fields.setdefault(n, fr)
        for kind, value in retry.supplier.items():
            res.supplier.setdefault(kind, value)
        for kind, value in retry.empfaenger.items():
            res.empfaenger.setdefault(kind, value)
        for kind, value in retry.lieferant_details.items():
            res.lieferant_details.setdefault(kind, value)
        if not res.line_items and retry.line_items:
            res.line_items = retry.line_items
        for n, why in retry.rejected.items():
            res.rejected.setdefault(n, why)
    if res.line_items and res.rejected.get("positionen") == "KI hat keine Positionen geliefert":
        res.rejected.pop("positionen")
    _absender_nicht_empfaenger(res)
    return res


def _absender_nicht_empfaenger(res: "FullExtraction") -> None:
    """Kleine Modelle verwechseln auf Scans gern Rechnungssteller und
    -empfaenger (Befund 09.10.2026: als Lieferant stand das Hotel selbst).
    Ist der gelesene Lieferantenname derselbe wie der Empfaenger, ist einer
    von beiden falsch -- dann lieber keinen Lieferantennamen als den falschen."""
    lief = norm(str(res.supplier.get("name") or ""))
    empf = norm(str(res.empfaenger.get("name") or ""))
    if lief and empf and (lief == empf or lief in empf or empf in lief):
        res.supplier.pop("name", None)
        res.rejected["lieferant_name"] = ("gleich dem Rechnungsempfaenger -- Aussteller "
                                          "und Empfaenger vermutlich verwechselt")
