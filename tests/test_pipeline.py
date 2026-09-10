from pathlib import Path

import pytest

from rechnung import pdf as pdfmod
from rechnung.identify import identify, supplier_from_doc
from rechnung.pipeline import AUTO, NO_TEMPLATE, UNKNOWN, process
from rechnung.store import Store
from rechnung.template import Anchor, FieldSpec, Template, apply_template
from rechnung.textnorm import iban_valid, parse_amount, parse_date

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def _template() -> Template:
    t = Template(id="muster__standard", supplier_id="muster", secured_at=2, confirmations=2)
    t.fields = [
        FieldSpec("rechnungsnummer", "number", True, anchor=Anchor("rechnungsnummer", 0), rel=(60, -4, 240, 12)),
        FieldSpec("rechnungsdatum", "date", True, anchor=Anchor("rechnungsdatum", 0), rel=(50, -4, 240, 12)),
        FieldSpec("netto", "amount", True, anchor=Anchor("nettobetrag", 0), rel=(120, -4, 340, 12)),
        FieldSpec("ust_betrag", "amount", True, anchor=Anchor("umsatzsteuer", 0), rel=(120, -4, 340, 12)),
        FieldSpec("brutto", "amount", True, anchor=Anchor("gesamtbetrag", 0), rel=(120, -4, 340, 12)),
        FieldSpec("iban", "iban", False, anchor=Anchor("iban", 0), rel=(20, -4, 380, 12)),
    ]
    return t


def test_parsers():
    assert parse_amount("1.234,56 EUR") == 1234.56
    assert parse_amount("99,90") == 99.90
    assert parse_amount("1234.56") == 1234.56
    assert parse_date("14.02.2026").isoformat() == "2026-02-14"
    assert parse_date("1. Februar 2026").isoformat() == "2026-02-01"
    assert iban_valid("DE89 3704 0044 0532 0130 00")
    assert not iban_valid("DE00 1234")


def test_identify_and_extract(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "re1.pdf", rechnungsnr="RE-2026-1", netto=1000.0)

    doc = pdfmod.load(inv)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    assert identify(doc, store.suppliers())[0].id == "muster"

    store.save_template(_template())
    fields = apply_template(doc, _template())
    assert fields["rechnungsnummer"].value == "RE-2026-1"
    assert fields["netto"].value == 1000.0
    assert fields["ust_betrag"].value == 190.0
    assert fields["brutto"].value == 1190.0
    assert iban_valid(fields["iban"].value)


def test_process_status(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "samples"
    samples.mkdir()
    a = make_invoice(samples / "a.pdf", rechnungsnr="RE-1", netto=500.0)

    # unbekannter Lieferant
    assert process(a, store).status == UNKNOWN

    doc = pdfmod.load(a)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    assert process(a, store).status == NO_TEMPLATE

    store.save_template(_template())
    r = process(a, store)
    assert r.status == AUTO
    assert r.overall_confidence >= 0.7
    assert all(c.ok is not False for c in r.checks)


def test_write_output_json_and_csv(tmp_path: Path):
    store = Store(tmp_path / "data")
    p = store.write_output("re1", {"rechnungsnummer": "RE-9", "netto": 100.0, "iban": None})
    assert p.exists()
    csv_path = p.with_suffix(".csv")
    assert csv_path.exists()
    lines = csv_path.read_text("utf-8").splitlines()
    assert lines[0] == "feld;wert"
    assert "rechnungsnummer;RE-9" in lines
    assert "iban;" in lines


def test_duplicate_detection(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "samples"
    samples.mkdir()
    a = make_invoice(samples / "a.pdf", rechnungsnr="RE-DUP", netto=500.0)
    doc = pdfmod.load(a)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    store.save_template(_template())

    store.mark_processed({"supplier_id": "muster", "rechnungsnummer": "RE-DUP"})
    r = process(a, store)
    dup = next(c for c in r.checks if c.name == "keine-dublette")
    assert dup.ok is False
