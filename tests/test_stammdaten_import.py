"""rechnung/stammdaten_import.py: Spaltenzuordnung, Zeilenpruefung, Fragen,
Anwenden. Grundsatz (Aaron 15.10.2026): nichts wird geraten -- jede
Unsicherheit ist eine Frage, uebernommen wird nur, was gruen ist oder
beantwortet wurde."""

from __future__ import annotations

from pathlib import Path

from rechnung import stammdaten_import as si
from rechnung.store import Store


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "data")


# ---- Spaltenzuordnung -------------------------------------------------

def test_spalten_vorschlag_erkennt_deutsche_und_englische_ueberschriften():
    v = si.spalten_vorschlag(["Name", "Straße", "PLZ", "Ort", "UID", "IBAN", "Vendor ID"])
    assert v["Name"] == "name"
    assert v["Straße"] == "strasse"
    assert v["PLZ"] == "plz"
    assert v["UID"] == "ustid"
    assert v["IBAN"] == "iban"
    assert v["Vendor ID"] == "kreditorennummer"


def test_unbekannte_spalte_bleibt_none():
    v = si.spalten_vorschlag(["Bemerkung frei Text"])
    assert v["Bemerkung frei Text"] is None


def test_layout_gedaechtnis_schlaegt_gelernte_zuordnung_vor(tmp_path):
    store = _store(tmp_path)
    headers = ["Firma", "UID-Nr"]
    mapping = {"Firma": "name", "UID-Nr": "ustid"}
    si.zuordnung_lernen(store, headers, mapping, si.ART_KREDITOR_DE, operator="aaron")
    gelernt = si.gelernte_zuordnung(store, headers)
    assert gelernt == {"mapping": mapping, "art": si.ART_KREDITOR_DE}
    # Reihenfolge der Spalten ist egal -- derselbe Satz Ueberschriften zaehlt
    gelernt2 = si.gelernte_zuordnung(store, list(reversed(headers)))
    assert gelernt2 == gelernt


# ---- Art-Vorschlag -----------------------------------------------------

def test_art_vorschlag_aus_blattname():
    assert si.art_vorschlag("Kreditoren Deutschland", {}) == si.ART_KREDITOR_DE
    assert si.art_vorschlag("Kreditoren Schweiz", {}) == si.ART_KREDITOR_CH
    assert si.art_vorschlag("Mandanten", {}) == si.ART_MANDANT
    assert si.art_vorschlag("Postfächer", {}) == si.ART_POSTFACH


def test_art_vorschlag_aus_spalten_wenn_blattname_unklar():
    assert si.art_vorschlag("Tabelle1", {"a": "email", "b": "passwort"}) == si.ART_POSTFACH
    assert si.art_vorschlag("Tabelle1", {"a": "land"}) == si.ART_GEMISCHT_LAND
    assert si.art_vorschlag("Tabelle1", {}) == si.ART_UNKLAR


# ---- Zeilenpruefung: gruen/gelb/rot + konkrete Fragen -----------------

def test_gueltige_de_zeile_ist_gruen(tmp_path):
    store = _store(tmp_path)
    headers = ["Name", "Straße", "PLZ", "Ort", "Land", "UID", "IBAN"]
    rows = [["Muster GmbH", "Hauptstr. 1", "12345", "Musterstadt", "DE", "DE123456789",
            "DE89370400440532013000"]]
    zeilen = si.zeilen_aufbauen(store, "Blatt1", headers, rows,
                                si.spalten_vorschlag(headers), si.ART_KREDITOR_DE)
    assert len(zeilen) == 1
    z = zeilen[0]
    assert z["status"] == si.STATUS_GRUEN
    assert z["fragen"] == []
    assert z["art"] == si.ART_KREDITOR_DE


def test_ungueltige_iban_pruefziffer_ist_frage(tmp_path):
    store = _store(tmp_path)
    headers = ["Name", "IBAN"]
    rows = [["Muster GmbH", "DE00000000000000000000"]]
    zeilen = si.zeilen_aufbauen(store, "Blatt1", headers, rows,
                                si.spalten_vorschlag(headers), si.ART_KREDITOR_DE)
    z = zeilen[0]
    assert z["status"] == si.STATUS_GELB
    assert any(f["feld"] == "iban" and "Pruefziffer" in f["grund"] for f in z["fragen"])


