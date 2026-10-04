"""Postfach-Baustein 11a: Positionstabelle und USt-Block selbst lernen.

learn.py lernte bisher nur Kopffelder automatisch -- die Positionstabelle
(line_items) und die USt-Aufstellung (taxblock) mussten von Hand
angeklickt werden. Jetzt werden beide aus bestaetigten Werten abgeleitet
(Werte im PDF wiederfinden -> Kopfzeile/Spaltenbereiche bzw. Block-Anker)
und nur gespeichert, wenn ein Probelauf sie exakt reproduziert."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import learn
from rechnung import pdf as pdfmod
from rechnung.identify import Supplier
from rechnung.pipeline import EXTRACT_TEMPLATE, Result
from rechnung.store import Store
from rechnung.template import apply_line_items, apply_template
from rechnung.textnorm import parse_tax_summary

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

W, H = A4


def _tabular_invoice(path: Path, rows=(("2", "Wartung Server", "150,00", "19", "300,00"),
                                      ("1", "Schulung Grundlagen", "200,00", "7", "200,00")),
                     netto="500,00", ust="71,00", brutto="571,00",
                     tax_lines=("Steuersatz 19 % 300,00 57,00", "Steuersatz 7 % 200,00 14,00"),
                     ) -> Path:
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

    hy = H - 170
    c.drawString(40, hy, "Menge")
    c.drawString(90, hy, "Bezeichnung")
    c.drawString(330, hy, "Einzelpreis")
    c.drawString(430, hy, "MwSt")
    c.drawRightString(540, hy, "Betrag")
    for i, (menge, bez, ep, satz, betrag) in enumerate(rows):
        ry = hy - 18 * (i + 1)
        c.drawString(40, ry, menge)
        c.drawString(90, ry, bez)
        c.drawRightString(380, ry, ep)
        c.drawString(430, ry, satz)
        c.drawRightString(540, ry, betrag)
    bottom = hy - 18 * (len(rows) + 1)
    c.drawString(40, bottom, "Zwischensumme")
    c.drawRightString(540, bottom, netto)

    by = bottom - 40
    for i, line in enumerate(tax_lines):
        c.drawString(40, by - i * 14, line)

    sy = by - 14 * len(tax_lines) - 30
    c.drawString(40, sy, "Nettobetrag")
    c.drawRightString(540, sy, netto + " EUR")
    c.drawString(40, sy - 14, "Umsatzsteuer")
    c.drawRightString(540, sy - 14, ust + " EUR")
    c.drawString(40, sy - 28, "Gesamtbetrag")
    c.drawRightString(540, sy - 28, brutto + " EUR")

    c.drawString(40, 40, "IBAN: DE89370400440532013000")
    c.showPage()
    c.save()
    return path


def _store(tmp_path: Path) -> Store:
    st = Store(tmp_path / "data")
    st.save_supplier(Supplier(id="tt", name="Tabellen Technik GmbH", ustids=["DE111222333"]))
    return st


def _confirmed_result(path: Path, line_items: list[dict], taxblock_rows: list[dict] | None):
    from rechnung.template import FieldResult

    doc = pdfmod.load(path)
    res = Result(file=str(path), sha256=doc.sha256, supplier_id="tt",
                 extraction=EXTRACT_TEMPLATE, status="review")
    res.line_items = line_items
    if taxblock_rows is not None:
        res.fields["ust_aufstellung"] = FieldResult(
            "ust_aufstellung", taxblock_rows, str(taxblock_rows), 1.0, "manual")
    return doc, res


_ITEMS = [
    {"menge": "2", "bezeichnung": "Wartung Server", "einzelpreis": 150.0, "mwst": "19",
     "betrag": 300.0},
    {"menge": "1", "bezeichnung": "Schulung Grundlagen", "einzelpreis": 200.0, "mwst": "7",
     "betrag": 200.0},
]
_TAXROWS = [{"satz": 7.0, "netto": 200.0, "ust": 14.0}, {"satz": 19.0, "netto": 300.0, "ust": 57.0}]


def test_tabelle_und_steuerblock_werden_gelernt_und_wiederverwendet(tmp_path: Path):
    store = _store(tmp_path)
    p1 = _tabular_invoice(tmp_path / "a.pdf")
    doc1, res1 = _confirmed_result(p1, _ITEMS, _TAXROWS)
    store.mark_confirmed(res1.sha256, "tester")

    outcome = learn.learn_from_confirmed(store, res1, doc1, "tester")
    assert outcome.saved is True
    assert "_positionen" in outcome.learned
    assert "ust_aufstellung" in outcome.learned

    tpl = store.template(outcome.template_id)
    assert tpl.line_items is not None

    # Probe auf der GLEICHEN Rechnung
    assert apply_line_items(doc1, tpl.line_items) == _ITEMS
    ustrow = apply_template(doc1, tpl)["ust_aufstellung"]
    assert ustrow.value == _TAXROWS

    # eine ZWEITE Rechnung desselben Layouts, andere Werte -- muss ebenso
    # korrekt gelesen werden, ohne dass irgendetwas neu angelernt wird
    rows2 = (("3", "Beratung", "100,00", "19", "300,00"),)
    p2 = _tabular_invoice(tmp_path / "b.pdf", rows=rows2, netto="300,00", ust="57,00",
                          brutto="357,00", tax_lines=("Steuersatz 19 % 300,00 57,00",))
    doc2 = pdfmod.load(p2)
    items2 = apply_line_items(doc2, tpl.line_items)
    assert items2 == [{"menge": "3", "bezeichnung": "Beratung", "einzelpreis": 100.0,
                       "mwst": "19", "betrag": 300.0}]
    ustrow2 = apply_template(doc2, tpl)["ust_aufstellung"]
    assert ustrow2.value == [{"satz": 19.0, "netto": 300.0, "ust": 57.0}]


def test_korrigierte_spalte_fuehrt_zu_korrekter_neuer_spec(tmp_path: Path):
    """Eine falsch angeklickte/urspruenglich falsch gelernte Spalte (hier:
    Menge/Einzelpreis vertauscht) wird durch die Korrektur ersetzt -- die
    NEU gelernte Spec liest die Tabelle danach richtig, nicht mehr die
    alte falsche."""
    store = _store(tmp_path)
    p1 = _tabular_invoice(tmp_path / "a.pdf")
    doc1, res1 = _confirmed_result(p1, _ITEMS, None)
    store.mark_confirmed(res1.sha256, "tester")
    outcome = learn.learn_from_confirmed(store, res1, doc1, "tester")
    assert outcome.saved and "_positionen" in outcome.learned
    tid = outcome.template_id

    # Vorlage manuell auf vertauschte Spalten "verschlimmbessert" (simuliert
    # eine urspruenglich falsche manuelle Zuordnung)
    tpl = store.template(tid)
    cols = {c.name: c for c in tpl.line_items.columns}
    cols["menge"].x0, cols["einzelpreis"].x0 = cols["einzelpreis"].x0, cols["menge"].x0
    cols["menge"].x1, cols["einzelpreis"].x1 = cols["einzelpreis"].x1, cols["menge"].x1
    store.save_template(tpl)
    falsch = apply_line_items(doc1, tpl.line_items)
    assert falsch != _ITEMS   # jetzt tatsaechlich kaputt

    # Mensch korrigiert (bestaetigt die RICHTIGEN Werte erneut)
    doc1b, res1b = _confirmed_result(p1, _ITEMS, None)
    res1b.template_id = tid
    store.mark_confirmed(res1b.sha256, "tester2")
    outcome2 = learn.learn_from_confirmed(store, res1b, doc1b, "tester2")
    assert outcome2.saved and "_positionen" in outcome2.learned

    tpl2 = store.template(tid)
    assert apply_line_items(doc1, tpl2.line_items) == _ITEMS


def test_nicht_reproduzierbare_tabelle_wird_nicht_gespeichert(tmp_path: Path):
    store = _store(tmp_path)
    p1 = _tabular_invoice(tmp_path / "a.pdf")
    kaputt = [dict(_ITEMS[0], einzelpreis=999.99),   # steht so nicht im PDF
             _ITEMS[1]]
    doc1, res1 = _confirmed_result(p1, kaputt, None)
    store.mark_confirmed(res1.sha256, "tester")

    outcome = learn.learn_from_confirmed(store, res1, doc1, "tester")
    assert "_positionen" not in outcome.learned
    assert outcome.saved is False or store.template(outcome.template_id) is None \
        or store.template(outcome.template_id).line_items is None


def test_nicht_reproduzierbare_steuerzeile_wird_nicht_gespeichert(tmp_path: Path):
    store = _store(tmp_path)
    p1 = _tabular_invoice(tmp_path / "a.pdf")
    kaputt_rows = [{"satz": 19.0, "netto": 999.0, "ust": 57.0}]   # passt nicht
    doc1, res1 = _confirmed_result(p1, [], kaputt_rows)
    store.mark_confirmed(res1.sha256, "tester")

    outcome = learn.learn_from_confirmed(store, res1, doc1, "tester")
    assert "ust_aufstellung" not in outcome.learned


def test_learn_taxblock_direkt():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = _tabular_invoice(Path(d) / "a.pdf")
        doc = pdfmod.load(p)
        spec = learn.learn_taxblock(doc, _TAXROWS)
        assert spec is not None
        from rechnung.template import Template
        probe = Template(id="x", supplier_id="x", fields=[spec])
        result = apply_template(doc, probe)["ust_aufstellung"]
        assert result.value == _TAXROWS


def test_learn_line_items_direkt():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = _tabular_invoice(Path(d) / "a.pdf")
        doc = pdfmod.load(p)
        spec = learn.learn_line_items(doc, _ITEMS)
        assert spec is not None
        assert apply_line_items(doc, spec) == _ITEMS


def test_gesichertes_template_wird_nicht_still_umgeschrieben(tmp_path: Path):
    store = _store(tmp_path)
    p1 = _tabular_invoice(tmp_path / "a.pdf")
    doc1, res1 = _confirmed_result(p1, _ITEMS, _TAXROWS)
    store.mark_confirmed(res1.sha256, "tester")
    outcome = learn.learn_from_confirmed(store, res1, doc1, "tester")
    tid = outcome.template_id
    tpl = store.template(tid)
    tpl.secured_at = 1
    tpl.confirmations = 10
    store.save_template(tpl)

    # abweichende (falsche) Positionen auf einer bestaetigten Rechnung
    andere_items = [dict(_ITEMS[0], betrag=1234.0)]
    doc2, res2 = _confirmed_result(p1, andere_items, None)
    res2.template_id = tid
    store.mark_confirmed(res2.sha256, "tester2")
    outcome2 = learn.learn_from_confirmed(store, res2, doc2, "tester2")
    assert "_positionen" not in outcome2.learned
    # die gesicherte Vorlage bleibt unveraendert
    assert store.template(tid).line_items.to_dict() == tpl.line_items.to_dict()
