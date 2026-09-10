"""Plausibilitaetspruefungen auf den extrahierten Feldern."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .textnorm import iban_valid, parse_amount, parse_date


@dataclass
class Check:
    name: str
    ok: bool | None  # None = nicht pruefbar (Felder fehlen)
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
        return Check("netto+ust=brutto", None, "Betraege unvollstaendig")
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


def check_iban_known(fields: dict, supplier) -> Check:
    fr = fields.get("iban")
    if supplier is None or not getattr(supplier, "ibans", None) or fr is None or not fr.raw:
        return Check("iban-stammdaten", None, "")
    val = (fr.value if isinstance(fr.value, str) else fr.raw or "").replace(" ", "").upper()
    return Check("iban-stammdaten", val in set(supplier.ibans),
                 val if val not in set(supplier.ibans) else "ok")


def check_ustid_known(fields: dict, supplier) -> Check:
    fr = fields.get("ustid")
    if supplier is None or not getattr(supplier, "ustids", None) or fr is None or not fr.raw:
        return Check("ustid-stammdaten", None, "")
    val = (fr.value if isinstance(fr.value, str) else fr.raw or "").replace(" ", "").upper()
    return Check("ustid-stammdaten", val in set(supplier.ustids),
                 val if val not in set(supplier.ustids) else "ok")


def check_tax_summary(fields: dict, tol: float = 0.05) -> Check:
    """Summe der USt-Aufstellung gegen Netto/USt-Kopf abgleichen."""
    fr = fields.get("ust_aufstellung")
    if fr is None or not isinstance(fr.value, list) or not fr.value:
        return Check("ust-aufstellung-summe", None, "keine Aufstellung")
    base = sum(r.get("netto") or 0 for r in fr.value)
    tax = sum(r.get("ust") or 0 for r in fr.value)
    netto, ust = _amt(fields, "netto"), _amt(fields, "ust_betrag")
    parts = []
    ok = True
    if netto is not None and base:
        d = abs(base - netto)
        parts.append(f"netto {d:.2f}")
        ok = ok and d <= tol
    if ust is not None and tax:
        d = abs(tax - ust)
        parts.append(f"ust {d:.2f}")
        ok = ok and d <= tol
    if not parts:
        return Check("ust-aufstellung-summe", None, "nicht abgleichbar")
    return Check("ust-aufstellung-summe", ok, ", ".join(parts))


def run_all(fields: dict, supplier, seen) -> list[Check]:
    supplier_id = getattr(supplier, "id", supplier) if supplier is not None else None
    sup_obj = supplier if not isinstance(supplier, str) else None
    return [
        check_totals(fields),
        check_iban(fields),
        check_date(fields),
        check_duplicate(fields, supplier_id, seen),
        check_iban_known(fields, sup_obj),
        check_ustid_known(fields, sup_obj),
        check_tax_summary(fields),
    ]
