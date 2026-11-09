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


def test_anwenden_prueft_nur_einmal_wenn_kein_vor_mitgegeben(tmp_path, monkeypatch):
    """Ohne ``vor``-Parameter berechnet ``anwenden()`` die Pruefung selbst --
    aber nur EINMAL (Regressionstest gegen die frueher doppelte Pruefung bei
    grossen Dateien, Aaron 23.10.2026)."""
    store = _store(tmp_path)
    headers = ["Name", "IBAN"]
    rows = [["Muster GmbH", "DE00000000000000000000"]]
    sheets = {"Blatt1": {"headers": headers, "rows": rows, "mapping": None,
                        "art": si.ART_KREDITOR_DE}}
    aufrufe = []
    original = si._vorschau_intern

    def zaehlend(*a, **kw):
        aufrufe.append(1)
        return original(*a, **kw)

    monkeypatch.setattr(si, "_vorschau_intern", zaehlend)
    antworten = {"Blatt1:1|iban": {"antwort": "so übernehmen"}}
    si.anwenden(store, sheets, antworten, operator="aaron")
    assert len(aufrufe) == 1


def test_anwenden_mit_vorberechnetem_vor_prueft_gar_nicht_neu(tmp_path, monkeypatch):
    """Server-Weg: ``vorschau()`` wird einmal aufgerufen und das Ergebnis an
    ``anwenden()`` weitergegeben -- dort darf die Pruefung dann NICHT noch
    einmal laufen."""
    store = _store(tmp_path)
    headers = ["Name", "IBAN"]
    rows = [["Muster GmbH", "DE00000000000000000000"]]
    sheets = {"Blatt1": {"headers": headers, "rows": rows, "mapping": None,
                        "art": si.ART_KREDITOR_DE}}
    antworten = {"Blatt1:1|iban": {"antwort": "so übernehmen"}}
    vor = si.vorschau(store, sheets, antworten)

    aufrufe = []
    original = si._vorschau_intern

    def zaehlend(*a, **kw):
        aufrufe.append(1)
        return original(*a, **kw)

    monkeypatch.setattr(si, "_vorschau_intern", zaehlend)
    ergebnis = si.anwenden(store, sheets, antworten, operator="aaron", vor=vor)
    assert len(aufrufe) == 0
    assert ergebnis["kreditoren"] == 1


def test_anwenden_fortschritt_wird_pro_zeile_mit_steigenden_werten_aufgerufen(tmp_path):
    store = _store(tmp_path)
    headers = ["Name", "IBAN"]
    rows = [
        ["Erste GmbH", ""],
        ["Zweite GmbH", ""],
        ["Dritte GmbH", ""],
    ]
    sheets = {"Blatt1": {"headers": headers, "rows": rows, "mapping": None,
                        "art": si.ART_KREDITOR_DE}}
    aufrufe = []
    si.anwenden(store, sheets, {}, operator="aaron",
               fortschritt=lambda fertig, gesamt: aufrufe.append((fertig, gesamt)))
    assert aufrufe == [(1, 3), (2, 3), (3, 3)]


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


# ---- DE/CH-Namensdublette automatisch verschmelzen (Entscheidung Aaron 16.10.2026) ----

def _de_ch_sheets(de_rows, ch_rows,
                  headers=("Kreditorennummer", "Name", "Strasse", "PLZ", "Ort", "UID", "IBAN")):
    h = list(headers)
    return {
        "Kreditoren Deutschland": {"headers": h, "rows": de_rows, "mapping": None,
                                   "art": si.ART_KREDITOR_DE},
        "Kreditoren Schweiz": {"headers": h, "rows": ch_rows, "mapping": None,
                               "art": si.ART_KREDITOR_CH},
    }


DE_ROW = ["70001", "Muster Handel GmbH", "Hauptstr. 1", "80331", "München",
          "DE123456789", "DE89370400440532013000"]
