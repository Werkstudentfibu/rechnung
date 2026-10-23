"""Antwort „zusammenführen (gleicher Lieferant)“ bei der Dubletten-Frage
(Aaron 19.10.2026). Nur synthetische Platzhalterdaten."""

from __future__ import annotations

import itertools
import random
from pathlib import Path

from rechnung import stammdaten_import as si
from rechnung.store import Store

from _xlsx_helpers import write_workbook
from rechnung.xlsx import header_and_rows, read_workbook

KOPF = ["Konto", "Name1", "Straße", "Land", "PLZ", "Ort", "USt-IdNr", "IBAN", "BIC"]
DE = "Kreditoren Deutschland"
CH = "Kreditoren Schweiz"
M = si.ANTWORT_ZUSAMMENFUEHREN
BEIDE = "beide übernehmen (sind unterschiedlich)"
IBAN1 = "DE89370400440532013000"
IBAN2 = "DE02120300000000202051"
UST1 = "DE123456789"
UST2 = "DE987654321"


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


def _r(konto="1", name="A GmbH", strasse="Weg 1", land="DE", plz="50667", ort="Köln",
       ust="", iban="", bic=""):
    return [konto, name, strasse, land, plz, ort, ust, iban, bic]


def _ant(*nrn, antwort=M):
    return {f"{DE}:{n}|dublette": {"antwort": antwort} for n in nrn}


def _vor(tmp_path, rows, ant=None, blatt=DE):
    return si.vorschau(_store(tmp_path), _sheets({blatt: [KOPF, *rows]}), ant)


def _z(vor, blatt=DE):
    return vor["blaetter"][blatt]["zeilen"]


# ---- Angebot der Antwort -----------------------------------------------------

def test_antwort_wird_bei_kreditor_dubletten_angeboten_und_ist_sammelfaehig(tmp_path):
    vor = _vor(tmp_path, [_r(), _r(strasse="Weg 2")])
    f = _z(vor)[0]["fragen"][0]
    assert f["feld"] == "dublette" and M in f["antwortmoeglichkeiten"]
    g = vor["gruppen"][0]
    assert g["anzahl"] == 2 and M in g["sammelfaehig"]


def test_antwort_nicht_bei_mandanten(tmp_path):
    kopf = ["Mandanten Nr.", "Bezeichnung", "WJ", "LM"]
    sh = _sheets({"Mandanten": [kopf, ["001", "ABC - X", "2026", "12"], ["1", "ABC - Y", "2026", "12"]]})
    vor = si.vorschau(_store(tmp_path), sh)
    fr = [f for z in _z(vor, "Mandanten") for f in z["fragen"] if f["feld"] == "dublette"]
    assert fr and all(M not in f["antwortmoeglichkeiten"] for f in fr)


# ---- 2er-Paar ------------------------------------------------------------------

def test_paar_vollstaendigere_zeile_gewinnt_abweichung_wird_notiz(tmp_path):
    rows = [_r(strasse="", plz="", ort="Bonn"),                        # Zeile 1: duenn
            _r(strasse="Weg 2", ort="Köln", ust=UST1, iban=IBAN1)]     # Zeile 2: voller
    vor = _vor(tmp_path, rows, _ant(1, 2))
    z1, z2 = _z(vor)
    assert z2["werte"]["ort"] == "Köln" and z2["werte"]["strasse"] == "Weg 2"
    assert z1["zusammengefasst"] == {"art": "dublette", "mit": f"{DE}:2"}
    assert f"{DE}:1" in z2["zusammengefuehrt_dublette"]
    assert "zusammengeführt aus Zeile Kreditoren Deutschland:1: Ort = Bonn" in z2["werte"]["notiz"]
    assert any("zusammengeführt" in h for h in z1["hinweise"])
    assert any("zusammengeführt" in h for h in z2["hinweise"])
    assert z2["beantwortet"] and z1["weglassen"]
    assert vor["zusammenfassung"]["zeilen_dublette_zusammengefuehrt"] == 1


