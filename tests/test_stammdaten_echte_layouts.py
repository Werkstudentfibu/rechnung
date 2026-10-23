"""Aarons echte Listen (Spaltenkoepfe 15.10.2026, nur Platzhalterwerte): Kreditoren
DE/CH (Konto/Name1/Name2/...), Mandanten (Mandanten Nr./Bezeichnung/WJ/LM),
Postfaecher (MDT/E-Mail/Passwort/Bezeichnung/Name). Grundsatz: nichts raten,
nichts still ueberschreiben."""

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
KRED_CH = [KRED_KOPF,
           ["80000", "Muster Treuhand AG", "Muster Treuhand AG", "1605", "",
            "Bahnhofstrasse 3", "CH", "8001", "Zürich"]]
MANDANTEN = [MAND_KOPF, ["001", "ABC - Beispiel-Mandant", "2026", "12"],
             ["002", "XYZ - Musterhotel Köln", "2026", "12"]]
POSTFAECHER = [POST_KOPF,
               ["001", "abc@example.com", "geheim-eins", "ABC - Beispiel-Mandant",
                "Beispiel-Mandant Hotel GmbH"],
               ["002", "xyz@example.com", "geheim-zwei", "XYZ - Musterhotel Köln",
                "Musterhotel Köln GmbH"]]


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "data")


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
    return {"Kreditoren Deutschland": KRED_DE, "Kreditoren Schweiz": KRED_CH,
            "Mandanten": MANDANTEN, "Postfächer": POSTFAECHER}


def _zeilen(vor: dict, blatt: str) -> list[dict]:
    return vor["blaetter"][blatt]["zeilen"]


def test_bekannte_layouts_werden_automatisch_zugeordnet():
    s = _sheets(_alle())
    assert s["Kreditoren Deutschland"]["mapping"] == {
        "Konto": "kreditorennummer", "Bezeichnung 1": "bezeichnung1", "Name1": "name1",
        "SammelKt": "notiz", "Name2": "name2", "Straße": "strasse", "Land": "land",
        "PlzOrt": "plz", "Ortsbezeichnung": "ort"}
    assert s["Mandanten"]["mapping"] == {
        "Mandanten Nr.": "mandant_verweis", "Bezeichnung": "name",
        "WJ": si.IGNORIEREN, "LM": si.IGNORIEREN}
    assert s["Postfächer"]["mapping"] == {
        "MDT": "mandant_verweis", "E-Mail": "email", "Passwort": "passwort",
        "Bezeichnung": "postfach_kuerzel", "Name": "postfach_name"}
    assert s["Kreditoren Deutschland"]["art"] == si.ART_KREDITOR_DE
    assert s["Kreditoren Schweiz"]["art"] == si.ART_KREDITOR_CH
    assert s["Mandanten"]["art"] == si.ART_MANDANT
    assert s["Postfächer"]["art"] == si.ART_POSTFACH
    # nichts bleibt unentschieden
    assert all(v is not None for blatt in s.values() for v in blatt["mapping"].values())


def test_kreditorenliste_ohne_blattnamen_entscheidet_die_land_spalte():
    s = _sheets({"Tabelle1": [KRED_KOPF, KRED_DE[1], KRED_CH[1]]})
    assert s["Tabelle1"]["art"] == si.ART_GEMISCHT_LAND
    vor = si.vorschau(_store(Path("/tmp/_x_land_" + str(id(s)))), s)
    arten = [z["art"] for z in _zeilen(vor, "Tabelle1")]
    assert arten == [si.ART_KREDITOR_DE, si.ART_KREDITOR_CH]


def test_mandantenblatt_ohne_sprechenden_namen_wird_erkannt():
    s = _sheets({"Tabelle1": MANDANTEN})
    assert s["Tabelle1"]["art"] == si.ART_MANDANT


def test_alles_gruen_und_uebernahme(tmp_path):
    store = _store(tmp_path)
    s = _sheets(_alle())
    vor = si.vorschau(store, s)
    assert vor["zusammenfassung"]["gelb"] == 0 and vor["zusammenfassung"]["rot"] == 0
    assert vor["zusammenfassung"]["gruen"] == 7
    erg = si.anwenden(store, s, {}, "aaron")
    assert (erg["kreditoren"], erg["mandanten"], erg["postfaecher"]) == (3, 2, 2)

    sup = store.supplier("70000")
    assert sup.name == "Musterfirma Beratungs- und Dienstleistungsgesellschaft mbH"
    assert sup.external_id == "70000" and sup.currency == "EUR"
    assert sup.address_lines == ["Musterstraße 1", "12345 Musterstadt", "DE"]
    assert "SammelKt: 1604" in sup.notes
    ch = store.supplier("80000")
    assert ch.currency == "CHF" and ch.address_lines[-1] == "CH"

    m = store.address("001")
    assert m.company_code == "001" and m.name == "ABC - Beispiel-Mandant"
    # auf einer Rechnung steht nur der Klartext, nie "ABC - ..."
    assert m.match_terms == ["Beispiel-Mandant"]
    assert "Kürzel: ABC" in m.notes
    assert "2026" not in m.notes   # WJ/LM werden ignoriert, nicht abgelegt

    pf = store.postfach("abc-example-com")
    assert pf.mandant_id == "001" and pf.mandant_name == "Beispiel-Mandant Hotel GmbH"
    assert pf.user == "abc@example.com"


