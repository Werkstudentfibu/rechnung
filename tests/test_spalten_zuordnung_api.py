"""SPEC Baustein 5 (Aaron 12.10.2026): die per Rahmen-Markierung (Baustein 2)
vom Menschen im Dropdown bestaetigte Spalten-Zuordnung wandert beim Klick
auf "Positionen uebernehmen" (/api/tabelle) ins globale Spaltenwoerterbuch."""

from __future__ import annotations

import pytest

from rechnung import synth, tabelle
from rechnung.pdf import load


def test_spalten_zuordnung_wird_ins_woerterbuch_uebernommen(tmp_path):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.store import Store
    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    client = TestClient(server_mod.create_app(store, samples))

    p = synth.make_invoice(samples / "a.pdf", netto=1000.0, satz=19)
    doc = load(p)
    words = doc.pages[0].words
    header = next(w for w in words if w.text.strip(":") == "Menge")
    top = header.top - 4.0
    bottom = max(w.bottom for w in words if header.top <= w.top <= header.top + 120)
    box = {"page": 0, "x0": 0.0, "top": top, "x1": doc.pages[0].width, "bottom": bottom + 4.0}

    assert store.spaltenwoerter() == {}
    for _ in range(2):
        r = client.post("/api/tabelle", json={
            "file": "a.pdf", "art": "positionen",
            "rows": [{"beschreibung": "x", "menge": 1, "einzelpreis_netto": 1,
                     "gesamt_netto": 1, "mwst_satz": 19, "waehrung": "EUR"}],
            "spalten_zuordnung": [
                {"header_text": "Beschreibung", "ziel": "beschreibung"},
                {"header_text": "Menge", "ziel": "menge"},
                {"header_text": "Einzelpreis", "ziel": "einzelpreis_netto"},
                {"header_text": "Gesamtpreis", "ziel": "gesamt_netto"},
                {"header_text": "MwSt", "ziel": "mwst_satz"},
                {"header_text": "Waehrung", "ziel": "waehrung"},
            ],
        }).json()
        assert r["ok"]

    buch = store.spaltenwoerter()
    assert buch["beschreibung"] == {"beschreibung": 2}
    assert buch["gesamtpreis"] == {"gesamt_netto": 2}


def test_ignorieren_und_leere_eintraege_werden_nicht_gelernt(tmp_path):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.store import Store
    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    client = TestClient(server_mod.create_app(store, samples))
    p = synth.make_invoice(samples / "a.pdf", netto=1000.0, satz=19)

    r = client.post("/api/tabelle", json={
        "file": "a.pdf", "art": "positionen",
        "rows": [{"beschreibung": "x"}],
        "spalten_zuordnung": [
            {"header_text": "Waehrung", "ziel": "ignorieren"},
            {"header_text": "", "ziel": "menge"},
        ],
    }).json()
    assert r["ok"]
    assert store.spaltenwoerter() == {}


def test_ust_art_lernt_nichts_ins_spaltenwoerterbuch(tmp_path):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.store import Store
    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    client = TestClient(server_mod.create_app(store, samples))
    synth.make_invoice(samples / "a.pdf", netto=1000.0, satz=19)

    r = client.post("/api/tabelle", json={
        "file": "a.pdf", "art": "ust",
        "rows": [{"satz": 19, "netto": 1000.0, "ust": 190.0}],
        "spalten_zuordnung": [{"header_text": "Satz", "ziel": "mwst_satz"}],
    }).json()
    assert r["ok"]
    assert store.spaltenwoerter() == {}
