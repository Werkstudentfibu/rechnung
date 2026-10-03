"""Bulk-Import der Abbyy-FlexiCapture-Stammdaten in die eigene Lieferanten-/
Rechnungsadressen-Verwaltung.

Abbyy FlexiCapture for Invoices fuehrt Lieferanten und Geschaeftsbereiche als
zwei getrennte "Data Sets" mit einem festen Spaltenschema (siehe
https://docs.abbyy.com/flexi-capture/invoice-reader/ir-columns-vendors und
.../ir-columns-bu). Dieses Modul liest genau dieses Schema aus einem CSV-Export
(ueblicher Exportweg aus der Abbyy Projekt-Setup-Station) und legt je Zeile
einen Lieferanten bzw. eine Rechnungsadresse an -- Upsert ueber die Abbyy-Id,
mehrfaches Einspielen derselben Datei dupliziert also nicht.

Stand 2026-09-18: Das Programm ist noch nicht im Unternehmen im Einsatz, es
gibt also noch keinen echten Abbyy-Export zum Testen -- dieser Import ist
gegen das oben verlinkte, offiziell dokumentierte Schema und synthetische
Testdaten gebaut, damit er einsatzbereit ist, sobald der echte Export vorliegt.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .billing import BillingAddress
from .identify import Supplier
from .store import Store

# Reihenfolge nach Wahrscheinlichkeit: Abbyy exportiert i.d.R. UTF-8, aeltere/
# deutsche Windows-Exporte liefern aber haeufig cp1252 (Umlaute kaputt sonst).
_ENCODINGS = ("utf-8-sig", "cp1252")


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    """CSV robust einlesen, Trennzeichen (',' oder ';') automatisch erkennen."""
    path = Path(path)
    text = None
    for enc in _ENCODINGS:
        try:
            text = path.read_text(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"{path}: weder UTF-8 noch cp1252 lesbar")

    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";" if sample.count(";") > sample.count(",") else ","

    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()}
            for row in reader]


def _get(row: dict[str, str], *names: str) -> str:
    """Spalte case-insensitiv suchen -- je nach Abbyy-Konfiguration/Exportweg
    kann die Gross-/Kleinschreibung der Spaltenkoepfe variieren."""
    lower = {k.lower(): v for k, v in row.items()}
    for n in names:
        v = lower.get(n.lower())
        if v:
            return v
    return ""


def _address_lines(row: dict[str, str]) -> list[str]:
    street = _get(row, "Street")
    zip_city = " ".join(x for x in (_get(row, "ZIP"), _get(row, "City")) if x).strip()
    state = _get(row, "State")
    return [x for x in (street, zip_city, state) if x]


def import_vendors_csv(path: str | Path, store: Store) -> int:
    """Abbyy-'Vendors'-Datensatz -> je Zeile ein Supplier. Gibt die Anzahl
    importierter/aktualisierter Lieferanten zurueck."""
    n = 0
    for row in _read_rows(path):
        sid = _get(row, "Id")
        if not sid:
            continue
        sup = store.supplier(sid) or Supplier(id=sid, name="")

        name = _get(row, "Name")
        if name:
            sup.name = name
        ustid = _get(row, "VATID")
        if ustid:
            sup.ustids = sorted(set(sup.ustids) | {ustid})
        iban = _get(row, "IBAN")
        if iban:
            sup.ibans = sorted(set(sup.ibans) | {iban.replace(" ", "").upper()})
        bank_code = _get(row, "BankCode")
        if bank_code:
            sup.bank_code = bank_code
        bank_account = _get(row, "BankAccount")
        if bank_account:
            sup.bank_account = bank_account
        national_vatid = _get(row, "NationalVATID")
        if national_vatid:
            sup.creditor_id = national_vatid
        lines = _address_lines(row)
        if lines:
            sup.address_lines = lines
        country = _get(row, "CountryCode").upper()
        if country:
            sup.currency = "CHF" if country == "CH" else "EUR"
        gl = _get(row, "GLCode")
        if gl and "GLCode:" not in sup.notes:
            sup.notes = (sup.notes + f"\nGLCode: {gl}").strip()

        store.save_supplier(sup)
        n += 1
    return n


def import_business_units_csv(path: str | Path, store: Store) -> int:
    """Abbyy-'BusinessUnits'-Datensatz -> je Zeile eine BillingAddress
    (= 'Rechnungsadresse'/Geschaeftsbereich im Programm). match_terms werden
    aus Name + Anschrift vorbelegt -- als Startpunkt, der nach dem Import
    gegen echte Rechnungen geprueft/verfeinert werden sollte, genau wie beim
    manuellen Anlegen im 'Rechnungsadressen'-Tab."""
    n = 0
    for row in _read_rows(path):
        aid = _get(row, "Id")
        if not aid:
            continue
        addr = store.address(aid) or BillingAddress(id=aid, name="")

        name = _get(row, "Name")
        if name:
            addr.name = name
        lines = _address_lines(row)
        if lines:
            addr.lines = lines
        addr.match_terms = sorted(set(addr.match_terms) | {t for t in ([name] + lines) if t})
        ustid = _get(row, "VATID")
        if ustid and "USt-IdNr:" not in addr.notes:
            addr.notes = (addr.notes + f"\nUSt-IdNr: {ustid}").strip()

        store.save_address(addr)
        n += 1
    return n
