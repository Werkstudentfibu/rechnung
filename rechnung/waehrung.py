"""Waehrung einer Rechnung sicher erkennen (Postfach-Baustein 7).

Liest aus dem Rechnungstext, in welcher Waehrung fakturiert wird (CHF/Fr./
SFr. oder EUR/Euro/€) und fuehrt sie als eigenes Feld ``waehrung`` --
geht unveraendert an JobRouter, OHNE jede Umrechnung (die macht die
Buchhaltung). Es gibt regelmaessig Schweizer Lieferanten, die auch in Euro
fakturieren -- die Waehrung ist also pro RECHNUNG zu bestimmen, nicht nur
einmal je Lieferant (siehe ``identify.Supplier.currency``, das bleibt ein
reiner Vorschlag fuer einen neuen Lieferanten).

Nichts wird geraten: stehen beide Hinweise auf der Rechnung (CHF und EUR)
oder eine andere Waehrung (USD, GBP, ...) im Text, bleibt das Feld leer und
die Pruefung ``waehrung`` ist rot -- Mensch. Findet sich gar kein Hinweis,
gilt wie bisher EUR als stillschweigende Standardannahme (die Pruefung ist
dann gruen, das Feld bleibt aber leer -- es wurde ja nichts gelesen)."""

from __future__ import annotations

import re

from .identify import DEFAULT_CURRENCY
from .template import FieldResult
from .validate import Check

SOURCE = "text"
CONFIDENCE = 0.85

KNOWN = (DEFAULT_CURRENCY, "CHF")

_CHF_WORD_RE = re.compile(r"(?i)\bCHF\b|\bSFr\.?\b")
# "Fr." nur in Zahlennaehe werten -- sonst Verwechslung mit "Fr." = Frau/Freitag
_CHF_FR_RE = re.compile(r"(?i)\bFr\.\s*(?=\d)|(?<=\d)\s*Fr\.\b")
_EUR_RE = re.compile(r"(?i)\bEUR\b|\bEuro\b")
_FOREIGN_RE = re.compile(r"(?i)\bUSD\b|\bGBP\b|\bUS-Dollar\b|\bPfund\s+Sterling\b|[$£]")


def detect(text: str) -> tuple[str | None, str]:
    """-> (Waehrung|None, Grund falls unklar/fremd; beides leer, wenn gar
    kein Hinweis im Text steht)."""
    text = text or ""
    chf = bool(_CHF_WORD_RE.search(text) or _CHF_FR_RE.search(text))
    eur = bool(_EUR_RE.search(text) or "€" in text)
    if chf and eur:
        return None, "Waehrung nicht eindeutig: CHF und EUR auf der Rechnung"
    if chf:
        return "CHF", ""
    if eur:
        return DEFAULT_CURRENCY, ""
    m = _FOREIGN_RE.search(text)
    if m:
        return None, f"Waehrung nicht EUR/CHF ({m.group(0)}) -- bitte pruefen"
    return None, ""


def apply(fields: dict, text: str, hinweise: list[str] | None = None) -> None:
    """Fehlendes Feld ``waehrung`` aus dem Text ergaenzen (Vorlage/
    E-Rechnung/Mensch haben Vorrang). Mutiert ``fields``/``hinweise``."""
    cur = fields.get("waehrung")
    if cur is not None and cur.source != "missing" and cur.value not in (None, ""):
        return
    code, why = detect(text)
    if code:
        fields["waehrung"] = FieldResult("waehrung", code, code, CONFIDENCE, SOURCE)
    elif why and hinweise is not None and why not in hinweise:
        hinweise.append(why)


def checks(fields: dict, text: str) -> list[Check]:
    """Pruefung ``waehrung``: erkannte Waehrung ist EUR oder CHF (gruen);
    mehrdeutig oder eine andere Waehrung ist rot -> Mensch. Kein Hinweis im
    Text ist (wie bisher) kein Fehler -- stillschweigend EUR angenommen."""
    fr = fields.get("waehrung")
    if fr is not None and fr.source != "missing" and fr.value:
        from .validate import _norm_currency
        wert = _norm_currency(fr.value)      # "€"/"Euro" ist EUR
        ok = wert in KNOWN
        return [Check("waehrung", ok, wert if ok else f"unbekannte Waehrung: {fr.value}")]
    _, why = detect(text)
    return [Check("waehrung", not why, why or f"{DEFAULT_CURRENCY} (angenommen)")]
