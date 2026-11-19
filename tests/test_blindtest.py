"""Blindtest (testsatz.py, blind=True, Aaron 19.11.2026): jede bestaetigte
Rechnung so lesen, als waere ihr Lieferant neu -- misst, wie gut das Programm
unbekannte Lieferanten liest und ob es ohne Mensch fehlerfrei gewesen waere.
Nur erfundene Rechnungen."""

from __future__ import annotations

import json

import pytest

from rechnung import human, plausi, testsatz
from rechnung.cli import main as cli_main
from rechnung.guard import Guard
from rechnung.pipeline import Result
from rechnung.template import FieldResult
from rechnung.validate import Check

pytest.importorskip("fastapi.testclient")

from test_testsatz import _bestaetigen, _guard  # noqa: E402


def _guard_blind(store):
    return Guard(store.root, timeout=0, target=testsatz.TARGET,
                 extra={"variante": "A", "blind": True}, protokoll=False)


def test_blind_blendet_gelerntes_des_eigenen_lieferanten_aus(tmp_path):
    """Normal liest die gelernte Vorlage alles; blind (ohne KI) gibt es fuer
    den Lieferanten keine Vorlage -- die Rechnungsnummer fehlt, der Lieferant
    wird ueber die Stammdaten (USt-IdNr/IBAN) trotzdem erkannt."""
    store, _c, _f = _bestaetigen(tmp_path)
    normal = testsatz.laufen(store, guard=_guard(store))
    assert normal["felder"]["rechnungsnummer"]["richtig"] == 2
    assert "blind" not in normal["filter"] and "blind" not in normal

    b = testsatz.laufen(store, nur_ohne_ki=True, blind=True, guard=_guard_blind(store))
    assert b["art"] == "blindtest" and b["filter"]["blind"] is True
    assert b["anzahl"] == 2
    assert b["felder"]["rechnungsnummer"]["richtig"] == 0
    assert b["felder"]["rechnungsnummer"]["fehlt"] == 2
    assert b["felder"]["lieferant"]["richtig"] == 2
    bl = b["blind"]
    assert bl["ki"] == {"aktiv": False, "modell": None}
    assert bl["lieferanten"] == 1
    assert bl["komplett_richtig"] == 0
    assert bl["sicher_heute"] == 0 and bl["sicher_ohne_ki_deckel"] == 0
    # warum nicht durch: Pflichtfelder fehlen
    assert any(g.startswith("Pflichtfeld fehlt") for g in bl["nicht_durch_gruende"])
    txt = testsatz.text(b)
    assert "Blindtest" in txt and "KI: aus" in txt and "Lieferanten im Test: 1" in txt


def test_blind_store_verbirgt_nur_den_eigenen_lieferanten(tmp_path):
    store, _c, _f = _bestaetigen(tmp_path)
    assert store.templates_for("alpha")
    assert plausi.profil(store, "alpha") is not None
    sup = store.supplier("alpha")
    assert sup.fingerprint

    blind = testsatz._BlindStore(store.root, "alpha")
    assert blind.templates_for("alpha") == []
    assert all(blind.template(t.id) is None for t in store.templates_for("alpha"))
    assert plausi.profil(blind, "alpha") is None
    bs = blind.supplier("alpha")
    assert bs.fingerprint == [] and bs.positionsmodus == "einzeln"
    # Stammdaten bleiben -- sie kaemen bei einem neuen Lieferanten aus dem Import
    assert bs.ustids == sup.ustids and bs.ibans == sup.ibans and bs.name == sup.name
    # ein anderer Lieferant ist nicht betroffen
    anders = testsatz._BlindStore(store.root, "beta")
    assert anders.templates_for("alpha") and plausi.profil(anders, "alpha") is not None
    # nichts wurde gespeichert
    assert store.supplier("alpha").fingerprint == sup.fingerprint


def _res_ki(werte: dict, conf: float = 0.6, checks=None) -> Result:
    return Result(file="x.pdf", sha256="ab" * 32, supplier_id="alpha", status="review",
                  fields={k: FieldResult(k, v, str(v), conf, "llm") for k, v in werte.items()},
                  checks=checks or [])


PFLICHT = {"rechnungsnummer": "AL-1", "rechnungsdatum": "2026-01-05", "netto": 100.0,
           "ust_betrag": 19.0, "brutto": 119.0}