def test_postfaecher_vor_mandanten_im_blatt_werden_trotzdem_verknuepft(tmp_path):
    store = _store(tmp_path)
    s = _sheets({"Postfächer": POSTFAECHER, "Mandanten": MANDANTEN})
    si.anwenden(store, s, {}, "aaron")
    assert {p.mandant_id for p in store.postfaecher()} == {"001", "002"}


def test_mdt_ohne_fuehrende_nullen_findet_den_mandanten(tmp_path):
    store = _store(tmp_path)
    post = [POST_KOPF, ["1", "abc@example.com", "x", "ABC - Beispiel-Mandant", "Name"]]
    s = _sheets({"Mandanten": MANDANTEN, "Postfächer": post})
    vor = si.vorschau(store, s)
    assert _zeilen(vor, "Postfächer")[0]["status"] == si.STATUS_GRUEN
    si.anwenden(store, s, {}, "aaron")
    assert store.postfach("abc-example-com").mandant_id == "001"


def test_wj_lm_machen_keine_frage_und_keine_ampel(tmp_path):
    vor = si.vorschau(_store(tmp_path), _sheets({"Mandanten": MANDANTEN}))
    assert all(z["status"] == si.STATUS_GRUEN and not z["fragen"]
               for z in _zeilen(vor, "Mandanten"))


def test_bezeichnung1_widerspruch_ist_eine_frage_kein_raten(tmp_path):
    kred = [KRED_KOPF, ["70000", "Ganz Andere Firma AG", "Musterfirma Beratungs- und",
                        "1604", "Dienstleistungsgesellschaft mbH", "Str 1", "DE", "12345", "Ort"]]
    z = _zeilen(si.vorschau(_store(tmp_path), _sheets({"Kreditoren Deutschland": kred})),
                "Kreditoren Deutschland")[0]
    assert z["status"] == si.STATUS_GELB
    f = [f for f in z["fragen"] if f["feld"] == "name"][0]
    assert "Bezeichnung 1" in f["grund"] and f["vorschlag"].startswith("Musterfirma Beratungs-")


def test_wort_mitten_getrennt_wird_ueber_bezeichnung1_erkannt(tmp_path):
    kred = [KRED_KOPF, ["70000", "Muster Dienstleistungsgesellschaft", "Muster Dienstleist",
                        "1604", "ungsgesellschaft mbH", "Str 1", "DE", "12345", "Ort"]]
    z = _zeilen(si.vorschau(_store(tmp_path), _sheets({"Kreditoren Deutschland": kred})),
                "Kreditoren Deutschland")[0]
    assert z["werte"]["name"] == "Muster Dienstleistungsgesellschaft mbH"
    assert z["status"] == si.STATUS_GRUEN and z["hinweise"]


def test_plzort_mit_ort_wird_getrennt_und_widerspruch_gefragt(tmp_path):
    ok = [KRED_KOPF, ["1", "A GmbH", "A GmbH", "1", "", "S 1", "DE", "50667 Köln", "Köln"]]
    z = _zeilen(si.vorschau(_store(tmp_path), _sheets({"Kreditoren Deutschland": ok})),
                "Kreditoren Deutschland")[0]
    assert z["werte"]["plz"] == "50667" and z["status"] == si.STATUS_GRUEN
    streit = [KRED_KOPF, ["1", "A GmbH", "A GmbH", "1", "", "S 1", "DE", "50667 Köln", "Bonn"]]
    z = _zeilen(si.vorschau(_store(tmp_path), _sheets({"Kreditoren Deutschland": streit})),
                "Kreditoren Deutschland")[0]
    assert z["status"] == si.STATUS_GELB and z["fragen"][0]["feld"] == "ort"


def test_plz_mit_verlorener_null_wird_automatisch_ergaenzt(tmp_path):
    kred = [KRED_KOPF, ["1", "A GmbH", "A GmbH", "1", "", "S 1", "DE", "1067", "Dresden"]]
    z = _zeilen(si.vorschau(_store(tmp_path), _sheets({"Kreditoren Deutschland": kred})),
                "Kreditoren Deutschland")[0]
    assert z["werte"]["plz"] == "01067" and z["status"] == si.STATUS_GRUEN and not z["fragen"]
    assert any("führende Null ergänzt" in h for h in z["hinweise"])


def test_vertippter_mdt_wird_ueber_das_kuerzel_gefunden(tmp_path):
    post = [POST_KOPF, ["002", "abc@example.com", "x", "ABC - Beispiel-Mandant", "Name"]]
    vor = si.vorschau(_store(tmp_path), _sheets({"Mandanten": MANDANTEN, "Postfächer": post}))
    z = _zeilen(vor, "Postfächer")[0]
    assert z["status"] == si.STATUS_GELB
    assert "Kürzel 'XYZ'" in z["fragen"][0]["grund"]


