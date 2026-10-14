"""SPEC Baustein 5 (Aaron 12.10.2026): das globale Spaltenwoerterbuch hilft
``tabelle.py`` bei einem unbekannten Kopfwort, das die fest eingebauten
Schluesselwoerter (``_HEADER_HINWEISE``) nicht abdecken -- erst ab 2
Bestaetigungen, nie gegen einen Widerspruch."""

from __future__ import annotations

from rechnung import tabelle
from rechnung.pdf import Document, Page, Word


def _word(text, x0, x1, top, bottom=None, page=0):
    return Word(text, x0, x1, top, bottom if bottom is not None else top + 10, page)


def _doc_mit_unbekanntem_kopfwort():
    """Eine Mini-Tabelle, deren Beschreibungs-Spalte "Art.-Bez." heisst --
    kein fest eingebautes Schluesselwort (siehe ``tabelle._HEADER_HINWEISE``)
    -- die uebrigen Kopfwoerter sind geschaeufig."""
    header = [
        _word("Art.-Bez.", 20, 80, 100),
        _word("Menge", 150, 190, 100),
        _word("Preis", 220, 260, 100),
        _word("Gesamt", 300, 340, 100),
    ]
    zeile1 = [
        _word("Schraube", 20, 80, 130),
        _word("2", 170, 190, 130),
        _word("10,00", 230, 260, 130),
        _word("20,00", 310, 340, 130),
    ]
    zeile2 = [
        _word("Mutter", 20, 80, 150),
        _word("4", 170, 190, 150),
        _word("5,00", 230, 260, 150),
        _word("20,00", 310, 340, 150),
    ]
    words = header + zeile1 + zeile2
    pg = Page(0, 600, 800, words)
    return Document(path="x", pages=[pg], sha256="x")


def test_unbekanntes_kopfwort_ohne_woerterbuch_findet_keine_tabelle():
    doc = _doc_mit_unbekanntem_kopfwort()
    assert tabelle.extract(doc, text="") is None


def test_woerterbuch_braucht_zwei_bestaetigungen():
    doc = _doc_mit_unbekanntem_kopfwort()
    nur_eine = {"art-bez": {"beschreibung": 1}}
    assert tabelle.extract(doc, text="", woerterbuch=nur_eine) is None


def test_woerterbuch_mit_zwei_bestaetigungen_erkennt_die_tabelle():
    doc = _doc_mit_unbekanntem_kopfwort()
    buch = {"art-bez": {"beschreibung": 2}}
    items = tabelle.extract(doc, text="", woerterbuch=buch)
    assert items is not None
    assert len(items) == 2
    assert {it["beschreibung"] for it in items} == {"Schraube", "Mutter"}
    assert round(sum(it["gesamt_netto"] for it in items), 2) == 40.0


def test_woerterbuch_bei_widerspruch_bleibt_unentschieden():
    doc = _doc_mit_unbekanntem_kopfwort()
    # "menge" genauso oft bestaetigt wie "beschreibung" -- Widerspruch,
    # das Wort darf nicht entscheiden.
    buch = {"art-bez": {"beschreibung": 2, "menge": 2}}
    assert tabelle.extract(doc, text="", woerterbuch=buch) is None


def test_markiere_tabelle_guess_nutzt_woerterbuch():
    doc = _doc_mit_unbekanntem_kopfwort()
    box = {"page": 0, "x0": 0.0, "top": 95.0, "x1": 600.0, "bottom": 160.0}
    grid_ohne = tabelle.markiere_tabelle(doc, [box])
    assert None in {c["guess"] for c in grid_ohne["columns"]}
    buch = {"art-bez": {"beschreibung": 2}}
    grid_mit = tabelle.markiere_tabelle(doc, [box], woerterbuch=buch)
    assert "beschreibung" in {c["guess"] for c in grid_mit["columns"]}
