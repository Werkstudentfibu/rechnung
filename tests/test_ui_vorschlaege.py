"""Postfach-Baustein 11b: Programm schlaegt vor, Mensch korrigiert -- auch
Positionstabelle und USt-Aufstellung.

- Vorschlaege werden im PDF umrahmt (``learn.markierungen``), mehrdeutige
  Werte bekommen keinen Rahmen.
- Positionstabelle/USt-Aufstellung sind als Ganzes korrigierbar
  (``/api/tabelle``), die Korrektur gewinnt in der Pipeline wie jede andere.
- Rechen-Pruefungen aus Baustein 9 live je Zeile (``/api/pruefen``).
- Bestaetigen nach einer Tabellen-Korrektur lernt die Tabellen-Spec (11a),
  die naechste Rechnung desselben Layouts wird damit korrekt gelesen."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import learn
from rechnung import pdf as pdfmod
from rechnung.identify import Supplier
from rechnung.pipeline import POSITIONEN_KEY, _apply_corrections
from rechnung.store import Store
from rechnung.template import FieldResult
from rechnung.validate import line_item_row, rechen_pruefungen, tax_row
from rechnung.web import server as server_mod

pytest.importorskip("reportlab")
pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402

from rechnung.synth import make_invoice  # noqa: E402

_ITEMS_PDF = [
    {"beschreibung": "Wartung Server", "menge": 2, "einzelpreis": 150.0,
     "gesamtpreis": 300.0, "mwst": 19, "waehrung": "EUR"},
    {"beschreibung": "Schulung Grundlagen", "menge": 1, "einzelpreis": 200.0,
     "gesamtpreis": 200.0, "mwst": 7, "waehrung": "EUR"},
]


def _client(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="muster", name="Muster Handels GmbH",
                                 ustids=["DE123456789"], ibans=["DE89370400440532013000"]))
    samples = tmp_path / "s"
    samples.mkdir()
    return TestClient(server_mod.create_app(store, samples)), store, samples


# ---- Rechen-Pruefungen je Zeile ------------------------------------------

def test_zeile_rot_gruen():
    assert line_item_row({"menge": "2", "einzelpreis_netto": 150.0, "gesamt_netto": 300.0})[0] is True
    ok, grund = line_item_row({"beschreibung": "Wartung", "menge": "2", "einzelpreis_netto": 150.0,
                               "gesamt_netto": 310.0})
    assert ok is False and "erwartet 300.00" in grund and "310.00" in grund
    # Pauschale: nicht beanstandet
    assert line_item_row({"beschreibung": "Pauschale", "gesamt_netto": 50.0})[0] is None


def test_steuerzeile_rot_gruen():
    by_rate = {19.0: 300.0}
    assert tax_row({"satz": 19, "netto": 300.0, "ust": 57.0}, by_rate)[0] is True
    ok, grund = tax_row({"satz": 19, "netto": 300.0, "ust": 50.0}, by_rate)
    assert ok is False and "57.00" in grund
    ok, grund = tax_row({"satz": 19, "netto": 280.0, "ust": 53.2}, by_rate)
    assert ok is False and "Positionen 300.00" in grund


def test_rechen_pruefungen_live_markiert_genau_die_falsche_zeile():
    fields = {"netto": FieldResult("netto", 500.0, "500,00", 1.0, "manual"),
              "ust_aufstellung": FieldResult("ust_aufstellung",
                                             [{"satz": 19.0, "netto": 300.0, "ust": 57.0},
                                              {"satz": 7.0, "netto": 200.0, "ust": 15.0}],
                                             "", 1.0, "manual")}
    items = [{"menge": "2", "einzelpreis_netto": 150.0, "gesamt_netto": 300.0, "mwst_satz": "19"},
             {"menge": "1", "einzelpreis_netto": 200.0, "gesamt_netto": 210.0, "mwst_satz": "7"}]
    out = rechen_pruefungen(fields, items)
    assert [z["ok"] for z in out["positionen"]] == [True, False]
    assert [z["ok"] for z in out["ust"]] == [True, False]
    names = {c["name"]: c["ok"] for c in out["checks"]}
    assert names["positionen-rechnung"] is False
    assert names["positionen-summe"] is False


# ---- Pipeline: Tabellen-Korrektur gewinnt ---------------------------------

def test_ust_korrektur_als_liste_bleibt_strukturiert():
    fields: dict = {}
    rows = [{"satz": 19.0, "netto": 300.0, "ust": 57.0}]
    _apply_corrections(fields, {"ust_aufstellung": rows, POSITIONEN_KEY: [{"a": 1}]})
    assert fields["ust_aufstellung"].value == rows
    assert fields["ust_aufstellung"].source == "manual"
    # die Positions-Korrektur ist kein Feld der Rechnung
    assert POSITIONEN_KEY not in fields


# ---- Markierungen im PDF --------------------------------------------------

def test_markierungen_rahmen_fuer_felder_und_tabellenzeilen(tmp_path: Path):
    p = make_invoice(tmp_path / "a.pdf", rechnungsnr="RE-2026-00077", items=_ITEMS_PDF)
    doc = pdfmod.load(p)
    current = {
        "rechnungsnummer": {"value": "RE-2026-00077", "source": "llm"},
        "rechnungsdatum": {"value": "2026-02-14", "source": "llm"},
        "iban": {"value": "DE89370400440532013000", "source": "library"},  # Stammdaten
        "netto": {"value": 500.0, "source": "missing"},
    }
    items = [{"beschreibung": "Wartung Server", "einzelpreis": 150.0, "gesamtpreis": 300.0},
             {"beschreibung": "gibt es nicht", "gesamtpreis": 999.0}]
    m = learn.markierungen(doc, current, items, [])
    assert set(m["felder"]) == {"rechnungsnummer", "rechnungsdatum"}
    box = m["felder"]["rechnungsnummer"]
    assert box["page"] == 0 and box["x1"] > box["x0"] and box["bottom"] > box["top"]
    assert m["positionen"][0] is not None
    assert m["positionen"][1] is None          # nicht gefunden -> kein Rahmen


def test_mehrdeutiger_wert_ohne_passendes_label_bekommt_keinen_rahmen(tmp_path: Path):
    # "EUR" steht in jeder Positionszeile und bei jeder Summe
    p = make_invoice(tmp_path / "a.pdf", items=_ITEMS_PDF)
    doc = pdfmod.load(p)
    m = learn.markierungen(doc, {"kostenstelle": {"value": "EUR", "source": "llm"}}, [], [])
    assert "kostenstelle" not in m["felder"]


# ---- API: Tabelle korrigieren, live pruefen, bestaetigen, lernen ----------

def test_invoice_liefert_markierungen_und_live_pruefung(tmp_path: Path):
    client, store, samples = _client(tmp_path)
    make_invoice(samples / "a.pdf", items=_ITEMS_PDF)
    d = client.get("/api/invoice", params={"file": "a.pdf"}).json()
    assert "markierungen" in d and "felder" in d["markierungen"]
    assert "pruefung_live" in d
    assert {"checks", "positionen", "ust"} <= set(d["pruefung_live"])
    assert d["positionen_korrigiert"] is False


def test_pruefen_endpoint_zeigt_falsche_zeile(tmp_path: Path):
    client, store, samples = _client(tmp_path)
    make_invoice(samples / "a.pdf", items=_ITEMS_PDF)
    r = client.post("/api/pruefen", json={
        "file": "a.pdf",
        "fields": {"netto": "500,00", "ust_betrag": "71,00", "brutto": "571,00"},
        "line_items": [{"menge": "2", "einzelpreis": "150,00", "gesamtpreis": "300,00"},
                       {"menge": "1", "einzelpreis": "200,00", "gesamtpreis": "250,00"}],
        "ust_aufstellung": [{"satz": "19", "netto": "300,00", "ust": "57,00"},
                            {"satz": "7", "netto": "200,00", "ust": "14,00"}],
    }).json()
    assert [z["ok"] for z in r["positionen"]] == [True, False]
    assert "erwartet 200.00" in r["positionen"][1]["detail"]
    assert [z["ok"] for z in r["ust"]] == [True, True]
    checks = {c["name"]: c for c in r["checks"]}
    assert checks["summen"]["ok"] is True if "summen" in checks else True
    assert checks["positionen-summe"]["ok"] is False


def test_tabellen_korrektur_wird_gespeichert_protokolliert_und_angezeigt(tmp_path: Path):
    client, store, samples = _client(tmp_path)
    make_invoice(samples / "a.pdf", items=_ITEMS_PDF)
    # "einzelpreis"/"gesamtpreis" sind Aliase -- die Korrektur kommt als
    # Standardnamen zurueck/gespeichert (SPEC Baustein 1, positionen.py).
    rows = [{"beschreibung": "Wartung Server", "menge": "2", "einzelpreis": "150,00",
             "gesamtpreis": "300,00", "_intern": "weg"},
            {"beschreibung": "", "menge": "", "einzelpreis": ""}]   # leere Zeile faellt weg
    r = client.post("/api/tabelle", json={"file": "a.pdf", "art": "positionen", "rows": rows,
                                          "quelle_vorher": "llm"}).json()
    assert r["rows"] == [{"beschreibung": "Wartung Server", "menge": "2",
                          "einzelpreis_netto": 150.0, "gesamt_netto": 300.0}]
    sha = pdfmod.load(samples / "a.pdf").sha256
    assert store.corrections(sha)[POSITIONEN_KEY] == r["rows"]

    client.post("/api/tabelle", json={"file": "a.pdf", "art": "ust",
                                      "rows": [{"satz": "19", "netto": "300,00", "ust": "57,00"}]})
    assert store.corrections(sha)["ust_aufstellung"] == [{"satz": 19.0, "netto": 300.0,
                                                          "ust": 57.0}]

    d = client.get("/api/invoice", params={"file": "a.pdf"}).json()
    # Die Rechenstufe (Baustein 2) ergaenzt die Waehrung aus dem Kopf in jede
    # Zeile (Herkunft "kopf") -- die uebrigen Spalten bleiben wie gespeichert.
    assert {k: v for k, v in d["line_items"][0].items() if k in r["rows"][0]} == r["rows"][0]
    assert d["line_items"][0]["waehrung"] == "EUR"
    assert d["line_items"][0]["_herkunft"]["waehrung"] == "kopf"
    assert d["positionen_korrigiert"] is True
    assert d["current"]["ust_aufstellung"]["value"] == [{"satz": 19.0, "netto": 300.0,
                                                         "ust": 57.0}]
    # jede Korrektur im Ereignisprotokoll (Fortschritt, 11c)
    from rechnung import eventlog
    felder = [e["field"] for e in store.events() if e.get("type") == "korrektur"]
    assert POSITIONEN_KEY in felder and "ust_aufstellung" in felder

    # Zuruecknehmen ueber den bestehenden Korrektur-Endpunkt
    client.delete("/api/correction", params={"file": "a.pdf", "field": POSITIONEN_KEY})
    assert POSITIONEN_KEY not in store.corrections(sha)


def test_ungueltige_tabellenart_abgelehnt(tmp_path: Path):
    client, _store, samples = _client(tmp_path)
    make_invoice(samples / "a.pdf", items=_ITEMS_PDF)
    r = client.post("/api/tabelle", json={"file": "a.pdf", "art": "kopf", "rows": []})
    assert r.status_code == 400


def test_korrigierte_tabelle_wird_beim_bestaetigen_gelernt(tmp_path: Path):
    """Mensch korrigiert die (vorgeschlagene) Positionstabelle -- inkl. einer
    frei benannten Spalte ("betrag" statt "gesamtpreis") -- und bestaetigt;
    gelernt wird der Standardname (SPEC Baustein 1: Vorlagen-Spaltennamen
    werden beim Anlernen auf die Standardnamen abgebildet), die naechste
    Rechnung desselben Layouts wird damit korrekt UND einheitlich gelesen."""
    client, store, samples = _client(tmp_path)
    make_invoice(samples / "a.pdf", rechnungsnr="RE-2026-00042", items=_ITEMS_PDF)
    for feld, wert in [("rechnungsnummer", "RE-2026-00042"), ("rechnungsdatum", "14.02.2026"),
                       ("netto", "500.00"), ("ust_betrag", "71.00"), ("brutto", "571.00")]:
        client.post("/api/correction", json={"file": "a.pdf", "field": feld, "value": wert})
    # Spalte frei als "betrag" benannt -- wird auf "gesamt_netto" abgebildet.
    rows = [{"beschreibung": "Wartung Server", "menge": "2", "einzelpreis": "150,00",
             "betrag": "300,00"},
            {"beschreibung": "Schulung Grundlagen", "menge": "1", "einzelpreis": "200,00",
             "betrag": "200,00"}]
    client.post("/api/tabelle", json={"file": "a.pdf", "art": "positionen", "rows": rows})

    r = client.post("/api/confirm", json={"file": "a.pdf", "template_id": None})
    assert r.status_code == 200, r.text
    learned = r.json()["learned"]
    assert learned["saved"] is True
    assert "_positionen" in learned["learned"]
    tpl = store.template(learned["template_id"])
    assert tpl.line_items is not None
    assert {c.name for c in tpl.line_items.columns} == {"beschreibung", "menge",
                                                         "einzelpreis_netto", "gesamt_netto"}

    make_invoice(samples / "b.pdf", rechnungsnr="RE-2026-00043", items=[
        {"beschreibung": "Beratung vor Ort", "menge": 3, "einzelpreis": 100.0,
         "gesamtpreis": 300.0, "mwst": 19, "waehrung": "EUR"}])
    d = client.get("/api/invoice", params={"file": "b.pdf"}).json()
    assert d["template_id"] == tpl.id
    assert len(d["line_items"]) == 1
    it = d["line_items"][0]
    assert it["beschreibung"] == "Beratung vor Ort"
    assert float(it["gesamt_netto"]) == 300.0 and float(it["einzelpreis_netto"]) == 100.0
    assert d["markierungen"]["positionen"][0] is not None
    assert d["pruefung_live"]["positionen"][0]["ok"] is True
