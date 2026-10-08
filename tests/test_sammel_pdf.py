"""Baustein 2: Sammel-PDF nur an sicheren Grenzen aufteilen, lange
Rechnungen zusammenhalten, im Zweifel an den Menschen."""

from __future__ import annotations

from pathlib import Path

from rechnung import human, pdf as pdfmod, split, synth
from rechnung.identify import Supplier
from rechnung.pipeline import process, scan_folder
from rechnung.store import Store


def _store(tmp_path) -> Store:
    st = Store(tmp_path / "data")
    st.save_supplier(Supplier(id="muster", name="Muster Handels GmbH",
                              ustids=["DE123456789"], ibans=["DE89370400440532013000"]))
    return st


def test_lange_rechnung_12_seiten_bleibt_ganz(tmp_path):
    soll = synth.make_multipage_invoice(tmp_path / "lang.pdf", seiten=12)
    a = split.analyse(pdfmod.load(soll["path"]))
    assert a.segments == [list(range(12))] and not a.uncertain
    # auch ohne Rechnungsnummer auf den Folgeseiten
    soll2 = synth.make_multipage_invoice(tmp_path / "lang2.pdf", seiten=12,
                                         nummer_jede_seite=False)
    a2 = split.analyse(pdfmod.load(soll2["path"]))
    assert len(a2.segments) == 1 and not a2.uncertain


def test_drei_rechnungen_werden_drei(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    soll = synth.make_sammel_pdf(inbox / "sammel.pdf", [
        {"rechnungsnr": "RE-1001", "seiten": 2},
        {"rechnungsnr": "RE-1002", "seiten": 1},
        {"rechnungsnr": "RE-1003", "seiten": 3},
    ])
    store = _store(tmp_path)
    a = split.analyse(pdfmod.load(inbox / "sammel.pdf"))
    assert a.segments == [[0, 1], [2], [3, 4, 5]]
    parts = split.prepare_folder(inbox, store)
    assert [p.name for p in parts] == ["sammel_teil1von3.pdf", "sammel_teil2von3.pdf",
                                       "sammel_teil3von3.pdf"]
    assert not (inbox / "sammel.pdf").exists()
    assert len(list((store.root / "sammel").glob("*sammel.pdf"))) == 1
    results = {Path(r.file).name: r for r in scan_folder(inbox, store)}
    for k, s in enumerate(soll, start=1):
        r = results[f"sammel_teil{k}von3.pdf"]
        assert r.sammel["teil"] == k and r.sammel["von"] == 3
        assert r.sammel["original_datei"] == "sammel.pdf"
        assert len(r.sammel["original_sha256"]) == 64
        assert not any("mehrere" in h for h in r.hold_reasons)
    # zweiter Scan teilt nichts erneut
    assert split.prepare_folder(inbox, store) == []


def test_einseitige_rechnungen_ohne_seitenzaehler(tmp_path):
    synth.make_sammel_pdf(tmp_path / "s.pdf", [{"rechnungsnr": f"RE-20{i}", "seiten": 1}
                                               for i in range(3)], zaehler=False)
    assert split.analyse(pdfmod.load(tmp_path / "s.pdf")).segments == [[0], [1], [2]]


def test_im_zweifel_nicht_trennen_sondern_mensch(tmp_path):
    # Zweite Rechnung beginnt, aber ohne Seitenzaehler und die erste ist
    # zweiseitig -> keine sichere Grenze
    p = tmp_path / "zweifel.pdf"
    synth.make_sammel_pdf(p, [{"rechnungsnr": "RE-3001", "seiten": 2},
                              {"rechnungsnr": "RE-3002", "seiten": 1}], zaehler=False)
    a = split.analyse(pdfmod.load(p))
    assert len(a.segments) == 1 and a.uncertain.startswith(split.MAYBE_MULTI)
    store = _store(tmp_path)
    assert split.split_file(p, store) == [] and p.exists()
    res = process(p, store, use_llm=False)
    assert any(h.startswith(split.MAYBE_MULTI) for h in res.hold_reasons)
    assert any(g["grund"].startswith(split.MAYBE_MULTI) for g in human.gruende(res))


def test_kein_summenblock_vor_neuer_nummer(tmp_path):
    """Seitenzaehler beginnt neu + neue Nummer, aber davor fehlt der
    Summenblock -> nicht trennen."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    p = tmp_path / "x.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    for no in ("RE-1", "RE-2"):
        c.drawString(300, 780, "Seite 1 von 1")
        c.drawString(300, 760, f"Rechnungsnummer: {no}")
        c.drawString(50, 400, "Pos 1  10,00")
        c.showPage()
    c.save()
    a = split.analyse(pdfmod.load(p))
    assert len(a.segments) == 1 and "kein Summenblock" in a.uncertain
