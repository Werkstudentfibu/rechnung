"""Zeitmessung je Verarbeitungsstufe (timing.py, SPEC Effizienz-Baustein 0)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from rechnung import cli, nightly
from rechnung.identify import supplier_from_doc
from rechnung.messen import messen, verteilung
from rechnung.pipeline import process
from rechnung.store import Store
from rechnung.timing import StageTimer, distribution, percentile

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402
from rechnung import pdf as pdfmod  # noqa: E402


# ---- StageTimer / percentile / distribution --------------------------------
def test_stage_timer_measures_and_accumulates():
    t = StageTimer()
    with t.stage("a"):
        time.sleep(0.01)
    with t.stage("a"):
        time.sleep(0.01)
    with t.stage("b"):
        pass
    assert t.times["a"] >= 0.02
    assert "b" in t.times


def test_stage_timer_add_nachtraegt_gemessene_dauer():
    t = StageTimer()
    t.add("ocr", 1.5)
    t.add("ocr", 0.5)
    assert t.times["ocr"] == 2.0


def test_percentile_empty_is_zero():
    assert percentile([], 90) == 0.0


def test_percentile_p90_of_sorted_values():
    values = list(range(1, 11))  # 1..10
    assert percentile(values, 90) == 9


def test_distribution_empty_dict():
    assert distribution([]) == {}


def test_distribution_reports_min_median_p90_max():
    d = distribution([1.0, 2.0, 3.0, 4.0, 5.0])
    assert d["anzahl"] == 5
    assert d["min"] == 1.0 and d["max"] == 5.0
    assert d["median"] == 3.0
    assert "p90" in d


# ---- pipeline.process() fuellt Result.stufenzeiten --------------------------
def test_process_records_stage_durations_for_unknown_supplier(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf")
    res = process(inv, store, use_llm=False)
    assert "laden" in res.stufenzeiten
    assert "lieferant" in res.stufenzeiten
    assert "pruefungen" in res.stufenzeiten
    assert all(v >= 0 for v in res.stufenzeiten.values())
    assert res.to_dict()["stufenzeiten"] == res.stufenzeiten


def test_process_records_vorlage_stage_with_template(tmp_path: Path):
    from rechnung import learn

    store = Store(tmp_path / "data")
    src = make_invoice(tmp_path / "a.pdf", rechnungsnr="RE-2026-00001")
    doc = pdfmod.load(src)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    tpl, _ = learn.learn_template(doc, "muster", {
        "rechnungsnummer": "RE-2026-00001", "rechnungsdatum": "2026-02-14",
        "netto": 1000.0, "ust_betrag": 190.0, "ust_satz": 19.0, "brutto": 1190.0})
    tpl.confirmations = 3
    store.save_template(tpl)
    res = process(src, store, use_llm=False)
    assert "vorlage" in res.stufenzeiten
    assert res.status == "auto"


# ---- rechnung/messen.py -----------------------------------------------------
def test_messen_processes_without_side_effects(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    make_invoice(samples / "a.pdf")
    make_invoice(samples / "b.pdf", rechnungsnr="RE-2026-00002")
    results = messen(samples, store)
    assert len(results) == 2
    assert all(r.stufenzeiten for r in results)
    # keine Nebenwirkungen: nichts gespeichert/abgelegt/eingereiht
    assert store.processed() == []
    assert store.events() == []
    assert store.audit_log() == []
    from rechnung.human import human_dir
    assert not human_dir(store.root).exists()


def test_verteilung_aggregates_stage_and_total_durations(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    make_invoice(samples / "a.pdf")
    make_invoice(samples / "b.pdf", rechnungsnr="RE-2026-00002")
    results = messen(samples, store)
    v = verteilung(results)
    assert "gesamt" in v and v["gesamt"]["anzahl"] == 2
    assert "lieferant" in v


def test_cli_messen_text_and_json(tmp_path: Path, capsys):
    samples = tmp_path / "s"
    samples.mkdir()
    make_invoice(samples / "a.pdf")
    assert cli.main(["--data", str(tmp_path / "data"), "--samples", str(samples), "messen"]) == 0
    out = capsys.readouterr().out
    assert "gesamt=" in out
    assert "Verteilung je Stufe" in out

    assert cli.main(["--data", str(tmp_path / "data2"), "--samples", str(samples),
                     "messen", "--json"]) == 0
    import json
    data = json.loads(capsys.readouterr().out)
    assert len(data["dateien"]) == 1
    assert "verteilung" in data


def test_cli_messen_empty_folder(tmp_path: Path, capsys):
    samples = tmp_path / "s"
    samples.mkdir()
    assert cli.main(["--data", str(tmp_path / "data"), "--samples", str(samples), "messen"]) == 0
    assert "Keine Rechnungen" in capsys.readouterr().out


# ---- Nachtlauf-Tagesbericht: Verteilung je Stufe ---------------------------
def test_tagesbericht_reports_stage_distribution(tmp_path: Path):
    from rechnung import eventlog

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    inv = make_invoice(samples / "a.pdf")
    res = process(inv, store, use_llm=False)
    eventlog.record(store, res, "aaron")

    tag = time.strftime("%Y-%m-%d")
    bericht = nightly.tagesbericht(store, samples, tag)
    assert "dauer_je_stufe" in bericht
    assert "lieferant" in bericht["dauer_je_stufe"]
    assert bericht["dauer_je_stufe"]["lieferant"]["anzahl"] == 1
