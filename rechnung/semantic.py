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
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .config import llm as _llm_cfg
from .template import STANDARD_FIELDS, FieldResult
from .textnorm import coerce, compact, find_ustids, iban_valid, norm, value_in_text

# Typen, die als einfacher String zurueckkommen -- taxblock/Positionen sind
# strukturiert und bewusst (noch) nicht Teil dieses Fallbacks (v1 von §7f).
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


def _call_ollama(cfg, prompt: str) -> str | None:
    body = json.dumps({
        "model": cfg.model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": cfg.keep_alive,
        "options": {"temperature": 0.0, "num_ctx": cfg.num_ctx},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{cfg.base_url}/api/generate", data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    return data.get("response")


def _auth_headers(cfg) -> dict[str, str]:
    if not cfg.api_key:
        return {}
    if not cfg.base_url.startswith("https://") and not cfg.base_url.startswith(
            ("http://127.0.0.1", "http://localhost")):
        print("WARNUNG: RECHNUNG_LLM_API_KEY wird ueber eine nicht-https-URL "
              f"geschickt ({cfg.base_url}).", file=sys.stderr)
    return {"Authorization": f"Bearer {cfg.api_key}"}


def _call_openai(cfg, prompt: str) -> str | None:
    """OpenAI-kompatibles /chat/completions (vLLM, llama.cpp-Server, ...)."""
    body = json.dumps({
        "model": cfg.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "response_format": {"type": "json_object"},
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


# Antwort-Cache je (Modell, Prompt): /api/invoice zeigt dem Menschen den
# Vorschlag, /api/confirm laeuft die Pipeline danach erneut -- ohne Cache
# koennte ein zweiter Modellaufruf (trotz temperature 0) minimal anders
# ausfallen, und bestaetigt waere etwas anderes als das Gesehene.
_resp_cache: dict[str, str] = {}
_RESP_CACHE_MAX = 64


def _call(cfg, prompt: str) -> str | None:
    key = hashlib.sha256(f"{cfg.backend}|{cfg.model}|{prompt}".encode("utf-8")).hexdigest()
    if key in _resp_cache:
        return _resp_cache[key]
    raw = _call_openai(cfg, prompt) if cfg.backend == "openai" else _call_ollama(cfg, prompt)
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


def extract(text: str, field_names: list[str], hints: dict[str, str],
            rejected: dict[str, str] | None = None) -> dict[str, FieldResult]:
    """Liefert je angefragtem, tatsaechlich gefundenem Feld ein FieldResult
    mit ``source="llm"``. Leer, wenn der Fallback aus/nicht erreichbar ist,
    keine Felder angefragt wurden, oder die Antwort nicht parsbar war.
    Werte, die nicht im Text stehen, landen statt im Ergebnis in
    ``rejected`` (Feld -> Grund)."""
    cfg = _llm_cfg()
    field_names = [n for n in field_names if STANDARD_FIELDS.get(n, ("text",))[0] in _SIMPLE_TYPES]
    if not cfg.enabled or not field_names or not available():
        return {}

    fields_desc = "\n".join(
        f"- {n} ({STANDARD_FIELDS.get(n, ('text',))[0]})" for n in field_names
    )
    hints_desc = "\n".join(f"- {k}: {v}" for k, v in hints.items() if v) or "(keine)"
    prompt = _PROMPT.format(fields=fields_desc, hints=hints_desc, text=_head_and_tail(text))

    parsed = _parse_json(_call(cfg, prompt))
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
_LINE_KEYS = {"beschreibung": "text", "menge": "amount", "einzelpreis": "amount",
              "netto": "amount", "ust_satz": "amount"}


def _schema(with_lines: bool) -> dict:
    """JSON-Schema der erwarteten Antwort -- geht als Vorgabe in den Prompt und
    ist Grundlage der Pruefung in ``_validate``."""
    scalar = {"type": ["string", "number", "null"]}
    props = {n: scalar for n, (t, _r) in STANDARD_FIELDS.items() if t in _SIMPLE_TYPES}
    props.update({k: {"type": ["string", "null"]} for k in _SUPPLIER_KEYS})
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
    line_items: list[dict] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)  # Feld -> Grund


_PROMPT_FULL = """Du liest den Text einer deutschen Geschaeftsrechnung und gibst NUR ein JSON-Objekt zurueck, keinen Fliesstext.

Gib jeden Wert GENAU so wieder, wie er im Text steht (gleiche Schreibweise, keine Umrechnung) -- jeder Wert wird anschliessend im Text gesucht und verworfen, wenn er dort nicht vorkommt. Nichts raten oder erfinden; unbekannte Felder auf null setzen.

Felder:
- rechnungsnummer, rechnungsdatum, netto, ust_betrag, ust_satz (Prozent), brutto, bestellnummer, kostenstelle
- lieferant_name, lieferant_iban, lieferant_ustid: Rechnungssteller (NICHT der Rechnungsempfaenger)
{lines}
JSON-Schema der Antwort:
{schema}

Rechnungstext:
---
{text}
---"""


def extract_full(text: str) -> FullExtraction:
    """Komplette Rechnung strukturiert auslesen (Stufe 2). Leer, wenn aus,
    nicht erreichbar oder unbrauchbare Antwort. Jedes Feld ist im Text belegt
    -- alles andere steht mit Grund in ``rejected``."""
    cfg = _llm_cfg()
    res = FullExtraction()
    if not cfg.enabled or not cfg.full_extraction or not available():
        return res
    schema = _schema(cfg.line_items)
    lines = ("- positionen: Liste der Rechnungspositionen mit beschreibung, menge, "
             "einzelpreis, netto (Positionssumme), ust_satz\n" if cfg.line_items else "")
    prompt = _PROMPT_FULL.format(lines=lines, schema=json.dumps(schema, ensure_ascii=False),
                                 text=_head_and_tail(text))
    parsed = _parse_json(_call(cfg, prompt))
    if parsed is None:
        return res
    data = _validate(parsed, schema, res.rejected)
    conf = round(min(cfg.max_confidence, 0.6), 2)

    for name, (vtype, _req) in STANDARD_FIELDS.items():
        val = data.get(name)
        if vtype not in _SIMPLE_TYPES or val in (None, ""):
            continue
        value, why = _check_value(text, name, val, vtype)
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
        if not ok or not verify_in_text(text, value, kind):
            res.rejected[key] = f"nicht im PDF gefunden/ungueltig: {val}"
            continue
        res.supplier[kind] = value
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
        if item.get("netto") is None or not value_in_text(text, item["netto"], "amount"):
            dropped += 1
            continue
        item["_quelle"] = "llm"
        res.line_items.append(item)
    if dropped:
        res.rejected["positionen"] = f"{dropped} Position(en) ohne belegten Betrag verworfen"
    return res
