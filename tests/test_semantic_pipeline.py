"""Wiring-Tests: semantischer Fallback (semantic.py) in pipeline.process()."""

from pathlib import Path

import pytest

from rechnung import pdf as pdfmod
from rechnung import pipeline
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import process
from rechnung.store import Store
from rechnung.template import Anchor, FieldResult, FieldSpec, Template

reportlab = pytest.importorskip("reportlab")


def _basic_setup(tmp_path: Path):
    from rechnung.synth import make_invoice

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    inv = make_invoice(samples / "a.pdf", rechnungsnr="RE-9", netto=200.0)
    doc = pdfmod.load(inv)

    sup = supplier_from_doc(doc, "muster", "Muster Handels GmbH")
    store.save_supplier(sup)

    t = Template(id="muster__standard", supplier_id="muster", secured_at=1, confirmations=1)
    t.fields = [
        FieldSpec("rechnungsnummer", "number", True, anchor=Anchor("rechnungsnummer", 0), rel=(60, -4, 240, 12)),
        FieldSpec("netto", "amount", True, anchor=Anchor("nettobetrag", 0), rel=(120, -4, 340, 12)),
        FieldSpec("ust_betrag", "amount", True, anchor=Anchor("umsatzsteuer", 0), rel=(120, -4, 340, 12)),
        FieldSpec("brutto", "amount", True, anchor=Anchor("gesamtbetrag", 0), rel=(120, -4, 340, 12)),
        # "bestellnummer" bewusst nicht angelernt -- Luecke fuer den Fallback
    ]
    store.save_template(t)
    return store, inv


def test_semantic_fallback_disabled_by_default_no_change(tmp_path: Path):
    """Ohne RECHNUNG_LLM_ENABLED bleibt das Verhalten exakt wie zuvor --
    keine Netzwerkanfrage, kein zusaetzliches Feld."""
    store, inv = _basic_setup(tmp_path)
    res = process(inv, store)
    assert "bestellnummer" not in res.fields
    assert res.fields["netto"].source == "template-anchor"


def test_semantic_fallback_fills_gap(tmp_path: Path, monkeypatch):
    store, inv = _basic_setup(tmp_path)

    def fake_extract(text, field_names, hints):
        assert "bestellnummer" in field_names
        assert "netto" not in field_names  # Template hat das schon stark geliefert
        return {"bestellnummer": FieldResult("bestellnummer", "PO-42", "PO-42", 0.6, "llm")}

    monkeypatch.setattr(pipeline.semantic, "extract", fake_extract)
    res = process(inv, store)
    assert res.fields["bestellnummer"].source == "llm"
    assert res.fields["bestellnummer"].value == "PO-42"
    # ein starker Template-Treffer wird nicht von einer schwaecheren LLM-Vermutung ueberschrieben
    assert res.fields["netto"].source == "template-anchor"


def test_semantic_fallback_never_beats_stronger_template_field(tmp_path: Path, monkeypatch):
    store, inv = _basic_setup(tmp_path)

    def fake_extract(text, field_names, hints):
        # versucht (fehlerhaft) auch ein bereits stark erkanntes Feld zu ueberschreiben
        return {"netto": FieldResult("netto", 999.0, "999", 0.6, "llm")}

    monkeypatch.setattr(pipeline.semantic, "extract", fake_extract)
    res = process(inv, store)
    assert res.fields["netto"].source == "template-anchor"
    assert res.fields["netto"].value != 999.0
