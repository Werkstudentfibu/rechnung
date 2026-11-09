"""SPEC Baustein 5 (Aaron 12.10.2026): globales, lieferantenuebergreifendes
Spaltenwoerterbuch -- ein Wort zaehlt erst ab 2 Bestaetigungen und nie gegen
einen Widerspruch (anderes Ziel genauso oft/oefter)."""

from __future__ import annotations

from pathlib import Path

from rechnung import spaltenwoerter as spw
from rechnung.store import Store


def test_ziel_braucht_mindestens_zwei_bestaetigungen():
    assert spw.ziel({"menge": 1}) is None
    assert spw.ziel({"menge": 2}) == "menge"


def test_ziel_bei_widerspruch_unentschieden():
    assert spw.ziel({"menge": 2, "einzelpreis_netto": 2}) is None
    assert spw.ziel({"menge": 3, "einzelpreis_netto": 2}) == "menge"


def test_lernen_zaehlt_hoch_ungueltige_eingabe_bleibt_unveraendert():
    leer: dict = {}
    nach_leer = spw.lernen(leer, "", "menge")
    assert nach_leer is leer
    nach_ignorieren = spw.lernen(leer, "Anz.", "ignorieren")
    assert nach_ignorieren is leer
    v1 = spw.lernen(leer, "Anz.", "menge")
    v2 = spw.lernen(v1, "Anz.", "menge")
    assert v2["anz"] == {"menge": 2}
    # Original unveraendert (neues dict je Aufruf)
    assert leer == {}


def test_hinweis_nutzt_norm():
    buch = {"anz": {"menge": 2}}
    assert spw.hinweis(buch, "Anz.") == "menge"
    assert spw.hinweis(buch, "ANZ") == "menge"
    assert spw.hinweis(buch, "unbekannt") is None
    assert spw.hinweis(None, "Anz.") is None


def test_store_spaltenwort_lernen_persistiert_und_auditiert(tmp_path: Path):
    store = Store(tmp_path / "data")
    assert store.spaltenwoerter() == {}
    store.spaltenwort_lernen("Anz.", "menge", "tester")
    store.spaltenwort_lernen("Anz.", "menge", "tester")
    buch = store.spaltenwoerter()
    assert buch["anz"] == {"menge": 2}
    entries = [a for a in store.audit_log() if a["action"] == "spaltenwort-lernen"]
    assert len(entries) == 2

    # Ungueltige Eingabe schreibt nichts (kein Audit-Eintrag, keine Datei-Aenderung)
    store.spaltenwort_lernen("", "menge", "tester")
    store.spaltenwort_lernen("Anz.", "ignorieren", "tester")
    assert store.spaltenwoerter()["anz"] == {"menge": 2}
    assert len([a for a in store.audit_log() if a["action"] == "spaltenwort-lernen"]) == 2

    # Persistenz: neue Store-Instanz liest dieselbe Datei
    store2 = Store(tmp_path / "data")
    assert store2.spaltenwoerter()["anz"] == {"menge": 2}