def test_gleichstand_fruehere_zeile_gewinnt(tmp_path):
    vor = _vor(tmp_path, [_r(ort="Bonn"), _r(ort="Köln")], _ant(1, 2))
    z1, z2 = _z(vor)
    assert z1["werte"]["ort"] == "Bonn" and z2["zusammengefasst"]["mit"] == f"{DE}:1"
    assert "Ort = Köln" in z1["werte"]["notiz"]


def test_leere_felder_des_gewinners_werden_aufgefuellt(tmp_path):
    rows = [_r(strasse="", plz="50668", ort="", bic="BYLADEM1001"),
            _r(strasse="Weg 2", bic="")]
    vor = _vor(tmp_path, rows, _ant(1, 2))
    z1, z2 = _z(vor)
    assert z2["werte"]["bic"] == "BYLADEM1001"
    assert "BIC" not in z2["werte"]["notiz"]      # Auffuellen ist keine Abweichung
    assert "PLZ = 50668" in z2["werte"]["notiz"]


# ---- Mehrwert / Einwert ----------------------------------------------------------

def test_mehrwertfelder_behalten_alle_werte(tmp_path):
    rows = [_r(ust=UST1, iban=IBAN1), _r(strasse="Weg 2", ust=UST2, iban=IBAN2)]
    ant = _ant(1, 2)
    sh = _sheets({DE: [KOPF, *rows]})
    store = _store(tmp_path)
    erg = si.anwenden(store, sh, ant, "t")
    assert erg["kreditoren"] == 1 and erg["dubletten_zusammengefuehrt"] == 1
    assert erg["zusammengefasst"] == 1
    (sup,) = store.suppliers()
    assert sorted(sup.ibans) == sorted([IBAN1, IBAN2])
    assert sorted(sup.ustids) == sorted([UST1, UST2])


def test_ungueltiger_zusatz_iban_wird_nicht_wert_sondern_notiz(tmp_path):
    rows = [_r(iban=IBAN1, strasse="Weg 1", ust=UST1), _r(strasse="", iban="DE00123456780000000000")]
    store = _store(tmp_path)
    si.anwenden(store, _sheets({DE: [KOPF, *rows]}), _ant(1, 2), "t")
    (sup,) = store.suppliers()
    assert sup.ibans == [IBAN1]
    assert "DE00123456780000000000" in sup.notes and "nicht als Wert übernommen" in sup.notes


def test_einwertkonflikt_kein_informationsverlust(tmp_path):
    rows = [_r(strasse="Weg 1", plz="50667", ort="Köln", ust=UST1),
            _r(strasse="Hafenstr. 9", plz="20095", ort="Hamburg")]
    store = _store(tmp_path)
    si.anwenden(store, _sheets({DE: [KOPF, *rows]}), _ant(1, 2), "t")
    (sup,) = store.suppliers()
    assert sup.address_lines[0] == "Weg 1"
    for teil in ("Hafenstr. 9", "20095", "Hamburg"):
        assert teil in sup.notes


# ---- Kreditorennummern ------------------------------------------------------------

def test_verschiedene_kreditorennummern_gewinner_bleibt_andere_in_notiz(tmp_path):
    rows = [_r(konto="1001", ust=UST1), _r(konto="1002", strasse="Weg 2", ust=UST1, iban=IBAN1)]
    store = _store(tmp_path)
    vor = _vor(tmp_path, rows, _ant(1, 2))
    assert [f["feld"] for z in _z(vor) for f in z["fragen"]] == ["dublette"]
    si.anwenden(store, _sheets({DE: [KOPF, *rows]}), _ant(1, 2), "t")
    (sup,) = store.suppliers()
    assert sup.id == "1002"                       # vollstaendigere Zeile 2
    assert "Kreditorennummer = 1001" in sup.notes