def test_gleiche_konto_nr_in_de_und_ch_wird_gefragt_und_nie_ueberschrieben(tmp_path):
    store = _store(tmp_path)
    ch = [KRED_KOPF, ["70000", "Muster Treuhand AG", "Muster Treuhand AG", "1605", "",
                      "Bahnhofstrasse 3", "CH", "8001", "Zürich"]]
    s = _sheets({"Kreditoren Deutschland": [KRED_KOPF, KRED_DE[1]], "Kreditoren Schweiz": ch})
    vor = si.vorschau(store, s)
    assert all(z["status"] == si.STATUS_GELB for b in ("Kreditoren Deutschland", "Kreditoren Schweiz")
               for z in _zeilen(vor, b))
    # nichts uebernommen, solange nicht beantwortet
    assert si.anwenden(store, s, {}, "aaron")["kreditoren"] == 0 and store.suppliers() == []
    antworten = {f"{z['schluessel']}|{f['feld']}": {"antwort": "beide übernehmen (sind unterschiedlich)"}
                 for b in ("Kreditoren Deutschland", "Kreditoren Schweiz")
                 for z in _zeilen(vor, b) for f in z["fragen"]}
    erg = si.anwenden(store, s, antworten, "aaron")
    assert erg["kreditoren"] == 2 and len(store.suppliers()) == 2
    assert {x.name for x in store.suppliers()} == {
        "Musterfirma Beratungs- und Dienstleistungsgesellschaft mbH", "Muster Treuhand AG"}


def test_zwei_spalten_auf_dasselbe_feld_werden_nicht_still_ueberschrieben(tmp_path):
    sheets = {"Postfächer": {
        "headers": ["MDT", "E-Mail", "Bezeichnung", "Name"],
        "rows": [["001", "a@example.com", "A", "B"]],
        "mapping": {"MDT": "mandant_verweis", "E-Mail": "email", "Bezeichnung": "name",
                    "Name": "name"},
        "art": si.ART_POSTFACH}}
    z = _zeilen(si.vorschau(_store(tmp_path), sheets), "Postfächer")[0]
    assert z["status"] == si.STATUS_GELB
    assert any("Zwei Spalten" in f["grund"] for f in z["fragen"])


def test_ungueltige_email_ist_eine_frage(tmp_path):
    post = [POST_KOPF, ["001", "kein-mailadresse", "x", "ABC - Beispiel-Mandant", "Name"]]
    z = _zeilen(si.vorschau(_store(tmp_path), _sheets({"Mandanten": MANDANTEN, "Postfächer": post})),
                "Postfächer")[0]
    assert any(f["feld"] == "email" for f in z["fragen"])


def test_doppelte_mandanten_nr_und_email_werden_gefragt(tmp_path):
    mand = [MAND_KOPF, ["001", "ABC - Eins", "1", "1"], ["1", "DEF - Zwei", "1", "1"]]
    vor = si.vorschau(_store(tmp_path), _sheets({"Mandanten": mand}))
    assert all(any(f["feld"] == "dublette" for f in z["fragen"]) for z in _zeilen(vor, "Mandanten"))


def test_erneuter_import_dupliziert_nichts(tmp_path):
    store = _store(tmp_path)
    s = _sheets(_alle())
    si.anwenden(store, s, {}, "aaron")
    n = (len(store.suppliers()), len(store.addresses()), len(store.postfaecher()))
    notes = (store.supplier("70000").notes, store.address("001").notes)
    erg = si.anwenden(store, s, {}, "aaron")
    # beim zweiten Mal meldet die Bibliothek Konflikte nur bei ABWEICHENDEN Werten
    assert (len(store.suppliers()), len(store.addresses()), len(store.postfaecher())) == n
    assert (store.supplier("70000").notes, store.address("001").notes) == notes
    # Zweiter Lauf mit identischen Daten: unveraenderte Zeilen werden anhand
    # ihres Fingerabdrucks erkannt und ueberSPRUNGEN statt neu geschrieben.
    assert erg["kreditoren"] + erg["uebersprungen"] + erg["unveraendert_uebersprungen"] >= 3


def test_passwort_steht_nirgends_im_klartext_ausser_in_der_geheimdatei(tmp_path):
    store = _store(tmp_path)
    si.anwenden(store, _sheets(_alle()), {}, "aaron")
    erlaubt = {"postfach_secrets.json", "postfach_secrets_unsicher.json"}
    for p in (tmp_path / "data").rglob("*"):
        if p.is_file() and p.name not in erlaubt:
            assert "geheim-eins" not in p.read_text("utf-8", errors="ignore"), p.name


def test_unbekannte_zusatzspalte_bleibt_unentschieden(tmp_path):
    s = _sheets({"Mandanten": [MAND_KOPF + ["Bemerkung"], ["001", "ABC - A", "1", "1", "x"]]})
    assert s["Mandanten"]["mapping"]["Bemerkung"] is None
    assert s["Mandanten"]["mapping"]["Mandanten Nr."] == "mandant_verweis"
