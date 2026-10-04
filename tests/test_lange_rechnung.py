"""Baustein 3: lange Scans -- Texterkennung innerhalb der Zeitgrenze nur
fuer Kopf- und Fussseiten, der Rest danach im Hintergrund."""

from __future__ import annotations

import re
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from rechnung import eventlog, learn, ocr, synth
from rechnung import pdf as pdfmod
from rechnung.identify import Supplier
from rechnung.pipeline import complete_background_ocr, extraction_text, load_document, process
from rechnung.store import Store


def _scan_from(text_pdf: Path, target: Path) -> Path:
    """Text-PDF -> reines Bild-PDF (wie ein Scan, ohne Textebene)."""
    src = pdfium.PdfDocument(str(text_pdf))
    imgs = [src[i].render(scale=0.5).to_pil().convert("RGB") for i in range(len(src))]
    src.close()
    imgs[0].save(target, save_all=True, append_images=imgs[1:])
    return target


@pytest.fixture
def lang(tmp_path, monkeypatch):
    soll = synth.make_multipage_invoice(tmp_path / "text.pdf", seiten=15,
                                         nummer_jede_seite=False)
    scan = _scan_from(soll["path"], tmp_path / "scan15.pdf")
    calls: list[int] = []

    def fake_tesseract(image, out_base, timeout):
        i = int(re.search(r"s(\d+)", Path(image).name).group(1))
        calls.append(i)
        one = pdfium.PdfDocument.new()
        src = pdfium.PdfDocument(str(soll["path"]))
        one.import_pages(src, pages=[i])
        one.save(str(out_base.with_suffix(".pdf")))
        src.close()
        one.close()
        out_base.with_suffix(".tsv").write_text(
            "level\tpage_num\tconf\ttext\n5\t1\t95.0\tRechnung\n", "utf-8")

    monkeypatch.setattr(ocr, "available", lambda: True)
    monkeypatch.setattr(ocr, "_run_tesseract", fake_tesseract)
    monkeypatch.setattr(ocr, "jobs", lambda: 1)
    monkeypatch.delenv("RECHNUNG_OCR_KOPF", raising=False)
    monkeypatch.delenv("RECHNUNG_OCR_FUSS", raising=False)
    return soll, scan, calls


def test_nur_kopf_und_fuss_innerhalb_der_zeitgrenze(lang, tmp_path):
    soll, scan, calls = lang
    assert not pdfmod.has_text_layer(scan)
    o = ocr.ensure_text(scan, tmp_path / "cache")
    assert sorted(calls) == [0, 1, 13, 14]
    assert o.partial and o.missing_pages == list(range(2, 13)) and o.pages == 15
    doc = pdfmod.load(o.pdf)
    assert len(doc.pages) == 15                       # Seitenzahlen bleiben gleich
    assert doc.pages[5].words == [] and doc.pages[14].words
    assert o.to_dict()["seiten_ohne_ocr"] == list(range(3, 14))
    # zweiter Aufruf: aus dem Cache, keine neue Erkennung
    ocr.ensure_text(scan, tmp_path / "cache")
    assert len(calls) == 4


def test_pipeline_liest_felder_und_rest_folgt_im_hintergrund(lang, tmp_path):
    soll, scan, calls = lang
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="muster", name="Muster Handels GmbH",
                                 ustids=["DE123456789"], ibans=["DE89370400440532013000"]))
    # Vorlage aus der Text-Version lernen (Kopf auf Seite 1, Summen auf Seite 15)
    tdoc = pdfmod.load(soll["path"])
    tpl, out = learn.learn_template(tdoc, "muster", {
        "rechnungsnummer": soll["rechnungsnummer"], "rechnungsdatum": "2026-02-14",
        "netto": soll["netto"], "ust_betrag": soll["ust"], "brutto": soll["brutto"]})
    assert tpl is not None, out.to_dict()
    from rechnung.template import LineItemSpec, Anchor, Column
    tpl.line_items = LineItemSpec(Anchor("pos", 0), [Column("bezeichnung", "text", 0, 0.6),
                                                     Column("gesamt", "amount", 0.6, 1)])
    store.save_template(tpl)

    res = process(scan, store, use_llm=False)
    assert sorted(calls) == [0, 1, 13, 14]
    assert res.seiten == 15, res.to_dict()
    assert res.template_id, (res.hold_reasons, res.status)
    assert res.fields["rechnungsnummer"].value == soll["rechnungsnummer"]
    assert res.fields["brutto"].value == pytest.approx(soll["brutto"])
    assert res.ocr["teilweise"] is True
    assert res.line_items == []
    assert any("Positionen erst nach vollstaendiger Texterkennung" in w for w in res.hinweise)
    # Zeitmessung mit Seitenzahl im Ereignisprotokoll
    ev = eventlog.record(store, res)
    assert ev["seiten"] == 15 and "ocr" in ev["stufenzeiten"]

    # Hintergrundschritt (ausserhalb der Zeitgrenze): Rest erkennen
    assert len(ocr.pending(store.root / "ocr")) == 1
    done = complete_background_ocr(store)
    assert done[0]["ok"] and done[0]["seiten"] == 15
    assert sorted(set(calls)) == list(range(15))
    assert ocr.pending(store.root / "ocr") == []
    doc, o = load_document(scan, store)
    assert not o.partial and all(p.words for p in doc.pages)


def test_kurzer_scan_wird_ganz_erkannt(tmp_path, monkeypatch, lang):
    soll, _scan, calls = lang
    kurz = synth.make_multipage_invoice(tmp_path / "kurz.pdf", seiten=5)
    scan = _scan_from(kurz["path"], tmp_path / "scan5.pdf")
    o = ocr.ensure_text(scan, tmp_path / "c2")
    assert not o.partial and len(calls) == 5


def test_ki_text_bei_langer_rechnung_nur_kopf_und_fuss(tmp_path):
    soll = synth.make_multipage_invoice(tmp_path / "lang.pdf", seiten=12)
    doc = pdfmod.load(soll["path"])
    t = extraction_text(doc)
    assert "Gesamtbetrag" in t and soll["rechnungsnummer"] in t
    assert "Artikel 0040" not in t        # Positionsseite in der Mitte
    assert len(t) < len(doc.text) / 2
