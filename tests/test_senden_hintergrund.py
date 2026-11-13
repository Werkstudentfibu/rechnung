"""Senden im Hintergrund (Wunsch Aaron 13.11.2026): schnelle Pruefung vorne,
Lernen + Freigeben in einer Warteschlange; uebersteht einen Neustart."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rechnung import human, jobrouter, pipeline, sende_warteschlange
from rechnung.web.server import create_app

from test_jobrouter_feedback import _secured_setup  # noqa: F401

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


@pytest.fixture(autouse=True)
def _ruhig(monkeypatch):
    monkeypatch.setenv("RECHNUNG_VORVERARBEITUNG", "0")


def _senden(c, name="a.pdf", **kw):
    d = c.get("/api/invoice", params={"file": name}).json()
    return c.post("/api/senden", json={"file": name, "template_id": d.get("template_id"),
                                      "hintergrund": True, **kw})


def test_nimmt_an_und_sendet_im_hintergrund(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    app = create_app(store, samples)
    c = TestClient(app)
    r = _senden(c)
    assert r.status_code == 200 and r.json()["angenommen"], r.text
    assert app.state.sender.warte_bis_leer(60)
    st = c.get("/api/senden/status").json()
    assert st["offen"] == 0 and st["fertig"] == 1 and st["fehler"] == []
    assert not (samples / "a.pdf").exists()
    assert list(jobrouter.queue_dir(store.root).glob("*.json"))


def test_pruefung_bleibt_vorne(tmp_path: Path):
    """Fehlende Pflichtfelder: sofort 400, kein Auftrag."""
    from rechnung.store import Store

    samples = tmp_path / "s"; samples.mkdir()
    make_invoice(samples / "x.pdf")
    store = Store(tmp_path / "data")
    app = create_app(store, samples)
    c = TestClient(app)
    r = c.post("/api/senden", json={"file": "x.pdf", "template_id": None, "hintergrund": True})
    assert r.status_code == 400
    assert c.get("/api/senden/status").json() == {"offen": 0, "aktuell": "", "dateien": [],
                                                  "fertig": 0, "fehler": []}


def test_dublette_wird_vorne_gemeldet(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    app = create_app(store, samples)
    c = TestClient(app)
    assert _senden(c).status_code == 200
    assert app.state.sender.warte_bis_leer(60)
    make_invoice(samples / "b.pdf", rechnungsnr="RE-2026-00001")
    r = _senden(c, "b.pdf")
    assert r.status_code == 409 and r.json()["code"] == "dublette"
    assert c.get("/api/senden/status").json()["offen"] == 0


def test_fehler_im_hintergrund_bleibt_sichtbar(tmp_path: Path, monkeypatch):
    store, samples = _secured_setup(tmp_path)
    app = create_app(store, samples)
    c = TestClient(app)
    monkeypatch.setattr(pipeline, "finalize", lambda *a, **k: {"finalized": False, "error": "test"})
    assert _senden(c).json()["angenommen"]
    assert app.state.sender.warte_bis_leer(60)
    fehler = c.get("/api/senden/status").json()["fehler"]
    assert [f["datei"] for f in fehler] == ["a.pdf"] and "Freigabe nicht möglich" in fehler[0]["detail"]
    assert (samples / "a.pdf").exists()                  # nichts verloren
    assert c.post("/api/senden/gesehen", json={"id": fehler[0]["id"]}).json() == {"ok": True}
    assert c.get("/api/senden/status").json()["fehler"] == []


def test_nicht_doppelt_und_in_liste_markiert(tmp_path: Path, monkeypatch):
    store, samples = _secured_setup(tmp_path)
    human.ablegen(store, pipeline.process(samples / "a.pdf", store),
                  [{"art": "pflichtfeld", "grund": "Test", "felder": []}])
    app = create_app(store, samples)
    c = TestClient(app)
    los = threading.Event()
    echt = pipeline.finalize

    def langsam(*a, **k):
        los.wait(30)
        return echt(*a, **k)

    monkeypatch.setattr(pipeline, "finalize", langsam)
    r1 = _senden(c).json()
    r2 = _senden(c).json()
    assert r1["angenommen"] and r2.get("schon_unterwegs")
    eintrag = c.get("/api/menschlich").json()["offen"][0]
    assert eintrag["wird_gesendet"] is True
    los.set()
    assert app.state.sender.warte_bis_leer(60)
    st = c.get("/api/senden/status").json()
    assert st["fertig"] == 1 and st["offen"] == 0
    assert c.get("/api/menschlich").json()["offen"] == []


def test_offener_auftrag_wird_nach_neustart_fortgesetzt(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    tid = store.templates_for("muster")[0].id
    (store.root / sende_warteschlange.DATEI).write_text(json.dumps([
        {"datei": "a.pdf", "template_id": tid, "als_dummy": False, "sha256": "", "status": "laeuft"}]),
        "utf-8")
    app = create_app(store, samples)
    assert app.state.sender.warte_bis_leer(60)
    assert not (samples / "a.pdf").exists()
    assert json.loads((store.root / sende_warteschlange.DATEI).read_text("utf-8")) == []


def test_oberflaeche_sendet_im_hintergrund():
    html = (Path(__file__).resolve().parent.parent / "rechnung" / "web" / "static" / "index.html").read_text("utf-8")
    assert "trotzdem, hintergrund: true" in html
    assert "async function sendeStatusAbfragen()" in html and "function sendeFehlerZeigen()" in html
    assert "if (d.angenommen) {" in html and "SENDE_LAEUFT.has(nm(r.dataset.file))" in html


def test_fehlerhinweis_uebersteht_neustart(tmp_path: Path):
    from rechnung.sende_warteschlange import SendeWarteschlange

    datei = tmp_path / "w.json"

    def kaputt(a):
        raise RuntimeError("Datei nicht gefunden")

    w = SendeWarteschlange(kaputt, datei)
    w.einreihen({"datei": "x.pdf"})
    assert w.warte_bis_leer(10)
    assert [f["datei"] for f in w.stand()["fehler"]] == ["x.pdf"]
    w2 = SendeWarteschlange(kaputt, datei)              # "Neustart"
    assert w2.fortsetzen() == 0                          # nichts erneut ausgefuehrt
    fehler = w2.stand()["fehler"]
    assert [f["datei"] for f in fehler] == ["x.pdf"]
    assert w2.gesehen(fehler[0]["id"])
    assert SendeWarteschlange(kaputt, datei).fortsetzen() == 0
    assert json.loads(datei.read_text("utf-8")) == []


def test_naechste_vergleicht_dateinamen():
    html = (Path(__file__).resolve().parent.parent / "rechnung" / "web" / "static" / "index.html").read_text("utf-8")
    assert "nm(r.dataset.file) !== nm(alt)" in html