CH_ROW = ["80002", "Muster Handel  GmbH", "Bahnhofstr. 9", "8001", "Zürich",
          "CHE-116.281.710 MWST", "CH9300762011623852957"]


def _gueltige_ch_uid():
    from rechnung.textnorm import che_valid
    for n in range(116281700, 116281800):
        c = f"CHE{n}"
        if che_valid(c):
            return f"CHE-{str(n)[:3]}.{str(n)[3:6]}.{str(n)[6:]} MWST"
    raise AssertionError


def test_de_ch_namensdublette_wird_ohne_frage_verschmolzen(tmp_path):
    store = _store(tmp_path)
    ch = list(CH_ROW)
    ch[5] = _gueltige_ch_uid()
    sheets = _de_ch_sheets([DE_ROW], [ch])
    v = si.vorschau(store, sheets)
    z = v["zusammenfassung"]
    assert z["gelb"] == 0 and z["rot"] == 0 and z["gruen"] == 2
    assert z["automatisch_verschmolzen_paare"] == 1 and z["automatisch_verschmolzen_zeilen"] == 2
    assert z["bereit_zum_uebernehmen"] == 2

    erg = si.anwenden(store, sheets, {}, "t")
    assert erg["kreditoren"] == 2 and erg["verschmolzene_lieferanten"] == 1
    sups = store.suppliers()
    assert len(sups) == 1
    s = sups[0]
    assert s.external_ids == {"DE": "70001", "CH": "80002"}
    assert s.external_id_fuer("DE") == "70001" and s.external_id_fuer("CH") == "80002"
    assert s.external_id_fuer(None) == ""          # nicht raten
    assert s.external_id == "70001"                # Rueckfall, deterministisch
    assert len(s.ibans) == 2 and len(s.ustids) == 2
    # zweite Anschrift geht nicht verloren
    assert "Zürich" in s.notes or "Zürich" in " ".join(s.address_lines)


def test_verschmolzene_adresse_widerspruch_bleibt_keine_frage(tmp_path):
    """Verschiedene Anschrift ist bei DE/CH der Normalfall (zwei Standorte) --
    kein Grund fuer eine Rueckfrage; beide Anschriften bleiben erhalten."""
    store = _store(tmp_path)
    ch = list(CH_ROW)
    ch[5] = _gueltige_ch_uid()
    sheets = _de_ch_sheets([DE_ROW], [ch])
    si.anwenden(store, sheets, {}, "t")
    s = store.suppliers()[0]
    assert s.address_lines == ["Hauptstr. 1", "80331 München", "DE"]
    assert "Anschrift CH: Bahnhofstr. 9, 8001 Zürich" in s.notes
    # erneuter Import: idempotent
    si.anwenden(store, sheets, {}, "t")
    s2 = store.suppliers()
    assert len(s2) == 1 and s2[0].notes.count("Anschrift CH") == 1


def test_dublette_innerhalb_eines_landes_bleibt_frage(tmp_path):
    store = _store(tmp_path)
    de2 = list(DE_ROW)
    de2[0] = "70099"
    de2[5] = "DE987654321"
    de2[6] = ""
    sheets = _de_ch_sheets([DE_ROW, de2], [])
    v = si.vorschau(store, sheets)
    zeilen = v["blaetter"]["Kreditoren Deutschland"]["zeilen"]
    assert all(any(f["feld"] == "dublette" for f in z["fragen"]) for z in zeilen)
    assert v["zusammenfassung"]["automatisch_verschmolzen_paare"] == 0


def test_de_de_ch_dreier_gruppe_bleibt_frage(tmp_path):
    store = _store(tmp_path)
    de2 = list(DE_ROW)
    de2[0], de2[5], de2[6] = "70099", "DE987654321", ""
    ch = list(CH_ROW)
    ch[5] = _gueltige_ch_uid()
    v = si.vorschau(store, _de_ch_sheets([DE_ROW, de2], [ch]))
    assert v["zusammenfassung"]["automatisch_verschmolzen_paare"] == 0
    assert v["zusammenfassung"]["gelb"] == 3