def test_falsches_ustid_format_de_ist_frage(tmp_path):
    store = _store(tmp_path)
    headers = ["Name", "UID"]
    rows = [["Muster GmbH", "DE12345"]]   # zu wenige Ziffern
    zeilen = si.zeilen_aufbauen(store, "Blatt1", headers, rows,
                                si.spalten_vorschlag(headers), si.ART_KREDITOR_DE)
    z = zeilen[0]
    assert any(f["feld"] == "ustid" and "Format" in f["grund"] for f in z["fragen"])


def test_gueltige_ch_uid_ist_gruen(tmp_path):
    store = _store(tmp_path)
    headers = ["Name", "UID"]
    rows = [["Muster AG", "CHE-123.456.788 MWST"]]
    zeilen = si.zeilen_aufbauen(store, "Blatt1", headers, rows,
                                si.spalten_vorschlag(headers), si.ART_KREDITOR_CH)
    z = zeilen[0]
    assert z["status"] == si.STATUS_GRUEN, z["fragen"]


def test_land_passt_nicht_zu_iban_de_kreditor_mit_ch_iban(tmp_path):
    store = _store(tmp_path)
    headers = ["Name", "IBAN"]
    rows = [["Muster GmbH", "CH9300762011623852957"]]
    zeilen = si.zeilen_aufbauen(store, "Blatt1", headers, rows,
                                si.spalten_vorschlag(headers), si.ART_KREDITOR_DE)
    z = zeilen[0]
    assert any(f["feld"] == "iban" and "Land passt nicht" in f["grund"] for f in z["fragen"])


def test_plz_format_falsch_fuer_deutschland(tmp_path):
    store = _store(tmp_path)
    headers = ["Name", "PLZ"]
    rows = [["Muster GmbH", "123"]]
    zeilen = si.zeilen_aufbauen(store, "Blatt1", headers, rows,
                                si.spalten_vorschlag(headers), si.ART_KREDITOR_DE)
    assert any(f["feld"] == "plz" for f in zeilen[0]["fragen"])


def test_plz_vierstellig_fuer_schweiz_ist_ok(tmp_path):
    store = _store(tmp_path)
    headers = ["Name", "PLZ"]
    rows = [["Muster AG", "8001"]]
    zeilen = si.zeilen_aufbauen(store, "Blatt1", headers, rows,
                                si.spalten_vorschlag(headers), si.ART_KREDITOR_CH)
    assert zeilen[0]["status"] == si.STATUS_GRUEN


def test_pflichtfeld_name_fehlt_ist_rot(tmp_path):
    store = _store(tmp_path)
    headers = ["IBAN"]
    rows = [["DE89370400440532013000"]]
    zeilen = si.zeilen_aufbauen(store, "Blatt1", headers, rows,
                                si.spalten_vorschlag(headers), si.ART_KREDITOR_DE)
    z = zeilen[0]
    assert z["status"] == si.STATUS_ROT
    assert any("Name" in f["grund"] for f in z["fragen"])


def test_gemischtes_land_entscheidet_pro_zeile(tmp_path):
    store = _store(tmp_path)
    headers = ["Name", "Land"]
    rows = [["Deutsche Firma", "DE"], ["Schweizer Firma", "CH"]]
    mapping = si.spalten_vorschlag(headers)
    zeilen = si.zeilen_aufbauen(store, "Blatt1", headers, rows, mapping, si.ART_GEMISCHT_LAND)
    assert zeilen[0]["art"] == si.ART_KREDITOR_DE
    assert zeilen[1]["art"] == si.ART_KREDITOR_CH


