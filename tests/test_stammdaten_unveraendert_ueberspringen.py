"""Stammdaten-Import ueberspringt unveraenderte Zeilen bei einem erneuten
Lauf (Entscheidung Aaron 23.10.2026): bei >10.000 Zeilen muss ein Neustart
mitten im "Übernehmen"-Schritt nicht die komplette Datei neu schreiben --
nur geaenderte Zeilen werden tatsaechlich gespeichert. Dubletten entstehen
dabei ohnehin nie (deterministische, inhaltsbasierte IDs), hier geht es NUR
um die Geschwindigkeit. Alle Werte sind synthetische Platzhalter."""

from __future__ import annotations

from pathlib import Path

from rechnung import stammdaten_import as si
from rechnung.store import Store
from rechnung.xlsx import header_and_rows, read_workbook

from _xlsx_helpers import write_workbook

KRED_KOPF = ["Konto", "Bezeichnung 1", "Name1", "SammelKt", "Name2", "Straße", "Land",
             "PlzOrt", "Ortsbezeichnung"]
MAND_KOPF = ["Mandanten Nr.", "Bezeichnung", "WJ", "LM"]
POST_KOPF = ["MDT", "E-Mail", "Passwort", "Bezeichnung", "Name"]

KRED_DE = [KRED_KOPF,
           ["70000", "Musterfirma Beratungs- und Dienstleist", "Musterfirma Beratungs- und",
            "1604", "Dienstleistungsgesellschaft mbH", "Musterstraße 1", "DE", "12345", "Musterstadt"],
           ["70001", "Beispiel Hotelbedarf GmbH", "Beispiel Hotelbedarf GmbH", "1604", "",
            "Hauptstraße 5", "DE", "50667", "Köln"]]
MANDANTEN = [MAND_KOPF, ["001", "ABC - Beispiel-Mandant", "2026", "12"]]
POSTFAECHER = [POST_KOPF,
               ["001", "abc@example.com", "geheim-eins", "ABC - Beispiel-Mandant",
                "Beispiel-Mandant Hotel GmbH"]]


def _store(tmp_path: Path, name: str = "data") -> Store:
    return Store(tmp_path / name)


def _sheets(blaetter: dict) -> dict:
    """Wie ``/api/stammdaten/parse``: Datei lesen, Zuordnung + Art vorschlagen."""
    out = {}
    for name, rows in read_workbook(write_workbook(blaetter)).items():
        headers, daten = header_and_rows(rows)
        mapping = si.spalten_vorschlag(headers)
        out[name] = {"headers": headers, "rows": daten, "mapping": mapping,
                     "art": si.art_vorschlag(name, mapping)}
    return out


def _alle() -> dict:
    return {"Kreditoren Deutschland": KRED_DE, "Mandanten": MANDANTEN,
            "Postfächer": POSTFAECHER}


# ---- (a) zweiter Lauf mit identischen Daten: alles unveraendert -----------

def test_zweiter_lauf_mit_identischen_daten_schreibt_nichts_neu(tmp_path):
    store = _store(tmp_path)
    sheets = _sheets(_alle())
    erg1 = si.anwenden(store, sheets, {}, "aaron")
    assert erg1["kreditoren"] == 2 and erg1["mandanten"] == 1 and erg1["postfaecher"] == 1
    assert erg1["unveraendert_uebersprungen"] == 0

    stand = [(s.id, s.name, tuple(s.address_lines)) for s in store.suppliers()]
    audit_laenge = len(store.audit_log())

    erg2 = si.anwenden(store, sheets, {}, "aaron")
    assert erg2["kreditoren"] == 0 and erg2["mandanten"] == 0 and erg2["postfaecher"] == 0
    assert erg2["unveraendert_uebersprungen"] == 4   # 2 Kreditoren + 1 Mandant + 1 Postfach
    # nichts wurde tatsaechlich geschrieben -- weder Inhalt noch Audit-Log
    assert [(s.id, s.name, tuple(s.address_lines)) for s in store.suppliers()] == stand
    assert len(store.audit_log()) == audit_laenge


# ---- (b) eine geaenderte Zeile wird trotzdem uebernommen ------------------

def test_geaenderte_zeile_wird_trotzdem_uebernommen(tmp_path):
    store = _store(tmp_path)
    sheets = _sheets(_alle())
    si.anwenden(store, sheets, {}, "aaron")

    geaendert = [KRED_KOPF,
                 ["70000", "Musterfirma Beratungs- und Dienstleist", "Musterfirma Beratungs- und",
                  "1604", "Dienstleistungsgesellschaft mbH", "Neue Straße 99", "DE", "12345",
                  "Musterstadt"],
                 KRED_DE[2]]
    sheets2 = _sheets({"Kreditoren Deutschland": geaendert, "Mandanten": MANDANTEN,
                       "Postfächer": POSTFAECHER})
    erg2 = si.anwenden(store, sheets2, {}, "aaron")
    # nur die geaenderte Zeile (70000) wird neu geschrieben, der Rest (70001,
    # Mandant, Postfach) bleibt unveraendert und wird uebersprungen
    assert erg2["kreditoren"] == 1
    assert erg2["unveraendert_uebersprungen"] == 3
    assert store.supplier("70000").address_lines[0] == "Neue Straße 99"


