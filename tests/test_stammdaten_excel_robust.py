"""Excel-Robustheit des Stammdaten-Imports (Aaron 17.10.2026): Zellwerte saeubern,
Dubletten ohne Informationsverlust zusammenfassen, PLZ-Null, Muellzeilen, grosse
Mengen. Nur synthetische Platzhalterdaten. Grundsatz bleibt: nichts raten --
automatisch nur ohne Informationsverlust und IMMER sichtbar (Hinweis + Zaehler)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from rechnung import stammdaten_import as si
from rechnung.identify import Supplier, identify_with_reason
from rechnung.store import Store
from rechnung.xlsx import header_and_rows, read_workbook, text_saeubern, zahl_ausschreiben

from _xlsx_helpers import Zahl, write_workbook

KOPF = ["Konto", "Bezeichnung 1", "Name1", "SammelKt", "Name2", "Straße", "Land",
        "PlzOrt", "Ortsbezeichnung"]
POST_KOPF = ["MDT", "E-Mail", "Passwort", "Bezeichnung", "Name"]
MAND_KOPF = ["Mandanten Nr.", "Bezeichnung", "WJ", "LM"]
DE = "Kreditoren Deutschland"
CH = "Kreditoren Schweiz"


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "data")


def _sheets(blaetter: dict) -> dict:
    out = {}
    for name, rows in read_workbook(write_workbook(blaetter)).items():
        headers, daten = header_and_rows(rows)
        mapping = si.spalten_vorschlag(headers)
        out[name] = {"headers": headers, "rows": daten, "mapping": mapping,
                     "art": si.art_vorschlag(name, mapping)}
    return out


def _k(konto, name, strasse="Musterstr. 1", plz="50667", ort="Köln", land="DE", n2=""):
    return [konto, name[:40], name, "1604", n2, strasse, land, plz, ort]


def _vor(tmp_path, blaetter):
    return si.vorschau(_store(tmp_path), _sheets(blaetter))


def _z(vor, blatt=DE):
    return vor["blaetter"][blatt]["zeilen"]


def _fragen(vor, blatt=DE):
    return [(z["zeile"], f["feld"]) for z in _z(vor, blatt) for f in z["fragen"]]


# ---- Schritt 1: Zellwerte ---------------------------------------------------

def test_zahl_ausschreiben():
    assert zahl_ausschreiben("12345.0") == "12345"
    assert zahl_ausschreiben("1.2345E+4") == "12345"
    assert zahl_ausschreiben("7.0001E+4") == "70001"
    assert zahl_ausschreiben("1E3") == "1000"
    assert zahl_ausschreiben("12345") == "12345"
    assert zahl_ausschreiben("12.5") == "12.5"            # nicht ganzzahlig: unveraendert
    assert zahl_ausschreiben("1.5E+0") == "1.5E+0"        # ebenfalls nicht ganzzahlig
    assert zahl_ausschreiben("abc") == "abc"
    assert zahl_ausschreiben("1E999") == "1E999"           # absurd gross: nicht ausschreiben


def test_nur_reine_zahlzellen_werden_umgeschrieben():
    daten = write_workbook({"B": [["Konto", "Name"],
                                  [Zahl("12345.0"), "12345.0"],     # Zahl vs. Text
                                  [Zahl("1.2345E+4"), "1.2345E+4"]]})
    rows = read_workbook(daten)["B"]
    assert rows[1] == ["12345", "12345.0"]
    assert rows[2] == ["12345", "1.2345E+4"]


def test_text_saeubern():
    assert text_saeubern("  Muster GmbH\t\n& Co.  KG ") == "Muster GmbH & Co. KG"
    assert text_saeubern("ＡＢＣ") == "ABC"                     # NFKC
    assert text_saeubern(None) == ""


def test_werte_werden_gesaeubert_passwort_nicht(tmp_path):
    post = [POST_KOPF, ["001", " a@example.com ", "ge  heim ", "ABC - X", "Name\tY"]]
    vor = si.vorschau(_store(tmp_path), _sheets({"Mandanten": [MAND_KOPF, ["001", "ABC - X", "", ""]],
                                                 "Postfächer": post}))
    w = _z(vor, "Postfächer")[0]["werte"]
    assert w["email"] == "a@example.com" and w["postfach_name"] == "Name Y"
    assert w["passwort"] == "ge  heim"     # nur getrimmt, innen unveraendert


def test_wiederholte_kopfzeile_wird_uebersprungen_mit_zaehler(tmp_path):
    kred = [KOPF, _k("1", "A GmbH"), [h.upper() for h in KOPF], _k("2", "B GmbH"),
            [" " + h + " " for h in KOPF]]
    vor = _vor(tmp_path, {DE: kred})
    assert [z["werte"]["kreditorennummer"] for z in _z(vor)] == ["1", "2"]
    assert vor["zusammenfassung"]["kopfzeilen_uebersprungen"] == 2
    assert [x["zeile"] for x in vor["automatisch"]["kopfzeilen"]] == [f"{DE}:2", f"{DE}:4"]
    assert not _fragen(vor)


# ---- Schritt 4: Leer-/Summenzeilen -----------------------------------------

def test_summen_und_trennzeilen_werden_uebersprungen(tmp_path):
    kred = [KOPF, _k("1", "A GmbH"),
            ["", "", "", "Summe: 1", "", "", "", "", ""],
            ["-"] * 9,
            ["", "", "Gesamt: 5", "", "", "", "", "", ""]]
    vor = _vor(tmp_path, {DE: kred})
    assert len(_z(vor)) == 1 and not _fragen(vor)
    assert vor["zusammenfassung"]["leerzeilen_uebersprungen"] == 3


def test_zeile_mit_konto_aber_ohne_name_bleibt_frage(tmp_path):
    kred = [KOPF, ["4711", "", "", "1604", "", "", "DE", "", ""]]
    vor = _vor(tmp_path, {DE: kred})
    assert [f["grund"] for z in _z(vor) for f in z["fragen"]] == ["Pflichtfeld fehlt: Name"]
    assert vor["zusammenfassung"]["leerzeilen_uebersprungen"] == 0


def test_zeile_mit_iban_ohne_name_und_konto_wird_nicht_verschluckt(tmp_path):
    kopf = ["Name", "IBAN", "Land"]
    vor = si.vorschau(_store(tmp_path), {"B": {
        "headers": kopf, "rows": [["", "DE89370400440532013000", "DE"]], "mapping": None,
        "art": "kreditor_de"}})
    assert vor["zusammenfassung"]["leerzeilen_uebersprungen"] == 0
    assert _fragen(vor, "B") == [(1, "name")]


# ---- Schritt 3: PLZ ohne fuehrende Null -------------------------------------

def test_plz_vierstellig_in_de_blatt_bekommt_null_mit_hinweis(tmp_path):
    kred = [KOPF, _k("1", "A GmbH", plz="1067", ort="Dresden"),
            _k("2", "B GmbH", plz=Zahl("1309"), ort="Dresden")]
    vor = _vor(tmp_path, {DE: kred})
    z = _z(vor)
    assert [x["werte"]["plz"] for x in z] == ["01067", "01309"]
    assert all(not x["fragen"] and x["status"] == "gruen" for x in z)
    assert all(any("führende Null ergänzt" in h for h in x["hinweise"]) for x in z)
    assert vor["zusammenfassung"]["fuehrende_nullen_ergaenzt"] == 2
    assert len(vor["automatisch"]["fuehrende_null"]) == 2


@pytest.mark.parametrize("plz,blatt,land", [
    ("123", DE, "DE"),          # 3-stellig: keine einzige Deutung
    ("ABCD", DE, "DE"),
    ("1067", DE, "CH"),         # Land-Spalte widerspricht dem Blatt
])
def test_plz_sonst_bleibt_frage(tmp_path, plz, blatt, land):
    vor = _vor(tmp_path, {DE: [KOPF, _k("1", "A GmbH", plz=plz, land=land)]})
    felder = {f["feld"] for z in _z(vor) for f in z["fragen"]}
    assert "plz" in felder or "land" in felder
    assert vor["zusammenfassung"]["fuehrende_nullen_ergaenzt"] == 0
    assert _z(vor)[0]["werte"]["plz"] == plz


def test_plz_ch_vierstellig_ist_gueltig_und_unberuehrt(tmp_path):
    vor = _vor(tmp_path, {CH: [KOPF, _k("8", "A AG", plz="8001", ort="Zürich", land="CH")]})
    assert not _fragen(vor, CH) and vor["zusammenfassung"]["fuehrende_nullen_ergaenzt"] == 0
    assert _z(vor, CH)[0]["werte"]["plz"] == "8001"


# ---- Schritt 2a/b: identische + ergaenzende Dubletten -----------------------

def test_identische_zeilen_werden_eine_ohne_frage(tmp_path):
    kred = [KOPF, _k("1", "A GmbH"), _k("1", "  a gmbh ", strasse="musterstr.  1"),
            _k("1", "A GmbH")]
    vor = _vor(tmp_path, {DE: kred})
    z = _z(vor)
    assert not _fragen(vor)
    assert not z[0].get("zusammengefasst")
    assert [x["zusammengefasst"]["art"] for x in z[1:]] == ["identisch", "identisch"]
    assert all(x["zusammengefasst"]["mit"] == f"{DE}:1" for x in z[1:])
    assert all(x["weglassen"] for x in z[1:])
    assert any("identisch mit Zeile" in h for h in z[1]["hinweise"])
    zs = vor["zusammenfassung"]
    assert zs["identische_zeilen_zusammengefasst"] == 2
    assert zs["gruen"] == 1 and zs["bereit_zum_uebernehmen"] == 1


def test_ergaenzende_zeilen_werden_vereinigt(tmp_path):
    kred = [KOPF, _k("1", "A GmbH", strasse="", plz="", ort=""),
            _k("1", "A GmbH", strasse="Weg 2", plz="50667", ort="Köln")]
    vor = _vor(tmp_path, {DE: kred})
    z = _z(vor)
    assert not _fragen(vor)
    assert z[0]["werte"]["strasse"] == "Weg 2" and z[0]["werte"]["plz"] == "50667"
    assert z[1]["zusammengefasst"] == {"art": "ergaenzend", "mit": f"{DE}:1"}
    assert any("aus Zeilen" in h and f"{DE}:2" in h for h in z[0]["hinweise"])
    assert vor["zusammenfassung"]["zeilen_ergaenzend_zusammengefuehrt"] == 1


def test_ergaenzend_ohne_konto_und_name_fehlt_nur_auf_einer_seite(tmp_path):
    kred = [KOPF, ["5", "", "", "1604", "", "", "DE", "", ""], _k("5", "A GmbH")]
    vor = _vor(tmp_path, {DE: kred})
    assert not _fragen(vor)                       # Pflichtfeld Name ist nach dem Zusammenfuehren da
    assert _z(vor)[0]["werte"]["name"] == "A GmbH"
    assert _z(vor)[0]["status"] == "gruen"


def test_zeile_ohne_konto_ergaenzt_genau_einen_vertraeglichen_datensatz(tmp_path):
    kred = [KOPF, _k("7", "A GmbH", strasse=""), _k("", "A GmbH", strasse="Weg 2")]
    vor = _vor(tmp_path, {DE: kred})
    assert not _fragen(vor)
    z = _z(vor)
    assert z[0]["werte"]["strasse"] == "Weg 2" and z[1]["zusammengefasst"]["art"] == "ergaenzend"


def test_zeile_ohne_konto_bei_zwei_moeglichen_datensaetzen_fragt(tmp_path):
    kred = [KOPF, _k("7", "A GmbH", strasse=""), _k("8", "A GmbH", strasse=""),
            _k("", "A GmbH", strasse="Weg 2")]
    vor = _vor(tmp_path, {DE: kred})
    # nicht raten, welcher der beiden Datensaetze gemeint ist: alle drei fragen
    assert sorted(_fragen(vor)) == [(1, "dublette"), (2, "dublette"), (3, "dublette")]
    assert not any(z.get("zusammengefasst") for z in _z(vor))


def test_unterschiedliche_kontonummer_ist_kein_ergaenzen(tmp_path):
    kred = [KOPF, _k("1", "A GmbH"), _k("2", "A GmbH")]
    vor = _vor(tmp_path, {DE: kred})
    assert not any(z.get("zusammengefasst") for z in _z(vor))
    assert vor["zusammenfassung"]["zeilen_ergaenzend_zusammengefuehrt"] == 0


# ---- Schritt 2c: gleicher Name, andere Kontonummer -> getrennt --------------

def test_gleicher_name_andere_konto_sind_getrennte_lieferanten_ohne_frage(tmp_path):
    kred = [KOPF, _k("1", "A GmbH"), _k("2", "A GmbH")]
    vor = _vor(tmp_path, {DE: kred})
    z = _z(vor)
    assert not _fragen(vor) and all(x["status"] == "gruen" for x in z)
    assert z[0]["getrennt_von"] == [f"{DE}:2"] and z[1]["getrennt_von"] == [f"{DE}:1"]
    assert "getrennter Lieferant (Entscheidung Aaron 17.10.)" in z[0]["hinweise"][0]
    assert vor["zusammenfassung"]["getrennte_lieferanten_gleicher_name"] == 2


def test_getrennte_lieferanten_werden_mit_eigener_id_angelegt(tmp_path):
    store = _store(tmp_path)
    sh = _sheets({DE: [KOPF, _k("1", "A GmbH"), _k("2", "A GmbH")]})
    erg = si.anwenden(store, sh, {}, "test")
    assert erg["kreditoren"] == 2
    ids = sorted(s.id for s in store.suppliers())
    assert ids == ["1", "2"]
    assert {s.name for s in store.suppliers()} == {"A GmbH"}
    # zweimal einspielen legt nichts neu an
    si.anwenden(store, sh, {}, "test")
    assert len(store.suppliers()) == 2


def test_rechnung_von_gleichnamigem_lieferant_wird_nie_still_dem_ersten_zugeordnet(tmp_path):
    from rechnung.pdf import Document, Page, Word
    sups = [Supplier(id="1", name="Muster Hotelbedarf GmbH"),
            Supplier(id="2", name="Muster Hotelbedarf GmbH")]
    woerter = [Word(w, 10.0 * i, 10.0 * i + 9, 10.0, 20.0, 0)
               for i, w in enumerate("Muster Hotelbedarf GmbH Rechnung 4711 Betrag 100,00 EUR".split())]
    doc = Document(Path("x.pdf"), [Page(0, 595.0, 842.0, woerter)], "sha")
    treffer, grund = identify_with_reason(doc, sups)
    assert treffer is None      # weder 1 noch 2 -- ein Mensch entscheidet
    assert grund


# ---- Schritt 2d: echte Widersprueche ----------------------------------------

def test_widerspruch_gleiche_zeilen_eine_frage_je_zeile_nicht_n_mal_n(tmp_path):
    kred = [KOPF] + [_k("1", "A GmbH", strasse=f"Str. {i}") for i in range(5)]
    vor = _vor(tmp_path, {DE: kred})
    assert sorted(_fragen(vor)) == [(i, "dublette") for i in range(1, 6)]   # 5 Fragen, nicht 20
    for z in _z(vor):
        assert len(z["fragen"]) == 1
        assert z["fragen"][0]["grund"].count("Zeile") == 1
        assert len(z["fragen"][0]["grund"].split("auch in Zeile ")[1].split(", ")) == 4


def test_widerspruch_gleicher_name_gleiche_konto_andere_anschrift(tmp_path):
    kred = [KOPF, _k("1", "A GmbH"), _k("1", "A GmbH", strasse="Ganz woanders 9")]
    vor = _vor(tmp_path, {DE: kred})
    assert sorted(_fragen(vor)) == [(1, "dublette"), (2, "dublette")]


def test_antwort_auf_widerspruch_funktioniert_wie_bisher(tmp_path):
    store = _store(tmp_path)
    sh = _sheets({DE: [KOPF, _k("1", "A GmbH"), _k("1", "A GmbH", strasse="Weg 9")]})
    ant = {f"{DE}:1|dublette": {"antwort": "diese Zeile übernehmen, andere weglassen"},
           f"{DE}:2|dublette": {"antwort": "Zeile weglassen"}}
    erg = si.anwenden(store, sh, ant, "test")
    assert erg["kreditoren"] == 1 and len(store.suppliers()) == 1


# ---- DE/CH-Verschmelzung (16.10.) funktioniert weiter, auch nach Zusammenfassen ----

def test_de_ch_verschmelzung_trotz_doppelter_de_zeile(tmp_path):
    de = [KOPF, _k("70001", "A GmbH"), _k("70001", "A GmbH")]
    ch = [KOPF, _k("80001", "A GmbH", plz="8001", ort="Zürich", land="CH")]
    vor = _vor(tmp_path, {DE: de, CH: ch})
    assert not _fragen(vor) and not _fragen(vor, CH)
    assert vor["zusammenfassung"]["automatisch_verschmolzen_paare"] == 1
    assert vor["zusammenfassung"]["identische_zeilen_zusammengefasst"] == 1
    erg = si.anwenden(_store(tmp_path), _sheets({DE: de, CH: ch}), {}, "t")
    assert erg["verschmolzene_lieferanten"] == 1


# ---- Mandanten / Postfaecher -------------------------------------------------

def test_mandanten_dubletten(tmp_path):
    mand = [MAND_KOPF, ["001", "ABC - Beispiel", "2026", "12"], ["1", "abc - beispiel", "2026", "12"],
            ["002", "XYZ - Muster", "", ""], ["002", "XYZ - Muster", "2026", "12"],
            ["003", "Erster", "", ""], ["003", "Zweiter", "", ""]]
    vor = _vor(tmp_path, {"Mandanten": mand})
    z = _z(vor, "Mandanten")
    assert z[1]["zusammengefasst"]["art"] == "identisch"          # 001 == 1 (Excel-Null)
    assert z[3]["zusammengefasst"]["art"] in ("identisch", "ergaenzend")   # WJ/LM ignoriert -> identisch
    assert sorted(_fragen(vor, "Mandanten")) == [(5, "dublette"), (6, "dublette")]   # echter Widerspruch


def test_postfach_dubletten_und_passwort_widerspruch(tmp_path):
    mand = [MAND_KOPF, ["001", "ABC - Beispiel", "", ""]]
    post = [POST_KOPF, ["001", "a@example.com", "pw", "ABC - Beispiel", "N"],
            ["001", "A@example.com", "pw", "ABC - Beispiel", "N"],       # identisch
            ["001", "b@example.com", "pw1", "ABC - Beispiel", "N"],
            ["001", "b@example.com", "pw2", "ABC - Beispiel", "N"]]      # Passwort widerspricht
    vor = _vor(tmp_path, {"Mandanten": mand, "Postfächer": post})
    z = _z(vor, "Postfächer")
    assert z[1]["zusammengefasst"]["art"] == "identisch"
    assert sorted(_fragen(vor, "Postfächer")) == [(3, "dublette"), (4, "dublette")]


def test_fehlende_mdt_fragt_weiterhin(tmp_path):
    mand = [MAND_KOPF, ["001", "ABC - Beispiel", "", ""]]
    post = [POST_KOPF, ["999", "a@example.com", "pw", "ABC - Beispiel", "N"]]
    vor = _vor(tmp_path, {"Mandanten": mand, "Postfächer": post})
    assert _fragen(vor, "Postfächer") == [(1, "mandant_verweis")]


# ---- zusammengefasste Zeilen werden nie uebernommen --------------------------

def test_anwenden_uebernimmt_zusammengefasste_zeile_nicht_doppelt(tmp_path):
    store = _store(tmp_path)
    sh = _sheets({DE: [KOPF, _k("1", "A GmbH", strasse="", plz="", ort=""), _k("1", "A GmbH"),
                       _k("1", "A GmbH")]})
    erg = si.anwenden(store, sh, {}, "t")
    assert erg["kreditoren"] == 1 and erg["zusammengefasst"] == 2
    s = store.supplier("1")
    assert "Musterstr. 1" in " ".join(s.address_lines)


def test_gruppen_enthalten_keine_zusammengefassten_zeilen(tmp_path):
    kred = [KOPF] + [_k("1", "A GmbH", plz="12")] * 5
    vor = _vor(tmp_path, {DE: kred})
    assert sum(g["anzahl"] for g in vor["gruppen"]) == 1      # nur die Hauptzeile fragt


# ---- Schritt 5: grosse Mengen -------------------------------------------------

def test_grosse_liste_bleibt_schnell_auch_mit_gefuellter_bibliothek(tmp_path):
    store = _store(tmp_path)
    for i in range(300):
        store.save_supplier(Supplier(id=f"alt-{i}", name=f"Alt {i} GmbH",
                                     ustids=[f"DE{300000000 + i}"], ibans=[f"DE{i:020d}"]))
    kred = [KOPF] + [_k(str(70000 + i), f"Firma {i} GmbH") for i in range(4000)]
    sh = _sheets({DE: kred})
    t0 = time.perf_counter()
    vor = si.vorschau(store, sh)
    dauer = time.perf_counter() - t0
    assert vor["zusammenfassung"]["gruen"] == 4000
    assert dauer < 2.0, f"vorschau() zu langsam: {dauer:.2f} s"


def test_bibliotheks_konflikt_gleiches_ergebnis_wie_vorher(tmp_path):
    store = _store(tmp_path)
    store.save_supplier(Supplier(id="s1", name="Alter Name GmbH", ustids=["DE123456789"]))
    kopf = ["Name", "UID", "Land"]
    sh = {"B": {"headers": kopf, "rows": [["Neuer Name GmbH", "DE 123456789", "DE"]],
                "mapping": None, "art": "kreditor_de"}}
    vor = si.vorschau(store, sh)
    f = vor["blaetter"]["B"]["zeilen"][0]["fragen"]
    assert [x["feld"] for x in f] == ["bibliothek"] and "name" in f[0]["grund"]


# ---- Schritt 6: Ausgabe fuer die UI ------------------------------------------

def test_vorschau_ist_json_faehig_und_nennt_alle_zaehler(tmp_path):
    kred = [KOPF, _k("1", "A GmbH"), _k("1", "A GmbH"), list(KOPF),
            ["", "", "", "Summe: 2", "", "", "", "", ""]]
    vor = _vor(tmp_path, {DE: kred})
    json.dumps(vor)
    zs = vor["zusammenfassung"]
    for k in ("identische_zeilen_zusammengefasst", "zeilen_ergaenzend_zusammengefuehrt",
              "fuehrende_nullen_ergaenzt", "leerzeilen_uebersprungen", "kopfzeilen_uebersprungen",
              "getrennte_lieferanten_gleicher_name"):
        assert k in zs
    assert zs["identische_zeilen_zusammengefasst"] == 1 and zs["kopfzeilen_uebersprungen"] == 1
    assert {"identisch", "ergaenzend", "fuehrende_null", "leerzeilen", "kopfzeilen"} <= \
        set(vor["automatisch"])


def test_ui_zeigt_die_neuen_zaehler():
    html = (Path(__file__).resolve().parent.parent / "rechnung" / "web" / "static"
            / "stammdaten.html").read_text("utf-8")
    for text in ("identische Zeile(n) zusammengefasst", "ergänzend zusammengeführt",
                 "führende Null(en) ergänzt", "Leer-/Summenzeile(n) übersprungen",
                 "wiederholte Kopfzeile(n) übersprungen", "<details class=\"auto\">"):
        assert text in html
