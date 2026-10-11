"""Rahmen ohne erkannte Woerter -> Ausschnitt per Tesseract lesen."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from rechnung import ocr
from rechnung.store import Store
from rechnung.web.server import create_app

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def _client(tmp_path):
    e = tmp_path / "eingang"; e.mkdir()
    make_invoice(e / "a.pdf")
    return TestClient(create_app(Store(tmp_path / "data"), e))


def test_ausschnitt_liest_text(tmp_path: Path, monkeypatch):
    aufrufe = []
    monkeypatch.setattr(ocr, "enabled", lambda: True)
    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "tesseract_cmd", lambda: "tesseract")
    def fake_run(args, timeout, **kw):
        aufrufe.append(args)
        assert Path(args[1]).exists()
        return SimpleNamespace(stdout="1.190,00\n EUR\n", returncode=0)
    monkeypatch.setattr(ocr, "_run", fake_run)
    r = _client(tmp_path).post("/api/ausschnitt-lesen",
                               json={"file": "a.pdf", "page": 0, "x0": 100, "y0": 100, "x1": 200, "y1": 130})
    assert r.status_code == 200 and r.json()["text"] == "1.190,00 EUR"
    assert "--psm" in aufrufe[0]


def test_ohne_texterkennung_klare_meldung(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    r = _client(tmp_path).post("/api/ausschnitt-lesen",
                               json={"file": "a.pdf", "page": 0, "x0": 1, "y0": 1, "x1": 50, "y1": 50})
    assert r.status_code == 409 and "Hand" in r.json()["detail"]
