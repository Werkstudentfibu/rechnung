"""Aaron 16.11.2026: "Hat das Programm aus der manuellen Tabellenerstellung
schon was gelernt?" -- Befund: eine Positionstabelle wurde nur gelernt, wenn
jede Beschreibung hoechstens 4 Woerter hatte (MAX_SPAN) und in EINER Zeile
stand. Laengere oder mehrzeilige Beschreibungen (der Normalfall) liessen das
Lernen still scheitern. Jetzt: beliebig lange Beschreibungen, mehrzeilige
Beschreibungen (auch bei der letzten Position), und die gelernte Vorlage
fuegt Fortsetzungszeilen wieder zu einer Position zusammen."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import learn, tabelle
from rechnung.cli import main as cli_main
from rechnung.identify import Supplier
from rechnung.pdf import load
from rechnung.pipeline import EXTRACT_TEMPLATE, Result
from rechnung.store import Store
from rechnung.template import apply_line_items

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402


def _de(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _rechnung(path: Path, positionen: list[tuple[list[str], float, float]]) -> Path:
    """``positionen``: (Beschreibungszeilen, Menge, Einzelpreis) -- die erste
    Zeile steht neben den Betraegen, weitere darunter ohne Betrag."""
    c = canvas.Canvas(str(path), pagesize=A4)
    _w, h = A4
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20 * mm, h - 25 * mm, "Lang Lebensmittel GmbH")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, h - 32 * mm, "USt-IdNr. DE987654321")
    y = h - 70 * mm
    c.setFont("Helvetica-Bold", 9)
    c.drawString(20 * mm, y, "Bezeichnung")
    c.drawRightString(120 * mm, y, "Menge")
    c.drawRightString(150 * mm, y, "Einzelpreis")
    c.drawRightString(185 * mm, y, "Gesamtpreis")
    y -= 7 * mm
    c.setFont("Helvetica", 9)
    netto = 0.0
    for zeilen, menge, ep in positionen:
        gp = round(menge * ep, 2)
        netto += gp
        c.drawString(20 * mm, y, zeilen[0])
        c.drawRightString(120 * mm, y, f"{menge:g}")
        c.drawRightString(150 * mm, y, _de(ep))
        c.drawRightString(185 * mm, y, _de(gp))
        for z in zeilen[1:]:
            y -= 4.5 * mm
            c.drawString(20 * mm, y, z)
        y -= 6 * mm
    y -= 4 * mm
    c.drawString(120 * mm, y, "Nettobetrag")
    c.drawRightString(190 * mm, y, _de(round(netto, 2)) + " EUR")
    c.drawString(20 * mm, 30 * mm, "IBAN DE89 3704 0044 0532 0130 00")
    c.save()
    return path


def _bestaetigt(doc) -> list[dict]:
    items = tabelle.extract(doc, text=doc.text)
    assert items is not None
    return [{k: v for k, v in it.items() if not k.startswith("_")} for it in items]


LANG = [(["Tomaten passiert in der Dose je 2kg"], 4, 3.95),
        (["Oel aus Oliven im Kanister 5l"], 2, 38.5)]
MEHRZEILIG = [(["Tomaten passiert in der Dose", "Herkunft Italien, 2,5 kg"], 4, 3.95),
              (["Olivenoel extra vergine", "Kanister 5 l"], 2, 38.5),
              (["Mehl Type 405"], 1, 21.9)]
LETZTE_MEHRZEILIG = [(["Mehl Type 405"], 1, 21.9),
                     (["Tomaten passiert in der Dose", "Herkunft Italien, 2,5 kg"], 4, 3.95)]


@pytest.mark.parametrize("positionen", [LANG, MEHRZEILIG, LETZTE_MEHRZEILIG],
                         ids=["lang", "mehrzeilig", "letzte_mehrzeilig"])
def test_tabelle_mit_langen_beschreibungen_wird_gelernt(tmp_path, positionen):
    doc = load(_rechnung(tmp_path / "a.pdf", positionen))
    bestaetigt = _bestaetigt(doc)
    assert [b["beschreibung"] for b in bestaetigt] == [" ".join(z) for z, _m, _e in positionen]
    spec = learn.learn_line_items(doc, bestaetigt)
    assert spec is not None
    gelesen = apply_line_items(doc, spec)
    assert [g["beschreibung"] for g in gelesen] == [b["beschreibung"] for b in bestaetigt]


def test_gelernte_vorlage_liest_naechste_rechnung_mit_mehrzeiligen_positionen(tmp_path):
    """Durch den Bestaetigungs-Pfad: gelernt aus einer Rechnung, angewendet
    auf die naechste desselben Lieferanten mit anderen, teils mehrzeiligen
    Positionen."""
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="lang", name="Lang Lebensmittel GmbH",
                                 ustids=["DE987654321"]))
    doc = load(_rechnung(tmp_path / "a.pdf", LANG))
    res = Result(file=str(doc.path), sha256=doc.sha256, supplier_id="lang",
                 extraction=EXTRACT_TEMPLATE, status="review")
    res.line_items = _bestaetigt(doc)
    store.mark_confirmed(res.sha256, "tester")
    outcome = learn.learn_from_confirmed(store, res, doc, "tester")
    assert "_positionen" in outcome.learned
    tpl = store.template(outcome.template_id)
    assert tpl.line_items is not None

    doc2 = load(_rechnung(tmp_path / "b.pdf", MEHRZEILIG))
    gelesen = apply_line_items(doc2, tpl.line_items)
    assert [(g["beschreibung"], g["gesamt_netto"]) for g in gelesen] == [
        ("Tomaten passiert in der Dose Herkunft Italien, 2,5 kg", 15.8),
        ("Olivenoel extra vergine Kanister 5 l", 77.0),
        ("Mehl Type 405", 21.9)]


def test_templates_zeigt_positionstabelle(tmp_path, capsys):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="lang", name="Lang Lebensmittel GmbH",
                                 ustids=["DE987654321"]))
    doc = load(_rechnung(tmp_path / "a.pdf", LANG))
    res = Result(file=str(doc.path), sha256=doc.sha256, supplier_id="lang",
                 extraction=EXTRACT_TEMPLATE, status="review")
    res.line_items = _bestaetigt(doc)
    store.mark_confirmed(res.sha256, "tester")
    learn.learn_from_confirmed(store, res, doc, "tester")

    assert cli_main(["--data", str(tmp_path / "data"), "templates"]) == 0
    out = capsys.readouterr().out
    assert "Positionstabelle: ja (" in out
    assert "Vorlagen (aktiv): 1, davon mit Positionstabelle: 1" in out
