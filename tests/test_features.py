"""Tests fuer die v1-Ausbaustufen: Stammdaten/feste Werte, eigene Felder,
Positionstabelle, USt-Aufstellung je Satz, Buchungsjournal."""

from pathlib import Path

import pytest

from rechnung import pdf as pdfmod
from rechnung.identify import Supplier, supplier_from_doc
from rechnung.pipeline import journal_rows, journal_totals, process
from rechnung.store import Store
from rechnung.template import (Anchor, Column, FieldSpec, LineItemSpec, Template,
                               apply_line_items, apply_template,
                               build_line_item_spec)
from rechnung.textnorm import parse_tax_summary

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

W, H = A4


def _tabular_invoice(path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, H - 50, "Tabellen Technik GmbH")
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 64, "E-Mail: buchhaltung@tabellen-technik.de")
    c.drawString(40, H - 76, "USt-IdNr: DE111222333")

    c.setFont("Helvetica", 10)
    c.drawString(40, H - 110, "Rechnungsnummer")
    c.drawString(200, H - 110, "TT-2026-0007")
    c.drawString(40, H - 124, "Rechnungsdatum")
    c.drawString(200, H - 124, "12.02.2026")

    # Positionstabelle
    hy = H - 170
    c.drawString(40, hy, "Menge")
    c.drawString(90, hy, "Bezeichnung")
    c.drawString(330, hy, "Einzelpreis")
    c.drawString(430, hy, "MwSt")
    c.drawRightString(540, hy, "Betrag")
    for i, (menge, bez, ep, satz, betrag) in enumerate([
        ("2", "Wartung Server", "150,00", "19", "300,00"),
        ("1", "Schulung Grundlagen", "200,00", "7", "200,00"),
    ]):
        ry = hy - 18 * (i + 1)
        c.drawString(40, ry, menge)
        c.drawString(90, ry, bez)
        c.drawRightString(380, ry, ep)
        c.drawString(430, ry, satz)
        c.drawRightString(540, ry, betrag)
    c.drawString(40, hy - 70, "Zwischensumme")
    c.drawRightString(540, hy - 70, "500,00")

    # USt-Aufstellung
    by = hy - 110
    c.drawString(40, by, "Steuersatz 19 % 300,00 57,00")
    c.drawString(40, by - 14, "Steuersatz 7 % 200,00 14,00")

    # Kopfsummen
    sy = by - 44
    c.drawString(40, sy, "Nettobetrag")
    c.drawRightString(540, sy, "500,00 EUR")
    c.drawString(40, sy - 14, "Umsatzsteuer")
    c.drawRightString(540, sy - 14, "71,00 EUR")
    c.drawString(40, sy - 28, "Gesamtbetrag")
    c.drawRightString(540, sy - 28, "571,00 EUR")

    c.drawString(40, 40, "IBAN: DE89370400440532013000")
    c.showPage()
    c.save()
    return path


def _word(doc, text):
    t = text.lower()
    for w in doc.all_words():
        if w.text.lower() == t:
            return w
    raise AssertionError(f"Wort {text!r} nicht im PDF")


# ---- Parser -------------------------------------------------------------

def test_parse_tax_summary_multi_rate():
    rows = parse_tax_summary("Steuersatz 19 % 300,00 57,00 Steuersatz 7 % 200,00 14,00")
    assert rows == [
        {"satz": 7.0, "netto": 200.0, "ust": 14.0},
        {"satz": 19.0, "netto": 300.0, "ust": 57.0},
    ]


# ---- Feste Werte aus den Stammdaten -----------------------------------

def test_fixed_field_from_library(tmp_path: Path):
    from rechnung.synth import make_invoice

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    inv = make_invoice(samples / "a.pdf", rechnungsnr="RE-5", netto=1000.0)
    doc = pdfmod.load(inv)

    sup = supplier_from_doc(doc, "muster", "Muster Handels GmbH")
    sup.fixed_fields = {"zahlungsziel": "14 Tage netto"}
    store.save_supplier(sup)

    t = Template(id="muster__standard", supplier_id="muster", secured_at=1, confirmations=1)
    t.fields = [
        FieldSpec("rechnungsnummer", "number", True, anchor=Anchor("rechnungsnummer", 0), rel=(60, -4, 240, 12)),
        FieldSpec("netto", "amount", True, anchor=Anchor("nettobetrag", 0), rel=(120, -4, 340, 12)),
        FieldSpec("ust_betrag", "amount", True, anchor=Anchor("umsatzsteuer", 0), rel=(120, -4, 340, 12)),
        FieldSpec("brutto", "amount", True, anchor=Anchor("gesamtbetrag", 0), rel=(120, -4, 340, 12)),
        FieldSpec("zahlungsziel", "text", False),  # kein Anker -> kommt aus Stammdaten
    ]
    store.save_template(t)

    res = process(inv, store)
    assert res.fields["zahlungsziel"].source == "library"
    assert res.fields["zahlungsziel"].value == "14 Tage netto"
    # echter Wert aus der Rechnung wird nicht ueberschrieben
    assert res.fields["netto"].source == "template-anchor"


# ---- Positionstabelle + USt-Aufstellung ------------------------------

