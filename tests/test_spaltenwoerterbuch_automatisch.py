"""SPEC Baustein 5 (Aaron 12.10.2026): "Einmal korrigieren = gelernt (ohne
Extraschritt)" gilt auch fuer das globale Spaltenwoerterbuch -- gelingt
``learn.learn_line_items`` (Probelauf reproduziert die bestaetigten
Positionen exakt), wandern die Kopfwoerter automatisch ins Woerterbuch,
OHNE dass der Mensch extra die Rahmen-Markierung benutzen muss."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import learn
from rechnung import pdf as pdfmod
from rechnung.identify import Supplier
from rechnung.pipeline import EXTRACT_TEMPLATE, Result
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

W, H = A4


def _tabular_invoice(path: Path) -> Path:
    """Ungewohnte Kopfwoerter ("Anz."/"E-Preis"), die die fest eingebauten
    Schluesselwoerter NICHT alle abdecken -- "Anz." passt auf "anz" (ist
    eingebaut), "E-Preis" NICHT (nur "ep"/"einzelpreis"/... siehe
    tabelle._HEADER_HINWEISE)."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(40, H - 50, "Tabellen Technik GmbH")
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 64, "USt-IdNr: DE111222333")
    c.setFont("Helvetica", 10)
    c.drawString(40, H - 110, "Rechnungsnummer")
    c.drawString(200, H - 110, "TT-2026-0007")

    hy = H - 170
    c.drawString(40, hy, "Anz.")
    c.drawString(90, hy, "Bez.")
    c.drawString(330, hy, "E-Preis")
    c.drawString(430, hy, "MwSt")
    c.drawRightString(540, hy, "Betrag")
    ry = hy - 18
    c.drawString(40, ry, "2")
    c.drawString(90, ry, "Wartung Server")
    c.drawRightString(380, ry, "150,00")
    c.drawString(430, ry, "19")
    c.drawRightString(540, ry, "300,00")
    bottom = ry - 18
    c.drawString(40, bottom, "Nettobetrag")
    c.drawRightString(540, bottom, "300,00 EUR")
    c.drawString(40, 40, "IBAN: DE89370400440532013000")
    c.showPage()
    c.save()
    return path


_ITEMS = [{"menge": "2", "beschreibung": "Wartung Server", "einzelpreis_netto": 150.0,
          "mwst_satz": "19", "gesamt_netto": 300.0}]


def test_erfolgreich_gelernte_tabelle_speist_das_globale_woerterbuch(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="tt", name="Tabellen Technik GmbH",
                                 ustids=["DE111222333"]))
    p = _tabular_invoice(tmp_path / "a.pdf")
    doc = pdfmod.load(p)
    res = Result(file=str(p), sha256=doc.sha256, supplier_id="tt",
                extraction=EXTRACT_TEMPLATE, status="review")
    res.line_items = _ITEMS
    store.mark_confirmed(res.sha256, "tester")

    assert store.spaltenwoerter() == {}
    outcome = learn.learn_from_confirmed(store, res, doc, "tester")
    assert outcome.saved and "_positionen" in outcome.learned

    buch = store.spaltenwoerter()
    # "E-Preis" ist kein eingebautes Schluesselwort -- landet trotzdem im
    # Woerterbuch (mit genau EINER Bestaetigung, noch nicht entschieden).
    assert buch.get("e-preis") == {"einzelpreis_netto": 1}
    assert buch.get("bez") == {"beschreibung": 1}
    assert buch.get("betrag") == {"gesamt_netto": 1}


def test_nicht_reproduzierbare_tabelle_speist_das_woerterbuch_nicht(tmp_path: Path):
    """Scheitert der Probelauf (hier: falsche/erfundene Werte, die so gar
    nicht im PDF stehen), darf auch das Woerterbuch nichts lernen --
    dieselbe "nie raten"-Regel wie beim Template selbst."""
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="tt", name="Tabellen Technik GmbH",
                                 ustids=["DE111222333"]))
    p = _tabular_invoice(tmp_path / "a.pdf")
    doc = pdfmod.load(p)
    res = Result(file=str(p), sha256=doc.sha256, supplier_id="tt",
                extraction=EXTRACT_TEMPLATE, status="review")
    res.line_items = [{"menge": "99", "bezeichnung": "Erfunden", "einzelpreis_netto": 1.0,
                       "mwst_satz": "0", "gesamt_netto": 99.0}]
    store.mark_confirmed(res.sha256, "tester")

    outcome = learn.learn_from_confirmed(store, res, doc, "tester")
    assert not outcome.saved
    assert store.spaltenwoerter() == {}
