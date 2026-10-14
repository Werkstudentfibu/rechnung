"""SPEC Baustein 3 (Aaron 12.10.2026): "Wenn das Programm einen Wert als Teil
eines Artikels erkennt, stehen sehr wahrscheinlich links und rechts davon in
der gleichen Zeile die restlichen Informationen zu diesem Artikel." --
markiert der Mensch per Rahmen EINE Zelle, schlaegt ``tabelle.zeilen_vorschlag``
die uebrigen Spalten derselben Zeile vor; ``zeilen_vorschlag_zulaessig`` ist
das Baustein-4-Gate ("nur uebernehmen, wenn die Zeilenpruefung aufgeht")."""

from __future__ import annotations

import pytest

from rechnung import tabelle
from rechnung.pdf import Document, Page, Word


def _row(text_menge="2", text_preis="10,00", text_gesamt="20,00", text_mwst="19%",
        text_waehrung="EUR") -> list[Word]:
    return [
        Word("Ersatzteile", 20, 60, 100, 110, 0),
        Word("X200", 61, 90, 100, 110, 0),
        Word(text_menge, 150, 160, 100, 110, 0),
        Word(text_preis, 200, 230, 100, 110, 0),
        Word(text_gesamt, 260, 290, 100, 110, 0),
        Word(text_mwst, 320, 340, 100, 110, 0),
        Word(text_waehrung, 360, 390, 100, 110, 0),
    ]


def _doc(words: list[Word]) -> Document:
    return Document(path="x", pages=[Page(0, 600, 800, words)], sha256="x")


def _idx(words: list[Word], text: str) -> list[int]:
    return [i for i, w in enumerate(words) if w.text == text]


def test_vorschlag_ausgehend_von_gesamtpreis():
    words = _row()
    doc = _doc(words)
    v = tabelle.zeilen_vorschlag(doc, 0, _idx(words, "20,00"), "gesamt_netto", {})
    assert v == {"beschreibung": "Ersatzteile X200", "menge": "2",
                "einzelpreis_netto": "10,00", "mwst_satz": "19%", "waehrung": "EUR"}


def test_vorschlag_ausgehend_von_beschreibung():
    words = _row()
    doc = _doc(words)
    idx = _idx(words, "Ersatzteile") + _idx(words, "X200")
    v = tabelle.zeilen_vorschlag(doc, 0, idx, "beschreibung", {})
    assert v["menge"] == "2"
    assert v["einzelpreis_netto"] == "10,00"
    assert v["gesamt_netto"] == "20,00"
    assert v["mwst_satz"] == "19%"
    assert v["waehrung"] == "EUR"


def test_bereits_bekannte_spalten_werden_nicht_vorgeschlagen():
    words = _row()
    doc = _doc(words)
    v = tabelle.zeilen_vorschlag(doc, 0, _idx(words, "20,00"), "gesamt_netto",
                                 {"waehrung": "EUR"})
    assert "waehrung" not in v
    assert "menge" in v


def test_andere_zeile_wird_nicht_mitgenommen():
    zeile1 = _row()
    zeile2 = [Word("Montagezubehoer", 20, 90, 200, 210, 0),
             Word("99,00", 260, 290, 200, 210, 0)]
    doc = _doc(zeile1 + zeile2)
    v = tabelle.zeilen_vorschlag(doc, 0, _idx(zeile1, "20,00"), "gesamt_netto", {})
    assert v.get("beschreibung") == "Ersatzteile X200"
    assert "Montagezubehoer" not in (v.get("beschreibung") or "")


def test_zulaessig_lehnt_widerspruechlichen_vorschlag_ab():
    # Menge 3 * Einzelpreis 10 = 30, aber die bestaetigte Zelle sagt 20 ->
    # die Zeilenpruefung widerspricht, der Vorschlag darf nicht gelten.
    vorschlag = {"menge": "3", "einzelpreis_netto": "10,00"}
    assert tabelle.zeilen_vorschlag_zulaessig(vorschlag, {}, "gesamt_netto", 20.0) is False


def test_zulaessig_erlaubt_passenden_vorschlag():
    vorschlag = {"menge": "2", "einzelpreis_netto": "10,00"}
    assert tabelle.zeilen_vorschlag_zulaessig(vorschlag, {}, "gesamt_netto", 20.0) is True


def test_zulaessig_erlaubt_nicht_pruefbaren_vorschlag():
    # Ohne Menge/Einzelpreis ist die Zeile nicht pruefbar (Pauschale) ->
    # ``None`` statt ``False``, gilt als zulaessig.
    assert tabelle.zeilen_vorschlag_zulaessig({"waehrung": "EUR"}, {}, "beschreibung",
                                              "Versand") is True


def test_unbekannte_zielspalte_liefert_nichts():
    words = _row()
    doc = _doc(words)
    assert tabelle.zeilen_vorschlag(doc, 0, _idx(words, "20,00"), "unbekannt", {}) == {}


# ---- API: /api/tabelle/zeile_vorschlag -----------------------------------

def test_api_zeile_vorschlag(tmp_path):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung import synth
    from rechnung.pdf import load
    from rechnung.store import Store
    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    client = TestClient(server_mod.create_app(store, samples))

    rows = [{"beschreibung": "Ersatzteile", "menge": 2, "einzelpreis": 100.0, "mwst": 19,
             "waehrung": "EUR"}]
    p = synth.make_invoice(samples / "a.pdf", items=rows, waehrung="EUR")
    doc = load(p)
    # Gesamtpreis (200,00, eindeutig von Einzelpreis 100,00 unterscheidbar)
    # der Position als bestaetigte Zelle nehmen.
    gesamt_word = next(w for w in doc.pages[0].words if w.text == "200,00")
    indices = [i for i, w in enumerate(doc.pages[0].words) if w is gesamt_word]

    r = client.post("/api/tabelle/zeile_vorschlag", json={
        "file": "a.pdf", "page": 0, "indices": indices, "ziel_spalte": "gesamt_netto",
        "wert": 200.0, "zeile": {},
    }).json()
    assert r["zulaessig"] is True
    assert r["vorschlag"]["menge"] == "2"
    assert "Ersatzteile" in r["vorschlag"]["beschreibung"]
