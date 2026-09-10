"""Plausibilitätsprüfungen auf den extrahierten Feldern."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .textnorm import iban_valid, parse_amount, parse_date


@dataclass
class Check:
    name: str
    ok: bool | None  # None = nicht prüfbar (Felder fehlen)
    detail: str = ""


def _amt(fields: dict, key: str) -> float | None:
    fr = fields.get(key)
    if fr is None:
        return None
    if isinstance(fr.value, (int, float)):
        return float(fr.value)
    return parse_amount(fr.raw)


def check_totals(fields: dict, tol: float = 0.02) -> Check:
    netto, ust, brutto = _amt(fields, "netto"), _amt(fields, "ust_betrag"), _amt(fields, "brutto")
    if None in (netto, ust, brutto):
        return Check("netto+ust=brutto", None, "Beträge unvollständig")
    diff = abs((netto + ust) - brutto)
    return Check("netto+ust=brutto", diff <= tol, f"Abweichung {diff:.2f}")


def check_iban(fields: dict) -> Check:
    fr = fields.get("iban")
    if fr is None or not fr.raw:
        return Check("iban-pruefziffer", None, "keine IBAN")
    val = fr.value if isinstance(fr.value, str) else fr.raw
    return Check("iban-pruefziffer", iban_valid(val), val or "")


def check_date(fields: dict, today: date | None = None) -> Check:
    fr = fields.get("rechnungsdatum")
    if fr is None or not fr.raw:
        return Check("rechnungsdatum-plausibel", None, "kein Datum")
    d = parse_date(fr.raw)
    if d is None:
        return Check("rechnungsdatum-plausibel", False, "nicht parsebar")
    today = today or date.today()
    ok = (today - timedelta(days=3650)) <= d <= (today + timedelta(days=14))
    return Check("rechnungsdatum-plausibel", ok, d.isoformat())


def check_duplicate(fields: dict, supplier_id: str | None, seen) -> Check:
    fr = fields.get("rechnungsnummer")
    if not supplier_id or fr is None or not fr.raw:
        return Check("keine-dublette", None, "")
    num = (fr.value if isinstance(fr.value, str) else fr.raw or "").strip()
    dup = seen(supplier_id, num)
    return Check("keine-dublette", not dup, f"{supplier_id}/{num}")


def run_all(fields: dict, supplier_id: str | None, seen) -> list[Check]:
    return [
        check_totals(fields),
        check_iban(fields),
        check_date(fields),
        check_duplicate(fields, supplier_id, seen),
    ]
