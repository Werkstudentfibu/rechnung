"""Fragen buendeln + Sammel-Antwort (Aaron 17.10.2026). Nur synthetische
Platzhalterdaten. Die Gruppen-Antwort setzt dieselben ``antworten``-Eintraege
wie das Einzel-Beantworten; ``anwenden()`` prueft weiterhin alles neu."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from rechnung import stammdaten_import as si
from rechnung.store import Store
from rechnung.web.server import create_app

KOPF = ["Name", "IBAN", "PLZ", "Land"]


def _sheets(rows):
    return {"Blatt": {"headers": KOPF, "rows": rows, "mapping": None, "art": "kreditor_de"}}


def _rows():
    # 3x ungueltige IBAN (verschiedene Werte), 2x PLZ 4-stellig (Vorschlag), 1x PLZ "ABC",
    # 1 gruene Zeile.
    return [
        ["Alpha GmbH", "DE00000000000000000001", "10115", "DE"],
        ["Beta GmbH", "DE00000000000000000002", "10115", "DE"],
        ["Gamma GmbH", "DE00000000000000000003", "10115", "DE"],
        ["Delta GmbH", "", "1234", "DE"],
        ["Epsilon GmbH", "", "5678", "DE"],
        ["Zeta GmbH", "", "ABC", "DE"],
        ["Eta GmbH", "", "10115", "DE"],
    ]


def _store(tmp_path: Path) -> Store:
    return Store(tmp_path / "data")


def test_muster_entfernt_eingebetteten_wert():
    assert si._grund_muster("iban", "IBAN-Pruefziffer ungueltig: DE123") == "IBAN-Pruefziffer ungueltig"
    assert si._grund_muster("plz", "PLZ-Format fuer DE erwartet 5-stellig: '1234'") \
        == "PLZ-Format fuer DE erwartet 5-stellig"
    assert si._grund_muster("bibliothek", "Gibt es schon in der Bibliothek (abc-1) mit anderen "
                                         "Werten bei: iban -- alt oder neu?") \
        == "Gibt es schon in der Bibliothek mit anderen Werten bei"
    assert si._grund_muster("ort", "PLZ-Spalte nennt 'X', Ortsbezeichnung 'Y' -- bitte entscheiden") \
        == "PLZ-Spalte nennt '…', Ortsbezeichnung '…' -- bitte entscheiden"


def test_gruppierung_mehrere_typen_sortiert_und_einzelfall(tmp_path):
    v = si.vorschau(_store(tmp_path), _sheets(_rows()))
    g = v["gruppen"]
    nach_muster = {x["muster"]: x for x in g}
    assert nach_muster["IBAN-Pruefziffer ungueltig"]["anzahl"] == 3
    plz4 = nach_muster["PLZ-Format fuer DE erwartet 5-stellig"]
    assert plz4["anzahl"] == 3                 # 1234, 5678, ABC -- gleiches Muster
    assert plz4["hat_vorschlag"] and plz4["anzahl_vorschlaege"] == 2
    assert not nach_muster["IBAN-Pruefziffer ungueltig"]["hat_vorschlag"]
    assert [x["anzahl"] for x in g] == sorted((x["anzahl"] for x in g), reverse=True)
    assert sum(x["anzahl"] for x in g) == 6    # gruene Zeile taucht nicht auf
    # Grund kommt nur einmal vor -> eigene Gruppe mit Anzahl 1
    einzel = si.vorschau(_store(tmp_path), _sheets([_rows()[0]]))["gruppen"]
    assert len(einzel) == 1 and einzel[0]["anzahl"] == 1
    # keine Werte in den Gruppen (nur Schluessel als Beispiele)
    assert "Alpha" not in str(g) and "0000000000" not in str(g)
    assert all(b.startswith("Blatt:") for x in g for b in x["beispiele"])


def test_beantwortete_fragen_verlassen_die_gruppe(tmp_path):
    store, sh = _store(tmp_path), _sheets(_rows())
    v = si.vorschau(store, sh)
    iban = next(x for x in v["gruppen"] if x["feld"] == "iban")
    res = si.sammelantwort(store, sh, {}, iban["id"], "so übernehmen")
    assert res["gesetzt"] == 3
    v2 = si.vorschau(store, sh, res["antworten"])
    assert all(x["feld"] != "iban" for x in v2["gruppen"])
    assert v2["zusammenfassung"]["bereit_zum_uebernehmen"] == v["zusammenfassung"]["bereit_zum_uebernehmen"] + 3


def test_gruppen_id_stabil(tmp_path):
    a = si.vorschau(_store(tmp_path), _sheets(_rows()))["gruppen"]
    b = si.vorschau(_store(tmp_path), _sheets(list(reversed(_rows()))))["gruppen"]
    assert {x["id"] for x in a} == {x["id"] for x in b}


def test_vorschlaege_uebernehmen_nur_wo_vorhanden(tmp_path):
    store, sh = _store(tmp_path), _sheets(_rows())
    plz = next(x for x in si.vorschau(store, sh)["gruppen"] if x["feld"] == "plz")
    res = si.sammelantwort(store, sh, {}, plz["id"], vorschlaege=True)
    assert res["gesetzt"] == 2
    assert {e["wert"] for e in res["antworten"].values()} == {"01234", "05678"}
    assert all(e["antwort"].startswith("korrigieren") for e in res["antworten"].values())
    v = si.vorschau(store, sh, res["antworten"])
    assert next(x for x in v["gruppen"] if x["feld"] == "plz")["anzahl"] == 1   # "ABC" bleibt offen


def test_unzulaessige_antworten(tmp_path):
    store, sh = _store(tmp_path), _sheets(_rows())
    iban = next(x for x in si.vorschau(store, sh)["gruppen"] if x["feld"] == "iban")
    import pytest
    with pytest.raises(ValueError):
        si.sammelantwort(store, sh, {}, iban["id"], "irgendwas")
    with pytest.raises(ValueError):
        si.sammelantwort(store, sh, {}, iban["id"], "korrigieren auf …", wert=" ")
    with pytest.raises(ValueError):
        si.sammelantwort(store, sh, {}, iban["id"], vorschlaege=True)   # kein Vorschlag vorhanden
    with pytest.raises(KeyError):
        si.sammelantwort(store, sh, {}, "gibtsnicht", "so übernehmen")


def test_dubletten_gruppe_ohne_einseitige_antwort():
    # "diese Zeile uebernehmen, andere weglassen" gaebe beiden Dubletten dieselbe Antwort
    z = [{"schluessel": f"B:{i}", "art": "kreditor_de", "fragen": [si._frage(
        "dublette", "Dublette in dieser Datei (name): auch in Zeile x",
        antworten=["beide übernehmen (sind unterschiedlich)",
                   "diese Zeile übernehmen, andere weglassen", "Zeile weglassen"])]} for i in (1, 2)]
    g = si.gruppen_bilden(z)
    assert len(g) == 1 and g[0]["anzahl"] == 2
    assert "diese Zeile übernehmen, andere weglassen" not in g[0]["sammelfaehig"]
    import pytest
    with pytest.raises(ValueError):
        si.gruppen_antwort(g, g[0]["id"], "diese Zeile übernehmen, andere weglassen")


def test_anwenden_prueft_nach_gruppenantwort_weiter_neu(tmp_path):
    store, sh = _store(tmp_path), _sheets(_rows())
    iban = next(x for x in si.vorschau(store, sh)["gruppen"] if x["feld"] == "iban")
    res = si.sammelantwort(store, sh, {}, iban["id"], "Zeile weglassen")
    erg = si.anwenden(store, sh, res["antworten"], "test")
    assert erg["kreditoren"] == 1          # nur die gruene Zeile; 3 weggelassen, 3 PLZ offen
    assert len(store.suppliers()) == 1


def _client(tmp_path: Path) -> TestClient:
    samples = tmp_path / "samples"
    samples.mkdir()
    return TestClient(create_app(Store(tmp_path / "data"), samples))


def test_api_gruppen_in_vorschau_und_gruppen_antwort(tmp_path):
    c = _client(tmp_path)
    sh = _sheets(_rows())
    v = c.post("/api/stammdaten/vorschau", json={"sheets": sh}).json()
    iban = next(x for x in v["gruppen"] if x["feld"] == "iban")
    r = c.post("/api/stammdaten/gruppen-antwort", json={
        "sheets": sh, "antworten": {}, "gruppen_id": iban["id"], "antwort": "so übernehmen"})
    assert r.status_code == 200
    d = r.json()
    assert d["gesetzt"] == 3
    assert set(d["antworten"]) == {"Blatt:1|iban", "Blatt:2|iban", "Blatt:3|iban"}
    assert all(e == {"antwort": "so übernehmen"} for e in d["antworten"].values())
    # bestehende Antworten bleiben erhalten; Vorschau mit dem Ergebnis ist ohne iban-Gruppe
    v2 = c.post("/api/stammdaten/vorschau", json={"sheets": sh, "antworten": d["antworten"]}).json()
    assert all(x["feld"] != "iban" for x in v2["gruppen"])
    assert v2["blaetter"]["Blatt"]["zeilen"][0]["fragen"][0]["beantwortet"] == "so übernehmen"


def test_api_gruppen_antwort_fehlerfaelle(tmp_path):
    c = _client(tmp_path)
    sh = _sheets(_rows())
    url = "/api/stammdaten/gruppen-antwort"
    assert c.post(url, json={"sheets": sh}).status_code == 400
    assert c.post(url, json={"sheets": sh, "gruppen_id": "x", "antwort": "so übernehmen"}).status_code == 404
    iban = next(x for x in c.post("/api/stammdaten/vorschau", json={"sheets": sh}).json()["gruppen"]
                if x["feld"] == "iban")
    assert c.post(url, json={"sheets": sh, "gruppen_id": iban["id"], "antwort": "Quatsch"}).status_code == 400
