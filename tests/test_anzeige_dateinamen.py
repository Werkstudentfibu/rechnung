"""Seitenbild auch bei echten Dateinamen und Scans ohne Texterkennung
(Befund Aaron 09.10.2026: per Mail abgeholte Rechnungen ohne PDF-Anzeige)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from rechnung.store import Store
from rechnung.web.server import create_app

pdfium = pytest.importorskip("pypdfium2")
reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

NAMEN = ["Rechnung #4711.pdf", "R&S Rechnung+1.pdf", "Rechnung Müller 01.pdf",
         "100%_Beleg; Kopie.pdf"]


@pytest.mark.parametrize("name", NAMEN)
def test_page_image_with_special_characters(tmp_path: Path, name: str):
    eingang = tmp_path / "eingang"; eingang.mkdir()
    make_invoice(eingang / name)
    c = TestClient(create_app(Store(tmp_path / "data"), eingang))
    d = c.get("/api/invoice?file=" + quote(name)).json()
    img = d["pages"][0]["image"]
    r = c.get(img)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_scan_without_ocr_is_still_shown(tmp_path: Path, monkeypatch):
    from rechnung import ocr
    monkeypatch.setattr(ocr, "available", lambda: False)
    eingang = tmp_path / "eingang"; eingang.mkdir()
    src = make_invoice(tmp_path / "text.pdf")
    doc = pdfium.PdfDocument(str(src))
    img = doc[0].render(scale=1).to_pil().convert("RGB")
    img.save(eingang / "Scan 1.pdf", "PDF")

    c = TestClient(create_app(Store(tmp_path / "data"), eingang))
    d = c.get("/api/invoice?file=" + quote("Scan 1.pdf")).json()
    assert d["pages"], "Scan ohne OCR muss trotzdem als Seite erscheinen"
    r = c.get(d["pages"][0]["image"])
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
