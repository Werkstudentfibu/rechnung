"""Teil A, Einbau in rechnen.rechne(): ein eindeutiges Summen-Tripel aus dem
Rechnungskontext (summen.py) gewinnt gegen gelesene Werte, die nicht
aufgehen oder fehlen -- eine menschliche Korrektur und der Schweizer
QR-Zahlteil-Betrag bleiben davon unberührt."""

from __future__ import annotations

from rechnung import rechnen
from rechnung.template import FieldResult

_TEXT = ("Rechnung Nettobetrag: 1.000,00 EUR zzgl. 19 % MwSt: 190,00 EUR "
         "Rechnungsbetrag: 1.190,00 EUR")


def _f(**kw):
    return {k: FieldResult(k, v, str(v), 0.6, src) for k, (v, src) in kw.items()}


def test_ki_verwechselt_netto_mit_brutto_kontext_korrigiert():
    # Befund Aaron 09.10.2026: die KI liest "Rechnungsbetrag" fuer sowohl
    # netto als auch brutto -- der Kontext kennt die drei Betraege eindeutig.
    fields = _f(brutto=(1190.0, "llm"), netto=(1190.0, "llm"), ust_satz=(19.0, "llm"),
               waehrung=("EUR", "llm"))
    rk = rechnen.rechne(fields, [], _TEXT)
    assert rk.fields["netto"].value == 1000.0
    assert rk.fields["netto"].source == rechnen.ERRECHNET
    assert rk.fields["ust_betrag"].value == 190.0
    assert any("Quelle: summen" in g for g in rk.ergaenzt)


def test_fehlende_betraege_werden_aus_kontext_ergaenzt():
    fields = _f(waehrung=("EUR", "llm"))
    rk = rechnen.rechne(fields, [], _TEXT)
    assert rk.fields["netto"].value == 1000.0
    assert rk.fields["ust_betrag"].value == 190.0
    assert rk.fields["brutto"].value == 1190.0


def test_manuelle_korrektur_bleibt_auch_gegen_den_kontext():
    fields = _f(brutto=(1190.0, "llm"), netto=(5.0, "manual"), waehrung=("EUR", "llm"))
    rk = rechnen.rechne(fields, [], _TEXT)
    assert rk.fields["netto"].value == 5.0
    assert rk.fields["netto"].source == "manual"


def test_qr_brutto_bleibt_massgeblich():
    fields = _f(brutto=(1190.0, "qr"), waehrung=("EUR", "llm"))
    rk = rechnen.rechne(fields, [], _TEXT)
    assert rk.fields["brutto"].value == 1190.0
    assert rk.fields["brutto"].source == "qr"
    assert rk.fields["netto"].value == 1000.0
    assert rk.fields["netto"].source == rechnen.ERRECHNET


def test_aufgehende_vorlagenwerte_bleiben_unangetastet():
    # Die gelesenen Werte gehen bereits auf -- summen.py darf nichts ueberschreiben.
    fields = _f(netto=(1000.0, "template-anchor"), ust_betrag=(190.0, "template-anchor"),
               brutto=(1190.0, "template-anchor"), waehrung=("EUR", "template-anchor"))
    rk = rechnen.rechne(fields, [], _TEXT)
    assert rk.fields["netto"].source == "template-anchor"
    assert rk.fields["netto"].value == 1000.0
    assert not any("Quelle: summen" in g for g in rk.ergaenzt)


def test_mischsaetze_ohne_gelesene_kopfwerte():
    text = "Steuersatz 7 % Netto 500,00 MwSt 35,00 Steuersatz 19 % Netto 300,00 MwSt 57,00"
    fields = _f(waehrung=("EUR", "llm"))
    rk = rechnen.rechne(fields, [], text)
    assert rk.fields["netto"].value == 800.0
    assert rk.fields["ust_betrag"].value == 92.0
    assert rk.fields["brutto"].value == 892.0
    # kein einzelner (falscher) Satz bei Mischsaetzen
    assert "ust_satz" not in rk.fields or rk.fields["ust_satz"].value is None
    assert len(rk.fields["ust_aufstellung"].value) == 2


def test_mehrdeutiger_kontext_setzt_nichts_und_nennt_grund():
    fields = _f(waehrung=("EUR", "llm"))
    rk = rechnen.rechne(fields, [], "Position A 100,00 EUR Position B 200,00 EUR Position C 300,00 EUR")
    assert "netto" not in rk.fields
    assert "brutto" not in rk.fields
    assert any("mehrdeutig" in g or "keine eindeutigen" in g for g in rk.gruende)
