"""Autonomie je Lieferant (modus.py, Aaron 19.11.2026: "Setze alles um"):
ein bewaehrter Lieferant laeuft ohne Klick, alle anderen bleiben in der
Testphase; Freigabe nur per Befehl mit Namen, Entzug automatisch bei einer
Fehlentscheidung. Nur erfundene Rechnungen."""

from __future__ import annotations

import json

import pytest

from rechnung import human, jobrouter, modus, shadow
from rechnung.cli import main as cli_main
from rechnung.pipeline import dispatch, finalize, process

pytest.importorskip("reportlab")

from test_modus import FOREIGN_IBAN, _safe_invoice, _setup, _shadow  # noqa: E402


def _bewaehrt(store, sid="muster", n=10, start=0):
    for i in range(start, start + n):
        _shadow(store, f"sha{sid}{i}", sid)


def test_fortschritt_je_lieferant(tmp_path, monkeypatch):
    store, _s = _setup(tmp_path)
    _bewaehrt(store, "a", 9)
    _shadow(store, "b0", "b")
    _shadow(store, "b1", "b", corrected=["netto"])          # Fehlentscheidung -> 0
    _shadow(store, "b2", "b")
    _shadow(store, "c0", "c", would_pass=False)             # unsicher: zaehlt nicht
    rows = {r["lieferant"]: r for r in modus.lieferanten_fortschritt(store)}
    assert rows["a"]["in_folge"] == 9 and not rows["a"]["bereit"]
    assert rows["b"]["in_folge"] == 1 and rows["b"]["letzte_fehlentscheidung"]["korrigiert"] == ["netto"]
    assert rows["c"]["in_folge"] == 0
    _shadow(store, "a9", "a")
    assert next(r for r in modus.lieferanten_fortschritt(store) if r["lieferant"] == "a")["bereit"]
    monkeypatch.setenv("RECHNUNG_AUTONOM_JE_LIEFERANT", "3")
    assert modus.ziel_je_lieferant() == 3


def test_freigeben_nur_mit_namen_und_wenn_bereit(tmp_path):
    store, _s = _setup(tmp_path)
    _bewaehrt(store, "muster", 9)
    with pytest.raises(ValueError, match="Namen"):
        modus.lieferant_freigeben(store, "muster", "")
    with pytest.raises(ValueError, match="9/10"):
        modus.lieferant_freigeben(store, "muster", "aaron")
    _bewaehrt(store, "muster", 1, start=9)
    modus.lieferant_freigeben(store, "muster", "aaron", "bewaehrt")
    assert modus.autonom_fuer(store, "muster") and not modus.autonom_fuer(store, "anders")
    assert not modus.autonom_fuer(store, None)
    assert modus.get(store) == modus.TESTPHASE          # programmweit unveraendert
    a = [x for x in store.audit_log() if x["action"] == "autonomie_lieferant"][-1]
    assert a["operator"] == "aaron" and a["detail"]["neu"] == "frei"


def test_freigegebener_lieferant_laeuft_ohne_klick_andere_nicht(tmp_path):
    store, samples = _setup(tmp_path)
    _bewaehrt(store)
    modus.lieferant_freigeben(store, "muster", "aaron")
    res = process(_safe_invoice(samples), store)
    out = dispatch(res, store)
    assert out["aktion"] == "autonom" and out["finalize"]["freigabe"] == "autonom"
    # unsicher bleibt beim Menschen, auch bei einem freien Lieferanten
    res2 = process(_safe_invoice(samples, n=44, iban=FOREIGN_IBAN), store)
    assert dispatch(res2, store)["aktion"] == "mensch"
    # ein anderer, nicht freigegebener Lieferant wartet auf den Klick
    modus.lieferant_entziehen(store, "muster", "aaron")
    res3 = process(_safe_invoice(samples, n=45), store)
    assert dispatch(res3, store) == {"aktion": "bestaetigung"}
    assert finalize(res3, store, autonomous=True)["error"] == "not_autonomous"


