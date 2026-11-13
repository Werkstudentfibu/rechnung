"""Raster im Tabellenrahmen (Wunsch Aaron 12.11.2026): das Programm schlaegt
Spalten- und Zeilengrenzen vor, der Mensch verschiebt/ergaenzt/entfernt sie,
die Tabelle wird nach dem Raster gelesen."""

from __future__ import annotations

import pytest

from rechnung import synth, tabelle
from rechnung.pdf import load


def _box(doc):
    words = doc.pages[0].words
    kopf = next(w for w in words if w.text.strip(":") == "Menge")
    unten = max(w.bottom for w in words if kopf.top <= w.top <= kopf.top + 120)
    return {"page": 0, "x0": 0.0, "top": kopf.top - 4, "x1": doc.pages[0].width, "bottom": unten + 4}


@pytest.fixture
def rechnung(tmp_path):
    doc = load(synth.make_invoice(tmp_path / "a.pdf", netto=1000.0, satz=19))
    return doc, _box(doc)


def test_vorschlag_hat_spalten_und_eine_zeile_je_position(rechnung):
    doc, box = rechnung
    grid = tabelle.markiere_tabelle(doc, [box])
    raster = tabelle.raster_vorschlag(doc, [box], grid=grid)
    assert len(raster["spalten"]) == len(grid["columns"]) - 1
    assert raster["kopf"]["0"] is True
    # Kopfzeile + 3 Positionen + Summenzeile (im Rahmen) = 5 Baender = 4 Grenzen
    assert len(raster["zeilen"]["0"]) == 4


def test_lesen_nach_vorschlag_entspricht_automatik(rechnung):
    doc, box = rechnung
    grid = tabelle.markiere_tabelle(doc, [box])
    raster = tabelle.raster_vorschlag(doc, [box], grid=grid)
    nach_raster = tabelle.raster_lesen(doc, [box], raster)
    assert nach_raster["rows"] == grid["rows"]
    assert [c["guess"] for c in nach_raster["columns"]] == [c["guess"] for c in grid["columns"]]
    items = tabelle.anwenden_markierung(nach_raster)
    assert round(sum(it["gesamt_netto"] for it in items), 2) == 1000.0


def test_spaltengrenze_entfernen_legt_spalten_zusammen(rechnung):
    doc, box = rechnung
    raster = tabelle.raster_vorschlag(doc, [box])
    vorher = tabelle.raster_lesen(doc, [box], raster)
    weniger = dict(raster, spalten=raster["spalten"][:-1])     # letzte Grenze weg
    nachher = tabelle.raster_lesen(doc, [box], weniger)
    assert len(nachher["columns"]) == len(vorher["columns"]) - 1
    assert nachher["rows"][0][-1] == (vorher["rows"][0][-2] + " " + vorher["rows"][0][-1]).strip()


def test_zeilengrenze_entfernen_legt_positionen_zusammen(rechnung):
    doc, box = rechnung
    raster = tabelle.raster_vorschlag(doc, [box])
    z = raster["zeilen"]["0"]
    zusammen = dict(raster, zeilen={"0": [z[0], z[1], z[3]]})  # Grenze zwischen Pos. 2 und 3 weg
    rows = tabelle.raster_lesen(doc, [box], zusammen)["rows"]
    assert len(rows) == 2
    assert rows[1][0] == "Montagezubehoer Versand- und Verpackungskosten"


def test_summenzeile_im_letzten_band_wird_abgeschnitten(rechnung):
    doc, box = rechnung
    raster = tabelle.raster_vorschlag(doc, [box])
    ohne_letzte = dict(raster, zeilen={"0": raster["zeilen"]["0"][:3]})
    rows = tabelle.raster_lesen(doc, [box], ohne_letzte)["rows"]
    assert len(rows) == 3 and not any("Nettobetrag" in c for r in rows for c in r)


