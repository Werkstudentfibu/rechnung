"""Lieferantenname nur im Logo (Aaron 19.11.2026, "Setze alles um"): wird
kein Lieferant erkannt, liest die Texterkennung einmal den Kopfbereich der
ersten Seite und sucht dort den Namen. Nur erfundene Rechnungen."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import ocr
from rechnung import pipeline
from rechnung.identify import Supplier
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

NAME = "Kaiser Getraenke Handel GmbH"


def _logo_png(path: Path, text: str = "KAISER GETRAENKE HANDEL GMBH") -> Path:
    from PIL import Image, ImageDraw, ImageFont

    im = Image.new("RGB", (1800, 160), "white")
    d = ImageDraw.Draw(im)
    d.text((20, 30), text, fill="black", font=ImageFont.load_default(size=80))
    im.save(path)
    return path


def _rechnung_mit_logo(path: Path, logo: Path, iban_im_text: str | None = None) -> Path:
    """Textebene ohne Lieferantennamen -- der steht nur im Logo-Bild."""
    w, h = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawImage(ImageReader(str(logo)), 40, h - 90, width=450, height=40)
    c.setFont("Helvetica", 10)
    y = h - 160
    for z in ("Rechnung", "Rechnungsnummer RE-77", "Rechnungsdatum 05.11.2026",
              "Nettobetrag 100,00 EUR", "Umsatzsteuer 19,00 EUR", "Rechnungsbetrag 119,00 EUR",
              *( [f"IBAN {iban_im_text}"] if iban_im_text else [])):
        c.drawString(40, y, z)
        y -= 16
    c.showPage()
    c.save()
    return path


def _store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="kaiser", name=NAME))        # wie Excel-Import: nur Name
    store.save_supplier(Supplier(id="anders", name="Anders Papier KG"))
    return store


def test_name_im_logo_wird_erkannt(tmp_path, monkeypatch):
    store = _store(tmp_path)
    pdf = _rechnung_mit_logo(tmp_path / "r.pdf", _logo_png(tmp_path / "logo.png"))
    aufrufe = []

    def fake_kopf(path, cache_dir, sha, timeout=30):
        aufrufe.append(sha)
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        (Path(cache_dir) / f"{sha}.kopf.txt").write_text(NAME.upper(), "utf-8")
        return NAME.upper()

    monkeypatch.setattr(ocr, "kopf_text", fake_kopf)
    # schnelle Listen-Verarbeitung: keine neue Kopf-Erkennung
    res = pipeline.process(pdf, store, use_llm=False)
    assert res.supplier_id is None and aufrufe == []
    # gruendlich (Vorbereiten/Pruefansicht): Kopf-Erkennung -> Name gefunden
    res = pipeline.process(pdf, store, use_llm=True)
    assert res.supplier_id == "kaiser"
    assert pipeline.LOGO_HINWEIS in res.hinweise
    assert len(aufrufe) == 1
    # danach reicht auch der schnelle Weg (Ergebnis liegt im Cache)
    res = pipeline.process(pdf, store, use_llm=False)
    assert res.supplier_id == "kaiser"


def test_logo_mit_fremder_iban_zaehlt_nicht(tmp_path, monkeypatch):
    store = _store(tmp_path)
    pdf = _rechnung_mit_logo(tmp_path / "r.pdf", _logo_png(tmp_path / "logo.png"))
    monkeypatch.setattr(ocr, "kopf_text",
                        lambda *a, **k: NAME + " IBAN DE89370400440532013000")
    res = pipeline.process(pdf, store, use_llm=True)
    assert res.supplier_id is None


def test_abschaltbar(tmp_path, monkeypatch):
    store = _store(tmp_path)
    pdf = _rechnung_mit_logo(tmp_path / "r.pdf", _logo_png(tmp_path / "logo.png"))
    monkeypatch.setattr(ocr, "kopf_text", lambda *a, **k: pytest.fail("darf nicht laufen"))
    monkeypatch.setenv("RECHNUNG_LOGO_TEXTERKENNUNG", "0")
    assert pipeline.process(pdf, store, use_llm=True).supplier_id is None
    monkeypatch.delenv("RECHNUNG_LOGO_TEXTERKENNUNG")
    monkeypatch.setenv("RECHNUNG_NAMENSERKENNUNG", "0")
    assert pipeline.process(pdf, store, use_llm=True).supplier_id is None


def test_bereits_erkannter_lieferant_braucht_kein_logo(tmp_path, monkeypatch):
    store = _store(tmp_path)
    pdf = _rechnung_mit_logo(tmp_path / "r.pdf", _logo_png(tmp_path / "logo.png", "ANDERES LOGO"))
    from reportlab.pdfgen import canvas as cv
    c = cv.Canvas(str(pdf), pagesize=A4)
    c.drawString(40, 700, NAME)
    c.drawString(40, 680, "Rechnungsnummer RE-1")
    c.showPage()
    c.save()
    monkeypatch.setattr(ocr, "kopf_text", lambda *a, **k: pytest.fail("darf nicht laufen"))
    res = pipeline.process(pdf, store, use_llm=True)
    assert res.supplier_id == "kaiser" and pipeline.LOGO_HINWEIS not in res.hinweise


@pytest.mark.skipif(not ocr.available(), reason="Tesseract nicht installiert")
def test_kopf_text_echte_texterkennung_mit_cache(tmp_path, monkeypatch):
    pdf = _rechnung_mit_logo(tmp_path / "r.pdf", _logo_png(tmp_path / "logo.png"))
    cache = tmp_path / "ocr"
    text = ocr.kopf_text(pdf, cache, "abc")
    assert "KAISER" in text.upper() and "GETRAENKE" in text.upper()
    assert (cache / "abc.kopf.txt").exists()
    monkeypatch.setattr(ocr, "_run", lambda *a, **k: pytest.fail("Cache haette reichen muessen"))
    assert ocr.kopf_text(pdf, cache, "abc") == text
