"""Befund Aaron 13.11.2026: gesendete Rechnungen blieben in 'Menschliche
Bearbeitung' stehen -- mit genau gleichem Dateinamen. Ursache: im Eingang lag
eine Datei gleichen Namens mit ANDEREM Inhalt; Oeffnen/Senden nahm die
Eingangsdatei, der Eintrag blieb unberuehrt."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rechnung import human
from rechnung.pipeline import process
from rechnung.web.server import create_app

from test_jobrouter_feedback import _secured_setup  # noqa: F401

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

GRUND = [{"art": "pflichtfeld", "grund": "Test", "felder": []}]


def _kollision(tmp_path: Path):
    """Eingang: a.pdf = Rechnung A. 'Menschliche Bearbeitung': Rechnung B,
    ebenfalls unter dem Namen a.pdf (z.B. aus dem Postfach)."""
    store, samples = _secured_setup(tmp_path)
    b = make_invoice(tmp_path / "b_quelle.pdf", rechnungsnr="RE-2026-00077", netto=777.0)
    rec = human.ablegen_datei(store, "a.pdf", b.read_bytes(), GRUND,
                              beilagen={".eml": b"From: x@example.org\n\nTest"})
    return store, samples, rec["sha256"]


def test_gleicher_name_anderer_inhalt_wird_eindeutig(tmp_path: Path):
    store, samples, sha_b = _kollision(tmp_path)
    c = TestClient(create_app(store, samples))
    offen = c.get("/api/menschlich").json()["offen"]
    assert [r["datei"] for r in offen] == [f"{sha_b[:8]}_a.pdf"]
    ordner = human.human_dir(store.root)
    assert (ordner / f"{sha_b[:8]}_a.pdf").exists() and not (ordner / "a.pdf").exists()
    assert (ordner / f"{sha_b[:8]}_a.pdf.eml").exists()      # Beilage mit umbenannt
    assert offen[0]["umbenannt_von"] == "a.pdf"
    # zweites Laden aendert nichts mehr
    assert [r["datei"] for r in c.get("/api/menschlich").json()["offen"]] == [f"{sha_b[:8]}_a.pdf"]


def test_eintrag_oeffnen_und_senden_trifft_den_eintrag(tmp_path: Path):
    store, samples, sha_b = _kollision(tmp_path)
    c = TestClient(create_app(store, samples))
    name = c.get("/api/menschlich").json()["offen"][0]["datei"]
    d = c.get("/api/invoice", params={"file": name}).json()
    r = c.post("/api/senden", json={"file": name, "template_id": d.get("template_id")})
    assert r.status_code == 200, r.text
    assert c.get("/api/menschlich").json()["offen"] == []
    assert store.is_confirmed(sha_b)
    assert (samples / "a.pdf").exists()                      # Rechnung A unberuehrt im Eingang


def test_gleicher_inhalt_bleibt_wie_er_ist(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    res = process(samples / "a.pdf", store)
    human.ablegen(store, res, GRUND)
    c = TestClient(create_app(store, samples))
    assert [r["datei"] for r in c.get("/api/menschlich").json()["offen"]] == ["a.pdf"]


def test_ohne_eingangsdatei_keine_aenderung(tmp_path: Path):
    store, samples, sha_b = _kollision(tmp_path)
    (samples / "a.pdf").unlink()
    assert human.namen_entwirren(store, samples) == 0
    assert [r["datei"] for r in human.offen(store)] == ["a.pdf"]


def test_dublette_kann_im_stapel_abgehakt_werden():
    html = (Path(__file__).resolve().parent.parent / "rechnung" / "web" / "static" / "index.html").read_text("utf-8")
    assert "Dann als erledigt abhaken?" in html
    assert "notiz: 'Dublette – Rechnung war schon gesendet'" in html