# ---- 3er-Verbund, transitiv, Reihenfolge ---------------------------------------------

def _drei():
    return [_r(strasse="", plz="", ort="", iban=IBAN1),                 # 1
            _r(ust=UST1, iban=IBAN1, ort="Köln"),                       # 2 (verbindet 1+3)
            _r(strasse="Weg 3", ort="Bonn", ust=UST1, bic="BYLADEM1001")]  # 3 (voller)


def test_dreier_verbund_ein_gewinner(tmp_path):
    vor = _vor(tmp_path, _drei(), _ant(1, 2, 3))
    z = _z(vor)
    # Zeile 2 und 3 gleich voll -> die fruehere (2) gewinnt
    assert z[1]["zusammengefuehrt_dublette"] == [f"{DE}:1", f"{DE}:3"]
    assert z[0]["zusammengefasst"]["mit"] == f"{DE}:2" and z[2]["zusammengefasst"]["mit"] == f"{DE}:2"
    assert z[1]["werte"]["bic"] == "BYLADEM1001" and "Ort = Bonn" in z[1]["werte"]["notiz"]
    assert vor["zusammenfassung"]["zeilen_dublette_zusammengefuehrt"] == 2


def test_ergebnis_unabhaengig_von_antwortreihenfolge(tmp_path):
    erg = set()
    for perm in itertools.permutations([1, 2, 3]):
        ant = {}
        for n in perm:
            ant.update(_ant(n))
        vor = _vor(tmp_path, _drei(), ant)
        erg.add(repr([(z["zusammengefasst"] if z.get("zusammengefasst") else z["werte"]) for z in _z(vor)]))
    assert len(erg) == 1


def test_transitiv_a_b_c_ueber_kette(tmp_path):
    # 1~2 ueber UID, 2~3 ueber IBAN, 1/3 teilen nichts direkt
    rows = [_r(konto="", name="Eins GmbH", ust=UST1),
            _r(konto="", name="Zwei GmbH", ust=UST1, iban=IBAN1, strasse="Weg 2"),
            _r(konto="", name="Drei GmbH", iban=IBAN1, strasse="Weg 3", ort="Bonn")]
    vor = _vor(tmp_path, rows, _ant(1, 2, 3))
    assert sum(1 for z in _z(vor) if z.get("zusammengefasst")) == 2


# ---- Mischantworten --------------------------------------------------------------------

def test_mischantwort_beide_uebernehmen_zieht_nicht_mit(tmp_path):
    rows = [_r(strasse="Weg 1"), _r(strasse="Weg 2"), _r(strasse="Weg 3")]
    ant = {**_ant(1, 2), **_ant(3, antwort=BEIDE)}
    vor = _vor(tmp_path, rows, ant)
    z = _z(vor)
    assert z[1]["zusammengefasst"]["mit"] == f"{DE}:1" and not z[2].get("zusammengefasst")
    assert "Straße = Weg 3" not in (z[0]["werte"].get("notiz") or "")


def test_mischantwort_weglassen_schliesst_zeile_aus(tmp_path):
    rows = [_r(strasse="Weg 1"), _r(strasse="Weg 2")]
    ant = {**_ant(1), **_ant(2, antwort="Zeile weglassen")}
    store = _store(tmp_path)
    vor = _vor(tmp_path, rows, ant)
    assert not any(z.get("zusammengefasst") for z in _z(vor))
    erg = si.anwenden(store, _sheets({DE: [KOPF, *rows]}), ant, "t")
    assert erg["kreditoren"] == 1 and erg["uebersprungen"] == 1
    assert "Weg 2" not in "".join(store.suppliers()[0].address_lines)


def test_einseitig_zusammenfuehren_ohne_partner_bleibt_einzelzeile(tmp_path):
    rows = [_r(strasse="Weg 1"), _r(strasse="Weg 2")]
    vor = _vor(tmp_path, rows, {**_ant(1), **_ant(2, antwort=BEIDE)})
    assert not any(z.get("zusammengefasst") for z in _z(vor))
    assert all(z["beantwortet"] for z in _z(vor))


