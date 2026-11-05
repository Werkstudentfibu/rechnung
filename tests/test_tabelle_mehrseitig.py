"""Aaron 05.11.2026: "Bei der Tabellenanlegung sollte es auch gehen, wenn man
sie ueber mehrere Seiten anlegt. Also fuer jede Seite ein Kasten, aber eine
Rechnung." -- End-to-End mit synthetischen 2-3-seitigen Rechnungen
(``synth.make_multipage_table_invoice``): Rahmen je Seite -> EINE
Positionsliste in Seitenreihenfolge, Kopf nur auf Seite 1, Zwischen-/
Uebertragssummen und "Seite k von n" nie als Position, Spaltenzuordnung von
Seite 1 gilt auch bei leicht verschobenen Folgeseiten."""

from __future__ import annotations

import pytest

from rechnung import synth, tabelle
from rechnung.pdf import load


def _rahmen(doc, mit_kopf_seite1=True, mit_uebertrag=False, praefix="Artikel"):
    """Je Seite ein Rahmen um die Positionszeilen (Seite 1: ab Kopfzeile)."""
    boxes = []
    for i, pg in enumerate(doc.pages):
        art = [w for w in pg.words if w.text.startswith(praefix)]
        if not art:
            continue
        top = min(w.top for w in art)
        if i == 0 and mit_kopf_seite1:
            kopf = next(w for w in pg.words if w.text.strip(":") == "Menge")
            top = kopf.top
        if i > 0 and mit_uebertrag:
            ue = [w for w in pg.words if w.text.startswith(("Uebertrag", "Zwischensumme"))
                  and w.top < top]
            if ue:
                top = min(w.top for w in ue)
        bottom = max(w.bottom for w in art)
        boxes.append({"page": i, "x0": 0.0, "top": top - 4.0, "x1": pg.width,
                      "bottom": bottom + 4.0})
    return boxes


def _namen(items):
    return [it["beschreibung"] for it in items]


def _soll_namen(soll):
    return [p["beschreibung"] for p in soll["positionen"]]


def _lesen(tmp_path, boxes_fn=_rahmen, **kw):
    soll = synth.make_multipage_table_invoice(tmp_path / "m.pdf", **kw)
    doc = load(soll["path"])
    grid = tabelle.markiere_tabelle(doc, boxes_fn(doc))
    return soll, doc, tabelle.anwenden_markierung(grid)


def test_drei_seiten_kopf_wiederholt(tmp_path):
    soll, _doc, items = _lesen(tmp_path, seiten=3, positionen_je_seite=4)
    assert _namen(items) == _soll_namen(soll)
    assert round(sum(i["gesamt_netto"] for i in items), 2) == soll["netto"]


def test_kopf_nur_auf_seite_1_mit_seitenkopf(tmp_path):
    soll, _doc, items = _lesen(tmp_path, seiten=3, positionen_je_seite=[5, 4, 3],
                               kopf_folgeseiten=False, seitenkopf=True)
    assert _namen(items) == _soll_namen(soll)
    assert round(sum(i["gesamt_netto"] for i in items), 2) == soll["netto"]


@pytest.mark.parametrize("zwischensumme", [False, True])
def test_uebertrag_und_zwischensumme_keine_position(tmp_path, zwischensumme):
    soll, _doc, items = _lesen(
        tmp_path, boxes_fn=lambda d: _rahmen(d, mit_uebertrag=True),
        seiten=3, positionen_je_seite=4, kopf_folgeseiten=False,
        uebertrag_oben=True, zwischensumme=zwischensumme)
    assert _namen(items) == _soll_namen(soll)


def test_rahmen_in_beliebiger_reihenfolge(tmp_path):
    soll = synth.make_multipage_table_invoice(tmp_path / "m.pdf", seiten=3,
                                              kopf_folgeseiten=False)
    doc = load(soll["path"])
    boxes = _rahmen(doc)
    boxes = [boxes[2], boxes[0], boxes[1]]   # Seite 3 zuerst gezogen
    items = tabelle.anwenden_markierung(tabelle.markiere_tabelle(doc, boxes))
    assert _namen(items) == _soll_namen(soll)


