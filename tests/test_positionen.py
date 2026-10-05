"""Selbstlernen 2, Baustein 1: Einheitliche Artikeltabelle.

Egal welche Quelle (Vorlage, KI, E-Rechnung, Tabellen-Korrektur im UI) --
am Ende stehen in jeder Position dieselben Standardnamen in derselben
Reihenfolge: pos, beschreibung, menge, einzelpreis_netto, gesamt_netto,
mwst_satz, waehrung (einheit optional)."""

from __future__ import annotations

import csv

from rechnung.positionen import STANDARD_COLUMNS, canonical_name, normalize_columns, ordered_columns
from rechnung.store import Store


def test_bekannte_aliase_werden_umbenannt():
    items = [{"bezeichnung": "Wartung", "anzahl": 2, "preis": 150.0,
             "summe": 300.0, "ust": 19, "currency": "EUR"}]
    out = normalize_columns(items)
    assert out == [{"beschreibung": "Wartung", "menge": 2, "einzelpreis_netto": 150.0,
                    "gesamt_netto": 300.0, "mwst_satz": 19, "waehrung": "EUR"}]


def test_unbekannte_und_interne_spalten_bleiben_unveraendert():
    items = [{"beschreibung": "Artikel", "rabatt_prozent": 10, "_quelle": "llm",
             "kategorie": "Buero"}]
    out = normalize_columns(items)
    assert out == items


def test_verschiedene_quellen_ergeben_identische_spaltennamen():
    """E-Rechnung (bezeichnung/einzelpreis/netto), KI (beschreibung/
    einzelpreis/netto) und eine Vorlage mit Freitext-Spalten ("betrag")
    landen nach der Normalisierung auf denselben Schluesseln."""
    einvoice_item = {"pos": "1", "bezeichnung": "Ersatzteil", "menge": 2.0,
                     "einheit": "ST", "einzelpreis": 10.0, "netto": 20.0, "ust_satz": 19.0}
    llm_item = {"beschreibung": "Ersatzteil", "menge": 2.0, "einzelpreis": 10.0,
               "netto": 20.0, "ust_satz": 19.0}
    template_item = {"beschreibung": "Ersatzteil", "menge": "2", "einzelpreis": "10,00",
                     "betrag": "20,00", "mwst": "19"}

    a, b, c = (normalize_columns([x])[0] for x in (einvoice_item, llm_item, template_item))
    gemeinsam = {"beschreibung", "menge", "einzelpreis_netto", "gesamt_netto", "mwst_satz"}
    assert set(a) & gemeinsam == gemeinsam
    assert set(b) == gemeinsam
    # Werte selbst bleiben unveraendert (nur umbenannt, nicht typisiert).
    assert a["einzelpreis_netto"] == 10.0 and b["einzelpreis_netto"] == 10.0
    assert c["einzelpreis_netto"] == "10,00"


def test_kollision_erste_quelle_gewinnt():
    # "einzelpreis" und "preis" sind beide Aliase von einzelpreis_netto --
    # kommt (theoretisch) beides in einer Zeile vor, gewinnt die erste.
    items = [{"einzelpreis": 10.0, "preis": 99.0}]
    out = normalize_columns(items)
    assert out == [{"einzelpreis_netto": 10.0}]


def test_canonical_name_ist_case_und_trennzeichen_tolerant():
    for variant in ("Einzelpreis", "EINZELPREIS", "EP", "unit_price", "Einzelpreis netto"):
        assert canonical_name(variant) == "einzelpreis_netto"
    assert canonical_name("kostenstelle") == "kostenstelle"  # kein Alias -> unveraendert


# ---- Spaltenreihenfolge (CSV/UI) -- stabil statt zufaellig (set()) --------

def test_ordered_columns_standardspalten_zuerst_dann_alphabetisch():
    items = [{"waehrung": "EUR", "beschreibung": "B", "rabatt_prozent": 1},
            {"mwst_satz": 19, "menge": 2, "kategorie": "X"}]
    assert ordered_columns(items) == [
        "beschreibung", "menge", "mwst_satz", "waehrung", "kategorie", "rabatt_prozent",
    ]


def test_csv_spaltenreihenfolge_stabil_ueber_mehrere_laeufe(tmp_path):
    """store.write_output() durfte vorher eine zufaellige Spaltenreihenfolge
    erzeugen (``set()`` ueber die Positions-Schluessel) -- jetzt ist sie
    stabil, unabhaengig von der Einfuege-Reihenfolge der Dict-Schluessel."""
    store = Store(tmp_path / "data")
    items_a = [{"waehrung": "EUR", "beschreibung": "A", "menge": 1, "gesamt_netto": 10.0}]
    items_b = [{"gesamt_netto": 10.0, "menge": 1, "beschreibung": "A", "waehrung": "EUR"}]

    def header(items):
        store.write_output("re", {"_positionen": items})
        with (tmp_path / "data" / "out" / "re.positionen.csv").open(
                encoding="utf-8-sig", newline="") as fh:
            return next(csv.reader(fh, delimiter=";"))

    h1 = header(items_a)
    h2 = header(items_b)
    assert h1 == h2 == ["beschreibung", "menge", "gesamt_netto", "waehrung", "herkunft"]


def test_standard_columns_enthaelt_feste_spalten():
    assert STANDARD_COLUMNS == (
        "pos", "beschreibung", "menge", "einzelpreis_netto", "gesamt_netto",
        "mwst_satz", "waehrung", "einheit",
    )
