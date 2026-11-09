"""Teil A (Befund Aaron 09.10.2026): Beträge aus dem Rechnungskontext
herleiten -- ohne KI, nur Formel + Schlüsselwörter + Lage im Text."""

from __future__ import annotations

from rechnung import summen


def test_deutsche_rechnung_zzgl_mwst():
    t = ("Rechnung RE-1000 Nettobetrag: 1.000,00 EUR zzgl. 19 % MwSt: 190,00 EUR "
         "Rechnungsbetrag: 1.190,00 EUR")
    erg = summen.summen_aus_kontext(t, "EUR")
    assert (erg.netto, erg.ust_betrag, erg.brutto, erg.ust_satz) == (1000.0, 190.0, 1190.0, 19.0)
    assert not erg.steuerzeilen


def test_schweizer_total_inkl_mwst_ohne_separaten_netto():
    t = "Rechnung Total inkl. MWST 8.1% CHF 1'081.35"
    erg = summen.summen_aus_kontext(t, "CHF")
    assert erg.brutto == 1081.35
    assert erg.ust_satz == 8.1
    assert erg.netto == round(1081.35 / 1.081, 2)
    assert erg.ust_betrag == round(1081.35 - erg.netto, 2)


def test_mischsaetze_7_und_19_werden_nicht_zusammengefasst():
    t = "Steuersatz 7 % Netto 500,00 MwSt 35,00 Steuersatz 19 % Netto 300,00 MwSt 57,00"
    erg = summen.summen_aus_kontext(t, "EUR")
    assert erg.netto == 800.0 and erg.ust_betrag == 92.0 and erg.brutto == 892.0
    assert erg.ust_satz is None   # kein Einzelsatz bei Mischsaetzen
    assert len(erg.steuerzeilen) == 2
    saetze = {z["satz"] for z in erg.steuerzeilen}
    assert saetze == {7.0, 19.0}
    sieben = next(z for z in erg.steuerzeilen if z["satz"] == 7.0)
    assert (sieben["netto"], sieben["ust"]) == (500.0, 35.0)


def test_zwischensumme_rabatt_versand_werden_nicht_verwechselt():
    t = ("Zwischensumme: 1.000,00 EUR Rabatt: -50,00 EUR Versand: 20,00 EUR "
         "Nettobetrag: 970,00 EUR MwSt 19 %: 184,30 EUR Gesamtbetrag: 1.154,30 EUR")
    erg = summen.summen_aus_kontext(t, "EUR")
    assert erg.netto == 970.0
    assert erg.brutto == 1154.3
    assert erg.ust_betrag == 184.3


def test_rechnung_ohne_steuer():
    t = "Nettobetrag: 500,00 EUR Gesamtbetrag: 500,00 EUR"
    erg = summen.summen_aus_kontext(t, "EUR")
    assert erg.netto == 500.0 and erg.brutto == 500.0 and erg.ust_betrag == 0.0
    assert erg.ust_satz == 0.0


def test_mehrdeutiger_fall_setzt_nichts():
    t = "Position A 100,00 EUR Position B 200,00 EUR Position C 300,00 EUR"
    erg = summen.summen_aus_kontext(t, "EUR")
    assert not erg.gefunden
    assert erg.netto is None and erg.brutto is None
    assert erg.hinweis


def test_kein_text_setzt_nichts():
    erg = summen.summen_aus_kontext("", "EUR")
    assert not erg.gefunden
    assert erg.hinweis


def test_zwei_gleich_plausible_bruttokandidaten_qr_entscheidet():
    t = ("Zwischensumme 1.000,00 EUR MwSt 19 % 190,00 EUR Gesamtbetrag 1.190,00 EUR "
         "Alter Gesamtbetrag 1.200,00 EUR")
    erg = summen.summen_aus_kontext(t, "EUR", qr_brutto=1190.00)
    assert erg.brutto == 1190.0
    assert erg.netto == 1000.0


def test_schweizer_tausendertrenner_und_dezimalpunkt():
    t = "Nettobetrag 1'000.00 CHF zzgl. 8.1% MWST 81.00 CHF Total 1'081.00 CHF"
    erg = summen.summen_aus_kontext(t, "CHF")
    assert (erg.netto, erg.ust_betrag, erg.brutto, erg.ust_satz) == (1000.0, 81.0, 1081.0, 8.1)


def test_unplausibler_satz_wird_nicht_uebernommen():
    # 35,00 / 500,00 = 7 % waere gueltig, aber der Steuerbetrag passt nicht
    # zum Netto (11,4 % ist in EUR kein gueltiger Satz) -- lieber nichts.
    t = "Nettobetrag 500,00 EUR Steuerbetrag 57,00 EUR Gesamtbetrag 557,00 EUR"
    erg = summen.summen_aus_kontext(t, "EUR")
    assert not erg.gefunden
