"""Je Positionszeile bestaetigen oder aendern (Aaron 05.11.2026): ✓ "Zeile
stimmt" zaehlt als Treffer, eine geaenderte/eingefuegte Zeile als Korrektur --
wie bei den Feldern. Der Status haengt am Zeileninhalt (Fingerabdruck), nicht
an der Zeilennummer."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rechnung import eventlog, positionen, testsatz
from rechnung.store import Store
from rechnung.web import server as server_mod

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def _client(tmp_path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "eingang"
    samples.mkdir()
    make_invoice(samples / "a.pdf", rechnungsnr="RE-9", netto=1000.0)
    return TestClient(server_mod.create_app(store, samples)), store


def _zeilen(client):
    d = client.get("/api/invoice", params={"file": "a.pdf"}).json()
    return d["line_items"], d["zeilen_status"]


def test_fingerabdruck_unabhaengig_von_pos_und_zahlenformat():
    a = {"pos": 1, "beschreibung": "Ersatzteile  X", "menge": 1, "einzelpreis_netto": 550.0,
         "gesamt_netto": 550.0}
    b = {"pos": 7, "beschreibung": "ersatzteile x", "menge": "1", "einzelpreis": "550,00",
         "gesamt_netto": "550,00"}
    assert positionen.zeilen_fingerabdruck(a) == positionen.zeilen_fingerabdruck(b)
    c = dict(a, gesamt_netto=551.0)
    assert positionen.zeilen_fingerabdruck(a) != positionen.zeilen_fingerabdruck(c)


def test_zeile_bestaetigen_ist_treffer_keine_korrektur(tmp_path):
    client, store = _client(tmp_path)
    items, status = _zeilen(client)
    assert len(items) == 3 and status == ["offen"] * 3

    r = client.post("/api/zeilen-bestaetigen", json={"file": "a.pdf", "rows": [items[1]]})
    assert r.status_code == 200

    items2, status2 = _zeilen(client)
    assert status2 == ["offen", "bestaetigt", "offen"]
    sha = next(iter(p.stem for p in (store.root / "corrections").glob("*.json"))) \
        if (store.root / "corrections").exists() else None
    assert sha
    assert store.corrections(sha) == {}                              # keine Korrektur
    typen = [e["type"] for e in store.events()]
    assert typen.count(eventlog.TYPE_ZEILE_BESTAETIGT) == 1
    assert eventlog.TYPE_CORRECTION not in typen
    stat = eventlog.zeilenstatistik(store)["gesamt"]
    assert stat["treffer"] == 1 and stat["korrigiert"] == 0 and stat["quote"] == 1.0

    # nochmal bestaetigen zaehlt nicht doppelt
    client.post("/api/zeilen-bestaetigen", json={"file": "a.pdf", "rows": [items[1]]})
    assert [e["type"] for e in store.events()].count(eventlog.TYPE_ZEILE_BESTAETIGT) == 1


def test_alle_zeilen_bestaetigen_macht_rechnung_nicht_fertig(tmp_path):
    client, store = _client(tmp_path)
    items, _ = _zeilen(client)
    client.post("/api/zeilen-bestaetigen", json={"file": "a.pdf", "rows": items})
    _items, status = _zeilen(client)
    assert status == ["bestaetigt"] * 3
    assert testsatz.bestaetigte(store) == []          # keine Rechnung bestaetigt
    assert eventlog.zeilenstatistik(store)["gesamt"]["treffer"] == 3


def test_geaenderte_zeile_ist_korrektur_und_status_folgt_dem_inhalt(tmp_path):
    client, store = _client(tmp_path)
    items, _ = _zeilen(client)
    client.post("/api/zeilen-bestaetigen", json={"file": "a.pdf", "rows": [items[0]]})

    # Mensch aendert Zeile 2, fuegt VOR Zeile 1 eine neue Zeile ein
    neu = {"beschreibung": "Neu eingefuegt", "menge": 1, "einzelpreis_netto": 1.0, "gesamt_netto": 1.0}
    geaendert = dict(items[1], gesamt_netto=551.0, einzelpreis_netto=551.0)
    rows = [neu, items[0], geaendert, items[2]]
    r = client.post("/api/tabelle", json={
        "file": "a.pdf", "art": "positionen", "rows": rows,
        "zeilen_status": ["korrigiert", "bestaetigt", "korrigiert", "offen"]})
    assert r.status_code == 200

    items2, status2 = _zeilen(client)
    # Reihenfolge der gespeicherten Korrektur; Status folgt dem Inhalt
    assert status2 == ["korrigiert", "bestaetigt", "korrigiert", "offen"]
    stat = eventlog.zeilenstatistik(store)["gesamt"]
    assert stat["treffer"] == 1 and stat["korrigiert"] == 2
    assert stat["quote"] == round(1 / 3, 2)

    # Zeile nachtraeglich veraendert -> nicht mehr bestaetigt
    spaeter = [dict(r_, beschreibung=r_.get("beschreibung") + " X") if i == 1 else r_
               for i, r_ in enumerate(items2)]
    st = positionen.zeilen_status(spaeter, store.zeilen(next(
        p.stem for p in (store.root / "corrections").glob("*.json"))))
    assert st[1] == "offen"


def test_qualitaet_zeigt_zeilenstatistik(tmp_path):
    client, _store = _client(tmp_path)
    items, _ = _zeilen(client)
    client.post("/api/zeilen-bestaetigen", json={"file": "a.pdf", "rows": items})
    q = client.get("/api/qualitaet").json()
    assert q["zeilenstatistik"]["gesamt"]["treffer"] == 3
