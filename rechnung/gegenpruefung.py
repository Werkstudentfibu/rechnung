"""Gegenpruefung fuer neue Lieferanten (Aaron 19.11.2026: "Setze alles um" --
Hebel 4, damit auch der lange Schwanz kleiner Lieferanten ohne Vorlage sicher
werden kann).

Ein Wert, den nur die KI geliefert hat, gilt nie allein als sicher (fester
Deckel, ``semantic.ki_konfidenz``). Diese Stufe liest die Pflichtfelder ein
zweites Mal, ohne KI und unabhaengig von ihr, mit woertlichen Regeln:

- Rechnungsnummer: Wert hinter einem Rechnungsnummer-Label ("Rechnungs-Nr.",
  "Rechnungsnummer", "Invoice No." ...), nur wenn genau ein Kandidat mit
  Ziffer gefunden wird;
- Rechnungsdatum: Datum hinter einem Rechnungsdatum-Label ("Rechnungsdatum",
  "Invoice date", "Rechnung vom" ...), nur wenn eindeutig -- ein blosses
  "Datum:" zaehlt nicht (Liefer-/Bestelldatum);
- Netto/USt/Brutto: Betragsherleitung aus dem Kontext (``summen.py``), nur
  ein eindeutiger, rechnerisch aufgehender Sieger.

Stimmen KI und zweite Lesung bei einem Feld ueberein, gilt dieses Feld als
belegt. Angewendet (KI-Wert auf ``CONF_MIN`` gehoben) wird das nur, wenn

- der Schalter an ist (``RECHNUNG_KI_GEGENPRUEFUNG=1``, Standard AUS -- erst
  nach den Blindtest-Zahlen auf dem Server einschalten),
- der Lieferant ueber eine harte Kennung (USt-IdNr/IBAN) aus den Stammdaten
  erkannt wurde (nicht nur ueber den Namen),
- und ALLE Pflichtfelder belegt sind (sonst bleibt alles unsicher).

Alle uebrigen Pruefungen (Rechnung geht auf, IBAN gegen Stammdaten, Dublette
...) bleiben unveraendert und koennen die Rechnung weiter zum Menschen
schicken. Das Ergebnis steht immer in ``res.gegenpruefung`` (auch bei
ausgeschaltetem Schalter) -- der Blindtest misst daraus, wie viele
Rechnungen mit Gegenpruefung durchgegangen waeren und ob sie richtig waren.
"""

from __future__ import annotations

import dataclasses
import os
import re

from .template import STANDARD_FIELDS
from .textnorm import parse_amount, parse_date

PFLICHTFELDER = tuple(n for n, (_t, req) in STANDARD_FIELDS.items() if req)
# Score-Grenze, ab der identify.py einen Lieferanten ueber USt-IdNr/IBAN
# (nicht nur ueber Name/Layout) erkannt hat
HART_ERKANNT = 0.5

_NUMMER_RE = re.compile(
    r"(?i)\b(?:rechnungs?[-\s]?(?:nr|nummer|no)\.?|rechnung\s+nr\.?|invoice[-\s]?(?:no|number|nr)\.?"
    r"|invoice\s*#|facture\s*n[o°]\.?|fattura\s*n\.?)\s*[:.#]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-/._]*)")
_DATUM_RE = re.compile(
    r"(?i)\b(?:rechnungs?[-\s]?datum|datum\s+der\s+rechnung|rechnung\s+vom|invoice\s+date"
    r"|date\s+of\s+invoice|date\s+de\s+(?:la\s+)?facture|data\s+(?:della\s+)?fattura)"
    r"\s*[:.]?\s*(\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}\.?\s+[A-Za-zÄÖÜäöü]+\.?\s+\d{4})")


def aktiv() -> bool:
    v = os.environ.get("RECHNUNG_KI_GEGENPRUEFUNG", "0").strip().lower()
    return v in ("1", "ja", "true", "an", "on", "yes")


def _nummer_norm(v) -> str:
    return re.sub(r"\s+", "", str(v or "")).rstrip(".:,;").casefold()


def _eindeutig(werte: list) -> object | None:
    werte = [w for w in werte if w not in (None, "")]
    return werte[0] if werte and all(w == werte[0] for w in werte) else None


