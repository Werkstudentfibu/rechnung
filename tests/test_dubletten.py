"""Baustein 4: Dubletten -- normalisierte Nummer, Lieferant+Brutto+Datum,
Mahnung zu bekannter Rechnung. Jeder Verdacht -> Mensch mit Ur-Rechnung."""

from __future__ import annotations

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from rechnung import dubletten, human, synth
from rechnung import pdf as pdfmod
from rechnung.identify import Supplier
from rechnung.pipeline import dispatch, finalize, process
from rechnung.store import Store
from rechnung.template import FieldResult


def _f(**kw):
    return {k: FieldResult(k, v, str(v), 0.9, "template-anchor") for k, v in kw.items()}


def test_norm_number():
    assert dubletten.norm_number("RE-2026-00042") == dubletten.norm_number("re 2026/42")
    assert dubletten.norm_number("000815") == dubletten.norm_number("815")
    assert dubletten.norm_number("RE-1") != dubletten.norm_number("RE-11")


PROCESSED = [{"supplier_id": "a", "sha256": "x" * 64, "file": "alt.pdf",
              "rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "2026-02-14",
              "brutto": 1190.0}]


def test_normalisierte_nummer_ist_verdacht():
    why, ref = dubletten.find(_f(rechnungsnummer="RE 2026 42"), "a", PROCESSED, "y" * 64)
    assert "normalisiert" in why and ref["file"] == "alt.pdf"


def test_gleicher_betrag_und_datum_ist_verdacht():
    why, ref = dubletten.find(_f(rechnungsnummer="RE-2026-99", brutto=1190.0,
                                 rechnungsdatum="2026-02-14"), "a", PROCESSED, "y" * 64)
    assert "Bruttobetrag" in why and ref["sha256"] == "x" * 64


def test_anderer_lieferant_oder_gleiche_datei_kein_verdacht():
    f = _f(rechnungsnummer="RE-2026-00042", brutto=1190.0, rechnungsdatum="2026-02-14")
    assert dubletten.find(f, "b", PROCESSED, "y" * 64) is None
    assert dubletten.find(f, "a", PROCESSED, "x" * 64) is None


def test_mahnung_im_titel_nicht_im_kleingedruckten(tmp_path):
    p = tmp_path / "m.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    c.drawString(60, 780, "1. Mahnung")
    c.drawString(60, 500, "Unsere Rechnung RE-2026-00042 vom 14.02.2026 ist noch offen.")
    c.save()
    doc = pdfmod.load(p)
    why, ref = dubletten.find(_f(rechnungsnummer="M-77"), "a", PROCESSED, doc.sha256, doc)
    assert why.startswith("Mahnung") and ref["rechnungsnummer"] == "RE-2026-00042"
    # Mahnung ohne bekannte Ur-Rechnung -> trotzdem Verdacht
    why2, ref2 = dubletten.find(_f(rechnungsnummer="M-77"), "a", [], doc.sha256, doc)
    assert "unbekannt" in why2 and ref2 is None
    # "Mahnung" nur im Kleingedruckten unten -> keine Mahnung
    p2 = tmp_path / "r.pdf"
    c = canvas.Canvas(str(p2), pagesize=A4)
    c.drawString(60, 780, "Rechnung")
    c.drawString(60, 60, "Bei Zahlungsverzug erfolgt eine Mahnung mit Gebuehr.")
    c.save()
    assert not dubletten.is_reminder(pdfmod.load(p2))


def test_pipeline_verdacht_geht_an_mensch_mit_ur_rechnung(tmp_path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="muster", name="Muster Handels GmbH",
                                 ustids=["DE123456789"], ibans=["DE89370400440532013000"]))
    a = synth.make_invoice(tmp_path / "a.pdf", rechnungsnr="RE-2026-00042")
    ra = process(a, store, use_llm=False)
    store.mark_confirmed(ra.sha256, "t")
    for k, v in {"rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "2026-02-14",
                 "netto": "1000.00", "ust_betrag": "190.00", "brutto": "1190.00"}.items():
        store.save_correction(ra.sha256, k, v)
    ra = process(a, store, use_llm=False)
    assert finalize(ra, store, operator="t", to_jobrouter=False)["finalized"]

    # dieselbe Rechnung, Nummer anders geschrieben, andere Datei (anderes Datum im Fuss)
    b = synth.make_invoice(tmp_path / "b.pdf", rechnungsnr="re-2026-042", lieferant="Muster Handels GmbH ")
    rb = process(b, store, use_llm=False)
    for k, v in {"rechnungsnummer": "re-2026-042"}.items():
        store.save_correction(rb.sha256, k, v)
    rb = process(b, store, use_llm=False)
    chk = next(c for c in rb.checks if c.name == "keine-dublette")
    assert chk.ok is False and "RE-2026-00042" in chk.detail and "a.pdf" in chk.detail
    assert rb.dublette["sha256"] == ra.sha256
    out = dispatch(rb, store)
    assert out["aktion"] == "mensch"
    rec = human.finde(store, rb.sha256)[1]
    assert rec["ur_rechnung"]["datei"] == "a.pdf"
