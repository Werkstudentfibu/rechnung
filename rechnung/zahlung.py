"""Faelligkeit und Skonto aus dem Rechnungstext (Postfach-Baustein 5).

Liest aus den Zahlungsbedingungen einer PDF-Rechnung:

- ``faelligkeit`` (ISO-Datum): absolut ("zahlbar bis 28.02.2026", "faellig
  am ...", "Faelligkeitsdatum ...") oder relativ ("zahlbar innerhalb von 30
  Tagen", "30 Tage netto", "Zahlungsziel 30 Tage") -- dann
  Rechnungsdatum + N Tage, ``zahlungsziel_tage`` = N.
- ``skonto_prozent`` + ``skonto_frist`` (ISO-Datum) bzw. ``skonto_tage``:
  "2 % Skonto bei Zahlung innerhalb von 10 Tagen", "abzueglich 3 % Skonto
  bis 24.02.2026", "10 Tage 2 % Skonto". XRechnung-Kennung
  ``#SKONTO#TAGE=14#PROZENT=2.00#`` wird ebenfalls verstanden.

Nichts wird geraten: widersprechen sich zwei Angaben (zwei verschiedene
Faelligkeitsdaten, gestaffeltes Skonto mit mehreren Saetzen, relative Frist
ohne Rechnungsdatum ...), bleibt das Feld leer und ``hinweise`` sagt
warum. Eine gelernte Vorlage (template.py, Felder ``faelligkeit``/
``skonto_prozent``/``skonto_frist``) hat Vorrang vor dieser Textregel.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .textnorm import parse_amount, parse_date

SOURCE = "text"   # FieldResult.source/origin: per Regel aus dem Rechnungstext
CONFIDENCE = 0.85

FELDER = ("faelligkeit", "zahlungsziel_tage", "skonto_prozent", "skonto_frist", "skonto_tage")

_DATE = r"(\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\.\s+[A-Za-zäöüÄÖÜ]+\.?\s+\d{4})"
_PCT = r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%"

_DUE_ABS = [
    re.compile(r"(?i)(?:zahlbar|f[äa]llig|zu\s+zahlen|zahlung)\s+(?:bis\s+(?:zum\s+)?|am\s+|sp[äa]testens\s+(?:am\s+|bis\s+)?)" + _DATE),
    re.compile(r"(?i)(?:f[äa]lligkeit(?:sdatum)?|zahlungsziel|due\s+date|payable\s+by)\s*:?\s*" + _DATE),
]
_DUE_REL = [
    re.compile(r"(?i)(?:zahlbar|zahlung)\s+(?:innerhalb|binnen)\s+(?:von\s+)?(\d{1,3})\s+tag"),
    re.compile(r"(?i)zahlungsziel\s*:?\s*(\d{1,3})\s+tag"),
    re.compile(r"(?i)(\d{1,3})\s+tage\s+(?:netto|rein\s+netto|ohne\s+abzug)"),
    re.compile(r"(?i)(?:payable|due)\s+within\s+(\d{1,3})\s+days"),
    re.compile(r"(?i)\bnet\s+(\d{1,3})\b"),
]
# (Muster, Gruppe Prozent, Gruppe Tage bzw. Datum)
_SKONTO_REL = [
    (re.compile(r"(?i)" + _PCT + r"\s*skonto\s+(?:bei\s+zahlung\s+)?(?:innerhalb|binnen)\s+(?:von\s+)?(\d{1,3})\s+tag"), 1, 2),
    (re.compile(r"(?i)(?:innerhalb|binnen)\s+(?:von\s+)?(\d{1,3})\s+tag\w*\s+(?:abz[üu]glich\s+)?" + _PCT + r"\s*skonto"), 2, 1),
    (re.compile(r"(?i)\b(\d{1,3})\s+tage\s+" + _PCT + r"\s*skonto"), 2, 1),
]
_SKONTO_ABS = [
    (re.compile(r"(?i)" + _PCT + r"\s*skonto\s+(?:bei\s+zahlung\s+)?bis\s+(?:zum\s+)?" + _DATE), 1, 2),
    (re.compile(r"(?i)bis\s+(?:zum\s+)?" + _DATE + r"\s+(?:abz[üu]glich\s+)?" + _PCT + r"\s*skonto"), 2, 1),
]
_SKONTO_ANY = re.compile(r"(?i)" + _PCT + r"\s*skonto|skonto\s*:?\s*" + _PCT)
_XR_SKONTO = re.compile(r"#SKONTO#TAGE=(\d+)#PROZENT=(\d+(?:\.\d+)?)#")


@dataclass
class Zahlung:
    werte: dict = field(default_factory=dict)       # Feld -> Wert
    hinweise: list[str] = field(default_factory=list)


def _iso(s: str) -> str | None:
    d = parse_date(s)
    return d.isoformat() if d else None


def _one(values: list, feld: str, z: Zahlung):
    """Genau ein eindeutiger Wert -> Wert, sonst None + Hinweis."""
    uniq = sorted({v for v in values if v is not None}, key=str)
    if len(uniq) == 1:
        return uniq[0]
    if len(uniq) > 1:
        z.hinweise.append(f"{feld}: mehrere Angaben ({', '.join(map(str, uniq))}) "
                          f"-- leer gelassen, bitte pruefen")
    return None


def _pct(s: str) -> float | None:
    v = parse_amount(s.replace(" ", ""))
    return v if v is not None and 0 < v < 50 else None


def parse(text: str, rechnungsdatum: str | date | None = None) -> Zahlung:
    """Zahlungsbedingungen aus dem Text lesen (siehe Moduldoku)."""
    z = Zahlung()
    text = re.sub(r"\s+", " ", text or "")
    rd = rechnungsdatum if isinstance(rechnungsdatum, date) else (
        parse_date(str(rechnungsdatum)) if rechnungsdatum else None)

    # --- Skonto --------------------------------------------------------
    sk_pct, sk_tage, sk_frist = [], [], []
    for m in _XR_SKONTO.finditer(text):
        sk_tage.append(int(m.group(1)))
        sk_pct.append(_pct(m.group(2)))
    for rx, gp, gt in _SKONTO_REL:
        for m in rx.finditer(text):
            sk_pct.append(_pct(m.group(gp)))
            sk_tage.append(int(m.group(gt)))
    for rx, gp, gd in _SKONTO_ABS:
        for m in rx.finditer(text):
            sk_pct.append(_pct(m.group(gp)))
            sk_frist.append(_iso(m.group(gd)))
    if not sk_pct:
        lone = [_pct(m.group(1) or m.group(2)) for m in _SKONTO_ANY.finditer(text)]
        if any(lone):
            z.hinweise.append("Skonto erwaehnt, aber keine eindeutige Frist -- leer gelassen")
    pct = _one(sk_pct, "skonto_prozent", z)
    tage = _one(sk_tage, "skonto_tage", z)
    frist = _one(sk_frist, "skonto_frist", z)
    if pct is not None:
        z.werte["skonto_prozent"] = pct
        if tage is not None:
            z.werte["skonto_tage"] = tage
            if rd is not None:
                calc = (rd + timedelta(days=tage)).isoformat()
                if frist and frist != calc:
                    z.hinweise.append(f"skonto_frist: {frist} passt nicht zu {tage} Tagen "
                                      f"ab Rechnungsdatum ({calc}) -- leer gelassen")
                    frist = None
                    del z.werte["skonto_tage"]
                else:
                    frist = calc
            elif frist is None:
                z.hinweise.append("skonto_frist: relative Frist ohne Rechnungsdatum -- leer gelassen")
        if frist:
            z.werte["skonto_frist"] = frist

    # --- Faelligkeit ---------------------------------------------------
    abs_d = [_iso(m.group(1)) for rx in _DUE_ABS for m in rx.finditer(text)]
    # "bis <Datum> ... Skonto" ist die Skontofrist, nicht die Faelligkeit
    abs_d = [d for d in abs_d if d and d != z.werte.get("skonto_frist")]
    rel = [int(m.group(1)) for rx in _DUE_REL for m in rx.finditer(text)]
    rel = [n for n in rel if n != z.werte.get("skonto_tage") or len(set(rel)) == 1]
    due = _one(abs_d, "faelligkeit", z)
    ziel = _one(rel, "zahlungsziel_tage", z)
    if ziel is not None:
        z.werte["zahlungsziel_tage"] = ziel
        if rd is not None:
            calc = (rd + timedelta(days=ziel)).isoformat()
            if due and due != calc:
                z.hinweise.append(f"faelligkeit: {due} passt nicht zu {ziel} Tagen ab "
                                  f"Rechnungsdatum ({calc}) -- leer gelassen")
                due = None
                del z.werte["zahlungsziel_tage"]
            elif due is None:
                due = calc
        elif due is None:
            z.hinweise.append("faelligkeit: relatives Zahlungsziel ohne Rechnungsdatum -- leer gelassen")
    if due:
        z.werte["faelligkeit"] = due
    return z


def apply(fields: dict, text: str, hinweise: list[str]) -> None:
    """Fehlende Zahlungsfelder aus dem Text ergaenzen (Vorlage/E-Rechnung/
    Mensch haben Vorrang). Mutiert ``fields`` und ``hinweise``."""
    from .template import FieldResult

    rd = fields.get("rechnungsdatum")
    z = parse(text, rd.value if rd is not None and rd.source != "missing" else None)
    for name, val in z.werte.items():
        cur = fields.get(name)
        if cur is not None and cur.source != "missing" and cur.value not in (None, ""):
            continue
        fields[name] = FieldResult(name, val, str(val), CONFIDENCE, SOURCE)
    for h in z.hinweise:
        if h not in hinweise:
            hinweise.append(h)
