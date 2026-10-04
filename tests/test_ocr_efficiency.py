"""Effizienz-Baustein 3: OCR nur fuer Seiten ohne Textebene, Parallelitaet
ueber Prozessorkerne (ocr.py/pdf.py)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from rechnung import ocr
from rechnung import pdf as pdfmod

reportlab = pytest.importorskip("reportlab")
pypdfium2 = pytest.importorskip("pypdfium2")
from rechnung.synth import make_invoice  # noqa: E402


def _mixed_pdf(path: Path) -> Path:
    """Zwei Seiten: die erste mit Textebene, die zweite reine Vektorgrafik
    (kein Text) -- wie eine Rechnung mit eingescanntem Lieferschein."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(72, 700, "Seite 1 hat Text: Rechnungsnummer RE-1")
    c.showPage()
    c.line(10, 10, 100, 100)
    c.showPage()
    c.save()
    return path


def _textless_pdf(path: Path) -> Path:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    c.line(10, 10, 100, 100)
    c.showPage()
    c.save()
    return path


# ---- pdf.page_has_text --------------------------------------------------
def test_page_has_text_distinguishes_pages(tmp_path: Path):
    p = _mixed_pdf(tmp_path / "mixed.pdf")
    assert pdfmod.page_has_text(p, 0) is True
    assert pdfmod.page_has_text(p, 1) is False


def test_page_has_text_out_of_range_is_false(tmp_path: Path):
    p = _textless_pdf(tmp_path / "x.pdf")
    assert pdfmod.page_has_text(p, 5) is False


# ---- ocr.jobs() -----------------------------------------------------------
def test_jobs_default_is_cpus_minus_one(monkeypatch):
    monkeypatch.delenv("RECHNUNG_OCR_JOBS", raising=False)
    monkeypatch.setattr(ocr.os, "cpu_count", lambda: 4)
    assert ocr.jobs() == 3


def test_jobs_at_least_one(monkeypatch):
    monkeypatch.delenv("RECHNUNG_OCR_JOBS", raising=False)
    monkeypatch.setattr(ocr.os, "cpu_count", lambda: 1)
    assert ocr.jobs() == 1


def test_jobs_env_override(monkeypatch):
    monkeypatch.setenv("RECHNUNG_OCR_JOBS", "2")
    assert ocr.jobs() == 2


def test_jobs_env_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("RECHNUNG_OCR_JOBS", "keine-zahl")
    monkeypatch.setattr(ocr.os, "cpu_count", lambda: 4)
    assert ocr.jobs() == 3


# ---- ensure_text: nur Seiten ohne Textebene gehen durch Tesseract --------
def test_ensure_text_skips_pages_with_text_layer(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: True)
    src = _mixed_pdf(tmp_path / "mixed.pdf")
    produced = make_invoice(tmp_path / "ocr_out.pdf", rechnungsnr="RE-OCR")
    calls = []

    def fake_run(image, out_base, timeout):
        calls.append(image)
        out_base.with_suffix(".pdf").write_bytes(produced.read_bytes())
        out_base.with_suffix(".tsv").write_text(
            "level\tpage_num\tconf\ttext\n5\t1\t70.0\tLieferschein\n", "utf-8")

    monkeypatch.setattr(ocr, "_run_tesseract", fake_run)
    res = ocr.ensure_text(src, tmp_path / "cache")
    assert res.error == ""
    assert len(calls) == 1          # nur die zweite (textlose) Seite
    assert res.pages == 2
    assert res.confidence == 70.0   # Konfidenz nur aus der OCR'ten Seite


def test_ensure_text_no_ocr_needed_reports_no_confidence(tmp_path: Path, monkeypatch):
    """Wenn jede Seite schon eine Textebene hat, ist "OCR-Konfidenz" keine
    sinnvolle Groesse -- None statt faelschlich 0.0 (sonst waere jede so
    behandelte Rechnung "unsicher")."""
    monkeypatch.setattr(ocr, "available", lambda: True)
    src = make_invoice(tmp_path / "re.pdf")
    called = []
    monkeypatch.setattr(ocr, "_run_tesseract", lambda *a: called.append(a))
    res = ocr.ensure_text(src, tmp_path / "cache")
    assert called == []
    assert res.confidence is None
    assert res.low is False


def test_ensure_text_all_pages_without_text_layer_runs_all(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: True)
    src = _textless_pdf(tmp_path / "scan.pdf")
    produced = make_invoice(tmp_path / "ocr_out.pdf", rechnungsnr="RE-OCR")
    calls = []

    def fake_run(image, out_base, timeout):
        calls.append(image)
        out_base.with_suffix(".pdf").write_bytes(produced.read_bytes())
        out_base.with_suffix(".tsv").write_text(
            "level\tpage_num\tconf\ttext\n5\t1\t90.0\tok\n", "utf-8")

    monkeypatch.setattr(ocr, "_run_tesseract", fake_run)
    res = ocr.ensure_text(src, tmp_path / "cache")
    assert len(calls) == 1
    assert res.confidence == 90.0


# ---- Parallelitaet --------------------------------------------------------
def test_ensure_text_runs_pages_in_parallel(tmp_path: Path, monkeypatch):
    """Mehrere textlose Seiten laufen gleichzeitig, nicht strikt
    nacheinander -- mit zwei Arbeitern und zwei 0,5s-"Tesseract"-Aufrufen
    dauert der Lauf insgesamt deutlich unter der seriellen Summe (>= 1,0s
    plus Rendern/Zusammenfuegen)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    p = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for _ in range(2):
        c.line(10, 10, 100, 100)
        c.showPage()
    c.save()

    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "jobs", lambda: 2)
    produced = make_invoice(tmp_path / "ocr_out.pdf", rechnungsnr="RE-OCR")

    def fake_run(image, out_base, timeout):
        time.sleep(0.5)
        out_base.with_suffix(".pdf").write_bytes(produced.read_bytes())
        out_base.with_suffix(".tsv").write_text(
            "level\tpage_num\tconf\ttext\n5\t1\t80.0\tok\n", "utf-8")

    monkeypatch.setattr(ocr, "_run_tesseract", fake_run)
    t0 = time.monotonic()
    res = ocr.ensure_text(p, tmp_path / "cache")
    elapsed = time.monotonic() - t0
    assert res.error == ""
    assert res.pages == 2
    assert elapsed < 0.9   # seriell waeren es >= 1,0s (plus Rendern/Merge)
