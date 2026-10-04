"""Nachtlauf (SPEC §7r): JobRouter-Rueckmeldungen, Layoutwechsel-Uebersicht,
Fehler-/Tagesbericht, Gesundheitschecks, Lock-Datei pausiert laufende
Abholung/Verarbeitung."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from rechnung import cli, jobrouter, lock, nightly
from rechnung import pdf as pdfmod
from rechnung.config import ImapConfig
from rechnung.guard import Guard
from rechnung.identify import supplier_from_doc
from rechnung.jobrouter import Feedback
from rechnung.pipeline import finalize, process
from rechnung.store import Store
from rechnung.template import Template

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


# ---- lock.py ---------------------------------------------------------------
def test_lock_wait_returns_immediately_when_unlocked(tmp_path: Path):
    t0 = time.monotonic()
    lock.wait_until_unlocked(tmp_path, max_wait=5, poll=0.01)
    assert time.monotonic() - t0 < 0.5


def test_lock_acquire_blocks_a_second_acquire(tmp_path: Path):
    with lock.acquire(tmp_path):
        assert lock.is_locked(tmp_path)
        with pytest.raises(RuntimeError):
            with lock.acquire(tmp_path):
                pass
    assert not lock.is_locked(tmp_path)  # Lock wird beim Verlassen freigegeben


def test_lock_wait_until_unlocked_waits_then_returns(tmp_path: Path):
    results = []

    def holder():
        with lock.acquire(tmp_path):
            time.sleep(0.2)
        results.append("released")

    t = threading.Thread(target=holder)
    t.start()
    time.sleep(0.05)
    assert lock.is_locked(tmp_path)
    t0 = time.monotonic()
    lock.wait_until_unlocked(tmp_path, max_wait=5, poll=0.02)
    assert time.monotonic() - t0 >= 0.1
    t.join()
    assert results == ["released"]


def test_guard_process_waits_for_lock(tmp_path: Path):
    """Guard.process() pausiert, solange der Nachtlauf laeuft -- sobald die
    Lock-Datei verschwindet, laeuft die Verarbeitung normal weiter."""
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    p = make_invoice(samples / "a.pdf")
    guard = Guard(store.root, timeout=0)  # timeout=0 -> direkt im Aufrufer
    order = []

    def hold_and_release():
        with lock.acquire(store.root):
            order.append("locked")
            time.sleep(0.15)
        order.append("unlocked")

    t = threading.Thread(target=hold_and_release)
    t.start()
    time.sleep(0.05)
    res = guard.process(p, use_llm=False)
    order.append("processed")
    t.join()
    assert res.file == str(p)
    assert order == ["locked", "unlocked", "processed"]


# ---- Gesundheitschecks ------------------------------------------------------
def test_health_checks_report_not_configured_without_imap_env(tmp_path: Path, monkeypatch):
    for var in ("RECHNUNG_IMAP_HOST", "RECHNUNG_IMAP_USER", "RECHNUNG_IMAP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    store = Store(tmp_path / "data")
    checks = nightly.health_checks(store)
    assert checks["ionos"]["ok"] is None
    assert checks["queue"]["ok"] is True
    assert checks["platte"]["ok"] is True


def test_health_check_queue_warns_when_backlog_large(tmp_path: Path):
    store = Store(tmp_path / "data")
    for i in range(nightly.QUEUE_WARN + 1):
        jobrouter.enqueue(store.root, {"file": f"r{i}.pdf", "sha256": f"sha{i}" * 4})
    checks = nightly.health_checks(store)
    assert checks["queue"]["ok"] is False


# ---- Tagesbericht ------------------------------------------------------------
def _secured_setup(tmp_path: Path):
    from rechnung import learn

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


def test_tagesbericht_counts_events_of_the_given_day(tmp_path: Path):
    from rechnung import eventlog, shadow

    store, samples = _secured_setup(tmp_path)
    res = process(samples / "a.pdf", store)
    store.mark_confirmed(res.sha256, "aaron")
    shadow.record(store, res, "aaron")
    eventlog.record(store, res, "aaron")
    finalize(res, store)

    tag = time.strftime("%Y-%m-%d")
    bericht = nightly.tagesbericht(store, samples, tag)
    assert bericht["verarbeitet"] == 1
    assert bericht["an_mensch"] == 0  # status AUTO
    assert bericht["fehlerquote"] == 0.0
    assert bericht["rueckstand"] == 0  # a.pdf bereits uebernommen

    gestern = "2000-01-01"
    assert nightly.tagesbericht(store, samples, gestern)["verarbeitet"] == 0


def test_tagesbericht_counts_unconfirmed_backlog(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    make_invoice(samples / "b.pdf", rechnungsnr="RE-2026-00002", netto=50.0)
    bericht = nightly.tagesbericht(store, samples)
    assert bericht["rueckstand"] == 2  # a.pdf und b.pdf noch nie uebernommen


# ---- run_nightly end-to-end --------------------------------------------------
def test_run_nightly_writes_report_and_applies_jobrouter_feedback(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    res = process(samples / "a.pdf", store)
    store.mark_confirmed(res.sha256, "aaron")
    fin = finalize(res, store)
    assert fin["finalized"]
    jobrouter.push_queue(store.root)  # Trockenlauf -> queue/done/

    fb_dir = store.root / "jobrouter_feedback"
    fb_dir.mkdir()
    (fb_dir / "f.json").write_text(json.dumps(
        Feedback(res.sha256, "a.pdf", fields={"rechnungsnummer": "RE-2026-00001-K"}).to_dict()),
        "utf-8")

    bericht = nightly.run_nightly(store, samples)
    assert Path(bericht["datei"]).exists()
    assert bericht["jobrouter_feedback"]["abgeholt"] == 1
    assert store.corrections(res.sha256)["rechnungsnummer"] == "RE-2026-00001-K"
    assert bericht["layoutwechsel"]["veraltete_vorlagen"] == []
    assert set(bericht["gesundheit"]) == {"ionos", "queue", "platte"}
    assert not lock.is_locked(store.root)
    actions = [a["action"] for a in store.audit_log()]
    assert "nachtlauf" in actions


def test_run_nightly_fails_fast_when_already_locked(tmp_path: Path):
    store = Store(tmp_path / "data")
    with lock.acquire(store.root):
        with pytest.raises(RuntimeError):
            nightly.run_nightly(store)


def test_run_nightly_reports_stale_templates(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    tpl = store.template("muster__standard")
    tpl.stale = True
    store.save_template(tpl)
    bericht = nightly.run_nightly(store, samples)
    assert bericht["layoutwechsel"]["veraltete_vorlagen"] == [
        {"template_id": "muster__standard", "supplier_id": "muster"}]


# ---- CLI --------------------------------------------------------------------
def test_cli_nacht_text_and_json(tmp_path: Path, capsys):
    store, samples = _secured_setup(tmp_path)
    assert cli.main(["--data", str(store.root), "--samples", str(samples), "nacht"]) == 0
    out = capsys.readouterr().out
    assert "Nachtlauf abgeschlossen" in out

    assert cli.main(["--data", str(store.root), "--samples", str(samples),
                     "nacht", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert "tagesbericht" in data and "gesundheit" in data


def test_cli_nacht_reports_error_when_locked(tmp_path: Path, capsys):
    store = Store(tmp_path / "data")
    with lock.acquire(store.root):
        assert cli.main(["--data", str(store.root), "nacht"]) == 1
    assert "laeuft bereits" in capsys.readouterr().out
