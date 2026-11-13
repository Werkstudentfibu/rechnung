"""Vorab-Lesen fuer 'Menschliche Bearbeitung' (Wunsch Aaron 13.11.2026):
Eintraege, die nicht im Eingang liegen, werden im Hintergrund vorbereitet; die
naechsten Rechnungen im Stapel kommen vor allen anderen dran."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rechnung import human, vorbereitung
from rechnung.vorverarbeitung import Vorverarbeiter
from rechnung.web.server import create_app

from test_jobrouter_feedback import _secured_setup  # noqa: F401

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

GRUND = [{"art": "pflichtfeld", "grund": "Test", "felder": []}]


def test_vorgezogene_dateien_zuerst_und_nur_einmal(tmp_path: Path):
    namen = ["a.pdf", "b.pdf", "c.pdf", "d.pdf"]
    for n in namen:
        (tmp_path / n).write_bytes(n.encode())
    gelesen: list[str] = []
    v = Vorverarbeiter(lambda p: gelesen.append(p.name))
    start = v._start
    v._start = lambda: None                      # erst alles einreihen, dann loslaufen
    v.einreihen([tmp_path / n for n in ("a.pdf", "b.pdf", "c.pdf")])
    v.einreihen([tmp_path / "d.pdf", tmp_path / "b.pdf"], vorne=True)
    v._start = start
    v._start()
    assert v.warte_bis_leer(10)
    assert gelesen == ["d.pdf", "b.pdf", "a.pdf", "c.pdf"]


@pytest.fixture
def an(monkeypatch):
    monkeypatch.setenv("RECHNUNG_VORVERARBEITUNG", "1")
    monkeypatch.setenv("RECHNUNG_LLM_ENABLED", "0")


def _nur_in_menschlich(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    b = make_invoice(tmp_path / "quelle.pdf", rechnungsnr="RE-2026-00055", netto=550.0)
    human.ablegen_datei(store, "nur_hier.pdf", b.read_bytes(), GRUND)
    return store, samples, human.datei_pfad(store, "nur_hier.pdf")


def test_eintrag_nur_in_menschlich_wird_vorbereitet(tmp_path: Path, an):
    store, samples, pfad = _nur_in_menschlich(tmp_path)
    app = create_app(store, samples)
    assert app.state.vorverarbeiter.warte_bis_leer(60)
    assert vorbereitung.ist_bereit(store.root, pfad)


def test_stapel_kann_naechste_vorziehen(tmp_path: Path, an):
    store, samples, pfad = _nur_in_menschlich(tmp_path)
    app = create_app(store, samples)
    assert app.state.vorverarbeiter.warte_bis_leer(60)
    c = TestClient(app)
    r = c.post("/api/vorbereiten", json={"files": ["nur_hier.pdf", "gibt-es-nicht.pdf"]})
    assert r.status_code == 200 and r.json()["eingereiht"] == 1


def test_ohne_vorverarbeitung_nichts_eingereiht(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_VORVERARBEITUNG", "0")
    store, samples, pfad = _nur_in_menschlich(tmp_path)
    c = TestClient(create_app(store, samples))
    assert c.post("/api/vorbereiten", json={"files": ["nur_hier.pdf"]}).json() == {"eingereiht": 0}
    assert c.get("/api/menschlich").status_code == 200


def test_stapel_laedt_naechste_vor():
    html = (Path(__file__).resolve().parent.parent / "rechnung" / "web" / "static" / "index.html").read_text("utf-8")
    assert "function stapelVorladen(file)" in html
    assert "stapelVorladen(next);" in html and "stapelVorladen(r.datei);" in html
