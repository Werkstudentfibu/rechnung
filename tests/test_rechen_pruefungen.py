"""Postfach-Baustein 9: Rechen-Pruefungen streng machen.

(a) je Position: menge * einzelpreis (abzueglich Rabatt/Nachlass) =
    Zeilensumme, Toleranz nur Rundung; Pauschalen (ohne Menge/Einzelpreis)
    werden nicht beanstandet.
(b) Positionssumme muss zum NETTO passen (0,02 + 0,01 je Position, nicht
    mehr 1 % des Betrags); gegen BRUTTO nur mit erkennbarem Bruttopreis-
    Kennzeichen im Text -- nicht mehr "netto ODER brutto, was passt".
(c) je Steuersatz: Positionssumme gegen die Zeile der USt-Aufstellung,
    USt-Betrag = netto * Satz.

Jede rote Rechen-Pruefung -> Mensch mit konkretem Grund; eine fehlende
Positionstabelle ist KEIN Fehler (Check bleibt None)."""

from __future__ import annotations

from rechnung import validate
from rechnung.template import FieldResult


def _fr(name, value):
    return FieldResult(name, value, str(value), 0.9, "template-anchor")


# ---- (a) Positionen rechnerisch richtig -----------------------------------

def test_vertauschte_spalten_faellt_auf():
    # Eigentlich 3 Stueck zu 12,50 = 37,50 -- Menge/Einzelpreis beim Lesen
    # vertauscht (Spalten falsch zugeordnet): 12,50 Stueck zu 3,00 wuerde
    # ebenfalls 37,50 ergeben, hier aber mit einer falschen Zeilensumme.
    items = [{"beschreibung": "Artikel", "menge": 3, "einzelpreis": 12.50,
             "gesamtpreis": 3.00}]
    chk = validate.check_line_item_math(items)
    assert chk.ok is False
    assert "erwartet 37.50" in chk.detail and "gefunden 3.00" in chk.detail


def test_falsche_zeilensumme():
    items = [{"beschreibung": "Artikel", "menge": 2, "einzelpreis": 15.00, "gesamtpreis": 35.00}]
    chk = validate.check_line_item_math(items)
    assert chk.ok is False
    assert "erwartet 30.00" in chk.detail and "gefunden 35.00" in chk.detail


def test_korrekte_zeile_ist_gruen():
    items = [{"beschreibung": "Artikel", "menge": 4, "einzelpreis": 9.99, "gesamtpreis": 39.96}]
    assert validate.check_line_item_math(items).ok is True


def test_pauschale_ohne_menge_wird_nicht_beanstandet():
    items = [{"beschreibung": "Pauschale Versand", "gesamtpreis": 9.90}]
    assert validate.check_line_item_math(items).ok is None


def test_rabattzeile_prozent_korrekt_eingerechnet():
    items = [{"beschreibung": "Artikel", "menge": 10, "einzelpreis": 10.00,
             "rabatt_prozent": 10, "gesamtpreis": 90.00}]
    assert validate.check_line_item_math(items).ok is True


def test_rabattzeile_betrag_korrekt_eingerechnet():
    items = [{"beschreibung": "Artikel", "menge": 10, "einzelpreis": 10.00,
             "rabatt_betrag": 5.00, "gesamtpreis": 95.00}]
    assert validate.check_line_item_math(items).ok is True


def test_rabatt_nicht_eingerechnet_faellt_auf():
    items = [{"beschreibung": "Artikel", "menge": 10, "einzelpreis": 10.00,
             "rabatt_prozent": 10, "gesamtpreis": 100.00}]
    chk = validate.check_line_item_math(items)
    assert chk.ok is False and "erwartet 90.00" in chk.detail


def test_rundungstoleranz_001_je_zeile():
    items = [{"beschreibung": "Artikel", "menge": 3, "einzelpreis": 3.335, "gesamtpreis": 10.01}]
    assert validate.check_line_item_math(items).ok is True


def test_fehlende_positionstabelle_ist_kein_fehler():
    assert validate.check_line_item_math(None).ok is None
    assert validate.check_line_item_math([]).ok is None


# ---- (b) Positionssumme vs. Netto/Brutto ----------------------------------

def test_positionssumme_gegen_netto_mit_toleranz_je_position():
    fields = {"netto": _fr("netto", 100.0)}
    items = [{"gesamtpreis": 33.34}, {"gesamtpreis": 33.33}, {"gesamtpreis": 33.33}]
    assert validate.check_line_items_total(fields, items).ok is True


