"""Gegenpruefung fuer neue Lieferanten (gegenpruefung.py, Aaron 19.11.2026):
KI-Werte ohne Vorlage werden nur sicher, wenn eine zweite, unabhaengige
Lesung sie bestaetigt -- Standard AUS, gemessen im Blindtest. Nur erfundene
Rechnungen."""

from __future__ import annotations

import pytest

from rechnung import gegenpruefung, human, pipeline, testsatz
from rechnung import pdf as pdfmod
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import Result, dispatch, process
from rechnung.store import Store
from rechnung.template import FieldResult

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

TEXT = ("Muster GmbH  Rechnungsnummer: RE-2026-0042  Rechnungsdatum: 05.11.2026\n"
        "Nettobetrag 100,00 EUR\nUmsatzsteuer 19% 19,00 EUR\nGesamtbetrag 119,00 EUR")
KI = {"rechnungsnummer": "RE-2026-0042", "rechnungsdatum": "2026-11-05", "netto": 100.0,
      "ust_betrag": 19.0, "brutto": 119.0}


def test_zweite_lesung():
    assert gegenpruefung.zweite_lesung(TEXT) == {
        "rechnungsnummer": "RE-2026-0042", "rechnungsdatum": "2026-11-05",
        "netto": 100.0, "ust_betrag": 19.0, "brutto": 119.0}


@pytest.mark.parametrize("text,fehlt", [
    ("Rechnungsnummer: RE-1  Rechnungs-Nr. RE-2", "rechnungsnummer"),      # zwei verschiedene
    ("Rechnungsnummer Kundennummer\nRE-1 K-5", "rechnungsnummer"),         # Label ohne Ziffer
    ("Datum: 05.11.2026", "rechnungsdatum"),                               # nur "Datum"
    ("Rechnungsdatum 05.11.2026 Rechnung vom 06.11.2026", "rechnungsdatum"),
])
def test_zweite_lesung_nur_eindeutig(text, fehlt):
    assert fehlt not in gegenpruefung.zweite_lesung(text)


def _res(werte=KI, score=0.6, sid="muster"):
    return Result(file="x.pdf", sha256="ab" * 32, supplier_id=sid, match_score=score,
                  status=pipeline.NO_TEMPLATE,
                  fields={k: FieldResult(k, v, str(v), 0.6, "llm") for k, v in werte.items()})


def test_standard_aus_nur_vermerkt(monkeypatch):
    monkeypatch.delenv("RECHNUNG_KI_GEGENPRUEFUNG", raising=False)
    res = _res()
    g = gegenpruefung.anwenden(res, TEXT)
    assert g["vollstaendig"] and g["hart_erkannt"] and not g["angewendet"]
    assert sorted(g["belegt"]) == sorted(KI)
    assert res.gegenpruefung is g and not human.ist_sicher(res)


def test_eingeschaltet_hebt_belegte_werte(monkeypatch):
    monkeypatch.setenv("RECHNUNG_KI_GEGENPRUEFUNG", "1")
    res = _res()
    assert gegenpruefung.anwenden(res, TEXT)["angewendet"]
    assert all(res.fields[f].confidence == pipeline.CONF_MIN for f in KI)
    assert res.fields["netto"].source == "llm"
    assert gegenpruefung.HINWEIS in res.hinweise
    assert human.ist_sicher(res)


@pytest.mark.parametrize("aenderung", [
    {"werte": dict(KI, rechnungsnummer="K-5")},     # KI hat Kundennummer erwischt
    {"werte": dict(KI, brutto=120.0)},             # Betrag weicht ab
    {"score": 0.2},                                # Lieferant nur ueber den Namen
    {"sid": None},                                 # Lieferant unbekannt
])
def test_greift_nicht_ohne_vollen_beleg(monkeypatch, aenderung):
    monkeypatch.setenv("RECHNUNG_KI_GEGENPRUEFUNG", "1")
    res = _res(**aenderung)
    assert not gegenpruefung.anwenden(res, TEXT)["angewendet"]
    assert all(fr.confidence == 0.6 for fr in res.fields.values())


def _ki_liest(monkeypatch, werte):
    def fake(res, doc, *a, **k):
        for name, v in werte.items():
            res.fields[name] = FieldResult(name, v, str(v), 0.6, "llm")
        res.extraction = "llm"
    monkeypatch.setattr(pipeline, "_llm_full", fake)


def _neuer_lieferant(tmp_path):
    store = Store(tmp_path / "data")
    p = make_invoice(tmp_path / "re.pdf")
    store.save_supplier(supplier_from_doc(pdfmod.load(p), "muster", "Muster Handels GmbH"))
    return store, p


WERTE_SYNTH = {"rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "2026-02-14",
               "netto": 1000.0, "ust_betrag": 190.0, "brutto": 1190.0}


def test_pipeline_ohne_schalter_bleibt_beim_menschen(tmp_path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_KI_GEGENPRUEFUNG", raising=False)
    store, p = _neuer_lieferant(tmp_path)
    _ki_liest(monkeypatch, WERTE_SYNTH)
    res = process(p, store)
    assert res.status == pipeline.NO_TEMPLATE and res.supplier_id == "muster"
    assert res.gegenpruefung["vollstaendig"] and not res.gegenpruefung["angewendet"]
    assert any(g["art"] == "unsicher" for g in human.gruende(res))
    assert not res.shadow["would_pass"]


def test_pipeline_mit_schalter_sicher(tmp_path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_KI_GEGENPRUEFUNG", "1")
    store, p = _neuer_lieferant(tmp_path)
    _ki_liest(monkeypatch, WERTE_SYNTH)
    res = process(p, store)
    assert res.gegenpruefung["angewendet"]
    assert human.gruende(res) == [], human.gruende(res)
    assert res.shadow["would_pass"]
    # Testphase: trotzdem Klick
    assert dispatch(res, store) == {"aktion": "bestaetigung"}


def test_blindtest_misst_gegenpruefung():
    res = _res()
    gegenpruefung.anwenden(res, TEXT)          # Schalter aus: nur vermerkt
    v = {"felder": {k: {"ergebnis": testsatz.RICHTIG, "quelle": "llm"} for k in KI},
         "positionen": {"soll": 0, "gefunden": 0, "korrekt": 0}}
    b = testsatz._blind_bewertung(res, v)
    assert b["sicher_mit_gegenpruefung"] and not b["sicher_heute"]
    out = [dict(b, lieferant="muster", felder=v["felder"])]
    bl = testsatz._blind_summe(out, ki={"aktiv": True, "modell": "m"})
    assert bl["sicher_mit_gegenpruefung"] == 1 and bl["gegenpruefung_fehlerhaft"] == 0
    assert "Waeren mit Gegenpruefung durchgegangen: 1, davon fehlerhaft: 0" in \
        "\n".join(testsatz._blind_text(bl, 1))