def test_verschobene_spalten_auf_folgeseiten(tmp_path):
    soll, _doc, items = _lesen(tmp_path, seiten=3, positionen_je_seite=4,
                               kopf_folgeseiten=False, versatz_folgeseiten_mm=9.0)
    assert _namen(items) == _soll_namen(soll)
    assert [i["gesamt_netto"] for i in items] == [p["gesamtpreis"] for p in soll["positionen"]]
    assert [i["einzelpreis_netto"] for i in items] == [p["einzelpreis"] for p in soll["positionen"]]


def test_automatische_erkennung_ohne_wiederholten_kopf(tmp_path):
    soll = synth.make_multipage_table_invoice(tmp_path / "m.pdf", seiten=3,
                                              kopf_folgeseiten=False, seitenkopf=True)
    doc = load(soll["path"])
    items = tabelle.extract(doc, netto=soll["netto"], text=doc.text)
    assert items is not None
    assert _namen(items) == _soll_namen(soll)


def test_beschreibung_mit_net_am_anfang_beendet_tabelle_nicht(tmp_path):
    soll = synth.make_multipage_table_invoice(tmp_path / "m.pdf", seiten=2,
                                              beschreibung_praefix="Netzwerkkabel")
    doc = load(soll["path"])
    items = tabelle.extract(doc, netto=soll["netto"], text=doc.text)
    assert items is not None and len(items) == 8


# ---- Lernen: das mehrseitige Muster wird gespeichert und wieder angewendet --

def _bestaetigt(soll):
    return [{"beschreibung": p["beschreibung"], "menge": p["menge"],
             "einzelpreis_netto": p["einzelpreis"], "gesamt_netto": p["gesamtpreis"],
             "mwst_satz": p["mwst"]} for p in soll["positionen"]]


@pytest.mark.parametrize("kopf_folge", [True, False])
def test_mehrseitige_vorlage_wird_gelernt_und_auf_andere_seitenzahl_angewendet(tmp_path, kopf_folge):
    from rechnung import learn
    from rechnung.template import LineItemSpec, apply_line_items

    kw = dict(kopf_folgeseiten=kopf_folge, seitenkopf=True, uebertrag_oben=not kopf_folge)
    soll3 = synth.make_multipage_table_invoice(tmp_path / "a.pdf", seiten=3,
                                               positionen_je_seite=[5, 4, 3], **kw)
    spec = learn.learn_line_items(load(soll3["path"]), _bestaetigt(soll3))
    assert spec is not None, "mehrseitige Positionen muessen lernbar sein"
    # Round-Trip ueber die Vorlagen-Speicherung
    spec = LineItemSpec.from_dict(spec.to_dict())

    # andere Rechnung desselben Lieferanten: 2 Seiten, andere Zeilenzahl
    soll2 = synth.make_multipage_table_invoice(tmp_path / "b.pdf", seiten=2,
                                               positionen_je_seite=[6, 5],
                                               rechnungsnr="RE-2", **kw)
    got = apply_line_items(load(soll2["path"]), spec)
    assert [g["beschreibung"] for g in got] == _soll_namen(soll2)
    assert [g["gesamt_netto"] for g in got] == [p["gesamtpreis"] for p in soll2["positionen"]]

    # und 4 Seiten
    soll4 = synth.make_multipage_table_invoice(tmp_path / "c.pdf", seiten=4,
                                               positionen_je_seite=[3, 3, 3, 2],
                                               rechnungsnr="RE-4", **kw)
    got = apply_line_items(load(soll4["path"]), spec)
    assert [g["beschreibung"] for g in got] == _soll_namen(soll4)


def test_einseitige_vorlage_bleibt_einseitig(tmp_path):
    from rechnung import learn
    from rechnung.template import apply_line_items

    soll = synth.make_multipage_table_invoice(tmp_path / "a.pdf", seiten=1, positionen_je_seite=4)
    spec = learn.learn_line_items(load(soll["path"]), _bestaetigt(soll))
    assert spec is not None
    got = apply_line_items(load(soll["path"]), spec)
    assert len(got) == 4
