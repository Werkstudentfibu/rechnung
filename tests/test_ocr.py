"""Tests fuer die Texterkennung (ocr.py, SPEC §7i/§7n).

Tesseract ist auf dieser Maschine i.d.R. nicht installiert -- deshalb wird
der eigentliche Tesseract-Aufruf (``_run_tesseract``) in allen Tests gemockt.
Getestet werden Erkennung (``needs_ocr``), sauberes Degradieren ohne
Tesseract, TSV-Auswertung, Caching und das Zusammenspiel mit der Pipeline
(Konfidenz-Herabstufung, Status ``zurueckgestellt`` ohne Tesseract).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from rechnung import ocr

reportlab = pytest.importorskip("reportlab")
pypdfium2 = pytest.importorskip("pypdfium2")
from rechnung.synth import make_invoice  # noqa: E402


def _textless_pdf(path: Path) -> Path:
    """Reine Vektorgrafik, kein Text -- fuer Tests, die bewusst eine Seite
    OHNE Textebene brauchen (seit Effizienz-Baustein 3 wird eine Seite mit
    Textebene gar nicht mehr per Tesseract erkannt, siehe ocr.page_has_text)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    c.line(10, 10, 100, 100)
    c.showPage()
    c.save()
    return path


# ---- needs_ocr --------------------------------------------------------

def test_needs_ocr_image_suffixes(tmp_path: Path):
    for suf in ocr.IMAGE_SUFFIXES:
        assert ocr.needs_ocr(tmp_path / f"scan{suf}")


def test_needs_ocr_pdf_with_text_layer(tmp_path: Path):
    pdf = make_invoice(tmp_path / "re.pdf", rechnungsnr="RE-1")
    assert not ocr.needs_ocr(pdf)