def test_spaltengrenze_verschieben_aendert_zuordnung(rechnung):
    doc, box = rechnung
    raster = tabelle.raster_vorschlag(doc, [box])
    vorher = tabelle.raster_lesen(doc, [box], raster)["rows"]
    # erste Spaltengrenze ganz nach links: erste Spalte wird leer
    verschoben = dict(raster, spalten=[0.01] + raster["spalten"][1:])
    nachher = tabelle.raster_lesen(doc, [box], verschoben)["rows"]
    assert all(r[0] == "" for r in nachher)
    assert nachher != vorher


def test_kopfzeile_aus_macht_kopf_zur_zeile(rechnung):
    doc, box = rechnung
    raster = tabelle.raster_vorschlag(doc, [box])
    ohne_kopf = dict(raster, kopf={"0": False})
    rows = tabelle.raster_lesen(doc, [box], ohne_kopf)["rows"]
    assert len(rows) == 4 and any("Menge" in c for c in rows[0])


def test_mapping_none_nimmt_vorschlag(rechnung):
    doc, box = rechnung
    grid = tabelle.markiere_tabelle(doc, [box])
    mit_none = tabelle.anwenden_markierung(grid, [None] * len(grid["columns"]))
    assert mit_none == tabelle.anwenden_markierung(grid)


def test_api_liefert_raster_und_liest_danach(tmp_path):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.store import Store
    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    doc = load(synth.make_invoice(samples / "a.pdf", netto=1000.0, satz=19))
    box = _box(doc)
    client = TestClient(server_mod.create_app(store, samples))
    r = client.post("/api/tabelle/markieren", json={"file": "a.pdf", "boxes": [box]}).json()
    assert r["raster"]["spalten"] and len(r["raster"]["zeilen"]["0"]) == 4
    assert len(r["items"]) == 3
    z = r["raster"]["zeilen"]["0"]
    raster = dict(r["raster"], zeilen={"0": [z[0], z[1], z[3]]})
    r2 = client.post("/api/tabelle/markieren", json={"file": "a.pdf", "boxes": [box], "raster": raster}).json()
    assert len(r2["rows"]) == 2 and r2["raster"]["zeilen"]["0"] == raster["zeilen"]["0"]


@pytest.mark.parametrize("kw, uebertrag", [
    (dict(seiten=3, positionen_je_seite=4), False),
    (dict(seiten=3, positionen_je_seite=[5, 4, 3], kopf_folgeseiten=False, seitenkopf=True), False),
    (dict(seiten=3, positionen_je_seite=4, kopf_folgeseiten=False, uebertrag_oben=True), True),
    (dict(seiten=3, positionen_je_seite=4, kopf_folgeseiten=False, uebertrag_oben=True,
          zwischensumme=True), True),
])
def test_mehrseitig_raster_liest_wie_automatik(tmp_path, kw, uebertrag):
    from tests.test_tabelle_mehrseitig import _rahmen

    soll = synth.make_multipage_table_invoice(tmp_path / "m.pdf", **kw)
    doc = load(soll["path"])
    boxes = _rahmen(doc, mit_uebertrag=uebertrag)
    raster = tabelle.raster_vorschlag(doc, boxes)
    assert set(raster["zeilen"]) == {str(b["page"]) for b in boxes}
    items = tabelle.anwenden_markierung(tabelle.raster_lesen(doc, boxes, raster))
    assert [i["beschreibung"] for i in items] == [p["beschreibung"] for p in soll["positionen"]]
    assert round(sum(i["gesamt_netto"] for i in items), 2) == soll["netto"]


def test_raster_bedienung_im_html():
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent / "rechnung" / "web" / "static" / "index.html").read_text("utf-8")
    for teil in ("function paintRaster(", "function rasterZiehen(", "function rasterSpalteHinzu(",
                 "function rasterZeileHinzu(", "function rasterSpalteWeg(", "function rasterZeileWeg(",
                 "raster: STATE.tabRahmen.raster", "Raster stimmt", "rasterKopfzeile("):
        assert teil in html, teil


