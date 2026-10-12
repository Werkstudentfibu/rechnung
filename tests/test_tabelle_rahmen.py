"""Baustein 2 (Aaron 12.10.2026): Tabelle im PDF markieren -- der Mensch
zieht einen Rahmen um die Tabelle (Kopfzeile bis letzte Position), das
Programm erkennt darin Spalten ueber die Wortluecken (``markiere_tabelle``)
und ordnet sie per Kopfwort den LI_STD-Spalten zu (Vorschlag, per Dropdown
aenderbar: ``anwenden_markierung``)."""

from __future__ import annotations

from rechnung import synth, tabelle
from rechnung.pdf import load


def _box_um_tabelle(doc, einschluss_text="Menge", padding=4.0):
    """Rahmen (PDF-Punkte) um die ganze Tabelle: von knapp oberhalb der
    Kopfzeile (identifiziert ueber ein bekanntes Kopfwort) bis zum Ende der
    Positionszeilen -- in den Tests ueber die Woerter selbst bestimmt statt
    die mm-Koordinaten aus synth.py nachzurechnen."""
    words = doc.pages[0].words
    header_word = next(w for w in words if w.text.strip(":") == einschluss_text)
    # Alle Woerter auf/unterhalb der Kopfzeile bis zur naechsten Leerzeile
    # (hier: alles bis 60pt unterhalb -- synth.make_invoice hat wenige
    # Positionen, die Tabelle ist kurz) gehoeren grosszuegig zur Tabelle.
    top = header_word.top - padding
    unten = max(w.bottom for w in words if w.top >= header_word.top and w.top <= header_word.top + 120)
    return {"page": 0, "x0": 0.0, "top": top, "x1": doc.pages[0].width, "bottom": unten + padding}


def test_markiere_tabelle_erkennt_spalten_und_zeilen(tmp_path):
    p = synth.make_invoice(tmp_path / "a.pdf", netto=1000.0, satz=19)
    doc = load(p)
    box = _box_um_tabelle(doc)
    grid = tabelle.markiere_tabelle(doc, [box])
    assert len(grid["rows"]) == 3   # 3 Standard-Positionen
    namen = {c["guess"] for c in grid["columns"]}
    assert {"beschreibung", "menge", "einzelpreis_netto", "gesamt_netto", "mwst_satz",
           "waehrung"} <= namen


def test_anwenden_markierung_liefert_positionen_wie_automatische_erkennung(tmp_path):
    p = synth.make_invoice(tmp_path / "a.pdf", netto=1000.0, satz=19)
    doc = load(p)
    box = _box_um_tabelle(doc)
    grid = tabelle.markiere_tabelle(doc, [box])
    items = tabelle.anwenden_markierung(grid)
    auto = tabelle.extract(doc, text=doc.text)
    assert len(items) == len(auto) == 3
    assert round(sum(it["gesamt_netto"] for it in items), 2) == 1000.0
    assert all(it["mwst_satz"] == 19.0 for it in items)
    assert all(it["beschreibung"] for it in items)


def test_mapping_ueberschreibt_den_vorschlag(tmp_path):
    p = synth.make_invoice(tmp_path / "a.pdf", netto=1000.0, satz=19)
    doc = load(p)
    box = _box_um_tabelle(doc)
    grid = tabelle.markiere_tabelle(doc, [box])
    # Waehrungsspalte bewusst ignorieren
    mapping = [("ignorieren" if c["guess"] == "waehrung" else c["guess"]) for c in grid["columns"]]
    items = tabelle.anwenden_markierung(grid, mapping)
    assert all("waehrung" not in it for it in items)


def test_mehrseitige_markierung_ueber_zwei_rahmen(tmp_path):
    soll = synth.make_multipage_table_invoice(tmp_path / "multi.pdf", seiten=2,
                                              positionen_je_seite=3)
    doc = load(soll["path"])
    boxes = []
    for pg in doc.pages:
        words = pg.words
        header = next((w for w in words if w.text.strip(":") == "Menge"), None)
        if header is None:
            continue
        top = header.top - 4.0
        unten = max(w.bottom for w in words if w.top >= header.top)
        boxes.append({"page": header.page, "x0": 0.0, "top": top, "x1": pg.width, "bottom": unten + 4.0})
    assert len(boxes) == 2
    grid = tabelle.markiere_tabelle(doc, boxes)
    items = tabelle.anwenden_markierung(grid)
    assert len(items) == 6
    assert round(sum(it["gesamt_netto"] for it in items), 2) == soll["netto"]
