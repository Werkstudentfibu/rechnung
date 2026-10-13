"""Ausgeblendete Felder (template.ausgeblendet, Aaron 08.10.2026)."""

from __future__ import annotations

from rechnung import jobrouter
from rechnung.template import STANDARD_FIELDS, _AUS_STANDARD, ausgeblendet, sichtbare_felder

AUS = {"bestellnummer", "skonto_prozent", "skonto_frist",
       "faelligkeit", "einkaeufer", "leistungsdatum"}   # Sonstige Daten raus (Aaron 13.10.2026)


def test_standard_blendet_die_fuenf_felder_aus(monkeypatch):
    monkeypatch.delenv("RECHNUNG_FELDER_AUS", raising=False)
    assert ausgeblendet() == AUS
    sichtbar = sichtbare_felder()
    assert not AUS & set(sichtbar)
    # Pflichtfelder bleiben immer sichtbar
    assert {n for n, (_t, req) in STANDARD_FIELDS.items() if req} <= set(sichtbar)
    assert set(_AUS_STANDARD.split(",")) == AUS


def test_leer_heisst_alle_felder_an(monkeypatch):
    monkeypatch.setenv("RECHNUNG_FELDER_AUS", "")
    assert ausgeblendet() == frozenset()
    assert sichtbare_felder() == STANDARD_FIELDS


def test_export_laesst_ausgeblendete_felder_weg(monkeypatch):
    monkeypatch.delenv("RECHNUNG_FELDER_AUS", raising=False)
    res = {"fields": {"rechnungsnummer": {"value": "RE-1"},
                      "skonto_prozent": {"value": 2.0},
                      "bestellnummer": {"value": "B-7"}}}
    payload = jobrouter.payload_from_result(res)
    flat = repr(payload)
    assert "RE-1" in flat
    assert "B-7" not in flat and "skonto_prozent" not in flat


def test_lernen_ueberspringt_ausgeblendete_felder(monkeypatch, tmp_path):
    from rechnung import learn
    from rechnung import pdf as pdfmod
    from rechnung.synth import make_invoice

    monkeypatch.delenv("RECHNUNG_FELDER_AUS", raising=False)
    doc = pdfmod.load(make_invoice(tmp_path / "a.pdf"))
    _tpl, out = learn.learn_template(doc, "muster", {"rechnungsdatum": "2026-02-14",
                                                     "bestellnummer": "X-9"})
    assert "bestellnummer" in out.skipped
