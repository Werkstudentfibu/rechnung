"""Plausibilitaetspruefungen auf den extrahierten Feldern."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

from .textnorm import iban_valid, norm_ustid, parse_amount, parse_date

# Positionsspalten kommen seit Baustein 1 (positionen.normalize_columns)
# bereits zentral umbenannt an -- menge/einzelpreis_netto/gesamt_netto/
# mwst_satz sind hier Standardnamen, keine Alias-Suche mehr noetig. Rabatt/
# Nachlass ist (noch) keine Standardspalte, deshalb bleibt dafuer eine
# kleine Alias-Liste.
_DISCOUNT_PCT_KEYS = ("rabatt_prozent", "nachlass_prozent", "rabatt_pct", "discount_percent",
                     "rabatt_%")
_DISCOUNT_AMT_KEYS = ("rabatt_betrag", "nachlass_betrag", "rabatt", "nachlass", "discount")

# "inkl. MwSt"/"Bruttopreise" im Positionskopf bzw. im Fliesstext -- nur
# dann darf die Positionssumme gegen BRUTTO geprueft werden (Baustein 9b)
_BRUTTO_HINT_RE = re.compile(r"(?i)inkl(?:usive)?\.?\s*(?:mwst|ust)|brutto[- ]?preise?|"
                             r"preise\s+brutto")


def looks_like_brutto_prices(text: str) -> bool:
    """Weist der Rechnungstext auf Bruttopreis-Positionen hin ("inkl. MwSt"/
    "Bruttopreise")? Oeffentliche Form von ``_BRUTTO_HINT_RE`` fuer die
    Rechenstufe (rechnen.py, Baustein 2)."""
    return bool(_BRUTTO_HINT_RE.search(text or ""))


def _first(item: dict, keys: tuple[str, ...]) -> float | None:
    """Erster Spaltenwert einer Position, der sich als Zahl lesen laesst --
    Tabellenwerte sind oft noch Rohtext ("19", "2,50"), nicht schon float."""
    for k in keys:
        if k not in item:
            continue
        v = item[k]
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, str) and v.strip():
            amt = parse_amount(v)
            if amt is not None:
                return amt
    return None


def _num(item: dict, key: str) -> float | None:
    """Wert EINER Standardspalte als Zahl, oder ``None`` (Rohtext wie
    "19"/"2,50" wird geparst, ein float/int-Wert direkt uebernommen)."""
    if key not in item:
        return None
    v = item[key]
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str) and v.strip():
        return parse_amount(v)
    return None


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
    # Zukunft: hoechstens 1 Tag Toleranz (Zeitzone/Ausstellung am Abend
    # vorher) -- frueher 14 Tage, Aaron 10.10.2026: ein Datum in der Zukunft
    # ist fast immer falsch gelesen, oft ein Jahreszahl-Dreher (2062/2026).
    if d > today + timedelta(days=1):
        grund = f"{d.isoformat()} liegt in der Zukunft"
        dreher = _jahres_dreher(d.year, today.year)
        if dreher:
            grund += f" -- Jahreszahl-Dreher? {d.year} statt {dreher}"
        return Check("rechnungsdatum-plausibel", False, grund)
    if d < today - timedelta(days=3650):
        return Check("rechnungsdatum-plausibel", False,
                     f"{d.isoformat()} aelter als 10 Jahre")
    # aelter als 1 Jahr: nur Hinweis (plausi.datum_hinweise), nicht rot
    return Check("rechnungsdatum-plausibel", True, d.isoformat())


def _jahres_dreher(jahr: int, heute: int) -> int | None:
    """2062 -> 2026 (letzte zwei Ziffern vertauscht), wenn das Ergebnis ein
    plausibles Jahr (dieses oder letztes) ist."""
    s = str(jahr)
    if len(s) != 4:
        return None
    t = int(s[:2] + s[3] + s[2])
    return t if t != jahr and heute - 1 <= t <= heute else None


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
    """Vergleich normalisiert (``norm_ustid``) -- die Stammdaten duerfen
    anders geschrieben sein als der Fund im Rechnungstext (Leerzeichen,
    Punkte, Bindestriche; Schweizer UID mit MWST-Zusatz)."""
    fr = fields.get("ustid")
    if supplier is None or not getattr(supplier, "ustids", None) or fr is None or not fr.raw:
        return Check("ustid-stammdaten", None, "")
    val = norm_ustid(fr.value if isinstance(fr.value, str) else fr.raw or "")
    known = {norm_ustid(u) for u in supplier.ustids}
    return Check("ustid-stammdaten", val in known, val if val not in known else "ok")


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


def check_amount_sanity(fields: dict) -> Check:
    netto, ust, brutto = _amt(fields, "netto"), _amt(fields, "ust_betrag"), _amt(fields, "brutto")
    have = [x for x in (netto, ust, brutto) if x is not None]
    if not have:
        return Check("betraege-plausibel", None, "keine Betraege")
    problems = []
    if any(x < 0 for x in have):
        problems.append("negativ")
    if netto is not None and brutto is not None and brutto + 0.02 < netto:
        problems.append("brutto < netto")
    if ust is not None and ust < 0:
        problems.append("ust negativ")
    return Check("betraege-plausibel", not problems, ", ".join(problems) or "ok")


def check_line_items_total(fields: dict, line_items: list[dict] | None,
                           text: str = "") -> Check:
    """Positionen muessen zum NETTO passen (Toleranz 0,02 + 0,01 je Position
    fuer Rundung -- NICHT 1 % des Betrags). Gegen BRUTTO nur, wenn die
    Rechnung erkennbar Bruttopreise ausweist ("inkl. MwSt"/"Bruttopreise" im
    Text) -- nicht mehr "netto ODER brutto, was zufaellig passt"."""
    if not line_items:
        return Check("positionen-summe", None, "keine Positionen")
    total = 0.0
    n = 0
    for it in line_items:
        v = _num(it, "gesamt_netto")
        if v is not None:
            total += v
            n += 1
    if not n:
        return Check("positionen-summe", None, "keine Summenspalte erkannt")
    tol = 0.02 + 0.01 * n
    netto, brutto = _amt(fields, "netto"), _amt(fields, "brutto")
    if netto is not None:
        d = abs(total - netto)
        if d <= tol:
            return Check("positionen-summe", True, f"Σ {total:.2f} ≈ netto")
        if not (brutto is not None and _BRUTTO_HINT_RE.search(text or "")):
            return Check("positionen-summe", False, f"Σ {total:.2f} vs netto {netto:.2f}")
    if brutto is not None and _BRUTTO_HINT_RE.search(text or ""):
        d = abs(total - brutto)
        return Check("positionen-summe", d <= tol,
                     f"Σ {total:.2f} {'≈' if d <= tol else 'vs'} brutto {brutto:.2f} "
                     f"(Bruttopreise)")
    ref = netto if netto is not None else brutto
    return Check("positionen-summe", False,
                 f"Σ {total:.2f} vs {ref:.2f}" if ref is not None else f"Σ {total:.2f}")


def line_item_row(it: dict, i: int = 1, tol: float = 0.01) -> tuple[bool | None, str]:
    """Rechen-Pruefung EINER Position (menge * einzelpreis abzueglich Rabatt =
    Zeilensumme) -> (ok, Grund). ``None``: Pauschale ohne Menge/Einzelpreis,
    nicht pruefbar. Auch fuer die Live-Anzeige je Zeile im UI (Baustein 11b)."""
    menge, preis = _num(it, "menge"), _num(it, "einzelpreis_netto")
    zeile = _num(it, "gesamt_netto")
    if menge is None or preis is None or zeile is None:
        return None, "keine Menge/Einzelpreis/Zeilensumme"
    erwartet = menge * preis
    pct = _first(it, _DISCOUNT_PCT_KEYS)
    amt = _first(it, _DISCOUNT_AMT_KEYS)
    if pct is not None:
        erwartet *= 1 - pct / 100
    elif amt is not None:
        erwartet -= amt
    if abs(round(erwartet, 2) - zeile) > tol:
        bez = it.get("beschreibung") or f"Position {i}"
        return False, f"{bez}: erwartet {erwartet:.2f}, gefunden {zeile:.2f}"
    return True, "ok"


def check_line_item_math(line_items: list[dict] | None, tol: float = 0.01) -> Check:
    """Je Position: menge * einzelpreis (abzueglich Rabatt/Nachlass, falls
    erkannt) = Zeilensumme. Zeilen ohne Menge/Einzelpreis (Pauschalen)
    werden nicht beanstandet."""
    if not line_items:
        return Check("positionen-rechnung", None, "keine Positionen")
    problems = []
    seen = False
    for i, it in enumerate(line_items, 1):
        ok, grund = line_item_row(it, i, tol)
        if ok is None:
            continue
        seen = True
        if not ok:
            problems.append(grund)
    if not seen:
        return Check("positionen-rechnung", None, "keine Zeile mit Menge+Einzelpreis")
    return Check("positionen-rechnung", not problems, "; ".join(problems) or "ok")


def _sums_by_rate(line_items: list[dict] | None) -> dict[float, float]:
    by_rate: dict[float, float] = {}
    for it in line_items or []:
        rate, base = _num(it, "mwst_satz"), _num(it, "gesamt_netto")
        if rate is None or base is None:
            continue
        by_rate[round(float(rate), 2)] = round(by_rate.get(round(float(rate), 2), 0.0) + base, 2)
    return by_rate


def tax_row(row: dict, by_rate: dict[float, float], tol: float = 0.02
            ) -> tuple[bool | None, str]:
    """Rechen-Pruefung EINER Zeile der USt-Aufstellung: Positionssumme mit
    diesem Satz = netto der Zeile (falls Positionen mit Satz-Spalte da), und
    USt = netto * Satz -> (ok, Grund). Auch fuer die Live-Anzeige im UI."""
    satz = row.get("satz")
    if satz is None:
        return None, "kein Satz"
    try:
        satz = round(float(satz), 2)
        netto_row = None if row.get("netto") is None else float(row["netto"])
        ust_row = None if row.get("ust") is None else float(row["ust"])
    except (TypeError, ValueError):
        return False, "Zeile nicht als Zahl lesbar"
    problems = []
    pos_sum = by_rate.get(satz)
    if pos_sum is not None and netto_row is not None and abs(pos_sum - netto_row) > tol:
        problems.append(f"{satz:g} %: Positionen {pos_sum:.2f} vs Aufstellung "
                        f"{netto_row:.2f}")
    if netto_row is not None and ust_row is not None:
        erwartet = round(netto_row * satz / 100, 2)
        if abs(erwartet - ust_row) > tol:
            problems.append(f"{satz:g} %: USt erwartet {erwartet:.2f}, gefunden "
                            f"{ust_row:.2f}")
    elif pos_sum is None:
        return None, "nicht pruefbar"
    return not problems, "; ".join(problems) or "ok"


def check_tax_rate_lines(fields: dict, line_items: list[dict] | None,
                         tol: float = 0.02) -> Check:
    """Je Steuersatz: Summe der Positionen mit Satz X gegen die Zeile "X %"
    der USt-Aufstellung (netto), und USt-Betrag dieser Zeile = netto * Satz."""
    if not line_items:
        return Check("steuersatz-positionen", None, "keine Positionen")
    fr = fields.get("ust_aufstellung")
    if fr is None or not isinstance(fr.value, list) or not fr.value:
        return Check("steuersatz-positionen", None, "keine USt-Aufstellung")
    by_rate = _sums_by_rate(line_items)
    if not by_rate:
        return Check("steuersatz-positionen", None, "keine Steuersatz-Spalte in den Positionen")
    problems = []
    for row in fr.value:
        ok, grund = tax_row(row, by_rate, tol)
        if ok is False:
            problems.append(grund)
    return Check("steuersatz-positionen", not problems, "; ".join(problems) or "ok")


def rechen_pruefungen(fields: dict, line_items: list[dict] | None, text: str = "") -> dict:
    """Live-Ansicht der Rechen-Pruefungen fuers UI (Postfach-Baustein 11b):
    die Summen-Pruefungen wie in ``run_all`` plus rot/gruen JE Zeile der
    Positionstabelle und der USt-Aufstellung -- damit der Mensch beim
    Korrigieren sieht, wo es hakt. Reine Anzeige, entscheidet nichts."""
    checks = [check_totals(fields), check_tax_summary(fields),
              check_line_items_total(fields, line_items, text),
              check_line_item_math(line_items), check_tax_rate_lines(fields, line_items)]
    fr = fields.get("ust_aufstellung")
    rows = fr.value if fr is not None and isinstance(fr.value, list) else []
    by_rate = _sums_by_rate(line_items)

    def _view(ok, grund):
        return {"ok": ok, "detail": grund}

    return {
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks],
        "positionen": [_view(*line_item_row(it, i)) for i, it in enumerate(line_items or [], 1)],
        "ust": [_view(*tax_row(r, by_rate)) for r in rows],
    }


def run_all(fields: dict, supplier, seen, line_items: list[dict] | None = None,
           text: str = "") -> list[Check]:
    supplier_id = getattr(supplier, "id", supplier) if supplier is not None else None
    sup_obj = supplier if not isinstance(supplier, str) else None
    return [
        check_totals(fields),
        check_amount_sanity(fields),
        check_iban(fields),
        check_date(fields),
        check_duplicate(fields, supplier_id, seen),
        check_iban_known(fields, sup_obj),
        check_ustid_known(fields, sup_obj),
        check_tax_summary(fields),
        check_line_items_total(fields, line_items, text),
        check_line_item_math(line_items),
        check_tax_rate_lines(fields, line_items),
    ]
