"""Baustein 6: Gutschrift/Rechnungskorrektur/Storno bei PDFs -- Belegart,
Vorzeichen konsistent, Verweis auf Ursprungsrechnung, unklar -> Mensch."""

from __future__ import annotations

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from rechnung import belegart, human, jobrouter
from rechnung import pdf as pdfmod
from rechnung.config import JobRouterConfig
from rechnung.identify import Supplier
from rechnung.pipeline import finalize, process
from rechnung.store import Store


@pytest.mark.parametrize("titel,art", [
    ("Gutschrift Nr. GS-12 zur Rechnung RE-4711", belegart.GUTSCHRIFT),
    ("CREDIT NOTE", belegart.GUTSCHRIFT),
    ("Rechnungskorrektur", belegart.KORREKTUR),
    ("Stornorechnung", belegart.STORNO),
    ("Rechnung", belegart.RECHNUNG),
    ("Invoice", belegart.RECHNUNG),
    ("2. Abschlagsrechnung", belegart.ABSCHLAG),
    ("Schlussrechnung", belegart.SCHLUSS),
    ("Rechnungsnummer: RE-1", None),            # Label, kein Titel
])
def test_aus_titel(titel, art):
    assert belegart.aus_titel(titel)[0] == art


def test_widerspruch_ist_unklar():
    art, why = belegart.aus_titel("Storno-Gutschrift")
    assert art is None and why.startswith("Belegart unklar")


def test_bezug():
    assert belegart.bezug("Gutschrift GS-1 zur Rechnung Nr. RE-2026-0042 vom 1.2.") == "RE-2026-0042"
    assert belegart.bezug("Korrektur der Rechnung 4711", "GS-1") == "4711"
    assert belegart.bezug("nichts") is None


def test_signed():
    assert belegart.signed({"belegart": "Gutschrift", "brutto": 119.0, "netto": -100.0}) == \
        {"belegart": "Gutschrift", "brutto": -119.0, "netto": -100.0}
    assert belegart.signed({"belegart": "Rechnung", "brutto": 119.0})["brutto"] == 119.0


def _beleg(path, titel, netto="100,00", ust="19,00", brutto="119,00", bezug=None):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(60, 800, "Muster Handels GmbH")
    c.setFont("Helvetica", 9)
    c.drawString(60, 788, "USt-IdNr.: DE123456789")
    c.setFont("Helvetica-Bold", 16)
    c.drawString(60, 720, titel)
    c.setFont("Helvetica", 10)
    c.drawString(300, 700, "Belegnummer:")
    c.drawString(400, 700, "GS-2026-7")
    c.drawString(300, 686, "Datum:")
    c.drawString(400, 686, "20.02.2026")
    if bezug:
        c.drawString(60, 640, bezug)
    for i, (label, val) in enumerate((("Nettobetrag", netto), ("Umsatzsteuer 19%", ust),
                                      ("Gesamtbetrag", brutto))):
        c.drawString(60, 500 - i * 16, label)
        c.drawString(300, 500 - i * 16, val + " EUR")
    c.drawString(60, 80, "IBAN DE89370400440532013000")
    c.save()
    return path


def _store(tmp_path):
    st = Store(tmp_path / "data")
    st.save_supplier(Supplier(id="muster", name="Muster Handels GmbH",
                              ustids=["DE123456789"], ibans=["DE89370400440532013000"]))
    return st


def _korrigiere(store, sha, **kw):
    for k, v in kw.items():
        store.save_correction(sha, k, v)


def test_gutschrift_positiv_gedruckt_wird_negativ_gefuehrt(tmp_path):
    store = _store(tmp_path)
    p = _beleg(tmp_path / "gs.pdf", "Gutschrift", bezug="Gutschrift zur Rechnung RE-2026-0042")
    sha = pdfmod.load(p).sha256
    _korrigiere(store, sha, rechnungsnummer="GS-2026-7", rechnungsdatum="20.02.2026",
                netto="100,00", ust_betrag="19,00", brutto="119,00")
    res = process(p, store, use_llm=False)
    assert res.fields["belegart"].value == "Gutschrift"
    assert res.fields["bezug_rechnungsnummer"].value == "RE-2026-0042"
    assert next(c for c in res.checks if c.name == "vorzeichen").ok is True
    body = jobrouter.payload_from_result(res.to_dict(), JobRouterConfig())
    assert body["fields"]["brutto"] == -119.0 and body["fields"]["netto"] == -100.0
    assert body["fields"]["belegart"] == "Gutschrift"
    store.mark_confirmed(sha, "t")
    finalize(res, store, operator="t", to_jobrouter=False)
    rec = store.processed()[-1]
    assert rec["brutto"] == -119.0 and rec["belegart"] == "Gutschrift"


def test_gutschrift_negativ_gedruckt_ist_plausibel(tmp_path):
    store = _store(tmp_path)
    p = _beleg(tmp_path / "gs2.pdf", "Gutschrift", "-100,00", "-19,00", "-119,00")
    _korrigiere(store, pdfmod.load(p).sha256, rechnungsnummer="GS-2026-7",
                rechnungsdatum="20.02.2026", netto="-100,00", ust_betrag="-19,00",
                brutto="-119,00")
    res = process(p, store, use_llm=False)
    chk = {c.name: c for c in res.checks}
    assert chk["betraege-plausibel"].ok is True and chk["vorzeichen"].ok is True
    # ohne Verweis auf Ursprungsrechnung: nur Hinweis
    assert any("Ursprungsrechnung" in h for h in res.hinweise)


def test_gemischte_vorzeichen_gehen_an_mensch(tmp_path):
    store = _store(tmp_path)
    p = _beleg(tmp_path / "x.pdf", "Rechnungskorrektur", "-100,00", "19,00", "-81,00")
    _korrigiere(store, pdfmod.load(p).sha256, rechnungsnummer="K-1",
                rechnungsdatum="20.02.2026", netto="-100,00", ust_betrag="19,00",
                brutto="-81,00")
    res = process(p, store, use_llm=False)
    assert next(c for c in res.checks if c.name == "vorzeichen").ok is False
    assert any("vorzeichen" in g["grund"] for g in human.gruende(res))


def test_unklare_belegart_geht_an_mensch(tmp_path):
    store = _store(tmp_path)
    p = _beleg(tmp_path / "u.pdf", "Storno-Gutschrift")
    res = process(p, store, use_llm=False)
    assert "belegart" not in res.fields
    assert any("Belegart unklar" in g["grund"] for g in human.gruende(res))
    # Mensch traegt die Belegart ein -> eindeutig
    _korrigiere(store, res.sha256, belegart="Storno")
    res = process(p, store, use_llm=False)
    assert next(c for c in res.checks if c.name == "belegart").ok is True