def test_fehlentscheidung_entzieht_automatisch(tmp_path):
    store, samples = _setup(tmp_path)
    _bewaehrt(store)
    modus.lieferant_freigeben(store, "muster", "aaron")
    res = process(_safe_invoice(samples), store)
    dispatch(res, store)
    jobrouter.push_queue(store.root)
    jobrouter.apply_feedback(store, [jobrouter.Feedback(sha256=res.sha256, file="re43.pdf",
                                                        fields={"netto": "543,00"})])
    assert not modus.autonom_fuer(store, "muster")
    a = [x for x in store.audit_log() if x["action"] == "autonomie_lieferant"][-1]
    assert a["operator"] == modus.AUTOMATISCH and a["detail"]["neu"] == "testphase"
    assert "netto" in a["detail"]["grund"]
    # Folge beginnt von vorn
    row = next(r for r in modus.lieferanten_fortschritt(store) if r["lieferant"] == "muster")
    assert row["in_folge"] == 0 and not row["bereit"]


def test_korrektur_beim_bestaetigen_entzieht_ebenfalls(tmp_path, monkeypatch):
    store, _s = _setup(tmp_path)
    _bewaehrt(store)
    modus.lieferant_freigeben(store, "muster", "aaron")
    rec = {"sha256": "neu1", "file": "x.pdf", "supplier_id": "muster",
           "would_pass": True, "corrected": ["brutto"], "wrong": True}
    store.shadow_append(rec)
    shadow._entziehen_bei_fehler(store, rec)
    assert not modus.autonom_fuer(store, "muster")
    # ohne Fehlentscheidung passiert nichts
    _bewaehrt(store, "muster", 10, start=20)
    modus.lieferant_freigeben(store, "muster", "aaron")
    shadow._entziehen_bei_fehler(store, dict(rec, wrong=False))
    assert modus.autonom_fuer(store, "muster")


def test_cli_autonomie(tmp_path, capsys):
    store, _s = _setup(tmp_path)
    _bewaehrt(store, "muster", 10)
    _bewaehrt(store, "zweiter", 4)
    d = ["--data", str(store.root)]
    assert cli_main(d + ["autonomie"]) == 0
    out = capsys.readouterr().out
    assert "Bereit zur Freigabe: 1" in out and "muster" in out and "zweiter: 4/10" in out
    assert cli_main(d + ["autonomie", "--alle-bereiten"]) == 2          # ohne Namen
    assert cli_main(d + ["autonomie", "--alle-bereiten", "--operator", "aaron"]) == 0
    out = capsys.readouterr().out
    assert "freigegeben: muster" in out and "Ohne Klick freigegeben: 1" in out
    assert cli_main(d + ["autonomie", "--freigeben", "zweiter", "--operator", "aaron"]) == 2
    assert "noch nicht bereit (4/10" in capsys.readouterr().out
    assert cli_main(d + ["modus"]) == 0
    assert "Lieferanten ohne Klick: 1" in capsys.readouterr().out
    assert cli_main(d + ["autonomie", "--entziehen", "muster", "--operator", "aaron"]) == 0
    assert "entzogen: muster" in capsys.readouterr().out
    assert cli_main(d + ["autonomie", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["lieferanten"]
    assert not any(r["frei"] for r in rows)


def test_api_modus_zeigt_lieferanten(tmp_path):
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod

    store, samples = _setup(tmp_path)
    _bewaehrt(store)
    client = TestClient(server_mod.create_app(store, samples))
    d = client.get("/api/modus").json()
    assert d["lieferanten_frei"] == 0 and d["lieferanten_bereit"] == 1
    modus.lieferant_freigeben(store, "muster", "aaron")
    d = client.get("/api/modus").json()
    assert d["lieferanten_frei"] == 1 and d["ziel_je_lieferant"] == 10
    assert human.offen(store) == []
