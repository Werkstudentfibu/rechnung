"""Web-API fuer den Ordner "Keine Rechnung" (Entscheidung Aaron 14.10.2026,
Nachtrag 14.10.2026 18:14): Liste, Vorschau, "Doch eine Rechnung", von Hand
aussortieren, Loeschen (inkl. Mehrfachauswahl + Pfad-Sicherheitsnetz)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import human, keine_rechnung
from rechnung.store import Store
from rechnung.web import server as server_mod

reportlab = pytest.importorskip("reportlab")
fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402

from test_server import _client  # noqa: E402


def _lieferschein_client(tmp_path: Path):
    from rechnung.synth import make_plain_doc

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    make_plain_doc(samples / "Lieferschein.pdf", titel="Lieferschein",
                   zeilen=["Lieferschein Nr. 1"])
    app = server_mod.create_app(store, samples)
    return TestClient(app), store, samples


def test_scan_sortiert_aus_und_api_zeigt_es(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    client, store, samples = _lieferschein_client(tmp_path)

    r = client.get("/api/scan")
    assert r.status_code == 200
    assert not (samples / "Lieferschein.pdf").exists()

    d = client.get("/api/keine-rechnung").json()
    assert len(d["offen"]) == 1
    rec = d["offen"][0]
    assert rec["datei"] == "Lieferschein.pdf"
    assert rec["beschriftung"] == "Lieferschein"

    # Vorschau
    rp = client.get("/api/keine-rechnung/datei", params={"file": "Lieferschein.pdf"})
    assert rp.status_code == 200 and rp.content.startswith(b"%PDF")


def test_vorschau_unbekannte_datei_404(tmp_path: Path):
    client, store, samples = _lieferschein_client(tmp_path)
    r = client.get("/api/keine-rechnung/datei", params={"file": "nichts.pdf"})
    assert r.status_code == 404


def test_doch_eine_rechnung_ueber_api(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    client, store, samples = _lieferschein_client(tmp_path)
    client.get("/api/scan")
    sha = client.get("/api/keine-rechnung").json()["offen"][0]["sha256"]

    r = client.post("/api/keine-rechnung/zurueck", json={"sha256": sha})
    assert r.status_code == 200
    assert (samples / "Lieferschein.pdf").exists()
    assert client.get("/api/keine-rechnung").json()["offen"] == []

    r2 = client.post("/api/keine-rechnung/zurueck", json={"sha256": "x" * 64})
    assert r2.status_code == 404


def test_von_hand_aussortieren_ueber_api(tmp_path: Path):
    client, store, inv = _client(tmp_path)
    r = client.get("/api/invoice", params={"file": "a.pdf"})
    assert r.status_code == 200

    from rechnung.pipeline import Result
    from rechnung.template import FieldResult

    res = Result(file=str(inv), sha256="cd" * 32, status="zurueckgestellt")
    res.hold_reasons = ["kein Lieferant"]
    res.fields = {"rechnungsnummer": FieldResult("rechnungsnummer", "X", "X", 0.9, "text")}
    human.ablegen(store, res, [{"art": human.ART_ZURUECKGESTELLT, "grund": "kein Lieferant",
                                "felder": []}])

    r2 = client.post("/api/keine-rechnung/von-hand", json={"file": "a.pdf"})
    assert r2.status_code == 200
    assert human.offen(store) == []
    assert len(keine_rechnung.offen(store)) == 1

    r3 = client.post("/api/keine-rechnung/von-hand", json={"file": "nichts.pdf"})
    assert r3.status_code == 404


def test_loeschen_ueber_api_inkl_mehrfachauswahl_und_traversal(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    client, store, samples = _lieferschein_client(tmp_path)
    client.get("/api/scan")
    name = client.get("/api/keine-rechnung").json()["offen"][0]["datei"]

    r = client.post("/api/keine-rechnung/loeschen", json={"namen": ["../geheim.txt"]})
    assert r.status_code == 200
    assert r.json()["fehler"] == ["geheim.txt"]

    r2 = client.post("/api/keine-rechnung/loeschen", json={"namen": [name]})
    assert r2.status_code == 200
    assert r2.json()["geloescht"] == [name]
    assert client.get("/api/keine-rechnung").json()["offen"] == []

    r3 = client.post("/api/keine-rechnung/loeschen", json={"namen": []})
    assert r3.status_code == 400