def test_land_nicht_erkennbar_ist_frage_und_unklar(tmp_path):
    store = _store(tmp_path)
    headers = ["Name"]
    rows = [["Firma ohne Land oder IBAN/UID"]]
    zeilen = si.zeilen_aufbauen(store, "Blatt1", headers, rows,
                                si.spalten_vorschlag(headers), si.ART_GEMISCHT_LAND)
    z = zeilen[0]
    assert z["art"] == si.ART_UNKLAR
    assert z["status"] == si.STATUS_ROT
    assert any(f["feld"] == "art" for f in z["fragen"])


def test_mandant_ohne_eigene_name_spalte_nutzt_mandant_spalte(tmp_path):
    """Ein Blatt mit nur einer Spalte 'Mandant' (statt 'Name') -- der Wert
    ist hier der eigene Name, kein Verweis auf einen anderen Mandanten."""
    store = _store(tmp_path)
    headers = ["Mandant", "PLZ", "Ort"]
    rows = [["Niederlassung Nord", "20095", "Hamburg"]]
    mapping = si.spalten_vorschlag(headers)
    zeilen = si.zeilen_aufbauen(store, "Mandanten", headers, rows, mapping, si.ART_MANDANT)
    z = zeilen[0]
    assert z["status"] == si.STATUS_GRUEN
    assert z["werte"]["name"] == "Niederlassung Nord"


def test_postfach_pflichtfelder(tmp_path):
    store = _store(tmp_path)
    headers = ["E-Mail", "Passwort"]
    rows = [["rechnung@beispiel.de", "geheim"]]
    mapping = si.spalten_vorschlag(headers)
    zeilen = si.zeilen_aufbauen(store, "Postfächer", headers, rows, mapping, si.ART_POSTFACH)
    z = zeilen[0]
    assert any(f["feld"] == "mandant_verweis" for f in z["fragen"])


# ---- Dubletten innerhalb der Datei -------------------------------------

def test_dublette_gleiche_uid_zweimal_in_der_datei(tmp_path):
    store = _store(tmp_path)
    sheets = {"Blatt1": {
        "headers": ["Name", "UID"],
        "rows": [["Firma A", "DE123456789"], ["Firma A GmbH", "DE123456789"]],
        "mapping": None, "art": si.ART_KREDITOR_DE,
    }}
    v = si.vorschau(store, sheets)
    zeilen = v["blaetter"]["Blatt1"]["zeilen"]
    assert all(any(f["feld"] == "dublette" for f in z["fragen"]) for z in zeilen)
    assert v["zusammenfassung"]["bereit_zum_uebernehmen"] == 0


# ---- Konflikt mit der Bibliothek ---------------------------------------

def test_konflikt_mit_bestehendem_lieferanten_abweichender_name(tmp_path):
    from rechnung.identify import Supplier

    store = _store(tmp_path)
    store.save_supplier(Supplier(id="DE123456789", name="Alter Name GmbH",
                                 ustids=["DE123456789"]))
    sheets = {"Blatt1": {
        "headers": ["Name", "UID"],
        "rows": [["Neuer Name GmbH", "DE123456789"]],
        "mapping": None, "art": si.ART_KREDITOR_DE,
    }}
    v = si.vorschau(store, sheets)
    z = v["blaetter"]["Blatt1"]["zeilen"][0]
    assert any(f["feld"] == "bibliothek" for f in z["fragen"])
    assert z["status"] == si.STATUS_GELB


def test_keine_frage_wenn_nur_leere_felder_ergaenzt_werden(tmp_path):
    """Treffer gegen die Bibliothek, aber ohne Widerspruch (nur Ergaenzung
    bisher leerer Felder) -- keine Frage, bleibt gruen."""
    from rechnung.identify import Supplier

    store = _store(tmp_path)
    store.save_supplier(Supplier(id="DE123456789", name="Muster GmbH", ustids=["DE123456789"]))
    sheets = {"Blatt1": {
        "headers": ["Name", "UID", "IBAN"],
        "rows": [["Muster GmbH", "DE123456789", "DE89370400440532013000"]],
        "mapping": None, "art": si.ART_KREDITOR_DE,
    }}
    v = si.vorschau(store, sheets)
    z = v["blaetter"]["Blatt1"]["zeilen"][0]
    assert not any(f["feld"] == "bibliothek" for f in z["fragen"])
    assert z["status"] == si.STATUS_GRUEN