def _tabular_template(doc) -> Template:
    header_cols = [
        (_word(doc, "Menge"), "menge", "number"),
        (_word(doc, "Bezeichnung"), "bezeichnung", "text"),
        (_word(doc, "Einzelpreis"), "einzelpreis", "amount"),
        (_word(doc, "MwSt"), "mwst", "number"),
        (_word(doc, "Betrag"), "betrag", "amount"),
    ]
    li = build_line_item_spec(header_cols, [_word(doc, "Zwischensumme")], doc.pages[0].width)

    t = Template(id="tt__standard", supplier_id="tt", secured_at=1, confirmations=1)
    t.fields = [
        FieldSpec("rechnungsnummer", "number", True, anchor=Anchor("rechnungsnummer", 0), rel=(50, -4, 260, 12)),
        FieldSpec("rechnungsdatum", "date", True, anchor=Anchor("rechnungsdatum", 0), rel=(50, -4, 260, 12)),
        FieldSpec("netto", "amount", True, anchor=Anchor("nettobetrag", 0), rel=(60, -4, 520, 12)),
        FieldSpec("ust_betrag", "amount", True, anchor=Anchor("umsatzsteuer", 0), rel=(60, -4, 520, 12)),
        FieldSpec("brutto", "amount", True, anchor=Anchor("gesamtbetrag", 0), rel=(60, -4, 520, 12)),
        FieldSpec("ust_aufstellung", "taxblock", False,
                  anchor=Anchor("steuersatz", 0), rel=(-6, -6, 520, 34)),
    ]
    t.line_items = li
    return t


def test_line_items_and_tax_breakdown(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    inv = _tabular_invoice(samples / "tt.pdf")
    doc = pdfmod.load(inv)

    store.save_supplier(Supplier(id="tt", name="Tabellen Technik GmbH",
                                 ustids=["DE111222333"]))
    tpl = _tabular_template(doc)
    store.save_template(tpl)

    items = apply_line_items(doc, tpl.line_items)
    assert len(items) == 2
    assert items[0]["menge"] == "2"
    assert items[0]["betrag"] == 300.0
    assert items[1]["mwst"] == "7"

    res = process(inv, store)
    # explizit markierter Block hat Prioritaet
    assert {r["satz"] for r in res.tax_breakdown} == {19.0, 7.0}
    by_rate = {r["satz"]: r for r in res.tax_breakdown}
    assert by_rate[19.0]["netto"] == 300.0
    assert by_rate[19.0]["ust"] == 57.0
    assert by_rate[7.0]["brutto"] == 214.0

    # USt-Aufstellungs-Summe vs Kopf (500 / 71) muss die Pruefung bestehen
    chk = {c.name: c for c in res.checks}
    assert chk["ust-aufstellung-summe"].ok is True
    # jeder Check-Name nur einmal (kein Dublett aus cross_check)
    names = [c.name for c in res.checks]
    assert len(names) == len(set(names))


def test_tax_breakdown_from_line_items_only(tmp_path: Path):
    """Ohne markierten Block: USt-Aufstellung aus den Positionen aufsummiert."""
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    inv = _tabular_invoice(samples / "tt.pdf")
    doc = pdfmod.load(inv)
    store.save_supplier(Supplier(id="tt", name="Tabellen Technik GmbH",
                                 ustids=["DE111222333"]))
    tpl = _tabular_template(doc)
    tpl.fields = [f for f in tpl.fields if f.name != "ust_aufstellung"]
    store.save_template(tpl)

    res = process(inv, store)
    by_rate = {r["satz"]: r for r in res.tax_breakdown}
    assert by_rate[19.0]["netto"] == 300.0
    assert by_rate[19.0]["ust"] == 57.0
    assert by_rate[7.0]["ust"] == 14.0


# ---- Buchungsjournal -------------------------------------------------

def test_journal_rows_and_totals(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.mark_processed({
        "file": "a.pdf", "supplier_id": "tt", "supplier_name": "Tabellen Technik GmbH",
        "billing_address_name": "Werk Sued",
        "rechnungsnummer": "TT-1", "rechnungsdatum": "2026-02-12",
        "netto": 500.0, "ust_betrag": 71.0, "brutto": 571.0,
        "tax_breakdown": [
            {"satz": 19.0, "netto": 300.0, "ust": 57.0, "brutto": 357.0},
            {"satz": 7.0, "netto": 200.0, "ust": 14.0, "brutto": 214.0},
        ],
    })
    store.mark_processed({
        "file": "b.pdf", "supplier_id": "tt", "supplier_name": "Tabellen Technik GmbH",
        "rechnungsnummer": "TT-2", "rechnungsdatum": "2026-03-01",
        "netto": 100.0, "ust_betrag": 19.0, "brutto": 119.0,
        "tax_breakdown": [{"satz": 19.0, "netto": 100.0, "ust": 19.0, "brutto": 119.0}],
    })

    rows = journal_rows(store)
    assert len(rows) == 2
    assert rows[0]["netto_19"] == 300.0
    assert rows[0]["ust_7"] == 14.0
    assert rows[1]["netto_7"] is None

    tot = journal_totals(rows)
    assert tot["rechnungen"] == 2
    assert tot["netto_gesamt"] == 600.0
    assert tot["netto_19"] == 400.0
    assert tot["ust_7"] == 14.0


# ---- Eigene Felder --------------------------------------------------

def test_custom_field_name(tmp_path: Path):
    from rechnung.synth import make_invoice

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    inv = make_invoice(samples / "a.pdf", rechnungsnr="RE-9", netto=100.0)
    doc = pdfmod.load(inv)

    t = Template(id="x__standard", supplier_id="x")
    t.fields = [FieldSpec("leistungszeitraum", "text", False,
                          anchor=Anchor("bestellnummer", 0), rel=(60, -4, 260, 12))]
    res = apply_template(doc, t)
    assert "leistungszeitraum" in res
    assert res["leistungszeitraum"].raw  # irgendein Wert wurde gezogen
