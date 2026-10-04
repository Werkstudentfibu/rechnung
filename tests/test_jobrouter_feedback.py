"""Rueckmeldung aus JobRouter (SPEC §7q): Adapter-Schnittstelle
(``jobrouter.FeedbackAdapter``), noch ohne echte JobRouter-API -- dafuer ein
dateibasierter Adapter fuer Tests/Simulation. Rueckmeldungen fliessen als
Korrekturen (Operator ``jobrouter``) in store.corrections, Schattenstatistik
und Ereignisprotokoll ein und stossen Neu-Lernen der Vorlage an."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnung import jobrouter, learn, shadow
from rechnung import pdf as pdfmod
from rechnung.config import JobRouterConfig
from rechnung.identify import supplier_from_doc
from rechnung.jobrouter import (Feedback, FileFeedbackAdapter, apply_feedback,
                               fetch_feedback, queued_invoices)
from rechnung.pipeline import finalize, process
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


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


def _confirmed_and_queued(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    res = process(samples / "a.pdf", store)
    assert res.shadow["would_pass"]
    store.mark_confirmed(res.sha256, "aaron")
    shadow.record(store, res, "aaron")  # wie der Confirm-Endpoint im Training-UI
    fin = finalize(res, store)
    assert fin["finalized"]
    jobrouter.push_queue(store.root)  # Trockenlauf -> landet in queue/done/
    return store, res


# ---- queued_invoices / Adapter --------------------------------------------
def test_queued_invoices_reads_done_folder(tmp_path: Path):
    store, res = _confirmed_and_queued(tmp_path)
    queued = queued_invoices(store.root)
    assert queued == [{"sha256": res.sha256, "file": "a.pdf"}]


def test_file_feedback_adapter_filters_by_queued_sha(tmp_path: Path):
    folder = tmp_path / "feedback"
    folder.mkdir()
    (folder / "x.json").write_text(json.dumps(
        Feedback("sha-a", "a.pdf", fields={"netto": "1200.00"}).to_dict()), "utf-8")
    (folder / "y.json").write_text(json.dumps(
        Feedback("sha-b", "b.pdf", fields={"netto": "5.00"}).to_dict()), "utf-8")

    adapter = FileFeedbackAdapter(folder)
    all_fb = adapter.fetch([])
    assert {fb.sha256 for fb in all_fb} == {"sha-a", "sha-b"}

    filtered = adapter.fetch([{"sha256": "sha-a", "file": "a.pdf"}])
    assert [fb.sha256 for fb in filtered] == ["sha-a"]


def test_file_feedback_adapter_missing_folder_returns_empty(tmp_path: Path):
    adapter = FileFeedbackAdapter(tmp_path / "nope")
    assert adapter.fetch([]) == []


def test_fetch_feedback_defaults_to_file_adapter_when_not_live(tmp_path: Path):
    store, res = _confirmed_and_queued(tmp_path)
    folder = store.root / "jobrouter_feedback"
    folder.mkdir()
    (folder / "f.json").write_text(json.dumps(
        Feedback(res.sha256, "a.pdf", fields={"rechnungsnummer": "RE-X"}).to_dict()), "utf-8")

    found = fetch_feedback(store.root, cfg=JobRouterConfig(enabled=False))
    assert len(found) == 1 and found[0].sha256 == res.sha256


def test_rest_feedback_adapter_handles_request_failure_gracefully(monkeypatch):
    import urllib.error

    from rechnung.jobrouter import RestFeedbackAdapter

    def boom(*a, **k):
        raise urllib.error.URLError("kein Netz")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    cfg = JobRouterConfig(enabled=True, base_url="https://jr.example", token="t")
    adapter = RestFeedbackAdapter(cfg)
    assert adapter.fetch([{"sha256": "abc"}]) == []


# ---- apply_feedback ---------------------------------------------------------
def test_apply_feedback_writes_correction_eventlog_and_shadow(tmp_path: Path):
    store, res = _confirmed_and_queued(tmp_path)
    fb = Feedback(res.sha256, "a.pdf", status="corrected",
                  fields={"rechnungsnummer": "RE-2026-00001-K"})

    out = apply_feedback(store, [fb])
    assert out[0]["sha256"] == res.sha256 and out[0]["fields"] == ["rechnungsnummer"]

    # store.corrections
    assert store.corrections(res.sha256)["rechnungsnummer"] == "RE-2026-00001-K"
    audit_ops = [a["operator"] for a in store.audit_log() if a["action"] == "correct"]
    assert "jobrouter" in audit_ops

    # Ereignisprotokoll
    korrekturen = [e for e in store.events() if e["type"] == "korrektur"]
    assert korrekturen and korrekturen[-1]["operator"] == "jobrouter"
    assert korrekturen[-1]["supplier_id"] == "muster"
    assert korrekturen[-1]["neu"] == "RE-2026-00001-K"

    # Schattenstatistik: war "wuerde durchlassen", jetzt nachtraeglich korrigiert
    last_shadow = store.shadow_log()[-1]
    assert last_shadow["sha256"] == res.sha256
    assert "rechnungsnummer" in last_shadow["corrected"] and last_shadow["wrong"] is True
    assert shadow.stats(store)["totals"]["fehlentscheidungen"] == 1


def test_apply_feedback_without_fields_is_noop(tmp_path: Path):
    store, res = _confirmed_and_queued(tmp_path)
    before = len(store.events())
    out = apply_feedback(store, [Feedback(res.sha256, "a.pdf", status="approved")])
    assert out[0]["learned"] is None
    assert len(store.events()) == before


def test_apply_feedback_ignores_feedback_without_sha(tmp_path: Path):
    store, _res = _confirmed_and_queued(tmp_path)
    assert apply_feedback(store, [Feedback("")]) == []
