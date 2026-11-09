"""Stufe 1: Vorlagen automatisch aus bestaetigten Rechnungen lernen (learn.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import learn
from rechnung import pdf as pdfmod
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import AUTO, NO_TEMPLATE, REVIEW, process
from rechnung.store import Store
from rechnung.template import Anchor, FieldSpec, Template, apply_template

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from rechnung.synth import make_invoice  # noqa: E402

CONFIRMED = {
    "rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "2026-02-14",
    "netto": 1000.0, "ust_betrag": 190.0, "ust_satz": 19.0, "brutto": 1190.0,
}


def _draw(path: Path, rows: list[tuple[float, float, str]]) -> Path:
    """Freies Mini-Layout: (x, y von oben, Text) je Eintrag."""
    c = canvas.Canvas(str(path), pagesize=A4)
    _w, h = A4
    c.setFont("Helvetica", 10)
    for x, y, text in rows:
        c.drawString(x, h - y, text)
    c.showPage()
    c.save()
    return path


@pytest.mark.parametrize("layout", ["a", "b"])
def test_learns_all_fields_and_generalizes(tmp_path: Path, layout: str):
    doc = pdfmod.load(make_invoice(tmp_path / "src.pdf", layout=layout))
    tpl, out = learn.learn_template(doc, "muster", CONFIRMED)
    assert tpl is not None
    assert set(out.learned) == set(CONFIRMED), out.skipped
    assert tpl.confirmations == 0 and not tpl.secured
    # USt-Satz steht auch in jeder Position ("19%") -- das Label entscheidet
    ust_satz = next(f for f in tpl.fields if f.name == "ust_satz")
    assert ust_satz.anchor.text == "umsatzsteuer"

    # Dasselbe Template auf eine NEUE Rechnung desselben Lieferanten
    other = pdfmod.load(make_invoice(tmp_path / "neu.pdf", layout=layout,
                                     rechnungsnr="RE-2026-00777", datum="03.05.2026",
                                     netto=2345.67, satz=7))
    got = {k: v.value for k, v in apply_template(other, tpl).items()}
    assert got == {"rechnungsnummer": "RE-2026-00777", "rechnungsdatum": "2026-05-03",
                   "netto": 2345.67, "ust_betrag": 164.2, "ust_satz": 7.0,
                   "brutto": 2509.87}


def test_value_formats_are_normalized(tmp_path: Path):
    """Bestaetigt als ISO-Datum/Float, im PDF als '14. Februar 2026'/'1.190,00'."""
    p = _draw(tmp_path / "f.pdf", [
        (40, 100, "Belegnummer:"), (160, 100, "RE 2026/00042"),
        (40, 120, "Rechnungsdatum:"), (160, 120, "14. Februar 2026"),
        (40, 200, "Gesamtbetrag"), (300, 200, "1.190,00 EUR"),
    ])
    doc = pdfmod.load(p)
    tpl, out = learn.learn_template(doc, "x", {"rechnungsdatum": "2026-02-14",
                                               "brutto": 1190.0})
    assert set(out.learned) == {"rechnungsdatum", "brutto"}
    assert {f.name: f.anchor.text for f in tpl.fields} == {
        "rechnungsdatum": "rechnungsdatum", "brutto": "gesamtbetrag"}
    # Rechnungsnummer "RE 2026/00042" wird zwar gefunden, aber die Region
    # liefert ueber coerce() nur "RE" -- nicht exakt reproduziert -> weglassen
    spans = learn.find_value_spans(doc, "RE-2026/00042", "number")
    assert len(spans) == 1 and len(spans[0]) == 2


def test_ambiguous_value_resolved_by_label(tmp_path: Path):
    """Brutto = Zahlbetrag: beide Zeilen tragen 1.190,00, 'Gesamtbetrag' ist
    das staerkere Label fuer brutto."""
    p = _draw(tmp_path / "amb.pdf", [
        (40, 200, "Zahlbetrag"), (300, 200, "1.190,00"),
        (40, 300, "Gesamtbetrag"), (300, 300, "1.190,00"),
    ])
    tpl, out = learn.learn_template(pdfmod.load(p), "x", {"brutto": 1190.0})
    assert out.learned == ["brutto"]
    assert tpl.fields[0].anchor.text == "gesamtbetrag"


def test_ambiguous_value_without_decision_is_dropped(tmp_path: Path):
    p = _draw(tmp_path / "amb2.pdf", [
        (40, 200, "Total"), (300, 200, "1.190,00"),
        (40, 300, "Rechnungsbetrag"), (300, 300, "1.190,00"),
        (40, 400, "Rechnungsnummer"), (300, 400, "R-1"),
    ])
    tpl, out = learn.learn_template(pdfmod.load(p), "x",
                                    {"brutto": 1190.0, "rechnungsnummer": "R-1"})
    assert "brutto" in out.skipped and "mehrdeutig" in out.skipped["brutto"]
    assert [f.name for f in tpl.fields] == ["rechnungsnummer"]


def test_ambiguous_without_matching_label_is_dropped(tmp_path: Path):
    p = _draw(tmp_path / "amb3.pdf", [
        (40, 200, "Position"), (300, 200, "55,00"),
        (40, 300, "Fracht"), (300, 320, "55,00"),
    ])
    tpl, out = learn.learn_template(pdfmod.load(p), "x", {"netto": 55.0})
    assert tpl is None and "netto" in out.skipped


def test_value_not_in_pdf_is_skipped(tmp_path: Path):
    doc = pdfmod.load(make_invoice(tmp_path / "s.pdf"))
    tpl, out = learn.learn_template(doc, "muster", dict(CONFIRMED, bestellnummer="PO-999"))
    assert out.skipped["bestellnummer"] == "nicht im PDF gefunden"
    assert "bestellnummer" not in {f.name for f in tpl.fields}


def test_probe_must_reproduce_exactly(tmp_path: Path):
    """Region wuerde 'Lager' mit einsammeln -> '4711 Lager' != '4711'."""
    p = _draw(tmp_path / "kst.pdf", [(40, 200, "Kostenstelle:"), (140, 200, "4711"),
                                      (175, 200, "Lager")])
    tpl, out = learn.learn_template(pdfmod.load(p), "x", {"kostenstelle": "4711"})
    assert tpl is None
    assert out.skipped["kostenstelle"] == "Probelauf liefert anderen Wert"


def _store_with_supplier(tmp_path: Path):
    store = Store(tmp_path / "data")
    src = make_invoice(tmp_path / "src.pdf")
    store.save_supplier(supplier_from_doc(pdfmod.load(src), "muster", "Muster Handels GmbH"))
    return store, src


def test_learn_from_confirmed_requires_confirmation(tmp_path: Path):
    store, src = _store_with_supplier(tmp_path)
    store.save_correction(pdfmod.load(src).sha256, "netto", "1000.00")
    res = process(src, store)
    out = learn.learn_from_confirmed(store, res, pdfmod.load(src))
    assert not out.saved and out.reason == "nicht bestaetigt"
    assert store.templates_for("muster") == []


def test_learn_from_confirmed_creates_unsecured_template(tmp_path: Path):
    store, src = _store_with_supplier(tmp_path)
    doc = pdfmod.load(src)
    for k, v in {"rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "14.02.2026",
                 "netto": "1.000,00", "ust_betrag": "190,00", "brutto": "1.190,00"}.items():
        store.save_correction(doc.sha256, k, v)
    res = process(src, store)
    assert res.status == NO_TEMPLATE and res.fields["netto"].source == "manual"
    store.mark_confirmed(doc.sha256, "tester")

    out = learn.learn_from_confirmed(store, res, doc, "tester")
    assert out.saved and out.created
    tpl = store.template("muster__standard")
    assert tpl.confirmations == 0 and not tpl.secured
    entry = [a for a in store.audit_log() if a["action"] == "template_learned"][-1]
    assert entry["detail"]["template_id"] == "muster__standard"
    assert entry["detail"]["anchors"]["netto"] == "nettobetrag"

    # Naechste Rechnung desselben Lieferanten: Template greift, aber nicht
    # gesichert -> review, nie auto.
    nxt = make_invoice(tmp_path / "nxt.pdf", rechnungsnr="RE-2026-00050", netto=420.0)
    r2 = process(nxt, store, use_llm=False)
    assert r2.template_id == "muster__standard"
    assert r2.status == REVIEW
    assert r2.fields["netto"].value == 420.0 and r2.fields["brutto"].value == 499.8


def test_secured_template_is_only_extended(tmp_path: Path):
    store, src = _store_with_supplier(tmp_path)
    doc = pdfmod.load(src)
    full, _ = learn.learn_template(doc, "muster", CONFIRMED)
    # gesichert, kennt aber nur netto (korrekt) -- Rechnungsnummer fehlt
    secured = Template(id="muster__standard", supplier_id="muster", confirmations=5,
                       fields=[f for f in full.fields if f.name == "netto"])
    store.save_template(secured)
    store.save_correction(doc.sha256, "rechnungsnummer", "RE-2026-00042")
    res = process(src, store, use_llm=False)
    store.mark_confirmed(doc.sha256)
    out = learn.learn_from_confirmed(store, res, doc)
    assert out.saved and not out.created
    # (die automatisch erkannte Positionstabelle wird seit 05.11.2026 mitgelernt:
    # eine Tabelle ohne Pos.-Spalte und mit Ganzzahl-Betraegen war vorher nie
    # "reproduzierbar")
    assert out.kept == ["netto"]
    assert [x for x in out.learned if x != "_positionen"] == ["rechnungsnummer"]
    tpl = store.template("muster__standard")
    assert {f.name for f in tpl.fields} == {"netto", "rechnungsnummer"}
    assert tpl.confirmations >= 5


def test_secured_template_with_conflict_is_not_touched(tmp_path: Path):
    store, src = _store_with_supplier(tmp_path)
    doc = pdfmod.load(src)
    # gesichert, kennt nur netto -- und das an falscher Stelle (Gesamtbetrag)
    bad = Template(id="muster__standard", supplier_id="muster", confirmations=5,
                   fields=[FieldSpec("netto", "amount", True,
                                     anchor=Anchor("gesamtbetrag", 0),
                                     rel=(265, -4, 392, 14))])
    store.save_template(bad)
    store.save_correction(doc.sha256, "netto", "1000,00")
    store.save_correction(doc.sha256, "rechnungsnummer", "RE-2026-00042")
    res = process(src, store, use_llm=False)
    store.mark_confirmed(doc.sha256)
    out = learn.learn_from_confirmed(store, res, doc)
    # Gesamtprobe: das Template wuerde netto weiterhin falsch liefern ->
    # gar nichts speichern, Konflikt im Audit sichtbar machen.
    assert not out.saved
    assert out.skipped["netto"] == "gesichertes Template liefert anderen Wert"
    tpl = store.template("muster__standard")
    assert [f.name for f in tpl.fields] == ["netto"]
    assert tpl.fields[0].anchor.text == "gesamtbetrag"
    rej = [a for a in store.audit_log() if a["action"] == "template_learn_rejected"]
    assert rej and rej[-1]["detail"]["reason"] == "Gesamtprobe: netto weicht ab"


def test_unsecured_template_is_corrected(tmp_path: Path):
    store, src = _store_with_supplier(tmp_path)
    doc = pdfmod.load(src)
    bad = Template(id="muster__standard", supplier_id="muster", confirmations=0,
                   fields=[FieldSpec("netto", "amount", True,
                                     anchor=Anchor("gesamtbetrag", 0),
                                     rel=(265, -4, 392, 14))])
    store.save_template(bad)
    store.save_correction(doc.sha256, "netto", "1000,00")
    res = process(src, store, use_llm=False)
    store.mark_confirmed(doc.sha256)
    out = learn.learn_from_confirmed(store, res, doc)
    assert "netto" in out.learned
    netto = next(f for f in store.template("muster__standard").fields if f.name == "netto")
    assert netto.anchor.text == "nettobetrag"


def test_existing_correct_template_is_kept(tmp_path: Path):
    store, src = _store_with_supplier(tmp_path)
    doc = pdfmod.load(src)
    tpl, _ = learn.learn_template(doc, "muster", CONFIRMED)
    store.save_template(tpl)
    res = process(src, store, use_llm=False)
    store.mark_confirmed(doc.sha256)
    out = learn.learn_from_confirmed(store, res, doc)
    # an den Feldern ist nichts neu zu lernen; hoechstens die erkannte Tabelle
    assert set(out.kept) == set(CONFIRMED)
    assert out.learned in ([], ["_positionen"])
    assert out.saved == bool(out.learned)


def test_einvoice_and_unknown_supplier_not_learned(tmp_path: Path):
    store = Store(tmp_path / "data")
    src = make_invoice(tmp_path / "src.pdf")
    doc = pdfmod.load(src)
    store.save_correction(doc.sha256, "netto", "1000,00")
    store.mark_confirmed(doc.sha256)
    res = process(src, store)
    assert res.supplier_id is None
    assert learn.learn_from_confirmed(store, res, doc).reason == "Lieferant unbekannt"


def test_server_confirm_without_template_learns(tmp_path: Path):
    """End-to-end ueber die API: Werte eintippen, bestaetigen -> Vorlage gelernt,
    naechste Rechnung wird damit vorbefuellt (aber weiter bestaetigungspflichtig)."""
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    a = make_invoice(samples / "a.pdf")
    store.save_supplier(supplier_from_doc(pdfmod.load(a), "muster", "Muster"))
    client = TestClient(server_mod.create_app(store, samples))

    # ohne Werte gibt es nichts zu bestaetigen
    assert client.post("/api/confirm", json={"file": "a.pdf"}).status_code == 400

    for k, v in {"rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "14.02.2026",
                 "netto": "1000,00", "ust_betrag": "190,00", "brutto": "1190,00"}.items():
        assert client.post("/api/correction",
                           json={"file": "a.pdf", "field": k, "value": v}).status_code == 200
    inv = client.get("/api/invoice", params={"file": "a.pdf"}).json()
    assert inv["template_id"] is None and inv["current"]["netto"]["source"] == "manual"

    r = client.post("/api/confirm", json={"file": "a.pdf"})
    assert r.status_code == 200, r.text
    assert r.json()["learned"]["saved"] is True
    assert store.is_confirmed(pdfmod.load(a).sha256)
    assert any(p["sha256"] == pdfmod.load(a).sha256 for p in store.processed())

    make_invoice(samples / "b.pdf", rechnungsnr="RE-2026-00043", netto=50.0)
    res = process(samples / "b.pdf", store)
    assert res.template_id == "muster__standard" and res.status != AUTO
    assert res.fields["rechnungsnummer"].value == "RE-2026-00043"
