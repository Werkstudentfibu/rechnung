"""QR-Zahlteil als strukturierte Quelle (Befund Aaron 09.10.2026: Zuercher
QR-Rechnung -- Lieferant = Hotel, Brutto = Netto, deutsche MwSt)."""

from __future__ import annotations

from rechnung import semantic, swissqr
from rechnung.template import FieldResult


def _qr(**kw):
    d = dict(iban="CH9300762011623852957", creditor_name="Zuerich Tourismus",
             betrag=1081.35, waehrung="CHF", debtor_name="AMERON Zuerich",
             referenz_typ="NON", referenz="", nachricht="")
    d.update(kw)
    return swissqr.SwissQR(**d)


def test_qr_waehrung_und_brutto_gewinnen_gegen_lesung():
    fields = {"waehrung": FieldResult("waehrung", "EUR", "EUR", 0.85, "text"),
              "brutto": FieldResult("brutto", 1000.0, "1000,00", 0.6, "llm")}
    hinweise = []
    swissqr.apply(fields, _qr(), hinweise)
    assert fields["waehrung"].value == "CHF"
    assert fields["brutto"].value == 1081.35 and fields["brutto"].source == "qr"
    assert any("QR-Zahlteil" in h for h in hinweise)


def test_korrektur_des_menschen_bleibt():
    fields = {"brutto": FieldResult("brutto", 999.0, "999", 1.0, "manual"),
              "waehrung": FieldResult("waehrung", "EUR", "EUR", 1.0, "manual")}
    swissqr.apply(fields, _qr(), [])
    assert fields["brutto"].value == 999.0 and fields["waehrung"].value == "EUR"


def test_qr_ohne_betrag_laesst_brutto():
    fields = {"brutto": FieldResult("brutto", 50.0, "50", 0.6, "llm")}
    swissqr.apply(fields, _qr(betrag=None), [])
    assert fields["brutto"].value == 50.0


def test_parteien_aus_qr_ueberschreiben_ki():
    lief = {"name": "AMERON Zuerich"}          # KI hat den Empfaenger erwischt
    empf = {}
    swissqr.parteien(lief, empf, _qr())
    assert lief["name"] == "Zuerich Tourismus"
    assert lief["iban"] == "CH9300762011623852957"
    assert empf["name"] == "AMERON Zuerich"


def test_lieferant_gleich_empfaenger_wird_verworfen():
    res = semantic.FullExtraction()
    res.supplier["name"] = "AMERON Hotel Zuerich"
    res.empfaenger["name"] = "AMERON Hotel Zuerich"
    semantic._absender_nicht_empfaenger(res)
    assert "name" not in res.supplier
    assert "lieferant_name" in res.rejected


def test_verschiedene_namen_bleiben():
    res = semantic.FullExtraction()
    res.supplier["name"] = "Zuerich Tourismus"
    res.empfaenger["name"] = "AMERON Zuerich"
    semantic._absender_nicht_empfaenger(res)
    assert res.supplier["name"] == "Zuerich Tourismus"


def test_reverse_charge_aus_text():
    from rechnung import belegart

    f = {}
    belegart.reverse_charge(f, "Steuerschuldnerschaft des Leistungsempfaengers (Reverse Charge)")
    assert f["reverse_charge"].value == "ja"
    g = {}
    belegart.reverse_charge(g, "Zahlbar innerhalb 14 Tagen")
    assert "reverse_charge" not in g
    h = {"reverse_charge": FieldResult("reverse_charge", "nein", "nein", 1.0, "manual")}
    belegart.reverse_charge(h, "Reverse Charge")
    assert h["reverse_charge"].value == "nein"
