"""Baustein 5: Faelligkeit und Skonto aus PDFs -- lesen, lernbar, an
JobRouter; Unsicheres bleibt leer mit Hinweis."""

from __future__ import annotations

import json

import pytest

from rechnung import jobrouter, learn, zahlung
from rechnung import pdf as pdfmod
from rechnung.config import JobRouterConfig
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import process
from rechnung.store import Store
from rechnung.synth import make_invoice


@pytest.mark.parametrize("text,erwartet", [
    ("Zahlbar bis 28.02.2026 auf IBAN", {"faelligkeit": "2026-02-28"}),
    ("Zahlbar innerhalb von 30 Tagen netto. 2 % Skonto bei Zahlung innerhalb von 10 Tagen.",
     {"faelligkeit": "2026-03-16", "zahlungsziel_tage": 30, "skonto_prozent": 2.0,
      "skonto_tage": 10, "skonto_frist": "2026-02-24"}),
    ("10 Tage 2% Skonto, 30 Tage netto",
     {"skonto_prozent": 2.0, "skonto_frist": "2026-02-24", "faelligkeit": "2026-03-16"}),
    ("Abzueglich 3% Skonto bis 24.02.2026, zahlbar bis 16.03.2026",
     {"skonto_prozent": 3.0, "skonto_frist": "2026-02-24", "faelligkeit": "2026-03-16"}),
    ("#SKONTO#TAGE=14#PROZENT=2.00#", {"skonto_prozent": 2.0, "skonto_frist": "2026-02-28"}),
    ("Zahlung innerhalb 14 Tagen ohne Abzug", {"faelligkeit": "2026-02-28"}),
])
def test_parse(text, erwartet):
    z = zahlung.parse(text, "2026-02-14")
    for k, v in erwartet.items():
        assert z.werte.get(k) == v, (k, z.werte, z.hinweise)
    assert z.hinweise == []


@pytest.mark.parametrize("text,leer", [
    ("3% Skonto innerhalb 7 Tagen, 2% Skonto innerhalb 14 Tagen", "skonto_prozent"),
    ("Zahlbar bis 28.02.2026. Zahlbar innerhalb 30 Tagen.", "faelligkeit"),
    ("Skonto 2% moeglich", "skonto_prozent"),
])
def test_unsicher_bleibt_leer_mit_hinweis(text, leer):
    z = zahlung.parse(text, "2026-02-14")
    assert leer not in z.werte and z.hinweise


def test_relative_frist_ohne_rechnungsdatum():
    z = zahlung.parse("zahlbar innerhalb 30 Tagen", None)
    assert "faelligkeit" not in z.werte and "ohne Rechnungsdatum" in z.hinweise[0]


def _store_with_supplier(tmp_path, pdf):
    store = Store(tmp_path / "data")
    store.save_supplier(supplier_from_doc(pdfmod.load(pdf), "muster", "Muster"))
    return store


def test_pipeline_liest_und_jobrouter_bekommt_felder(tmp_path):
    p = make_invoice(tmp_path / "a.pdf", zahlungstext=(
        "Zahlbar innerhalb von 30 Tagen netto, 2 % Skonto bei Zahlung innerhalb von 10 Tagen."))
    store = _store_with_supplier(tmp_path, p)
    store.save_correction(pdfmod.load(p).sha256, "rechnungsdatum", "14.02.2026")
    res = process(p, store, use_llm=False)
    assert res.fields["faelligkeit"].value == "2026-03-16"
    assert res.fields["skonto_prozent"].value == 2.0
    assert res.fields["skonto_frist"].value == "2026-02-24"
    assert res.to_dict()["fields"]["skonto_prozent"]["origin"] == "text"
    mp = tmp_path / "map.json"
    mp.write_text(json.dumps({"skonto_prozent": "CashDiscountPercent",
                              "faelligkeit": "DueDate"}))
    body = jobrouter.payload_from_result(res.to_dict(), JobRouterConfig(mapping_file=str(mp)))
    assert body["fields"]["CashDiscountPercent"] == 2.0
    assert body["fields"]["DueDate"] == "2026-03-16"
    assert body["fields"]["skonto_frist"] == "2026-02-24"


def test_gestaffeltes_skonto_hinweis_geht_mit(tmp_path):
    p = make_invoice(tmp_path / "a.pdf", zahlungstext=(
        "3% Skonto innerhalb 7 Tagen, 2% Skonto innerhalb 14 Tagen, 30 Tage netto"))
    store = _store_with_supplier(tmp_path, p)
    res = process(p, store, use_llm=False)
    assert "skonto_prozent" not in res.fields
    assert any("skonto_prozent" in h for h in res.hinweise)
    assert jobrouter.payload_from_result(res.to_dict(), JobRouterConfig())["hints"]


def test_faelligkeit_in_vorlage_lernbar(tmp_path):
    """Ein vom Menschen eingetragener Wert (hier: Skonto-Satz, den die
    Textregel wegen fehlender Frist nicht nimmt) wird mit gelernt."""
    p = make_invoice(tmp_path / "a.pdf", zahlungstext="Skonto: 2,5 % (siehe AGB)")
    doc = pdfmod.load(p)
    tpl, out = learn.learn_template(doc, "muster", {"skonto_prozent": 2.5,
                                                     "rechnungsnummer": "RE-2026-00042"})
    assert tpl is not None and "skonto_prozent" in out.learned, out.to_dict()
    from rechnung.template import apply_template
    assert apply_template(doc, tpl)["skonto_prozent"].value == 2.5