# ---- Mandant-Verweis ----------------------------------------------------

def test_mandant_verweis_nicht_auffindbar_ist_frage(tmp_path):
    store = _store(tmp_path)
    sheets = {"Kreditoren": {
        "headers": ["Name", "UID", "Mandant"],
        "rows": [["Firma A", "DE123456789", "Nicht existierender Mandant"]],
        "mapping": None, "art": si.ART_KREDITOR_DE,
    }}
    v = si.vorschau(store, sheets)
    z = v["blaetter"]["Kreditoren"]["zeilen"][0]
    assert any(f["feld"] == "mandant_verweis" for f in z["fragen"])


def test_mandant_verweis_gefunden_in_derselben_datei(tmp_path):
    store = _store(tmp_path)
    sheets = {
        "Mandanten": {"headers": ["Mandant"], "rows": [["Niederlassung Nord"]],
                     "mapping": None, "art": si.ART_MANDANT},
        "Kreditoren": {"headers": ["Name", "UID", "Mandant"],
                      "rows": [["Firma A", "DE123456789", "Niederlassung Nord"]],
                      "mapping": None, "art": si.ART_KREDITOR_DE},
    }
    v = si.vorschau(store, sheets)
    z = v["blaetter"]["Kreditoren"]["zeilen"][0]
    assert not any(f["feld"] == "mandant_verweis" for f in z["fragen"])


# ---- Antworten + Anwenden ------------------------------------------------

def test_gelbe_zeile_ohne_antwort_wird_nicht_uebernommen(tmp_path):
    store = _store(tmp_path)
    sheets = {"Blatt1": {
        "headers": ["Name", "IBAN"],
        "rows": [["Muster GmbH", "DE00000000000000000000"]],
        "mapping": None, "art": si.ART_KREDITOR_DE,
    }}
    ergebnis = si.anwenden(store, sheets, antworten=None, operator="aaron")
    assert ergebnis["kreditoren"] == 0
    assert ergebnis["uebersprungen"] == 1
    assert store.suppliers() == []


def test_gelbe_zeile_mit_antwort_so_uebernehmen_wird_uebernommen(tmp_path):
    store = _store(tmp_path)
    headers = ["Name", "IBAN"]
    rows = [["Muster GmbH", "DE00000000000000000000"]]
    sheets = {"Blatt1": {"headers": headers, "rows": rows, "mapping": None,
                        "art": si.ART_KREDITOR_DE}}
    antworten = {"Blatt1:1|iban": {"antwort": "so übernehmen"}}
    ergebnis = si.anwenden(store, sheets, antworten, operator="aaron")
    assert ergebnis["kreditoren"] == 1
    assert len(store.suppliers()) == 1


def test_zeile_weglassen_wird_nicht_uebernommen(tmp_path):
    store = _store(tmp_path)
    headers = ["Name", "IBAN"]
    rows = [["Muster GmbH", "DE00000000000000000000"]]
    sheets = {"Blatt1": {"headers": headers, "rows": rows, "mapping": None,
                        "art": si.ART_KREDITOR_DE}}
    antworten = {"Blatt1:1|iban": {"antwort": "Zeile weglassen"}}
    ergebnis = si.anwenden(store, sheets, antworten, operator="aaron")
    assert ergebnis["kreditoren"] == 0
    assert store.suppliers() == []


def test_korrektur_wert_wird_uebernommen(tmp_path):
    store = _store(tmp_path)
    headers = ["Name", "IBAN"]
    rows = [["Muster GmbH", "DE00000000000000000000"]]
    sheets = {"Blatt1": {"headers": headers, "rows": rows, "mapping": None,
                        "art": si.ART_KREDITOR_DE}}
    richtige_iban = "DE89370400440532013000"
    antworten = {"Blatt1:1|iban": {"antwort": "korrigieren auf …", "wert": richtige_iban}}
    ergebnis = si.anwenden(store, sheets, antworten, operator="aaron")
    assert ergebnis["kreditoren"] == 1
    sup = store.suppliers()[0]
    assert richtige_iban in sup.ibans