def _wort(text, x0, top, h=7.0):
    from rechnung.pdf import Word

    return Word(text=text, x0=x0, x1=x0 + 6.0 * len(text), top=top, bottom=top + h, page=0)


def _doc_mit(woerter, breite=842.0, hoehe=595.0):
    from rechnung.pdf import Document, Page

    return Document(path=None, pages=[Page(index=0, width=breite, height=hoehe, words=woerter)], sha256="x")


def test_mehrzeiliger_kopf_ist_ein_band_und_zellen_in_lesereihenfolge():
    """Nachbau Tankrechnung (12.11.2026): Kopf ueber drei Zeilen, Betraege
    einer Zeile mit leicht verschiedener Hoehe, Brutto-Spalte rechts."""
    w = [
        _wort("Bezeichnung", 60, 176), _wort("Menge", 300, 176), _wort("Betrag", 420, 176), _wort("Betrag", 560, 176),
        _wort("Netto", 420, 183), _wort("Brutto", 560, 183),
        _wort("EUR", 420, 190), _wort("EUR", 560, 190),
        _wort("Diesel", 60, 206), _wort("140,94", 300, 206.4), _wort("209,91", 420, 206.6), _wort("249,79", 560, 205.9),
        _wort("Super", 60, 218), _wort("436,24", 300, 218.3), _wort("661,28", 420, 217.8), _wort("786,91", 560, 218.2),
        _wort("Gesamtbetrag", 60, 232), _wort("871,19", 420, 232), _wort("1036,70", 560, 232),
    ]
    doc = _doc_mit(w)
    box = {"page": 0, "x0": 40, "top": 172, "x1": 800, "bottom": 245}
    raster = tabelle.raster_vorschlag(doc, [box])
    assert raster["kopf"]["0"] is True
    # Kopf (3 Zeilen) | Diesel | Super | Gesamtbetrag = 4 Baender
    assert len(raster["zeilen"]["0"]) == 3
    g = tabelle.raster_lesen(doc, [box], raster)
    assert len(g["rows"]) == 2
    assert [c["header_text"] for c in g["columns"]][-2:] == ["Betrag Netto EUR", "Betrag Brutto EUR"]
    guesses = [c["guess"] for c in g["columns"]]
    assert guesses.count("gesamt_netto") == 1 and guesses[-1] is None
    items = tabelle.anwenden_markierung(g)
    assert [i["beschreibung"] for i in items] == ["Diesel", "Super"]
    assert [i["gesamt_netto"] for i in items] == [209.91, 661.28]


def test_rahmen_ohne_kopf_erste_zeile_ist_position():
    w = [_wort("Diesel", 60, 206), _wort("209,91", 420, 206),
         _wort("Super", 60, 218), _wort("661,28", 420, 218)]
    doc = _doc_mit(w)
    box = {"page": 0, "x0": 40, "top": 200, "x1": 800, "bottom": 230}
    raster = tabelle.raster_vorschlag(doc, [box])
    assert raster["kopf"]["0"] is False
    assert len(tabelle.raster_lesen(doc, [box], raster)["rows"]) == 2


def test_feld_hat_vorrang_vor_tabellenrahmen():
    """Aaron 13.11.2026: mit offenem 'Tabelle im PDF markieren' landete ein
    Rahmen fuer ein Feld in der Tabelle. Jetzt: gewaehltes Feld hat Vorrang."""
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent / "rechnung" / "web" / "static" / "index.html").read_text("utf-8")
    assert "if (STATE.tabRahmen.active && !feldZiel()) { tabRahmenBoxGezogen(wrap, page, r); return; }" in html
    assert "else feldZielLoesen();" in html and "function rahmenFuerTabelle()" in html
    assert "Nächster Rahmen für die Tabelle" in html
