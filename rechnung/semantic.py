"""Semantischer Fallback ueber ein lokales LLM (Ollama), siehe SPEC-v1.md §7f.

Ergaenzt die Template-Extraktion, ersetzt sie nicht: `apply_template()`
bleibt die primaere Quelle. Dieser Layer greift nur bei Feldern, die dort
fehlen oder schwach sind, und bekommt das bisher erkannte Template-Ergebnis
als *Hinweis* mit -- nicht als Zwang. Damit toleriert er Layout-Abweichungen,
die eine starre Anker-Position verpasst, ohne dass ein Lieferant deshalb neu
angelernt werden muss.

Standardmaessig AUS (``RECHNUNG_LLM_ENABLED``). Ohne das laeuft das Programm
exakt wie zuvor -- kein Netzwerkzugriff, keine neue Abhaengigkeit (reines
stdlib ``urllib`` gegen ein lokales Ollama auf ``127.0.0.1``).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from .config import llm as _llm_cfg
from .template import STANDARD_FIELDS, FieldResult
from .textnorm import coerce

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


def extract(text: str, field_names: list[str], hints: dict[str, str]) -> dict[str, FieldResult]:
    """Liefert je angefragtem, tatsaechlich gefundenem Feld ein FieldResult
    mit ``source="llm"``. Leer, wenn der Fallback aus/nicht erreichbar ist,
    keine Felder angefragt wurden, oder die Antwort nicht parsbar war."""
    cfg = _llm_cfg()
    field_names = [n for n in field_names if STANDARD_FIELDS.get(n, ("text",))[0] in _SIMPLE_TYPES]
    if not cfg.enabled or not field_names or not available():
        return {}

    fields_desc = "\n".join(
        f"- {n} ({STANDARD_FIELDS.get(n, ('text',))[0]})" for n in field_names
    )
    hints_desc = "\n".join(f"- {k}: {v}" for k, v in hints.items() if v) or "(keine)"
    prompt = _PROMPT.format(fields=fields_desc, hints=hints_desc, text=_head_and_tail(text))

    raw = _call_ollama(cfg, prompt)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}

    out: dict[str, FieldResult] = {}
    for name in field_names:
        val = parsed.get(name)
        if val in (None, ""):
            continue
        vtype = STANDARD_FIELDS.get(name, ("text", False))[0]
        value, ok = coerce(str(val), vtype)
        if not ok:
            continue
        conf = min(cfg.max_confidence, 0.6)
        out[name] = FieldResult(name, value, str(val), round(conf, 2), "llm")
    return out
