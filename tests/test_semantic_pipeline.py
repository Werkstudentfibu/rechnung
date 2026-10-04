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

    # confirmations >= GEGENCHECK_MIN (Standard 5): der Gegencheck (Postfach-
    # Baustein 10) ist hier nicht Gegenstand -- diese Tests pruefen die
    # gezielten KI-Anfragen fuer Luecken (Effizienz-Baustein 1/2), ein
    # zweiter, unabhaengiger semantic.extract()-Aufruf wuerde die strengen
    # field_names-Erwartungen der Mocks hier stoeren.
    t = Template(id="muster__standard", supplier_id="muster", secured_at=1, confirmations=5)
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
    """Nachgefragt wird nur das fehlende Pflichtfeld (rechnungsdatum) --
    nicht das nie angelernte optionale ``bestellnummer`` (SPEC
    Effizienz-Baustein 1: KI liest gezielt statt alles)."""
    store, inv = _basic_setup(tmp_path)

    def fake_extract(text, field_names, hints, rejected=None):
        assert field_names == ["rechnungsdatum"]
        return {"rechnungsdatum": FieldResult("rechnungsdatum", "2026-02-14", "14.02.2026", 0.6, "llm")}

    monkeypatch.setattr(pipeline.semantic, "extract", fake_extract)
    res = process(inv, store)
    assert res.fields["rechnungsdatum"].source == "llm"
    assert res.fields["rechnungsdatum"].value == "2026-02-14"
    assert "bestellnummer" not in res.fields
    # ein starker Template-Treffer wird nicht von einer schwaecheren LLM-Vermutung ueberschrieben
    assert res.fields["netto"].source == "template-anchor"


def test_semantic_fallback_asks_optional_field_of_template(tmp_path: Path, monkeypatch):
    """Ein optionales Feld, das die Vorlage kennt, aber diesmal nicht findet,
    wird nachgefragt -- eines, das sie nicht kennt, nicht."""
    store, inv = _basic_setup(tmp_path)
    t = store.template("muster__standard")
    t.fields.append(FieldSpec("bestellnummer", "number", False, anchor=Anchor("gibtsnicht", 0),
                              rel=(60, -4, 240, 12)))
    store.save_template(t)
    asked = []

    def fake_extract(text, field_names, hints, rejected=None):
        asked.extend(field_names)
        return {}

    monkeypatch.setattr(pipeline.semantic, "extract", fake_extract)
    process(inv, store)
    assert sorted(asked) == ["bestellnummer", "rechnungsdatum"]


def test_semantic_fallback_not_called_when_template_complete(tmp_path: Path, monkeypatch):
    store, inv = _basic_setup(tmp_path)
    t = store.template("muster__standard")
    t.fields.append(FieldSpec("rechnungsdatum", "date", True, anchor=Anchor("rechnungsdatum", 0),
                              rel=(60, -4, 240, 12)))
    store.save_template(t)
    assert process(inv, store, use_llm=False).fields["rechnungsdatum"].source == "template-anchor"

    def fake_extract(text, field_names, hints, rejected=None):
        raise AssertionError(f"KI unnoetig gefragt: {field_names}")

    monkeypatch.setattr(pipeline.semantic, "extract", fake_extract)
    res = process(inv, store)
    assert all(fr.source != "llm" for fr in res.fields.values())


def test_semantic_fallback_never_beats_stronger_template_field(tmp_path: Path, monkeypatch):
    store, inv = _basic_setup(tmp_path)

    def fake_extract(text, field_names, hints, rejected=None):
        # versucht (fehlerhaft) auch ein bereits stark erkanntes Feld zu ueberschreiben
        return {"netto": FieldResult("netto", 999.0, "999", 0.6, "llm")}

    monkeypatch.setattr(pipeline.semantic, "extract", fake_extract)
    res = process(inv, store)
    assert res.fields["netto"].source == "template-anchor"
    assert res.fields["netto"].value != 999.0