def test_ohne_ki_deckel_hebt_nur_den_deckel_auf():
    res = _res_ki(PFLICHT)
    assert not human.ist_sicher(res)
    assert human.ist_sicher(testsatz._ohne_ki_deckel(res))
    # Original bleibt unveraendert
    assert res.fields["netto"].confidence == 0.6
    # aus anderem Grund unsicher (z.B. unsicheres Scan-Wort: 0.5) -> bleibt unsicher
    res2 = _res_ki(PFLICHT)
    res2.fields["rechnungsnummer"] = FieldResult("rechnungsnummer", "AL-1", "AL-1", 0.5, "llm")
    assert not human.ist_sicher(testsatz._ohne_ki_deckel(res2))
    # rote Pruefung bleibt rot
    res3 = _res_ki(PFLICHT, checks=[Check("betraege-plausibel", False, "x")])
    assert not human.ist_sicher(testsatz._ohne_ki_deckel(res3))


def _v(ergebnisse: dict, pos=(0, 0)) -> dict:
    return {"felder": {k: {"ergebnis": e, "soll": 1, "ist": 1, "quelle": "llm"}
                       for k, e in ergebnisse.items()},
            "positionen": {"soll": pos[0], "gefunden": pos[1], "korrekt": pos[1]}}


def test_bewertung_und_summe():
    alle_richtig = {k: testsatz.RICHTIG for k in (*PFLICHT, "lieferant")}
    nummer_falsch = dict(alle_richtig, rechnungsnummer=testsatz.FALSCH)
    out = []
    for i, erg in enumerate((alle_richtig, nummer_falsch)):
        v = _v(erg, pos=(2, 1) if i else (0, 0))
        e = {"lieferant": f"l{i}", "felder": v["felder"], **testsatz._blind_bewertung(_res_ki(PFLICHT), v)}
        out.append(e)
    assert out[0]["komplett_richtig"] and not out[1]["komplett_richtig"]
    assert out[1]["falsche_felder"] == ["rechnungsnummer"]
    assert all(f["sicher_ohne_ki_deckel"] and not f["sicher_heute"] for f in out)

    bl = testsatz._blind_summe(out, ki={"aktiv": True, "modell": "m"})
    assert bl["lieferanten"] == 2
    assert bl["komplett_richtig"] == 1 and bl["komplett_quote"] == 50.0
    assert bl["sicher_ohne_ki_deckel"] == 2 and bl["davon_fehlerhaft"] == 1
    assert bl["fehler_felder"] == {"rechnungsnummer": 1}
    assert bl["davon_positionen_abweichend"] == 1
    assert bl["nach_quelle"]["rechnungsnummer"]["llm"] == {"richtig": 1, "falsch": 1, "quote": 50.0}
    lines = "\n".join(testsatz._blind_text(bl, 2))
    assert "KI: an (m)" in lines
    assert "Waeren ohne KI-Deckel durchgegangen: 2, davon fehlerhaft: 1 (rechnungsnummer 1)" in lines
    assert "rechnungsnummer" in lines and "KI 1/2 (50 %)" in lines


def test_stichprobe_reihum_ueber_lieferanten():
    todo = [{"supplier_id": s, "n": i} for i, s in enumerate("aaabbc")]
    assert [f["supplier_id"] for f in testsatz._verteilt(todo, 4)] == ["a", "b", "c", "a"]
    assert len(testsatz._verteilt(todo, 99)) == 6


def test_letzter_bericht_ohne_filter_ignoriert_blindtests(tmp_path):
    store, _c, _f = _bestaetigen(tmp_path)
    normal = testsatz.pruefen(store, guard=_guard(store))
    blind = testsatz.pruefen(store, nur_ohne_ki=True, blind=True, guard=_guard_blind(store))
    assert testsatz.letzter_bericht(store.root)["datei"] == normal["datei"]
    assert testsatz.letzter_bericht(store.root, blind["filter"])["datei"] == blind["datei"]
    # Vergleich nur mit dem letzten Blindtest
    zweiter = testsatz.pruefen(store, nur_ohne_ki=True, blind=True, guard=_guard_blind(store))
    assert zweiter["vergleich"]["vorher"] == blind["datei"]


def test_cli_blind(tmp_path, capsys):
    store, _c, _f = _bestaetigen(tmp_path)
    ziel = tmp_path / "blind.json"
    assert cli_main(["--data", str(store.root), "testsatz", "--blind", "--ohne-ki", "--max", "1",
                     "--kein-bericht", "--ausgabe", str(ziel)]) == 0
    out = capsys.readouterr().out
    assert "Blindtest -- jeder Lieferant so gelesen, als waere er neu " \
           "(Variante A, ohne KI, Stichprobe 1): 1 Rechnung(en)" in out
    b = json.loads(ziel.read_text("utf-8"))
    assert b["anzahl"] == 1 and b["blind"]["lieferanten"] == 1
    assert not (store.root / "berichte").exists()
