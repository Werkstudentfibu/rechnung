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


def test_senden_ohne_lieferant_wird_abgelehnt(tmp_path: Path):
    from rechnung.store import Store
    samples = tmp_path / "eingang"; samples.mkdir()
    make_invoice(samples / "Rechnung #1.pdf")
    store = Store(tmp_path / "data")
    c = TestClient(create_app(store, samples))
    r = c.post("/api/senden", json={"file": "Rechnung #1.pdf", "template_id": None})
    assert r.status_code == 400 and "Lieferant" in r.json()["detail"]
    assert (samples / "Rechnung #1.pdf").exists()
