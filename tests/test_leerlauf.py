"""Leerlauf-Arbeit (leerlauf.py, Aaron 19.11.2026): wenn nichts mehr wartet,
offene Rechnungen mit neuem Lernstand neu lesen und den Blindtest in Etappen
rechnen -- nur lesen und messen, neue Rechnungen haben Vorrang. Nur
erfundene Rechnungen."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from rechnung import leerlauf, testsatz, vorbereitung as vb
from rechnung.guard import Guard
from rechnung.nightly import run_nightly

pytest.importorskip("reportlab")
pytest.importorskip("fastapi.testclient")
from rechnung.synth import make_invoice  # noqa: E402

from test_jobrouter_feedback import _secured_setup  # noqa: E402
from test_testsatz import _bestaetigen  # noqa: E402

NACHT = datetime(2026, 11, 19, 23, 0)
MITTAG = datetime(2026, 11, 19, 12, 0)


@pytest.fixture(autouse=True)
def _nachts(monkeypatch):
    monkeypatch.setenv("RECHNUNG_LEERLAUF", "20-6")


@pytest.mark.parametrize("wert,stunde,erlaubt", [
    ("20-6", 23, True), ("20-6", 5, True), ("20-6", 6, False), ("20-6", 12, False),
    ("8-17", 9, True), ("8-17", 17, False), ("aus", 23, False), ("immer", 12, True),
])
def test_zeitfenster(monkeypatch, wert, stunde, erlaubt):
    monkeypatch.setenv("RECHNUNG_LEERLAUF", wert)
    assert leerlauf.jetzt_erlaubt(datetime(2026, 11, 19, stunde, 30)) is erlaubt


def _vorbereitet(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    make_invoice(samples / "b.pdf", rechnungsnr="RE-2026-00002")
    vb.vorbereiten_lauf(store, samples, abholen=False, leerlauf=False)
    return store, samples


def _lernstand_aendern(store):
    t = time.time() + 5
    for p in (store.root / "templates").glob("*.json"):
        os.utime(p, (t, t))


def _guard(store):
    return Guard(store.root, timeout=120, mode="thread")


def test_neu_lesen_nur_nach_neuem_lernstand(tmp_path):
    store, samples = _vorbereitet(tmp_path)
    assert leerlauf.neu_zu_lesen(store, samples) == []
    erg = leerlauf.laufen(store, samples, jetzt=NACHT, guard=_guard(store))
    assert erg["gelaufen"] and erg["neu_gelesen"] == 0

    _lernstand_aendern(store)
    assert len(leerlauf.neu_zu_lesen(store, samples)) == 2
    erg = leerlauf.laufen(store, samples, jetzt=NACHT, guard=_guard(store))
    assert erg["neu_gelesen"] == 2
    for p in samples.glob("*.pdf"):
        assert vb.marker(store.root, vb.datei_sha(p)).get("neu_ausgewertet")
    # nichts bestaetigt, nichts gesendet
    assert not any(store.is_confirmed(vb.datei_sha(p)) for p in samples.glob("*.pdf"))
    assert not (store.root / "queue").exists() or not list((store.root / "queue").iterdir())
    # Stand fuer den Nachtbericht
    assert leerlauf.letzter_stand(store.root)["neu_gelesen"] == 2


def test_tagsueber_und_bei_neuen_rechnungen_nichts(tmp_path):
    store, samples = _vorbereitet(tmp_path)
    _lernstand_aendern(store)
    assert leerlauf.laufen(store, samples, jetzt=MITTAG)["grund"] == "ausserhalb der Leerlauf-Zeit"
    make_invoice(samples / "neu.pdf", rechnungsnr="RE-2026-00099")
    assert leerlauf.neue_warten(store, samples)
    erg = leerlauf.laufen(store, samples, jetzt=NACHT)
    assert not erg["gelaufen"] and erg["grund"] == "neue Rechnungen warten"


def test_neue_rechnung_unterbricht(tmp_path, monkeypatch):
    store, samples = _vorbereitet(tmp_path)
    _lernstand_aendern(store)
    aufrufe = []
    echt = vb.neu_auswerten

    def neu_auswerten(guard, st, p):
        aufrufe.append(p.name)
        make_invoice(samples / "neu.pdf", rechnungsnr="RE-2026-00099")   # kommt waehrenddessen
        return echt(guard, st, p)

    monkeypatch.setattr(vb, "neu_auswerten", neu_auswerten)
    erg = leerlauf.laufen(store, samples, jetzt=NACHT, guard=_guard(store))
    assert len(aufrufe) == 1 and erg["neu_gelesen"] == 1
    assert erg["blindtest"] is None


def test_zeitbudget(tmp_path, monkeypatch):
    store, samples = _vorbereitet(tmp_path)
    _lernstand_aendern(store)
    monkeypatch.setattr(leerlauf, "budget_s", lambda: 0.0)
    erg = leerlauf.laufen(store, samples, jetzt=NACHT, guard=_guard(store))
    assert erg["neu_gelesen"] == 0 and erg["grund"] == "Zeit um oder neue Rechnungen"


def _blind_guard(store):
    return Guard(store.root, timeout=0, target=testsatz.TARGET,
                 extra={"variante": "A", "blind": True}, protokoll=False)


def test_blindtest_in_etappen(tmp_path):
    store, _c, _f = _bestaetigen(tmp_path)
    n = {"faelle": 0}

    def nach_einem() -> bool:
        n["faelle"] += 1
        return n["faelle"] > 1

    e1 = testsatz.blind_etappe(store, bis=time.monotonic() + 600, abbrechen=nach_einem,
                               guard=_blind_guard(store))
    assert e1 == {"fertig": False, "erledigt": 1, "offen": 1, "bericht": None}
    assert testsatz.blind_laufend(store.root)["offen"]
    e2 = testsatz.blind_etappe(store, bis=time.monotonic() + 600, guard=_blind_guard(store))
    assert e2["fertig"] and e2["erledigt"] == 2
    assert testsatz.blind_laufend(store.root) is None
    b = json.loads(Path(e2["bericht"]).read_text("utf-8"))
    assert b["art"] == "blindtest" and b["anzahl"] == 2 and b["leerlauf"]["gestartet"]
    assert b["filter"] == {"lieferant": None, "variante": "A", "nur_ohne_ki": False,
                           "blind": True, "ki": False}
    # gleiche Messung wie der Befehl "testsatz --blind" -> vergleichbar
    b_cli = testsatz.laufen(store, blind=True, guard=_blind_guard(store))
    assert b_cli["filter"] == b["filter"]


def test_blindtest_faellig(tmp_path, monkeypatch):
    store, _c, _f = _bestaetigen(tmp_path)
    assert leerlauf.blindtest_faellig(store)
    e = testsatz.blind_etappe(store, bis=time.monotonic() + 600, guard=_blind_guard(store))
    assert not leerlauf.blindtest_faellig(store)
    alt = time.time() - 8 * 86400
    os.utime(e["bericht"], (alt, alt))
    assert leerlauf.blindtest_faellig(store)
    monkeypatch.setenv("RECHNUNG_LEERLAUF_BLINDTEST_TAGE", "0")
    assert not leerlauf.blindtest_faellig(store)


def test_leerlauf_rechnet_blindtest_und_nachtbericht(tmp_path):
    store, _c, _f = _bestaetigen(tmp_path)
    leer = tmp_path / "eingang"
    leer.mkdir()
    erg = leerlauf.laufen(store, leer, jetzt=NACHT, blind_guard=_blind_guard(store))
    assert erg["gelaufen"] and erg["blindtest"]["fertig"]
    nb = run_nightly(store, leer)
    ll = nb["leerlauf"]
    assert ll["letzter_durchgang"]["blindtest"]["fertig"]
    assert ll["blindtest"]["anzahl"] == 2 and ll["blindtest"]["ki"] == {"aktiv": False, "modell": None}
    assert ll["blindtest_laufend"] is None


def test_vorbereiten_lauf_haengt_leerlauf_an(tmp_path, monkeypatch, capsys):
    from rechnung import cli

    store, samples = _secured_setup(tmp_path)
    monkeypatch.setenv("RECHNUNG_LEERLAUF", "immer")
    b = vb.vorbereiten_lauf(store, samples, abholen=False)
    assert b["leerlauf"]["gelaufen"] is True
    b = vb.vorbereiten_lauf(store, samples, abholen=False, leerlauf=False)
    assert b["leerlauf"] is None
    assert cli.main(["--data", str(store.root), "--samples", str(samples), "vorbereiten",
                     "--ohne-abholen", "--ohne-leerlauf", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["leerlauf"] is None