def test_apply_revalidates_server_side_ignores_client_claimed_green(tmp_path):
    """Sicherheitsrelevant: ``anwenden`` darf dem Client NIE eine falsche
    Ampel glauben -- die Pruefung laeuft serverseitig komplett neu."""
    store = _store(tmp_path)
    headers = ["IBAN"]   # kein Name -> Pflichtfeld fehlt, egal was der Client behauptet
    rows = [["DE89370400440532013000"]]
    sheets = {"Blatt1": {"headers": headers, "rows": rows, "mapping": None,
                        "art": si.ART_KREDITOR_DE}}
    ergebnis = si.anwenden(store, sheets, antworten=None, operator="aaron")
    assert ergebnis["kreditoren"] == 0
    assert store.suppliers() == []


def test_mandant_wird_angelegt(tmp_path):
    store = _store(tmp_path)
    headers = ["Mandant", "PLZ", "Ort"]
    rows = [["Niederlassung Nord", "20095", "Hamburg"]]
    sheets = {"Mandanten": {"headers": headers, "rows": rows, "mapping": None,
                           "art": si.ART_MANDANT}}
    ergebnis = si.anwenden(store, sheets, antworten=None, operator="aaron")
    assert ergebnis["mandanten"] == 1
    assert len(store.addresses()) == 1
    assert store.addresses()[0].name == "Niederlassung Nord"


def test_postfach_wird_angelegt_und_passwort_verschluesselt_gesetzt(tmp_path):
    from rechnung import secrets as secrets_mod
    from rechnung.billing import BillingAddress

    store = _store(tmp_path)
    store.save_address(BillingAddress(id="nord", name="Niederlassung Nord"))
    headers = ["E-Mail", "Passwort", "Mandant", "Server"]
    rows = [["rechnung@nord.example", "geheim123", "Niederlassung Nord", "imap.ionos.de:993"]]
    sheets = {"Postfächer": {"headers": headers, "rows": rows, "mapping": None,
                            "art": si.ART_POSTFACH}}
    ergebnis = si.anwenden(store, sheets, antworten=None, operator="aaron")
    assert ergebnis["postfaecher"] == 1
    assert "hinweis_passwoerter" in ergebnis
    pf = store.postfaecher()[0]
    assert pf.mandant_id == "nord"
    assert pf.user == "rechnung@nord.example"
    assert secrets_mod.get_password(store, pf.id) == "geheim123"


def test_hinweis_fehlt_wenn_kein_passwort_in_der_datei_stand(tmp_path):
    from rechnung.billing import BillingAddress

    store = _store(tmp_path)
    store.save_address(BillingAddress(id="nord", name="Niederlassung Nord"))
    headers = ["E-Mail", "Mandant"]
    rows = [["rechnung@nord.example", "Niederlassung Nord"]]
    sheets = {"Postfächer": {"headers": headers, "rows": rows, "mapping": None,
                            "art": si.ART_POSTFACH}}
    ergebnis = si.anwenden(store, sheets, antworten=None, operator="aaron")
    assert "hinweis_passwoerter" not in ergebnis


def test_bestehender_lieferant_kann_aktualisiert_oder_als_neuer_angelegt_werden(tmp_path):
    from rechnung.identify import Supplier

    store = _store(tmp_path)
    store.save_supplier(Supplier(id="DE123456789", name="Alter Name GmbH", ustids=["DE123456789"]))
    headers = ["Name", "UID"]
    rows = [["Neuer Name GmbH", "DE123456789"]]
    sheets = {"Blatt1": {"headers": headers, "rows": rows, "mapping": None,
                        "art": si.ART_KREDITOR_DE}}
    antworten = {"Blatt1:1|bibliothek": {"antwort": "als neuen, separaten Datensatz anlegen"}}
    ergebnis = si.anwenden(store, sheets, antworten, operator="aaron")
    assert ergebnis["kreditoren"] == 1
    namen = {s.name for s in store.suppliers()}
    assert namen == {"Alter Name GmbH", "Neuer Name GmbH"}
