"""Baustein 4 (Aaron 12.10.2026): Jede Zeile der Positionstabelle pruefen --
zusaetzlich zur Rechen-Pruefung (menge * einzelpreis = gesamt) auch
Beschreibung (Pflicht, kein Zahlen-/Mengenrest), Waehrung (= Rechnungs-
waehrung) und Steuersatz (muss in der Steueraufschluesselung vorkommen).

Diese Pruefungen sind reine Anzeige (``rechen_pruefungen``) -- sie duerfen
das bestehende Rueckstell-Verhalten von ``check_line_item_math``/``run_all``
nicht verschaerfen (SPEC: "Rote Zeilen sind Rueckstellgrund nur, wenn schon
heute positionen-* rot zurueckstellt")."""

from __future__ import annotations

from rechnung import validate
from rechnung.template import FieldResult


def _fr(name, value):
    return FieldResult(name, value, str(value), 0.9, "template-anchor")


def test_vollstaendige_richtige_zeile_ist_gruen():
    it = {"beschreibung": "Wartung Server", "menge": 2, "einzelpreis_netto": 150.0,
          "gesamt_netto": 300.0, "mwst_satz": 19, "waehrung": "EUR"}
    ok, grund = validate.line_item_row_full(it, 1, "EUR", {19.0, 7.0})
    assert ok is True, grund


def test_leere_beschreibung_ist_rot():
    it = {"beschreibung": "", "menge": 2, "einzelpreis_netto": 150.0, "gesamt_netto": 300.0}
    ok, grund = validate.line_item_row_full(it, 1, "EUR", None)
    assert ok is False
    assert "Beschreibung fehlt" in grund


def test_beschreibung_mit_zahlenrest_ist_rot():
    it = {"beschreibung": "Wartung Server 2 320,00", "menge": 2, "einzelpreis_netto": 150.0,
          "gesamt_netto": 300.0}
    ok, grund = validate.line_item_row_full(it, 1, "EUR", None)
    assert ok is False
    assert "Zahlen-/Mengenrest" in grund


def test_artikelnummer_am_ende_ist_kein_zahlenrest():
    # Ganze Zahl ohne Komma-Dezimalstellen -- typische Artikelnummer, kein Betrag.
    it = {"beschreibung": "Schraube M8 Art. 12345", "menge": 2, "einzelpreis_netto": 1.50,
          "gesamt_netto": 3.00}
    ok, grund = validate.line_item_row_full(it, 1, "EUR", None)
    assert ok is True, grund


def test_abweichende_waehrung_ist_rot():
    it = {"beschreibung": "Artikel", "menge": 1, "einzelpreis_netto": 10.0,
          "gesamt_netto": 10.0, "waehrung": "CHF"}
    ok, grund = validate.line_item_row_full(it, 1, "EUR", None)
    assert ok is False
    assert "Währung" in grund


def test_gleiche_waehrung_verschieden_geschrieben_ist_gruen():
    it = {"beschreibung": "Artikel", "menge": 1, "einzelpreis_netto": 10.0,
          "gesamt_netto": 10.0, "waehrung": "Euro"}
    ok, grund = validate.line_item_row_full(it, 1, "EUR", None)
    assert ok is True, grund


def test_steuersatz_ausserhalb_der_aufstellung_ist_rot():
    it = {"beschreibung": "Artikel", "menge": 1, "einzelpreis_netto": 10.0,
          "gesamt_netto": 10.0, "mwst_satz": 16}
    ok, grund = validate.line_item_row_full(it, 1, "EUR", {19.0, 7.0})
    assert ok is False
    assert "Steuersatz" in grund


def test_rechenfehler_geht_vor_sonstigen_pruefungen():
    it = {"beschreibung": "", "menge": 2, "einzelpreis_netto": 15.0, "gesamt_netto": 999.0}
    ok, grund = validate.line_item_row_full(it, 1, "EUR", None)
    assert ok is False
    assert "erwartet" in grund   # Rechenfehler-Grund, nicht "Beschreibung fehlt"


def test_pauschale_ohne_menge_wird_trotzdem_auf_beschreibung_geprueft():
    it = {"beschreibung": "", "gesamt_netto": 50.0}
    ok, grund = validate.line_item_row_full(it, 1, "EUR", None)
    assert ok is False
    assert "Beschreibung fehlt" in grund


def test_zusatzpruefungen_verschaerfen_nicht_check_line_item_math():
    # Dieselbe Zeile, die bei line_item_row_full wegen Beschreibung rot
    # waere, bleibt bei der fuer Rueckstellung genutzten Pruefung unberuehrt.
    items = [{"beschreibung": "", "menge": 2, "einzelpreis_netto": 150.0, "gesamt_netto": 300.0}]
    chk = validate.check_line_item_math(items)
    assert chk.ok is True


def test_rechen_pruefungen_nutzt_die_erweiterte_zeilenpruefung():
    fields = {"waehrung": _fr("waehrung", "EUR"),
             "ust_aufstellung": _fr("ust_aufstellung", [{"satz": 19.0, "netto": 300.0, "ust": 57.0}])}
    items = [{"beschreibung": "Artikel", "menge": 1, "einzelpreis_netto": 10.0,
             "gesamt_netto": 10.0, "waehrung": "CHF"}]
    out = validate.rechen_pruefungen(fields, items)
    assert out["positionen"][0]["ok"] is False
    assert "Währung" in out["positionen"][0]["detail"]