def zweite_lesung(text: str) -> dict:
    """Pflichtfelder ohne KI, nur woertliche Regeln -> {feld: wert}; Felder
    ohne eindeutigen Treffer fehlen."""
    from .summen import summen_aus_kontext

    out: dict = {}
    nummern = [m.group(1).rstrip(".:,;") for m in _NUMMER_RE.finditer(text or "")]
    nummern = [n for n in nummern if any(c.isdigit() for c in n)]
    nr = _eindeutig([_nummer_norm(n) for n in nummern])
    if nr:
        out["rechnungsnummer"] = next(n for n in nummern if _nummer_norm(n) == nr)
    daten = [parse_date(m.group(1)) for m in _DATUM_RE.finditer(text or "")]
    d = _eindeutig(daten)
    if d:
        out["rechnungsdatum"] = d.isoformat()
    erg = summen_aus_kontext(text or "")
    for feld in ("netto", "ust_betrag", "brutto"):
        v = getattr(erg, feld, None)
        if v is not None:
            out[feld] = round(float(v), 2)
    return out


def _gleich(feld: str, a, b) -> bool:
    if a in (None, "") or b in (None, ""):
        return False
    typ = STANDARD_FIELDS.get(feld, ("text",))[0]
    if typ == "amount":
        try:
            x = float(a) if isinstance(a, (int, float)) else parse_amount(str(a))
            y = float(b) if isinstance(b, (int, float)) else parse_amount(str(b))
        except (TypeError, ValueError):
            return False
        return x is not None and y is not None and abs(x - y) <= 0.01 + 1e-9
    if typ == "date":
        x, y = parse_date(str(a)), parse_date(str(b))
        return x is not None and x == y
    return _nummer_norm(a) == _nummer_norm(b)


def pruefen(res, text: str) -> dict:
    """-> {"lesung", "belegt": [Felder, in denen KI und zweite Lesung
    uebereinstimmen], "hart_erkannt", "vollstaendig"}"""
    lesung = zweite_lesung(text)
    belegt = []
    for feld in PFLICHTFELDER:
        fr = (res.fields or {}).get(feld)
        if fr is None or fr.source != "llm" or fr.value in (None, ""):
            continue
        if _gleich(feld, fr.value, lesung.get(feld)):
            belegt.append(feld)
    hart = bool(res.supplier_id) and (res.match_score or 0) >= HART_ERKANNT
    ki_pflicht = [f for f in PFLICHTFELDER
                  if (fr := (res.fields or {}).get(f)) is not None and fr.source == "llm"]
    vollstaendig = (all(f in (res.fields or {}) and res.fields[f].value not in (None, "")
                        for f in PFLICHTFELDER)
                    and bool(ki_pflicht) and set(ki_pflicht) <= set(belegt))
    return {"lesung": {k: str(v) for k, v in lesung.items()}, "belegt": belegt,
            "hart_erkannt": hart, "vollstaendig": vollstaendig}


def heben(res, felder) -> None:
    """KI-Werte der genannten Felder auf ``CONF_MIN`` heben (Quelle bleibt
    "llm" -- die Anzeige zeigt weiter, dass die KI gelesen hat)."""
    from .pipeline import CONF_MIN

    for feld in felder:
        fr = res.fields.get(feld)
        if fr is not None and fr.source == "llm" and fr.confidence < CONF_MIN:
            res.fields[feld] = dataclasses.replace(fr, confidence=CONF_MIN)


HINWEIS = ("KI-Werte durch eine zweite, unabhaengige Lesung belegt (Gegenpruefung: "
           "Rechnungsnummer, Datum, Betraege) -- Lieferant ueber USt-IdNr/IBAN erkannt")


def anwenden(res, text: str) -> dict:
    """Nach der KI-Volllesung (Rechnung ohne Vorlage): immer pruefen und in
    ``res.gegenpruefung`` vermerken; nur bei eingeschaltetem Schalter, hart
    erkanntem Lieferanten und vollstaendig belegten Pflichtfeldern heben."""
    g = pruefen(res, text)
    g["angewendet"] = False
    if aktiv() and g["hart_erkannt"] and g["vollstaendig"]:
        heben(res, g["belegt"])
        g["angewendet"] = True
        if HINWEIS not in res.hinweise:
            res.hinweise.append(HINWEIS)
    res.gegenpruefung = g
    return g
