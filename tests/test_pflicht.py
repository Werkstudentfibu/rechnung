"""Pflichtangaben § 14 Abs. 4 UStG / § 33 UStDV / Art. 26 MWSTG -- reine
Hinweis-Pruefung (rechnung/pflicht.py, Aaron 10.10.2026)."""

from __future__ import annotations

from rechnung import human, pflicht
from rechnung import pdf as pdfmod
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import Result, process
from rechnung.store import Store
from rechnung.template import STANDARD_FIELDS, FieldResult, ki_felder

from conftest import SUPPLIERS, make_invoice

VOLL = """Alpha Werkzeug GmbH - Musterstrasse 1 - 12345 Musterstadt
Kunde GmbH, Hauptstr. 5, 80331 München
Rechnung Nr. AL-1  Rechnungsdatum 05.09.2026  Leistungsdatum: 01.09.2026
Pos Bezeichnung Menge Preis
1 Schraube 1000 1,00
Netto 19 % 1.000,00  USt 190,00  Gesamt 1.190,00
USt-IdNr: DE123456789"""


def R(text_fields: dict, **kw) -> Result:
    res = Result(file="x.pdf", sha256="x", supplier_id=kw.pop("supplier_id", "alpha"),
                 supplier_name=kw.pop("supplier_name", "Alpha Werkzeug GmbH"),
                 billing_address_name=kw.pop("billing_address_name", "Kunde GmbH"), **kw)
    res.fields = {k: FieldResult(k, v, str(v), 1.0, "template-anchor")
                  for k, v in text_fields.items()}
    return res


KOPF = {"rechnungsnummer": "AL-1", "rechnungsdatum": "2026-09-05", "netto": 1000.0,
        "ust_betrag": 190.0, "brutto": 1190.0, "ust_satz": 19.0, "ustid": "DE123456789"}


def test_vollstaendige_rechnung():
    res = R(KOPF, line_items=[{"beschreibung": "Schraube", "menge": 1000, "gesamt_netto": 1000.0}],
            tax_breakdown=[{"satz": 19.0, "netto": 1000.0, "ust": 190.0}])
    pflicht.apply_leistungsdatum(res.fields, VOLL)
    assert res.fields["leistungsdatum"].value == "2026-09-01"
    e = pflicht.pruefen(res, VOLL)
    assert e["regelwerk"] == pflicht.UStG
    assert e["fehlt"] == [], e
    assert pflicht.hinweis_text(e) is None


def test_fehlende_angaben_werden_begruendet():
    text = "Alpha Werkzeug GmbH\nRechnung AL-1 vom 05.09.2026\nGesamt 1.190,00"
    felder = {k: v for k, v in KOPF.items() if k != "ustid"}
    felder.update(netto=1000.0, ust_betrag=190.0, brutto=1190.0)
    res = R(felder, billing_address_name=None)
    e = pflicht.pruefen(res, text)
    assert pflicht.LEISTUNG in e["fehlt"] and pflicht.STEUERNR in e["fehlt"]
    assert pflicht.EMPFAENGER in e["fehlt"] and pflicht.LEISTENDER in e["fehlt"]
    h = pflicht.hinweis_text(e)
    assert h.startswith("Pflichtangaben (§ 14 Abs. 4 UStG) fehlt: ")
    assert "Zeitpunkt/Zeitraum der Leistung" in h


def test_leistungsdatum_gleich_rechnungsdatum_und_zeitraum():
    res = R(KOPF, tax_breakdown=[{"satz": 19.0, "netto": 1000.0, "ust": 190.0}])
    for text in ("Leistungsdatum entspricht Rechnungsdatum",
                 "Das Rechnungsdatum entspricht dem Leistungsdatum.",
                 "Leistungszeitraum: 01.09.2026 - 30.09.2026",
                 "Leistungen im September 2026"):
        p = next(p for p in pflicht.pruefen(res, text)["punkte"] if p["name"] == pflicht.LEISTUNG)
        assert p["ok"] is True, text
    # zwei verschiedene Lieferdaten: kein Feld (kein Raten)
    assert pflicht.leistungsdatum_aus_text("Lieferdatum 01.09.2026, Lieferdatum 03.09.2026") is None