def test_positionssumme_nicht_mehr_ein_prozent_toleranz():
    """Bei einem hohen Betrag waere 1 % viel zu grosszuegig -- jetzt gilt
    nur noch 0,02 + 0,01 je Position."""
    fields = {"netto": _fr("netto", 10_000.0)}
    items = [{"gesamtpreis": 9960.0}]   # Abweichung 40 -- frueher durch 1% (100) gedeckt
    chk = validate.check_line_items_total(fields, items)
    assert chk.ok is False


def test_summe_passt_nur_zu_brutto_bei_nettopreis_rechnung_ist_rot():
    """Ohne Bruttopreis-Kennzeichen darf die Summe NICHT gegen brutto
    durchgehen, auch wenn sie rein zahlenmaessig passt."""
    fields = {"netto": _fr("netto", 100.0), "brutto": _fr("brutto", 119.0)}
    items = [{"gesamtpreis": 119.0}]
    chk = validate.check_line_items_total(fields, items, text="Nettopreise, zzgl. 19 % MwSt")
    assert chk.ok is False


def test_summe_gegen_brutto_mit_kennzeichen_ist_gruen():
    fields = {"netto": _fr("netto", 100.0), "brutto": _fr("brutto", 119.0)}
    items = [{"gesamtpreis": 119.0}]
    chk = validate.check_line_items_total(fields, items, text="Alle Preise inkl. MwSt")
    assert chk.ok is True and "brutto" in chk.detail


def test_fehlende_positionstabelle_summe_ist_kein_fehler():
    assert validate.check_line_items_total({}, None).ok is None


# ---- (c) je Steuersatz: Positionen vs. USt-Aufstellung --------------------

def test_mischsaetze_7_19_passt():
    fields = {"ust_aufstellung": _fr("ust_aufstellung", [
        {"satz": 19, "netto": 300.0, "ust": 57.0},
        {"satz": 7, "netto": 200.0, "ust": 14.0},
    ])}
    items = [{"menge": "2", "einzelpreis": 150.0, "mwst": "19", "betrag": 300.0},
            {"menge": "1", "einzelpreis": 200.0, "mwst": "7", "betrag": 200.0}]
    assert validate.check_tax_rate_lines(fields, items).ok is True
    assert validate.check_line_item_math(items).ok is True


def test_mischsaetze_positionssumme_je_satz_abweicht():
    fields = {"ust_aufstellung": _fr("ust_aufstellung", [
        {"satz": 19, "netto": 300.0, "ust": 57.0},
        {"satz": 7, "netto": 200.0, "ust": 14.0},
    ])}
    items = [{"menge": "2", "einzelpreis": 140.0, "mwst": "19", "betrag": 280.0},
            {"menge": "1", "einzelpreis": 200.0, "mwst": "7", "betrag": 200.0}]
    chk = validate.check_tax_rate_lines(fields, items)
    assert chk.ok is False and "19 %" in chk.detail


def test_ust_betrag_falsch_berechnet_faellt_auf():
    fields = {"ust_aufstellung": _fr("ust_aufstellung", [
        {"satz": 19, "netto": 100.0, "ust": 25.0},   # muesste 19.00 sein
    ])}
    items = [{"betrag": 100.0, "mwst": "19"}]
    chk = validate.check_tax_rate_lines(fields, items)
    assert chk.ok is False and "erwartet 19.00" in chk.detail and "gefunden 25.00" in chk.detail


def test_fehlende_aufstellung_ist_kein_fehler():
    assert validate.check_tax_rate_lines({}, [{"betrag": 1, "mwst": 19}]).ok is None
    assert validate.check_tax_rate_lines({"ust_aufstellung": _fr("ust_aufstellung", [])},
                                         [{"betrag": 1, "mwst": 19}]).ok is None


# ---- run_all: alle drei neuen Pruefungen sind dabei -----------------------

def test_run_all_enthaelt_neue_pruefungen():
    fields = {"netto": _fr("netto", 30.0), "ust_betrag": _fr("ust_betrag", 5.7),
             "brutto": _fr("brutto", 35.7),
             "ust_aufstellung": _fr("ust_aufstellung", [{"satz": 19, "netto": 30.0, "ust": 5.7}])}
    items = [{"menge": 2, "einzelpreis": 15.0, "gesamtpreis": 30.0, "mwst": 19, "betrag": 30.0}]
    names = {c.name for c in validate.run_all(fields, None, lambda *a: False, items)}
    assert {"positionen-summe", "positionen-rechnung", "steuersatz-positionen"} <= names