# ---- (c) Fingerabdruck ueberlebt einen simulierten Neustart ---------------

def test_fingerabdruck_ueberlebt_neustart(tmp_path):
    """Store neu aus der Platte oeffnen (nicht dasselbe Objekt weiterbenutzen)
    simuliert einen Programm-Neustart (Absturz/Update/CMD-Fenster zu) mitten
    in einem grossen Import."""
    store1 = _store(tmp_path)
    sheets = _sheets(_alle())
    si.anwenden(store1, sheets, {}, "aaron")
    del store1   # explizit nichts mehr vom alten Objekt im Speicher nutzen

    store2 = _store(tmp_path)   # neues Objekt, liest von der Platte
    erg2 = si.anwenden(store2, sheets, {}, "aaron")
    assert erg2["kreditoren"] == 0 and erg2["mandanten"] == 0 and erg2["postfaecher"] == 0
    assert erg2["unveraendert_uebersprungen"] == 4


# ---- (d) alte Datensaetze ohne Fingerabdruck-Feld werden nicht faelschlich
#          als "unveraendert" uebersprungen -----------------------------------

def test_alter_datensatz_ohne_fingerabdruck_wird_normal_geschrieben(tmp_path):
    store = _store(tmp_path)
    sheets = _sheets(_alle())
    si.anwenden(store, sheets, {}, "aaron")

    # Datensaetze "von vor dieser Aenderung" simulieren: Fingerabdruck-Feld
    # leeren, wie es bei einem suppliers.json/addresses.json/postfaecher.json
    # von vor dem Feature der Fall waere (from_dict() liefert dafuer "").
    for s in store.suppliers():
        s.stammdaten_import_hash = ""
        store.save_supplier(s)
    for a in store.addresses():
        a.stammdaten_import_hash = ""
        store.save_address(a)
    for p in store.postfaecher():
        p.stammdaten_import_hash = ""
        store.save_postfach(p)
    audit_laenge = len(store.audit_log())

    erg = si.anwenden(store, sheets, {}, "aaron")
    # trotz identischem Zeileninhalt: ohne gespeicherten Fingerabdruck gilt
    # die Zeile IMMER als geaendert, nichts wird faelschlich uebersprungen
    assert erg["unveraendert_uebersprungen"] == 0
    assert erg["kreditoren"] == 2 and erg["mandanten"] == 1 and erg["postfaecher"] == 1
    assert len(store.audit_log()) > audit_laenge
    # und ab jetzt haben alle wieder einen Fingerabdruck
    assert all(s.stammdaten_import_hash for s in store.suppliers())
    assert all(a.stammdaten_import_hash for a in store.addresses())
    assert all(p.stammdaten_import_hash for p in store.postfaecher())


# ---- Passwort-Fingerabdruck: Klartext zaehlt, nicht der verschluesselte
#      Blob (der sich bei jeder Verschluesselung aendert) -------------------

def test_postfach_passwort_unveraendert_wird_nicht_neu_verschluesselt(tmp_path):
    from rechnung import secrets as secrets_mod

    store = _store(tmp_path)
    sheets = _sheets(_alle())
    si.anwenden(store, sheets, {}, "aaron")
    pid = store.postfaecher()[0].id
    blob_vorher = secrets_mod.get_password(store, pid)
    assert blob_vorher == "geheim-eins"

    erg2 = si.anwenden(store, sheets, {}, "aaron")
    assert erg2["postfaecher"] == 0 and erg2["passwoerter_gesetzt"] == 0
    assert erg2["unveraendert_uebersprungen"] >= 1
    assert secrets_mod.get_password(store, pid) == "geheim-eins"


def test_postfach_geaendertes_passwort_wird_uebernommen(tmp_path):
    from rechnung import secrets as secrets_mod

    store = _store(tmp_path)
    sheets = _sheets(_alle())
    si.anwenden(store, sheets, {}, "aaron")

    neues_passwort = [POST_KOPF,
                      ["001", "abc@example.com", "neues-geheimnis", "ABC - Beispiel-Mandant",
                       "Beispiel-Mandant Hotel GmbH"]]
    sheets2 = _sheets({"Kreditoren Deutschland": KRED_DE, "Mandanten": MANDANTEN,
                       "Postfächer": neues_passwort})
    erg2 = si.anwenden(store, sheets2, {}, "aaron")
    assert erg2["postfaecher"] == 1 and erg2["passwoerter_gesetzt"] == 1
    pid = store.postfaecher()[0].id
    assert secrets_mod.get_password(store, pid) == "neues-geheimnis"
