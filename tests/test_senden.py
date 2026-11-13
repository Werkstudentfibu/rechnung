"""Ein Klick "Senden": pruefen, lernen, fuer JobRouter freigeben, aus dem
Eingang nehmen (Aaron 11.10.2026)."""

from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from rechnung import jobrouter
from rechnung.web.server import create_app

from test_jobrouter_feedback import _secured_setup  # noqa: F401

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def test_senden_reiht_ein_und_nimmt_aus_eingang(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    c = TestClient(create_app(store, samples))
    d = c.get("/api/invoice?file=a.pdf").json()
    r = c.post("/api/senden", json={"file": "a.pdf", "template_id": d["template_id"]})
    assert r.status_code == 200, r.text
    assert r.json()["gesendet"] and r.json()["queued"]
    assert not (samples / "a.pdf").exists()
    assert len(list((store.root / "erledigt").iterdir())) == 1
    assert list(jobrouter.queue_dir(store.root).glob("*.json"))
    assert store.is_confirmed(d["sha256"]) if "sha256" in d else True


def test_senden_ohne_lieferant_blockiert_nicht_mehr_wegen_lieferant(tmp_path: Path):
    """Seit 17.10.2026 ersetzt der Dummy-Lieferant den fehlenden Lieferanten;
    blockiert wird nur noch wegen fehlender Pflichtfelder (test_dummy_lieferant.py)."""
    from rechnung.store import Store
    samples = tmp_path / "eingang"; samples.mkdir()
    make_invoice(samples / "Rechnung #1.pdf")
    store = Store(tmp_path / "data")
    c = TestClient(create_app(store, samples))
    r = c.post("/api/senden", json={"file": "Rechnung #1.pdf", "template_id": None})
    assert r.status_code == 400 and "Lieferant" not in r.json()["detail"]
    assert (samples / "Rechnung #1.pdf").exists()


def test_dublette_braucht_bestaetigung(tmp_path: Path):
    import shutil
    store, samples = _secured_setup(tmp_path)
    c = TestClient(create_app(store, samples))
    d = c.get("/api/invoice?file=a.pdf").json()
    shutil.copy(samples / "a.pdf", tmp_path / "kopie.pdf")
    assert c.post("/api/senden", json={"file": "a.pdf", "template_id": d["template_id"]}).status_code == 200
    # dieselbe Rechnung kommt noch einmal (andere Datei, gleicher Inhalt/gleiche Nummer)
    make_invoice(samples / "b.pdf", rechnungsnr="RE-2026-00001")
    d2 = c.get("/api/invoice?file=b.pdf").json()
    r = c.post("/api/senden", json={"file": "b.pdf", "template_id": d2["template_id"]})
    assert r.status_code == 409 and r.json()["code"] == "dublette"
    assert (samples / "b.pdf").exists()
    r = c.post("/api/senden", json={"file": "b.pdf", "template_id": d2["template_id"], "trotzdem": True})
    assert r.status_code == 200


def _nur_in_menschlich(tmp_path: Path):
    """Rechnung liegt nur im Ordner 'Menschliche Bearbeitung', nicht im Eingang."""
    from rechnung import human

    store, samples = _secured_setup(tmp_path)
    human.ablegen_datei(store, "a.pdf", (samples / "a.pdf").read_bytes(),
                        [{"art": "pflichtfeld", "grund": "Test", "felder": []}])
    (samples / "a.pdf").unlink()
    return store, samples


def test_senden_aus_menschlicher_bearbeitung(tmp_path: Path):
    """Befund Aaron 13.11.2026: Bestaetigen loeschte die Arbeitskopie, die
    Freigabe danach scheiterte (FileNotFoundError, dann 'Datei nicht
    gefunden'). Jetzt: senden klappt, danach ist die Arbeitskopie weg."""
    from rechnung import human

    store, samples = _nur_in_menschlich(tmp_path)
    c = TestClient(create_app(store, samples))
    d = c.get("/api/invoice?file=a.pdf").json()
    r = c.post("/api/senden", json={"file": "a.pdf", "template_id": d["template_id"]})
    assert r.status_code == 200, r.text
    assert r.json()["gesendet"] and r.json()["queued"]
    assert list(jobrouter.queue_dir(store.root).glob("*.json"))
    assert human.datei_pfad(store, "a.pdf") is None
    assert [x for x in human.offen(store) if x["datei"] == "a.pdf"] == []


def test_freigabe_scheitert_arbeitskopie_bleibt(tmp_path: Path, monkeypatch):
    """Scheitert die Freigabe, bleibt die Rechnung in 'Menschliche
    Bearbeitung' -- ein zweiter Versuch findet sie wieder."""
    from rechnung import human
    from rechnung import pipeline

    store, samples = _nur_in_menschlich(tmp_path)
    c = TestClient(create_app(store, samples))
    d = c.get("/api/invoice?file=a.pdf").json()
    monkeypatch.setattr(pipeline, "finalize", lambda *a, **k: {"finalized": False, "error": "test"})
    r = c.post("/api/senden", json={"file": "a.pdf", "template_id": d["template_id"]})
    assert r.status_code == 409 and "Freigabe nicht möglich" in r.json()["detail"]
    assert human.datei_pfad(store, "a.pdf") is not None
    monkeypatch.undo()
    r = c.post("/api/senden", json={"file": "a.pdf", "template_id": d["template_id"]})
    assert r.status_code == 200, r.text


def test_bestaetigen_allein_raeumt_weiter_auf(tmp_path: Path):
    """Der Knopf 'Bestaetigen' (ohne Senden) entfernt die Arbeitskopie wie bisher."""
    from rechnung import human

    store, samples = _nur_in_menschlich(tmp_path)
    c = TestClient(create_app(store, samples))
    d = c.get("/api/invoice?file=a.pdf").json()
    r = c.post("/api/confirm", json={"file": "a.pdf", "template_id": d["template_id"]})
    assert r.status_code == 200, r.text
    assert human.datei_pfad(store, "a.pdf") is None