def test_de_ch_gleiche_uid_oder_iban_bleibt_frage(tmp_path):
    """Teilen DE- und CH-Zeile auch IBAN, ist es kein reiner Namenstreffer."""
    store = _store(tmp_path)
    ch = list(CH_ROW)
    ch[5] = ""
    ch[6] = DE_ROW[6]    # absichtlich dieselbe IBAN (Land passt dann nicht -> eigene Frage)
    v = si.vorschau(store, _de_ch_sheets([DE_ROW], [ch]))
    assert v["zusammenfassung"]["automatisch_verschmolzen_paare"] == 0
    for blatt in v["blaetter"].values():
        for z in blatt["zeilen"]:
            assert any(f["feld"] == "dublette" for f in z["fragen"])


def test_nur_uid_treffer_ueber_laender_bleibt_frage(tmp_path):
    store = _store(tmp_path)
    de = ["70001", "Alpha GmbH", "", "", "", "DE123456789", ""]
    de2 = ["70002", "Beta GmbH", "", "", "", "DE123456789", ""]
    v = si.vorschau(store, _de_ch_sheets([de, de2], []))
    assert v["zusammenfassung"]["automatisch_verschmolzen_paare"] == 0
    assert v["zusammenfassung"]["gelb"] == 2


def test_supplier_external_ids_roundtrip_und_altbestand():
    from rechnung.identify import Supplier

    s = Supplier(id="x", name="X", external_id="5", external_ids={"DE": "5", "CH": "6"})
    assert Supplier.from_dict(s.to_dict()).external_ids == {"DE": "5", "CH": "6"}
    alt = Supplier.from_dict({"id": "y", "name": "Y", "external_id": "9"})
    assert alt.external_ids == {} and alt.external_id_fuer("CH") == "9"
    assert alt.external_id_fuer(None) == "9"


def test_land_aus_beleg_text_waehlt_nummer_je_land():
    from rechnung.identify import Supplier, land_aus_beleg_text

    s = Supplier(id="x", name="X", ustids=["DE123456789", _gueltige_ch_uid()],
                 ibans=["DE89370400440532013000", "CH9300762011623852957"],
                 external_id="5", external_ids={"DE": "5", "CH": "6"})
    assert land_aus_beleg_text(s, "Rechnung ... IBAN CH93 0076 2011 6238 5295 7") == "CH"
    assert s.external_id_fuer("CH") == "6"
    assert land_aus_beleg_text(s, "USt-IdNr DE123456789") == "DE"
    assert land_aus_beleg_text(s, "kein Hinweis") is None
    assert land_aus_beleg_text(s, "DE123456789 und CH9300762011623852957") is None


def test_pipeline_lieferantennummer_je_land_und_hold_ohne_land():
    from types import SimpleNamespace

    from rechnung.identify import Supplier
    from rechnung.pipeline import _setze_lieferantennummer

    s = Supplier(id="x", name="X", external_id="5", external_ids={"DE": "5", "CH": "6"})
    res = SimpleNamespace(supplier_external_id=None, hold_reasons=[])
    _setze_lieferantennummer(res, s, "CH")
    assert res.supplier_external_id == "6" and not res.hold_reasons
    res = SimpleNamespace(supplier_external_id=None, hold_reasons=[])
    _setze_lieferantennummer(res, s, None)
    assert res.supplier_external_id is None and len(res.hold_reasons) == 1
    # Normalfall: eine Nummer -> gilt immer, kein Hold
    einfach = Supplier(id="y", name="Y", external_id="9")
    res = SimpleNamespace(supplier_external_id=None, hold_reasons=[])
    _setze_lieferantennummer(res, einfach, None)
    assert res.supplier_external_id == "9" and not res.hold_reasons