def test_needs_ocr_pdf_without_text_layer(tmp_path: Path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    p = tmp_path / "scan.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    c.line(10, 10, 100, 100)  # nur Vektorgrafik, kein Text
    c.showPage()
    c.save()
    assert ocr.needs_ocr(p)


def test_needs_ocr_other_suffix_is_false(tmp_path: Path):
    p = tmp_path / "notiz.txt"
    p.write_text("kein Rechnungsformat")
    assert not ocr.needs_ocr(p)


# ---- available() / tesseract_cmd --------------------------------------

def test_tesseract_cmd_env_override(monkeypatch):
    monkeypatch.setenv("RECHNUNG_TESSERACT", "/opt/bin/tesseract")
    assert ocr.tesseract_cmd() == "/opt/bin/tesseract"


def test_available_false_without_binary(monkeypatch):
    ocr._avail = None
    monkeypatch.setattr(ocr, "tesseract_cmd", lambda: None)
    assert ocr.available() is False


def test_available_false_without_german_pack(monkeypatch):
    ocr._avail = None
    monkeypatch.setattr(ocr, "tesseract_cmd", lambda: "/usr/bin/tesseract")
    monkeypatch.setattr(ocr.subprocess, "run",
                         lambda *a, **k: type("R", (), {"stdout": "eng\nfra\n"})())
    assert ocr.available() is False
    ocr._avail = None


def test_available_true_and_cached(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return type("R", (), {"stdout": "deu\neng\n"})()

    ocr._avail = None
    monkeypatch.setattr(ocr, "tesseract_cmd", lambda: "/usr/bin/tesseract")
    monkeypatch.setattr(ocr.subprocess, "run", fake_run)
    assert ocr.available() is True
    assert ocr.available() is True
    assert len(calls) == 1  # zweiter Aufruf kommt aus dem Cache
    ocr._avail = None


def test_available_false_on_subprocess_error(monkeypatch):
    ocr._avail = None
    monkeypatch.setattr(ocr, "tesseract_cmd", lambda: "/usr/bin/tesseract")

    def boom(*a, **k):
        raise OSError("kaputt")

    monkeypatch.setattr(ocr.subprocess, "run", boom)
    assert ocr.available() is False
    ocr._avail = None


# ---- Konfidenz-Schwellen -----------------------------------------------

def test_min_confidence_default_and_env(monkeypatch):
    monkeypatch.delenv("RECHNUNG_OCR_MIN_CONF", raising=False)
    assert ocr.min_confidence() == 80.0
    monkeypatch.setenv("RECHNUNG_OCR_MIN_CONF", "90")
    assert ocr.min_confidence() == 90.0
    monkeypatch.setenv("RECHNUNG_OCR_MIN_CONF", "keine-zahl")
    assert ocr.min_confidence() == 80.0


def test_word_min_confidence_default_and_env(monkeypatch):
    monkeypatch.delenv("RECHNUNG_OCR_WORD_MIN_CONF", raising=False)
    assert ocr.word_min_confidence() == 60.0
    monkeypatch.setenv("RECHNUNG_OCR_WORD_MIN_CONF", "40")
    assert ocr.word_min_confidence() == 40.0


# ---- OcrResult -----------------------------------------------------------

def test_ocrresult_low_property(monkeypatch):
    monkeypatch.delenv("RECHNUNG_OCR_MIN_CONF", raising=False)
    assert ocr.OcrResult("sha", confidence=90.0).low is False
    assert ocr.OcrResult("sha", confidence=50.0).low is True
    assert ocr.OcrResult("sha", confidence=None).low is False


def test_ocrresult_to_dict():
    res = ocr.OcrResult("sha", confidence=85.0, low_words=["x"], pages=2, seconds=1.23)
    d = res.to_dict()
    assert d["engine"] == "tesseract"
    assert d["lang"] == "deu"
    assert d["unsichere_woerter"] == 1
    assert d["pages"] == 2
    assert d["low"] is False


# ---- _parse_tsv ------------------------------------------------------

def test_parse_tsv_confidences_and_low_words():
    tsv = ("level\tpage_num\tconf\ttext\n"
           "5\t1\t95.0\tRechnung\n"
           "5\t1\t30.0\tUnsch4rf\n"
           "5\t1\t-1\t \n"
           "5\t1\t-1\tRand\n"
           "5\t1\t88\t42,00\n")
    confs, low = ocr._parse_tsv(tsv, word_min=60.0)
    assert confs == [95.0, 30.0, 88.0]
    assert low == [ocr.norm("Unsch4rf")]


def test_parse_tsv_missing_header_returns_empty():
    confs, low = ocr._parse_tsv("foo\tbar\n1\t2\n", word_min=60.0)
    assert confs == [] and low == []


def test_parse_tsv_empty_text_returns_empty():
    confs, low = ocr._parse_tsv("", word_min=60.0)
    assert confs == [] and low == []


# ---- ensure_text: sauberes Degradieren ohne Tesseract -------------------

def test_ensure_text_unavailable(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: False)
    src = tmp_path / "scan.jpg"
    src.write_bytes(b"fake-jpeg-bytes")
    cache = tmp_path / "cache"
    res = ocr.ensure_text(src, cache)
    assert res.error == ocr.OCR_UNAVAILABLE
    assert res.pdf is None
    assert not cache.exists()  # kein Cache-Verzeichnis ohne Erfolg angelegt


def test_ensure_text_unreadable_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: True)
    src = tmp_path / "kaputt.png"
    src.write_bytes(b"das-ist-kein-bild")
    res = ocr.ensure_text(src, tmp_path / "cache")
    assert "nicht lesbar" in res.error


# ---- ensure_text: Cache -------------------------------------------------

def test_ensure_text_cache_hit_skips_available_check(tmp_path: Path, monkeypatch):
    src = tmp_path / "scan.jpg"
    src.write_bytes(b"inhalt")
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{sha}.pdf").write_bytes(b"%PDF-1.4 fake")
    (cache / f"{sha}.json").write_text(json.dumps({
        "confidence": 77.5, "low_words": ["foo"], "pages": 3, "seconds": 2.5,
    }), "utf-8")

    def must_not_be_called():
        raise AssertionError("available() sollte bei einem Cache-Treffer nicht laufen")

    monkeypatch.setattr(ocr, "available", must_not_be_called)
    res = ocr.ensure_text(src, cache)
    assert res.sha256 == sha
    assert res.pdf == cache / f"{sha}.pdf"
    assert res.confidence == 77.5
    assert res.low_words == ["foo"]
    assert res.pages == 3
    assert res.error == ""


def test_ensure_text_corrupt_cache_meta_falls_back(tmp_path: Path, monkeypatch):
    src = tmp_path / "scan.jpg"
    src.write_bytes(b"inhalt")
    sha = hashlib.sha256(src.read_bytes()).hexdigest()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{sha}.pdf").write_bytes(b"%PDF fake")
    (cache / f"{sha}.json").write_text("{kaputt", "utf-8")
    monkeypatch.setattr(ocr, "available", lambda: False)
    res = ocr.ensure_text(src, cache)
    assert res.error == ocr.OCR_UNAVAILABLE


# ---- ensure_text: voller Ablauf mit gemocktem Tesseract -----------------

def test_ensure_text_full_run_and_cache_reuse(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda: True)
    # Der "Scan" ist hier eine Vektorgrafik-PDF ohne Textebene -- es geht um
    # den OCR-Ablauf (Rendern, Tesseract je Seite, Zusammenfuegen, Konfidenz),
    # nicht um echte Bildqualitaet. Tesseract selbst wird unten gemockt. Eine
    # normale Test-Rechnung ginge seit Baustein 3 (Seiten mit Textebene
    # werden uebersprungen) gar nicht mehr durch Tesseract.
    src = _textless_pdf(tmp_path / "scan.pdf")
    produced = make_invoice(tmp_path / "ocr_out.pdf", rechnungsnr="RE-9")

    def fake_run(image, out_base, timeout):
        out_base.with_suffix(".pdf").write_bytes(produced.read_bytes())
        out_base.with_suffix(".tsv").write_text(
            "level\tpage_num\tconf\ttext\n"
            "5\t1\t91.0\tRechnung\n"
            "5\t1\t20.0\tverschwommen\n", "utf-8")

    monkeypatch.setattr(ocr, "_run_tesseract", fake_run)
    cache = tmp_path / "cache"
    res = ocr.ensure_text(src, cache)
    assert res.error == ""
    assert res.pdf == cache / f"{res.sha256}.pdf"
    assert res.pdf.exists()
    assert res.pages == 1
    assert res.confidence == pytest.approx(55.5, abs=0.01)
    assert res.low_words == [ocr.norm("verschwommen")]
    meta = json.loads((cache / f"{res.sha256}.json").read_text("utf-8"))
    assert meta["confidence"] == res.confidence

    calls = []
    monkeypatch.setattr(ocr, "_run_tesseract", lambda *a: calls.append(a))
    res2 = ocr.ensure_text(src, cache)
    assert res2.confidence == res.confidence
    assert calls == []  # Cache-Treffer, kein erneuter Tesseract-Lauf


def test_ensure_text_tesseract_timeout(tmp_path: Path, monkeypatch):
    import subprocess as sp

    monkeypatch.setattr(ocr, "available", lambda: True)
    src = _textless_pdf(tmp_path / "scan.pdf")

    def fake_run(image, out_base, timeout):
        raise sp.TimeoutExpired(cmd="tesseract", timeout=timeout or 1)

    monkeypatch.setattr(ocr, "_run_tesseract", fake_run)
    res = ocr.ensure_text(src, tmp_path / "cache", timeout=1)
    assert "Zeitgrenze" in res.error


# ---- Zusammenspiel mit der Pipeline -------------------------------------

def test_process_image_without_tesseract_is_deferred(tmp_path: Path, monkeypatch):
    from rechnung.pipeline import DEFERRED, process
    from rechnung.store import Store

    monkeypatch.setattr(ocr, "available", lambda: False)
    store = Store(tmp_path / "data")
    scan = tmp_path / "scan.jpg"
    scan.write_bytes(b"fake-jpeg")

    res = process(scan, store)
    assert res.status == DEFERRED
    assert any("OCR nicht verfuegbar" in r for r in res.hold_reasons)
    assert res.ocr is not None and res.ocr["error"] == ocr.OCR_UNAVAILABLE


def test_process_scan_low_confidence_forces_review(tmp_path: Path, monkeypatch):
    from rechnung.identify import supplier_from_doc
    from rechnung.pipeline import REVIEW, process
    from rechnung.store import Store
    from rechnung.template import Anchor, FieldSpec, Template
    from rechnung import pdf as pdfmod

    store = Store(tmp_path / "data")
    quelle = make_invoice(tmp_path / "re1.pdf", rechnungsnr="RE-2026-1", netto=1000.0)
    doc = pdfmod.load(quelle)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))

    tpl = Template(id="muster__standard", supplier_id="muster", secured_at=2, confirmations=3)
    tpl.fields = [
        FieldSpec("rechnungsnummer", "number", True, anchor=Anchor("rechnungsnummer", 0),
                  rel=(60, -4, 240, 12)),
        FieldSpec("rechnungsdatum", "date", True, anchor=Anchor("rechnungsdatum", 0),
                  rel=(50, -4, 240, 12)),
        FieldSpec("netto", "amount", True, anchor=Anchor("nettobetrag", 0), rel=(120, -4, 340, 12)),
        FieldSpec("ust_betrag", "amount", True, anchor=Anchor("umsatzsteuer", 0),
                  rel=(120, -4, 340, 12)),
        FieldSpec("brutto", "amount", True, anchor=Anchor("gesamtbetrag", 0), rel=(120, -4, 340, 12)),
    ]
    store.save_template(tpl)

    # Vorab pruefen: ohne OCR laeuft dieselbe Rechnung als AUTO durch -- der
    # Test zeigt also wirklich die OCR-bedingte Herabstufung, nicht einen
    # anderen Grund fuer 'review'.
    assert process(quelle, store).status == "auto"

    scan = tmp_path / "scan.jpg"
    scan.write_bytes(b"fake-jpeg")
    fake_ocr = ocr.OcrResult(sha256="deadbeef" * 8, pdf=quelle, confidence=40.0,
                             low_words=[], pages=1, seconds=0.5)
    monkeypatch.setattr(ocr, "needs_ocr", lambda p: True)
    monkeypatch.setattr(ocr, "ensure_text", lambda *a, **k: fake_ocr)

    res = process(scan, store)
    assert res.status == REVIEW
    assert res.ocr is not None and res.ocr["low"] is True
    assert any(c.name == "ocr-konfidenz" and c.ok is False for c in res.checks)
    # alle aus dem Rechnungstext gelesenen Felder sind als OCR-Herkunft markiert
    d = res.to_dict()
    assert d["fields"]["rechnungsnummer"]["via"] == "ocr"
    # Stammdaten (library) sind keine OCR-Herkunft
    if "iban" in d["fields"] and d["fields"]["iban"]["origin"] == "library":
        assert d["fields"]["iban"]["via"] is None
    # Schattenbetrieb darf eine unsichere OCR-Rechnung nie durchlassen
    assert res.shadow["would_pass"] is False


def test_portable_tesseract_liegt_neben_app():
    p = ocr.portable_tesseract()
    assert p.parts[-4:] == ("tesseract", "Library", "bin", "tesseract.exe")
    assert p.parents[3] == Path(ocr.__file__).resolve().parents[2]


def test_tessdata_env_fuer_conda_layout(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
    exe = tmp_path / "Library" / "bin" / "tesseract.exe"
    exe.parent.mkdir(parents=True); exe.write_bytes(b"")
    td = tmp_path / "Library" / "share" / "tessdata"; td.mkdir(parents=True)
    env = ocr._tessdata_env(str(exe))
    assert env is not None and env["TESSDATA_PREFIX"] == str(td)
    monkeypatch.setenv("TESSDATA_PREFIX", "x")          # gesetzt -> nicht anfassen
    assert ocr._tessdata_env(str(exe)) is None
    assert ocr._tessdata_env("/usr/bin/tesseract") is None
