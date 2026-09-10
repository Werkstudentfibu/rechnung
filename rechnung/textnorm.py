"""Textnormalisierung und Wertparser (Beträge, Datum, IBAN, USt-IdNr)."""

from __future__ import annotations

import re
from datetime import date

from dateutil import parser as _dateparser

_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

_MONATE = {
    "januar": 1, "februar": 2, "maerz": 3, "marz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
}


def norm(s: str) -> str:
    """Kleinschreibung, Umlaute ersetzt, alles Nicht-Alphanumerische zu '-'."""
    s = s.lower().translate(_UMLAUT)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


_AMOUNT_RE = re.compile(r"-?\d[\d.\s ]*,\d{2}|-?\d[\d,\s ]*\.\d{2}|-?\d+")


def parse_amount(s: str) -> float | None:
    """Erkennt '1.234,56', '1 234,56', '1234.56' und '1234'."""
    if not s:
        return None
    m = _AMOUNT_RE.search(s.replace(" ", " "))
    if not m:
        return None
    t = m.group(0).replace(" ", "")
    if "," in t:  # deutsches Format: Punkt = Tausender, Komma = Dezimal
        t = t.replace(".", "").replace(",", ".")
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def parse_date(s: str) -> date | None:
    if not s:
        return None
    s = s.strip()
    # deutsche Monatsnamen
    low = s.lower().translate(_UMLAUT)
    m = re.search(r"(\d{1,2})\.?\s+([a-z]+)\s+(\d{4})", low)
    if m and m.group(2) in _MONATE:
        try:
            return date(int(m.group(3)), _MONATE[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}", s)
    if m:
        try:
            return _dateparser.parse(m.group(0), dayfirst=True).date()
        except (ValueError, OverflowError):
            return None
    m = re.search(r"\d{4}-\d{2}-\d{2}", s)
    if m:
        try:
            return _dateparser.parse(m.group(0)).date()
        except (ValueError, OverflowError):
            return None
    return None


_IBAN_RE = re.compile(
    r"[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?|[A-Z]{2}\d{2}[A-Z0-9]{11,30}"
)


def find_iban(s: str) -> str | None:
    """Erst pruefziffernvalide Kandidaten, sonst der erste syntaktische Treffer."""
    fallback = None
    for m in _IBAN_RE.finditer(s.upper()):
        cand = re.sub(r"\s+", "", m.group(0))
        if iban_valid(cand):
            return cand
        fallback = fallback or cand
    return fallback


def iban_valid(iban: str | None) -> bool:
    if not iban:
        return False
    iban = re.sub(r"\s+", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


_USTID_RE = re.compile(r"\b(DE\s?\d{9}|[A-Z]{2}\s?[A-Z0-9]{8,12})\b")


def find_ustids(s: str) -> set[str]:
    """EU-USt-IdNr: 2 Laenderbuchstaben + Kennung. Kennung muss >=2 Ziffern
    enthalten, sonst matchen auch normale Grossbuchstaben-Woerter."""
    out = set()
    for m in _USTID_RE.finditer(s.upper()):
        cand = re.sub(r"\s+", "", m.group(1))
        if sum(c.isdigit() for c in cand[2:]) >= 2:
            out.add(cand)
    return out


_EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)", re.I)
_FREEMAIL = {"gmail.com", "googlemail.com", "web.de", "gmx.de", "gmx.net", "outlook.com", "hotmail.com", "t-online.de"}


def find_domains(s: str) -> set[str]:
    return {d.lower() for d in _EMAIL_RE.findall(s) if d.lower() not in _FREEMAIL}


def coerce(raw: str, value_type: str):
    """Rohtext -> (Wert, ok?) je nach Feldtyp."""
    raw = (raw or "").strip()
    if not raw:
        return None, False
    if value_type == "amount":
        v = parse_amount(raw)
        return v, v is not None
    if value_type == "date":
        v = parse_date(raw)
        return (v.isoformat() if v else None), v is not None
    if value_type == "iban":
        v = find_iban(raw) or re.sub(r"\s+", "", raw)
        return v, iban_valid(v)
    if value_type == "ustid":
        ids = find_ustids(raw)
        v = next(iter(ids), re.sub(r"\s+", "", raw))
        return v, bool(ids)
    if value_type == "number":
        m = re.search(r"[A-Za-z0-9][\w\-/.]*", raw)
        return (m.group(0) if m else raw), bool(m)
    return raw, True
