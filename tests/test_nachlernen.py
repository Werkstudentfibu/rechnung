"""Nachlernen aus bereits bestaetigten Rechnungen (nachlernen.py, Aaron
19.11.2026). Nur erfundene Rechnungen."""

from __future__ import annotations

import json
import types

import pytest

from rechnung import nachlernen, testsatz
from rechnung.cli import main as cli_main

pytest.importorskip("fastapi.testclient")

from test_testsatz import _bestaetigen, _guard  # noqa: E402


def _vorlagen_weg(store, sid="alpha"):
    for t in store.templates_for(sid):
        store.delete_template(t.id)


def test_anzeige_speichert_nichts_ausfuehren_lernt(tmp_path):
    """Vorlage fehlt (z.B. weil das Lernen damals scheiterte): die Anzeige
    zeigt, was gelernt wuerde, speichert aber nichts; --ausfuehren legt die
    Vorlage an, ohne Bestaetigungen hochzuzaehlen."""
    store, _c, _f = _bestaetigen(tmp_path)
    _vorlagen_weg(store)
    woerter = store.spaltenwoerter()

    b = nachlernen.laufen(store)
    assert not b["ausgefuehrt"] and b["rechnungen"] == 2
    assert b["mit_aenderung"] >= 1 and b["vorlagen_neu"] == 1
    assert "rechnungsnummer" in b["felder_gelernt"]
    assert store.templates_for("alpha") == []
    assert store.spaltenwoerter() == woerter
    assert "nur Anzeige" in nachlernen.text(b)

    with pytest.raises(ValueError):
        nachlernen.laufen(store, ausfuehren=True)

    b2 = nachlernen.laufen(store, ausfuehren=True, operator="aaron")
    assert b2["ausgefuehrt"] and b2["vorlagen_neu"] == 1
    tpls = store.templates_for("alpha")
    assert len(tpls) == 1 and tpls[0].confirmations == 0
    assert any(a["action"] == "template_nachgelernt" and a["operator"] == "aaron"
               for a in store.audit_log())
    # die Vorlage liest jetzt beide Rechnungen
    t = testsatz.laufen(store, guard=_guard(store))
    assert t["felder"]["rechnungsnummer"]["richtig"] == 2

    # zweiter Lauf: nichts Neues mehr
    b3 = nachlernen.laufen(store)
    assert b3["mit_aenderung"] == 0


def test_gesicherte_vorlage_wird_nicht_umgeschrieben(tmp_path):
    store, _c, files = _bestaetigen(tmp_path)
    tpl = store.templates_for("alpha")[0]
    tpl.confirmations = 3
    store.save_template(tpl)
    vorher = store.template(tpl.id).to_dict()
    # Soll-Wert verfaelschen: die gesicherte Vorlage liest etwas anderes
    from rechnung import pdf as pdfmod

    sha = pdfmod.load(files[0]).sha256
    p = store.root / "testsatz" / f"{sha}.json"
    rec = json.loads(p.read_text("utf-8"))
    rec["felder"]["rechnungsnummer"] = "XX-999"
    p.write_text(json.dumps(rec), "utf-8")

    b = nachlernen.laufen(store, ausfuehren=True, operator="aaron")
    fall = next(f for f in b["faelle"] if f["sha256"] == sha)
    assert "rechnungsnummer" not in fall["gelernt"]
    assert fall["uebersprungen"].get("rechnungsnummer") == "gesichertes Template liefert anderen Wert"
    assert store.template(tpl.id).to_dict()["fields"] == vorher["fields"]


def test_nur_gelesene_bzw_eingetragene_werte():
    fall = {"felder": {"rechnungsnummer": "AL-1", "netto": 100.0, "brutto": 119.0,
                       "iban": "DE89370400440532013000"},
            "quellen": {"rechnungsnummer": "template-anchor", "netto": "errechnet",
                        "brutto": "manual", "iban": "library"}}
    assert nachlernen._werte(fall) == {"rechnungsnummer": "AL-1", "brutto": 119.0}


def test_positionen_nur_wenn_heute_gleich_gelesen():
    fall = {"positionen": [{"gesamt_netto": 10.0, "menge": 1}, {"gesamt_netto": 5.0}]}
    sup = types.SimpleNamespace(positionsmodus="einzeln")
    gleich = types.SimpleNamespace(line_items=[
        {"gesamt_netto": 10.0, "menge": 1, "_herkunft": {}},
        {"gesamt_netto": 5.0, "beschreibung": "x", "_herkunft": {"gesamt_netto": "errechnet"}}])
    items, grund = nachlernen._positionen(fall, gleich, sup)
    assert grund == "" and items[1] == {"beschreibung": "x"}   # errechnete Zelle raus
    anders = types.SimpleNamespace(line_items=[{"gesamt_netto": 10.0, "menge": 1}])
    items, grund = nachlernen._positionen(fall, anders, sup)
    assert items is None and "anders gelesen" in grund
    eine = types.SimpleNamespace(positionsmodus="eine_position")
    assert nachlernen._positionen(fall, gleich, eine)[0] is None


def test_cli(tmp_path, capsys):
    store, _c, _f = _bestaetigen(tmp_path)
    _vorlagen_weg(store)
    assert cli_main(["--data", str(store.root), "nachlernen"]) == 0
    out = capsys.readouterr().out
    assert "Nachlernen -- nur Anzeige" in out and "--ausfuehren --operator" in out
    assert cli_main(["--data", str(store.root), "nachlernen", "--ausfuehren"]) == 2
    assert cli_main(["--data", str(store.root), "nachlernen", "--ausfuehren",
                     "--operator", "aaron"]) == 0
    assert store.templates_for("alpha")