# ---- Nur gleiche Art --------------------------------------------------------------------

def test_de_und_ch_werden_nicht_ueber_die_dublette_zusammengefuehrt(tmp_path):
    de = [KOPF, _r(konto="1", ust=UST1), _r(konto="1", strasse="Weg 2", ust=UST1)]
    ch = [KOPF, _r(konto="9", land="CH", plz="8001", ort="Zürich", iban=IBAN1, ust=UST1)]
    sh = _sheets({DE: de, CH: ch})
    ant = {**_ant(1, 2), f"{CH}:1|dublette": {"antwort": M}}
    vor = si.vorschau(_store(tmp_path), sh, ant)
    assert _z(vor, CH)[0].get("zusammengefasst") is None
    assert _z(vor)[1]["zusammengefasst"]["mit"] == f"{DE}:1"


# ---- Sammelantwort ------------------------------------------------------------------------

def test_sammelantwort_fuer_ganze_gruppe_und_idempotenz(tmp_path):
    # je Firma zwei Zeilen mit demselben Konto + Widerspruch
    rows = [x for k in (1, 2, 3) for x in (
        _r(konto=str(k), name=f"Firma {k} GmbH", strasse="Weg 1"),
        _r(konto=str(k), name=f"Firma {k} GmbH", strasse="Weg 2", iban=IBAN1 if k == 1 else ""))]
    store, sh = _store(tmp_path), _sheets({DE: [KOPF, *rows]})
    vor = si.vorschau(store, sh)
    g = next(g for g in vor["gruppen"] if g["feld"] == "dublette")
    assert g["anzahl"] == 6 and M in g["sammelfaehig"]
    r = si.sammelantwort(store, sh, {}, g["id"], M)
    assert r["gesetzt"] == 6
    vor2 = si.vorschau(store, sh, r["antworten"])
    assert vor2["zusammenfassung"]["zeilen_dublette_zusammengefuehrt"] == 3
    assert not vor2["gruppen"]
    erg = si.anwenden(store, sh, r["antworten"], "t")
    assert erg["kreditoren"] == 3 and erg["dubletten_zusammengefuehrt"] == 3
    assert len(store.suppliers()) == 3
    stand = sorted((s.id, s.name, tuple(s.ibans), s.notes, tuple(s.address_lines)) for s in store.suppliers())
    erg2 = si.anwenden(store, sh, r["antworten"], "t")      # nochmal: nichts Neues
    assert len(store.suppliers()) == 3
    assert stand == sorted((s.id, s.name, tuple(s.ibans), s.notes, tuple(s.address_lines))
                           for s in store.suppliers())
    # Zweiter Lauf mit identischem Inhalt: Fingerabdruck-Vergleich erkennt
    # die Zeilen als unveraendert und ueberSPRINGT sie, statt sie (ohne
    # Datenaenderung) erneut zu schreiben.
    assert erg2["unveraendert_uebersprungen"] == 3
    assert erg2["kreditoren"] == 0 and erg2["dubletten_zusammengefuehrt"] == 0


def test_zufaellige_zeilenreihenfolge_der_antworten_aendert_nichts(tmp_path):
    rows = _drei()
    base = None
    for seed in range(5):
        keys = [1, 2, 3]
        random.Random(seed).shuffle(keys)
        ant = {}
        for n in keys:
            ant.update(_ant(n))
        store = Store(tmp_path / f"d{seed}")
        si.anwenden(store, _sheets({DE: [KOPF, *rows]}), ant, "t")
        (sup,) = store.suppliers()
        stand = (sup.id, sup.name, tuple(sup.ibans), tuple(sup.ustids), sup.notes, tuple(sup.address_lines))
        assert base in (None, stand)
        base = stand
