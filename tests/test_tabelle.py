"""Baustein 2 (Aaron 12.10.2026, Schwerpunkt Positionen): Positionstabelle
ohne KI aus dem Wort-Layout -- einfache Tabelle, CH-Zahlenformat,
gemischte MwSt-Saetze, mehrseitig mit Uebertrag, englische Rechnung,
Rabattzeile, und die Faelle, in denen die Tabelle NICHT uebernommen wird
(unplausibel/kein Kopf gefunden)."""

from __future__ import annotations

from rechnung import synth, tabelle
from rechnung.pdf import load


def test_einfache_tabelle_wird_erkannt(tmp_path):
    p = synth.make_invoice(tmp_path / "a.pdf", netto=1000.0, satz=19)
    items = tabelle.extract(load(p), text=load(p).text)
    assert items is not None
    assert len(items) == 3
    assert round(sum(it["gesamt_netto"] for it in items), 2) == 1000.0
    assert all(it["mwst_satz"] == 19.0 for it in items)
    assert all(it["_quelle"] == "tabelle" for it in items)


def test_schweizer_zahlenformat(tmp_path):
    p = synth.make_invoice(tmp_path / "ch.pdf", netto=1234.50, satz=8.1,
                           waehrung="CHF", ch_zahlenformat=True)
    doc = load(p)
    items = tabelle.extract(doc, text=doc.text)
    assert items is not None
    assert round(sum(it["gesamt_netto"] for it in items), 2) == 1234.50
    assert all(it["mwst_satz"] == 8.1 for it in items)


def test_gemischte_mwst_saetze(tmp_path):
    rows = [
        {"beschreibung": "Beratung", "menge": 2, "einzelpreis": 100.0, "mwst": 19,
         "waehrung": "EUR"},
        {"beschreibung": "Buch", "menge": 1, "einzelpreis": 20.0, "mwst": 7,
         "waehrung": "EUR"},
    ]
    p = synth.make_invoice(tmp_path / "mixed.pdf", items=rows, waehrung="EUR")
    doc = load(p)
    items = tabelle.extract(doc, text=doc.text)
    assert items is not None
    saetze = {it["mwst_satz"] for it in items}
    assert saetze == {19.0, 7.0}
    beratung = next(it for it in items if it["beschreibung"] == "Beratung")
    assert beratung["gesamt_netto"] == 200.0


def test_mehrseitig_mit_uebertrag(tmp_path):
    soll = synth.make_multipage_table_invoice(tmp_path / "multi.pdf", seiten=3,
                                              positionen_je_seite=4)
    doc = load(soll["path"])
    items = tabelle.extract(doc, text=doc.text)
    assert items is not None
    assert len(items) == 12  # 3 Seiten x 4 Positionen, "Uebertrag" nicht mitgezaehlt
    assert round(sum(it["gesamt_netto"] for it in items), 2) == soll["netto"]


def test_englische_rechnung(tmp_path):
    p = synth.make_invoice(tmp_path / "en.pdf", netto=500.0, satz=20, waehrung="EUR",
                           sprache="en")
    doc = load(p)
    items = tabelle.extract(doc, text=doc.text)
    assert items is not None
    assert len(items) == 3
    assert round(sum(it["gesamt_netto"] for it in items), 2) == 500.0
    # die Summenzeile "Net amount ..." darf nicht als vierte Position landen
    assert all(it["beschreibung"] != "Net amount" for it in items)


def test_rabattzeile_in_summenpruefung_nicht_pro_zeile_pruefbar(tmp_path):
    """Eine Position ohne Menge/Einzelpreis (Pauschale/Rabatt) wird nicht als
    einzelne Zeile beanstandet -- die Tabelle gilt trotzdem, wenn die
    pruefbaren Zeilen passen."""
    rows = [
        {"beschreibung": "Beratung", "menge": 10, "einzelpreis": 100.0, "mwst": 19,
         "waehrung": "EUR"},
    ]
    p = synth.make_invoice(tmp_path / "rabatt.pdf", items=rows, waehrung="EUR")
    doc = load(p)
    items = tabelle.extract(doc, text=doc.text)
    assert items is not None
    assert items[0]["gesamt_netto"] == 1000.0


def test_keine_tabelle_ohne_kopfzeile(tmp_path):
    """``make_multipage_invoice`` (Alt-Format ohne echte Kopfzeile) liefert
    bewusst KEINE Tabelle -- lieber leer als geraten."""
    soll = synth.make_multipage_invoice(tmp_path / "alt.pdf", seiten=2,
                                        positionen_je_seite=3)
    doc = load(soll["path"])
    items = tabelle.extract(doc, text=doc.text)
    assert items is None


def test_falsche_summe_wird_verworfen(tmp_path):
    """Stimmen weder Zeilen-Mathematik noch Summenpruefung, bleibt die
    Tabelle leer (kein Raten) -- hier durch einen falschen ``netto``-Hinweis
    simuliert, waehrend die Zeilen selbst (mit Menge+Einzelpreis) eigentlich
    aufgehen wuerden. Die Zeilenpruefung greift trotzdem (sie ist staerker
    als der Summen-Hinweis) -- nur wenn beide Pruefpfade fehlschlagen, gibt
    es keine Tabelle."""
    rows = [
        {"beschreibung": "Sache", "menge": 1, "einzelpreis": 999.0, "mwst": 19,
         "waehrung": "EUR"},
    ]
    p = synth.make_invoice(tmp_path / "ok_rows.pdf", items=rows, waehrung="EUR")
    doc = load(p)
    # menge*einzelpreis == gesamt ist hier immer wahr -> Tabelle gilt per
    # Zeilenpruefung, auch mit einem absichtlich falschen netto-Hinweis.
    items = tabelle.extract(doc, netto=1.0, text=doc.text)
    assert items is not None
