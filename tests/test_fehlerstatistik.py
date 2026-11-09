"""Fehlerstatistik je Lieferant und Feld (eventlog.fehlerstatistik,
Aaron 10.10.2026)."""

from __future__ import annotations

import json
from datetime import datetime

from rechnung import eventlog
from rechnung.cli import main as cli_main
from rechnung.identify import Supplier
from rechnung.pipeline import POSITIONEN_KEY
from rechnung.store import Store
from rechnung.template import Template

HEUTE = datetime(2026, 10, 10, 12, 0)


def _gold(store, sha, sid, bestaetigt):
    d = store.root / "testsatz"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sha}.json").write_text(json.dumps({
        "sha256": sha, "supplier_id": sid, "supplier_name": sid.title(),
        "bestaetigt": bestaetigt, "felder": {"brutto": 1.0}}), "utf-8")


def _korr(store, sha, sid, feld, ts):
    store.event_append({"type": eventlog.TYPE_CORRECTION, "ts": ts, "sha256": sha,
                        "field": feld, "supplier_id": sid, "alt": None, "neu": "x"})


def _store(tmp_path) -> Store:
    store = Store(tmp_path / "data")
    # Alpha: 8 Rechnungen, 4 alt (August), 4 neu (Oktober); Rechnungsnummer
    # in 6 korrigiert (alle 4 alten, 2 neue) -> 75 %, Trend besser
    for i in range(8):
        ts = "2026-08-01T10:00:00+0200" if i < 4 else "2026-10-05T10:00:00+0200"
        _gold(store, f"a{i}", "alpha", ts)
        if i < 6:
            _korr(store, f"a{i}", "alpha", "rechnungsnummer", ts)
            _korr(store, f"a{i}", "alpha", "rechnungsnummer", ts)  # zweiter Klick zaehlt nicht
    _korr(store, "a7", "alpha", POSITIONEN_KEY, "2026-10-05T10:00:00+0200")
    # Beta: 2 Rechnungen, IBAN in beiden korrigiert
    for i in range(2):
        _gold(store, f"b{i}", "beta", "2026-10-01T10:00:00+0200")
        _korr(store, f"b{i}", "beta", "iban", "2026-10-01T10:00:00+0200")
    # korrigiert, aber nicht bestaetigt -> zaehlt als Rechnung von Gamma
    _korr(store, "g0", "gamma", "brutto", "2026-10-09T10:00:00+0200")
    store.save_supplier(Supplier(id="alpha", name="Alpha Werkzeug GmbH"))
    return store


def test_anteil_trend_und_lohnend(tmp_path):
    fs = eventlog.fehlerstatistik(_store(tmp_path), heute=HEUTE)
    z = {(r["lieferant"], r["feld"]): r for r in fs["zeilen"]}
    a = z[("alpha", "rechnungsnummer")]
    assert (a["korrigiert"], a["rechnungen"], a["anteil"]) == (6, 8, 0.75)
    assert (a["anteil_4_wochen"], a["anteil_davor"], a["trend"]) == (0.5, 1.0, "besser")
    assert a["lieferant_name"] == "Alpha Werkzeug GmbH"
    assert z[("alpha", POSITIONEN_KEY)]["feld_label"] == "Positionen"
    g = z[("gamma", "brutto")]
    assert (g["korrigiert"], g["rechnungen"], g["anteil"]) == (1, 1, 1.0)
    assert fs["zeilen"][0]["lieferant"] == "alpha"          # meiste korrigierte Rechnungen zuerst
    texte = [r["text"] for r in fs["lohnend"]]
    assert texte[0] == ("Alpha Werkzeug GmbH: rechnungsnummer in 6 von 8 Rechnungen "
                        "korrigiert → Vorlage anlernen")
    assert any("Beta: iban in 2 von 2" in t and "Stammdaten" in t for t in texte)
    # Einzelfaelle (1 Rechnung) sind keine "lohnende" Verbesserung
    assert not any(r["lieferant"] == "gamma" for r in fs["lohnend"])


def test_mit_vorlage_nachtrainieren(tmp_path):
    store = _store(tmp_path)
    store.save_template(Template(id="alpha__standard", supplier_id="alpha"))
    fs = eventlog.fehlerstatistik(store, heute=HEUTE)
    assert fs["lohnend"][0]["empfehlung"] == "Vorlage nachtrainieren (Anker prüfen)"


def test_leer(tmp_path):
    fs = eventlog.fehlerstatistik(Store(tmp_path / "data"), heute=HEUTE)
    assert fs["zeilen"] == [] and fs["lohnend"] == []


def test_cli_und_nachtlauf(tmp_path, capsys):
    store = _store(tmp_path)
    assert cli_main(["--data", str(store.root), "fehlerstatistik"]) == 0
    out = capsys.readouterr().out
    assert "Lohnendste Verbesserungen" in out and "6/8" in out
    from rechnung.nightly import run_nightly
    b = run_nightly(store)
    assert b["fehlerstatistik"]["lohnend"]
