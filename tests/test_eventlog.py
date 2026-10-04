"""Vollstaendiges Fehler-/Ereignisprotokoll (eventlog.py) + Fehlerbericht."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnung import cli, eventlog
from rechnung import pdf as pdfmod
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import AUTO, process, scan_folder
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402
from rechnung import learn  # noqa: E402


def _secured_setup(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    src = make_invoice(samples / "a.pdf", rechnungsnr="RE-2026-00001")
    doc = pdfmod.load(src)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    tpl, _ = learn.learn_template(doc, "muster", {
        "rechnungsnummer": "RE-2026-00001", "rechnungsdatum": "2026-02-14",
        "netto": 1000.0, "ust_betrag": 190.0, "ust_satz": 19.0, "brutto": 1190.0})
    tpl.confirmations = 3
    store.save_template(tpl)
    return store, samples


# ---- record() -----------------------------------------------------------
def test_record_captures_status_checks_fields_duration(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    res = process(samples / "a.pdf", store)
    res.duration_s = 1.23
    rec = eventlog.record(store, res, "aaron")
    assert rec["type"] == "verarbeitung"
    assert rec["file"] == "a.pdf" and rec["supplier_id"] == "muster"
    assert rec["status"] == AUTO and rec["duration_s"] == 1.23
    assert rec["operator"] == "aaron"
    assert "template" in rec["stages"]
    assert rec["fields"]["rechnungsnummer"]["origin"] == "template"
    assert any(c["name"] == "netto+ust=brutto" for c in rec["checks"])
    assert store.events() == [rec]


def test_scan_folder_logs_one_event_per_result(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    make_invoice(samples / "b.pdf", rechnungsnr="RE-2026-00002", netto=50.0)
    results = scan_folder(samples, store)
    events = [e for e in store.events() if e["type"] == "verarbeitung"]
    assert len(events) == len(results) == 2
    assert {e["file"] for e in events} == {"a.pdf", "b.pdf"}


# ---- record_correction() -------------------------------------------------
def test_record_correction_tracks_old_and_new_value(tmp_path: Path):
    store = Store(tmp_path / "data")
    eventlog.record_correction(store, "sha1", "rechnungsnummer", None, "RE-1",
                               operator="aaron", supplier_id="muster",
                               supplier_name="Muster", file="a.pdf")
    rec = store.events()[-1]
    assert rec["type"] == "korrektur" and rec["alt"] is None and rec["neu"] == "RE-1"
    assert rec["supplier_id"] == "muster" and rec["operator"] == "aaron"


# ---- report() -------------------------------------------------------------
def _check_event(store, supplier_id, check_name, ok, detail="", ts=None):
    rec = {"type": "verarbeitung", "ts": ts or "2026-10-01T10:00:00+0000",
          "file": "x.pdf", "sha256": "s1", "supplier_id": supplier_id,
          "supplier_name": supplier_id, "stages": ["template"], "status": "review",
          "hold_reasons": [], "duration_s": 1.0, "aborted": "",
          "fields": {}, "checks": [{"name": check_name, "ok": ok, "detail": detail}],
          "operator": "?"}
    store.event_append(rec)


def test_report_groups_checks_by_name_and_supplier_sorted_by_frequency(tmp_path: Path):
    store = Store(tmp_path / "data")
    for _ in range(3):
        _check_event(store, "alpha", "iban-stammdaten", False, "IBAN weicht ab")
    _check_event(store, "beta", "iban-stammdaten", False, "IBAN weicht ab")
    _check_event(store, "alpha", "netto+ust=brutto", True, "ok")  # gruen, zaehlt nicht

    data = eventlog.report(store)
    assert len(data["pruefungen"]) == 2
    top = data["pruefungen"][0]
    assert top["pruefung"] == "iban-stammdaten" and top["lieferant"] == "alpha"
    assert top["anzahl"] == 3
    assert len(top["beispiele"]) == 3
    second = data["pruefungen"][1]
    assert second["lieferant"] == "beta" and second["anzahl"] == 1


def test_report_filters_by_since_date(tmp_path: Path):
    store = Store(tmp_path / "data")
    _check_event(store, "alpha", "iban-stammdaten", False, ts="2026-09-01T10:00:00+0000")
    _check_event(store, "alpha", "iban-stammdaten", False, ts="2026-10-05T10:00:00+0000")
    data = eventlog.report(store, since="2026-10-01")
    assert data["pruefungen"][0]["anzahl"] == 1


def test_report_counts_missing_and_low_confidence_fields(tmp_path: Path):
    store = Store(tmp_path / "data")
    rec = {"type": "verarbeitung", "ts": "2026-10-01T10:00:00+0000", "file": "x.pdf",
          "sha256": "s1", "supplier_id": "alpha", "supplier_name": "Alpha",
          "stages": [], "status": "review", "hold_reasons": [], "duration_s": 1.0,
          "aborted": "", "checks": [], "operator": "?",
          "fields": {
              "rechnungsnummer": {"origin": None, "confidence": 0.0},
              "netto": {"origin": "llm", "confidence": 0.4},
              "brutto": {"origin": "template", "confidence": 0.95},
          }}
    store.event_append(rec)
    data = eventlog.report(store)
    felder = {r["feld"]: r for r in data["felder"]}
    assert "rechnungsnummer" in felder and "netto" in felder
    assert "brutto" not in felder  # hohe Konfidenz -> nicht im Bericht


def test_report_groups_corrections_by_field_and_supplier(tmp_path: Path):
    store = Store(tmp_path / "data")
    eventlog.record_correction(store, "s1", "rechnungsnummer", "A", "B",
                               supplier_id="alpha", file="x.pdf")
    eventlog.record_correction(store, "s2", "rechnungsnummer", "C", "D",
                               supplier_id="alpha", file="y.pdf")
    data = eventlog.report(store)
    assert data["korrekturen"][0]["feld"] == "rechnungsnummer"
    assert data["korrekturen"][0]["anzahl"] == 2
    assert "'A' -> 'B'" in data["korrekturen"][0]["beispiele"][0]["grund"]


# ---- CLI ------------------------------------------------------------------
def test_cli_fehlerbericht_text_and_json(tmp_path: Path, capsys):
    store = Store(tmp_path / "data")
    _check_event(store, "alpha", "iban-stammdaten", False, "IBAN weicht ab")
    assert cli.main(["--data", str(store.root), "fehlerbericht"]) == 0
    out = capsys.readouterr().out
    assert "iban-stammdaten" in out and "alpha" in out

    assert cli.main(["--data", str(store.root), "fehlerbericht", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["pruefungen"][0]["pruefung"] == "iban-stammdaten"


def test_cli_fehlerbericht_empty(tmp_path: Path, capsys):
    store = Store(tmp_path / "data")
    assert cli.main(["--data", str(store.root), "fehlerbericht"]) == 0
    assert "Keine Ereignisse" in capsys.readouterr().out


# ---- Server ---------------------------------------------------------------
def _client(store, samples):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod
    return TestClient(server_mod.create_app(store, samples))


def test_server_confirm_logs_event(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    client = _client(store, samples)
    r = client.post("/api/confirm", json={"file": "a.pdf", "template_id": "muster__standard"})
    assert r.status_code == 200
    events = [e for e in store.events() if e["type"] == "verarbeitung"]
    assert events and events[-1]["file"] == "a.pdf" and events[-1]["status"] == AUTO


def test_server_correction_tracks_alt_neu(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    client = _client(store, samples)
    client.post("/api/correction", json={"file": "a.pdf", "field": "rechnungsnummer",
                                         "value": "RE-X"})
    client.post("/api/correction", json={"file": "a.pdf", "field": "rechnungsnummer",
                                         "value": "RE-Y"})
    korrekturen = [e for e in store.events() if e["type"] == "korrektur"]
    assert len(korrekturen) == 2
    assert korrekturen[0]["alt"] is None and korrekturen[0]["neu"] == "RE-X"
    assert korrekturen[1]["alt"] == "RE-X" and korrekturen[1]["neu"] == "RE-Y"
    assert korrekturen[0]["supplier_id"] == "muster"


def test_api_fehlerbericht_endpoint(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    client = _client(store, samples)
    client.post("/api/confirm", json={"file": "a.pdf", "template_id": "muster__standard"})
    r = client.get("/api/fehlerbericht")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"seit", "pruefungen", "felder", "korrekturen", "menschlich"}
