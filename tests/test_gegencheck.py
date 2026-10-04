"""Postfach-Baustein 10: Gegencheck in der Testphase -- solange eine
Vorlage noch unter GEGENCHECK_MIN Bestaetigungen hat, liest die KI
zusaetzlich (unabhaengig) dieselbe Rechnung; weicht sie bei einem
Pflichtfeld/Betrag/der Positionssumme ab, geht die Rechnung an den
Menschen. Aktiv in der Testphase, im autonomen Modus per Schalter (Default
aus); entfaellt ab GEGENCHECK_MIN Bestaetigungen; nur wenn das
Zeitbudget reicht."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import eventlog, human, modus, pipeline
from rechnung import pdf as pdfmod
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import process
from rechnung.store import Store
from rechnung.template import Anchor, FieldResult, FieldSpec, Template

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def _setup(tmp_path: Path, confirmations: int = 1):
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    inv = make_invoice(samples / "a.pdf", rechnungsnr="RE-9", datum="14.02.2026",
                       netto=200.0, satz=19)
    doc = pdfmod.load(inv)
    sup = supplier_from_doc(doc, "muster", "Muster Handels GmbH")
    store.save_supplier(sup)
    t = Template(id="muster__standard", supplier_id="muster", secured_at=1,
                confirmations=confirmations)
    t.fields = [
        FieldSpec("rechnungsnummer", "number", True,
                  anchor=Anchor("rechnungsnummer", 0), rel=(60, -4, 240, 12)),
        FieldSpec("rechnungsdatum", "date", True,
                  anchor=Anchor("rechnungsdatum", 0), rel=(60, -4, 240, 12)),
        FieldSpec("netto", "amount", True, anchor=Anchor("nettobetrag", 0), rel=(120, -4, 340, 12)),
        FieldSpec("ust_betrag", "amount", True, anchor=Anchor("umsatzsteuer", 0), rel=(120, -4, 340, 12)),
        FieldSpec("brutto", "amount", True, anchor=Anchor("gesamtbetrag", 0), rel=(120, -4, 340, 12)),
    ]
    store.save_template(t)
    return store, inv


_KI_PASSEND = {
    "rechnungsnummer": "RE-9", "rechnungsdatum": "2026-02-14",
    "netto": 200.0, "ust_betrag": 38.0, "brutto": 238.0,
}


def _fake(werte: dict):
    def fake_extract(text, field_names, hints, rejected=None):
        assert hints == {}   # unabhaengig -- keine Vorlagenwerte als Hinweis im Prompt
        return {n: FieldResult(n, v, str(v), 0.6, "llm") for n, v in werte.items()
                if n in field_names}
    return fake_extract


def test_ki_bestaetigt_vorlage_bleibt_sicher(tmp_path, monkeypatch):
    store, inv = _setup(tmp_path)
    monkeypatch.setattr(pipeline.semantic, "extract", _fake(_KI_PASSEND))
    res = process(inv, store)
    assert res.hold_reasons == []
    assert res.status != pipeline.DEFERRED


def test_ki_weicht_bei_pflichtfeld_ab_geht_an_mensch(tmp_path, monkeypatch):
    store, inv = _setup(tmp_path)
    abweichend = dict(_KI_PASSEND, netto=999.0)
    monkeypatch.setattr(pipeline.semantic, "extract", _fake(abweichend))
    res = process(inv, store)
    assert res.status == pipeline.DEFERRED
    grund = next(g for g in res.hold_reasons if "Gegencheck" in g)
    assert "netto" in grund and "Vorlage" in grund and "KI" in grund and "999" in grund
    assert any("Gegencheck" in g["grund"] for g in human.gruende(res))


def test_ab_gegencheck_min_bestaetigungen_entfaellt_er(tmp_path, monkeypatch):
    store, inv = _setup(tmp_path, confirmations=5)

    def boom(*a, **k):
        raise AssertionError("Gegencheck sollte ab GEGENCHECK_MIN nicht mehr laufen")

    monkeypatch.setattr(pipeline.semantic, "extract", boom)
    res = process(inv, store)
    assert res.status != pipeline.DEFERRED


def test_konfigurierbare_schwelle(tmp_path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_GEGENCHECK_MIN", "2")
    store, inv = _setup(tmp_path, confirmations=2)

    def boom(*a, **k):
        raise AssertionError("ab der konfigurierten Schwelle nicht mehr laufen")

    monkeypatch.setattr(pipeline.semantic, "extract", boom)
    res = process(inv, store)
    assert res.status != pipeline.DEFERRED


def test_im_autonomen_modus_standardmaessig_aus(tmp_path, monkeypatch):
    store, inv = _setup(tmp_path)
    modus.set_mode(store, modus.AUTONOM, "test")

    def boom(*a, **k):
        raise AssertionError("im autonomen Modus per Default aus")

    monkeypatch.setattr(pipeline.semantic, "extract", boom)
    process(inv, store)


def test_im_autonomen_modus_per_schalter_aktiv(tmp_path, monkeypatch):
    store, inv = _setup(tmp_path)
    modus.set_mode(store, modus.AUTONOM, "test")
    monkeypatch.setenv("RECHNUNG_GEGENCHECK_AUTONOM", "1")
    called: list = []

    def fake_extract(text, field_names, hints, rejected=None):
        called.append(field_names)
        return {}

    monkeypatch.setattr(pipeline.semantic, "extract", fake_extract)
    process(inv, store)
    assert called


def test_budget_knapp_wird_uebersprungen_und_vermerkt(tmp_path, monkeypatch):
    store, inv = _setup(tmp_path)
    monkeypatch.setenv("RECHNUNG_TIMEOUT", "10")   # kleiner als allein die Sicherheitsmarge

    def boom(*a, **k):
        raise AssertionError("Budget reicht nicht -- darf nicht aufgerufen werden")

    monkeypatch.setattr(pipeline.semantic, "extract", boom)
    res = process(inv, store)
    assert any("Gegencheck uebersprungen" in h for h in res.hinweise)
    rec = eventlog.record(store, res)
    assert any("Gegencheck uebersprungen" in h for h in rec["hinweise"])


def test_ohne_llm_kein_gegencheck(tmp_path, monkeypatch):
    store, inv = _setup(tmp_path)

    def boom(*a, **k):
        raise AssertionError("use_llm=False -- kein Modellaufruf")

    monkeypatch.setattr(pipeline.semantic, "extract", boom)
    res = process(inv, store, use_llm=False)
    assert res.hold_reasons == []