def test_reverse_charge_und_null_prozent():
    f = dict(KOPF, ust_betrag=0.0, ust_satz=0.0, brutto=1000.0)
    res = R(f, tax_breakdown=[{"satz": 0.0, "netto": 1000.0, "ust": 0.0}])
    p = next(p for p in pflicht.pruefen(res, VOLL)["punkte"] if p["name"] == pflicht.STEUER)
    assert p["ok"] is False and "0 %" in p["detail"]
    p = next(p for p in pflicht.pruefen(res, VOLL + "\nSteuerschuldnerschaft des "
                                        "Leistungsempfängers (§ 13b UStG)")["punkte"]
             if p["name"] == pflicht.STEUER)
    assert p["ok"] is True


def test_kleinbetragsrechnung():
    f = {"rechnungsdatum": "2026-09-05", "brutto": 23.80, "ust_satz": 19.0}
    res = R(f, billing_address_name=None)
    e = pflicht.pruefen(res, "Alpha Werkzeug GmbH, 12345 Musterstadt\n1 Stk. Kaffee 23,80 inkl. 19 % MwSt")
    assert e["regelwerk"] == pflicht.UStDV
    namen = [p["name"] for p in e["punkte"]]
    assert pflicht.EMPFAENGER not in namen and pflicht.NUMMER not in namen
    assert pflicht.LEISTUNG not in namen
    assert e["fehlt"] == []


def test_schweiz_mwstg():
    f = {"rechnungsnummer": "CH-1", "rechnungsdatum": "2026-09-05", "brutto": 1081.0,
         "netto": 1000.0, "ust_betrag": 81.0, "ust_satz": 8.1, "waehrung": "CHF"}
    res = R(f)
    text = "Beta AG, 8001 Zürich\nKunde GmbH, 3000 Bern\nCHE-123.456.788 MWST\nArtikel Beratung"
    e = pflicht.pruefen(res, text)
    assert e["regelwerk"] == pflicht.MWSTG
    assert e["fehlt"] == [], e
    lp = next(p for p in e["punkte"] if p["name"] == pflicht.LEISTUNG)
    assert "Rechnungsdatum" in lp["detail"]
    e = pflicht.pruefen(res, text.replace(" MWST", ""))
    assert pflicht.MWSTNR in e["fehlt"]


def test_leistungsdatum_feld_nie_per_ki():
    assert STANDARD_FIELDS["leistungsdatum"] == ("date", False)
    assert "leistungsdatum" not in ki_felder()


def test_pipeline_haengt_gruppe_an_und_haelt_nicht_zurueck(tmp_path):
    store = Store(tmp_path / "data")
    src = make_invoice(tmp_path / "a.pdf", invoice_no="AL-2026-0001", datum="05.01.2026",
                       netto="1.000,00", ust="190,00", brutto="1.190,00", **SUPPLIERS["alpha"])
    doc = pdfmod.load(src)
    store.save_supplier(supplier_from_doc(doc, "alpha", "Alpha Werkzeug GmbH"))
    for k, v in {"rechnungsnummer": "AL-2026-0001", "rechnungsdatum": "05.01.2026",
                 "netto": "1000,00", "ust_betrag": "190,00", "brutto": "1190,00"}.items():
        store.save_correction(doc.sha256, k, v)
    res = process(src, store, use_llm=False)
    pf = res.pflichtangaben
    assert pf and pf["regelwerk"] == pflicht.UStG
    assert pflicht.LEISTUNG in pf["fehlt"]
    assert any(h.startswith("Pflichtangaben (") for h in res.hinweise)
    assert res.to_dict()["pflichtangaben"] == pf
    # nie ein Grund fuer den Menschen
    assert not any("Pflicht" in g["grund"] and g["art"] != "pflichtfeld"
                   for g in human.gruende(res))
    assert res._pruef_text == ""
