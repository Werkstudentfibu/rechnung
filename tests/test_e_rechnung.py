"""E-Rechnungen bevorzugen: Kandidaten + Anschreiben (rechnung/e_rechnung.py,
Aaron 10.10.2026)."""

from __future__ import annotations

import json

from rechnung import e_rechnung, eventlog
from rechnung.cli import main as cli_main
from rechnung.identify import Supplier
from rechnung.store import Store


def _gold(store, sha, sid, name, extraktion="template", original=None):
    d = store.root / "testsatz"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sha}.json").write_text(json.dumps({
        "sha256": sha, "supplier_id": sid, "supplier_name": name, "extraktion": extraktion,
        "original": original or f"{sha}.pdf", "felder": {"brutto": 1.0}}), "utf-8")


def _store(tmp_path) -> Store:
    store = Store(tmp_path / "data")
    for i in range(3):
        _gold(store, f"a{i}", "alpha", "Alpha")
    _gold(store, "b0", "beta", "Beta")
    _gold(store, "b1", "beta", "Beta")
    _gold(store, "c0", "gamma", "Gamma", extraktion="einvoice", original="c0.xml")
    _gold(store, "d0", "delta", "Delta")
    _gold(store, "d1", "delta", "Delta", extraktion="einvoice")
    # Beta: Scans (OCR) und viele Korrekturen
    for sha in ("b0", "b1"):
        store.event_append({"type": eventlog.TYPE_PROCESSED, "sha256": sha, "stages": ["ocr"]})
    for i in range(5):
        eventlog.record_correction(store, "b0", "brutto", None, i, supplier_id="beta")
    eventlog.record_correction(store, "a0", "brutto", None, 1, supplier_id="alpha")
    store.save_supplier(Supplier(id="beta", name="Beta Buerobedarf AG",
                                 address_lines=["Hauptstr. 1", "10115 Berlin"]))
    return store


def test_kandidaten_sortiert_nach_anzahl_und_aufwand(tmp_path):
    rows = e_rechnung.kandidaten(_store(tmp_path))
    assert [r["lieferant"] for r in rows] == ["alpha", "beta", "delta"]  # gamma: nur E-Rechnung
    a, b, d = rows
    assert (a["pdf"], a["scan"], a["korrekturen"]) == (3, 0, 1)
    assert (b["pdf"], b["scan"], b["korrekturen"], b["rechnungen_mit_korrektur"]) == (0, 2, 5, 1)
    assert b["lieferant_name"] == "Beta Buerobedarf AG"
    assert (d["pdf"], d["e_rechnung"]) == (1, 1)
    assert b["korrekturen_je_rechnung"] == 2.5


def test_gleiche_anzahl_mehr_korrekturen_zuerst(tmp_path):
    store = _store(tmp_path)
    _gold(store, "b2", "beta", "Beta")      # Beta jetzt 3 wie Alpha, aber mehr Korrekturen
    assert [r["lieferant"] for r in e_rechnung.kandidaten(store)][:2] == ["beta", "alpha"]


def test_anschreiben_fuellt_nur_bekanntes(tmp_path):
    store = _store(tmp_path)
    t = e_rechnung.anschreiben(store, "beta", empfang_adresse="rechnung@firma.de")
    assert "Beta Buerobedarf AG" in t and "10115 Berlin" in t and "rechnung@firma.de" in t
    assert "{FIRMA}" in t and "{ANSPRECHPARTNER}" in t      # nichts geraten
    assert "1. Januar 2025" in t and "800.000 Euro" in t
    assert "ZUGFeRD" in t and "XRechnung" in t


def test_cli(tmp_path, capsys):
    store = _store(tmp_path)
    assert cli_main(["--data", str(store.root), "e-rechnung-kandidaten"]) == 0
    out = capsys.readouterr().out
    assert "Beta Buerobedarf AG" in out and "Gamma" not in out
    assert cli_main(["--data", str(store.root), "e-rechnung-kandidaten", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["lieferant"] == "alpha"
    assert cli_main(["--data", str(store.root), "e-rechnung-kandidaten",
                     "--anschreiben", "beta", "--an", "eingang@x.de"]) == 0
    assert "eingang@x.de" in capsys.readouterr().out
