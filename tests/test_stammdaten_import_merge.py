"""Explizite Zusammenfuehrung eines Excel-Kreditors in einen bestehenden
Bibliotheks-Kreditor (Aaron 22.10.2026)."""
from __future__ import annotations

from rechnung import stammdaten_import as si
from rechnung.identify import Supplier
from rechnung.store import Store


def _sheets(name: str, nummer: str = "9002") -> dict:
    headers = ["Name", "UID", "Kreditorennummer"]
    rows = [[name, "DE123456789", nummer]]
    mapping = {"Name": "name", "UID": "ustid", "Kreditorennummer": "kreditorennummer"}
    return {"Kreditoren": {"headers": headers, "rows": rows,
                           "mapping": mapping, "art": si.ART_KREDITOR_DE}}


def test_excel_vorschau_bietet_zusammenfuehren_mit_bestehendem_kreditor(tmp_path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(
        id="bestehender-kreditor", name="Alte Schreibweise GmbH",
        ustids=["DE123456789"], external_id="4711", currency="EUR"))
    sheets = _sheets("Neue Schreibweise GmbH")

    v = si.vorschau(store, sheets)
    z = v["blaetter"]["Kreditoren"]["zeilen"][0]
    frage = next(f for f in z["fragen"] if f["feld"] == "bibliothek")
    assert si.ANTWORT_BIBLIOTHEK_ZUSAMMENFUEHREN in frage["antwortmoeglichkeiten"]
    assert "bibliothek_id" not in z  # interne Ziel-ID gehoert nicht in die Vorschau

    antworten = {f"{z['schluessel']}|bibliothek": {
        "antwort": si.ANTWORT_BIBLIOTHEK_ZUSAMMENFUEHREN}}
    result = si.anwenden(store, sheets, antworten, "test")

    assert result["kreditoren"] == 1
    assert [s.id for s in store.suppliers()] == ["bestehender-kreditor"]
    merged = store.supplier("bestehender-kreditor")
    assert merged.name == "Alte Schreibweise GmbH"  # bestehender Datensatz bleibt
    assert merged.external_id == "4711"             # Konfliktwert wird nicht ueberschrieben
    assert "Neue Schreibweise GmbH" in merged.notes
    assert "9002" in merged.notes
    assert store.audit_log()[-1]["action"] == "stammdaten_kreditor_zusammengefuehrt"


def test_konfliktfreier_excel_treffer_verwendet_interne_bibliotheks_id(tmp_path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="alte-interne-id", name="Muster GmbH",
                                 ustids=["DE123456789"]))
    sheets = _sheets("Muster GmbH", nummer="")
    sheets["Kreditoren"]["headers"] = ["Name", "UID"]
    sheets["Kreditoren"]["rows"] = [["Muster GmbH", "DE123456789"]]
    sheets["Kreditoren"]["mapping"] = {"Name": "name", "UID": "ustid"}

    v = si.vorschau(store, sheets)
    z = v["blaetter"]["Kreditoren"]["zeilen"][0]
    assert not any(f["feld"] == "bibliothek" for f in z["fragen"])
    si.anwenden(store, sheets, {}, "test")

    assert [s.id for s in store.suppliers()] == ["alte-interne-id"]
