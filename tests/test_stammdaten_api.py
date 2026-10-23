"""Web-API fuer den Excel-Stammdaten-Import und die Postfach-Verwaltung
(Entscheidung Aaron 15.10.2026) -- Reiter "Stammdaten" im Training-UI."""

from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from rechnung.store import Store
from rechnung.web.server import create_app

from _xlsx_helpers import write_workbook


def _client(tmp_path: Path) -> TestClient:
    store = Store(tmp_path / "data")
    samples = tmp_path / "samples"
    samples.mkdir()
    return TestClient(create_app(store, samples))


def test_parse_liefert_mapping_und_art_vorschlag(tmp_path: Path):
    c = _client(tmp_path)
    data = write_workbook({"Kreditoren Deutschland": [
        ["Name", "UID", "IBAN"],
        ["Muster GmbH", "DE123456789", "DE89370400440532013000"],
    ]})
    r = c.post("/api/stammdaten/parse", json={"inhalt": base64.b64encode(data).decode()})
    assert r.status_code == 200
    d = r.json()
    sheet = d["sheets"]["Kreditoren Deutschland"]
    assert sheet["mapping"]["UID"] == "ustid"
    assert sheet["art"] == "kreditor_de"
    assert sheet["rows"] == [["Muster GmbH", "DE123456789", "DE89370400440532013000"]]


def test_parse_leere_datei_ist_400(tmp_path: Path):
    c = _client(tmp_path)
    r = c.post("/api/stammdaten/parse", json={"inhalt": ""})
    assert r.status_code == 400


def test_vorschau_zeigt_fragen(tmp_path: Path):
    c = _client(tmp_path)
    sheets = {"Blatt1": {
        "headers": ["Name", "IBAN"],
        "rows": [["Muster GmbH", "DE00000000000000000000"]],
        "mapping": None, "art": "kreditor_de",
    }}
    r = c.post("/api/stammdaten/vorschau", json={"sheets": sheets})
    assert r.status_code == 200
    z = r.json()["blaetter"]["Blatt1"]["zeilen"][0]
    assert z["status"] == "gelb"
    assert z["fragen"][0]["feld"] == "iban"


def _uebernehmen_warten(c: TestClient) -> dict:
    """Laeuft im Hintergrund (Fortschrittsanzeige, Aaron 23.10.2026) --
    im Test auf das Ende warten statt eine synchrone Antwort zu erwarten."""
    import time

    for _ in range(300):
        st = c.get("/api/stammdaten/uebernehmen/fortschritt").json()
        if not st["laeuft"]:
            return st
        time.sleep(0.01)
    raise AssertionError("Uebernehmen lief nicht zu Ende")


def test_uebernehmen_legt_lieferant_an_und_lernt_zuordnung(tmp_path: Path):
    c = _client(tmp_path)
    headers = ["Name", "UID"]
    sheets = {"Blatt1": {
        "headers": headers, "rows": [["Muster GmbH", "DE123456789"]],
        "mapping": None, "art": "kreditor_de",
    }}
    r = c.post("/api/stammdaten/uebernehmen", json={"sheets": sheets})
    assert r.status_code == 200
    assert r.json()["gestartet"] is True
    st = _uebernehmen_warten(c)
    assert not st["fehler"], st["fehler"]
    d = st["ergebnis"]
    assert d["kreditoren"] == 1

    r2 = c.get("/api/suppliers")
    assert r2.json()[0]["name"] == "Muster GmbH"

    # Zuordnung wurde gelernt -- ein zweiter Parse-Aufruf mit denselben
    # Ueberschriften (andere Reihenfolge) schlaegt sie direkt vor.
    data = write_workbook({"Blatt2": [list(reversed(headers)), ["DE987654321", "Andere GmbH"]]})
    r3 = c.post("/api/stammdaten/parse", json={"inhalt": base64.b64encode(data).decode()})
    assert r3.json()["sheets"]["Blatt2"]["gelernt"] is True


def test_uebernehmen_fortschritt_vor_lauf_und_am_ende(tmp_path: Path):
    c = _client(tmp_path)
    anfang = c.get("/api/stammdaten/uebernehmen/fortschritt").json()
    assert anfang["laeuft"] is False

    headers = ["Name"]
    rows = [["Firma Eins"], ["Firma Zwei"], ["Firma Drei"]]
    sheets = {"Blatt1": {"headers": headers, "rows": rows, "mapping": None, "art": "kreditor_de"}}
    r = c.post("/api/stammdaten/uebernehmen", json={"sheets": sheets})
    assert r.json()["gestartet"] is True

    st = _uebernehmen_warten(c)
    assert st["laeuft"] is False
    assert st["fertig"] == st["gesamt"] == 3
    assert st["ergebnis"]["kreditoren"] == 3


def test_postfaecher_crud_und_passwort_nie_in_antwort(tmp_path: Path):
    c = _client(tmp_path)
    r = c.post("/api/postfaecher", json={
        "id": "nord", "mandant_name": "Niederlassung Nord", "user": "nord@example.de",
        "passwort": "ganzGeheim!",
    })
    assert r.status_code == 200

    r2 = c.get("/api/postfaecher")
    pf = r2.json()[0]
    assert pf["user"] == "nord@example.de"
    assert pf["passwort_gesetzt"] is True
    assert "passwort" not in pf and "ganzGeheim!" not in r2.text

    r3 = c.delete("/api/postfaecher/nord")
    assert r3.status_code == 200
    assert c.get("/api/postfaecher").json() == []


def test_postfaecher_testen_meldet_fehler_ohne_abzusturzen(tmp_path: Path):
    c = _client(tmp_path)
    c.post("/api/postfaecher", json={"id": "a", "user": "a@example.de",
                                     "host": "imap.invalid", "passwort": "x"})
    r = c.post("/api/postfaecher/testen")
    assert r.status_code == 200
    out = r.json()["ergebnisse"]
    assert len(out) == 1 and out[0]["ok"] is False
