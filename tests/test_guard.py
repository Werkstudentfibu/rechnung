"""Zeitgrenze je Rechnung (guard.py, SPEC §7m): eine haengende oder
abstuerzende Rechnung wird abgebrochen und zurueckgestellt, die naechste
laeuft normal weiter."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from rechnung.guard import Guard
from rechnung.pipeline import DEFERRED, scan_folder
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

TARGET = "_guard_helpers:maybe_hang"


def _files(tmp_path: Path, *names: str) -> Path:
    d = tmp_path / "in"
    d.mkdir()
    for i, n in enumerate(names):
        make_invoice(d / n, rechnungsnr=f"RE-{i}")
    return d


def test_hanging_invoice_does_not_block_queue(tmp_path: Path):
    store = Store(tmp_path / "data")
    d = _files(tmp_path, "a_haengt.pdf", "b_ok.pdf", "c_ok.pdf")
    t0 = time.monotonic()
    with Guard(store.root, timeout=2, target=TARGET) as g:
        res = scan_folder(d, store, guard=g)
    assert time.monotonic() - t0 < 30
    by = {Path(r.file).name: r for r in res}
    hung = by["a_haengt.pdf"]
    assert hung.status == DEFERRED and hung.aborted == "zeitgrenze"
    assert "Zeitgrenze 2 s" in hung.hold_reasons[0]
    assert hung.shadow["would_pass"] is False and hung.sha256
    assert by["b_ok.pdf"].aborted == "" and by["b_ok.pdf"].hold_reasons[0].startswith("Lieferant")
    assert by["c_ok.pdf"].aborted == ""
    assert by["b_ok.pdf"].duration_s > 0
    log = [a for a in store.audit_log() if a["action"] == "verarbeitung_abgebrochen"]
    assert len(log) == 1 and log[0]["detail"]["file"] == "a_haengt.pdf"


def test_crash_and_exception_are_contained(tmp_path: Path):
    store = Store(tmp_path / "data")
    d = _files(tmp_path, "a_absturz.pdf", "b_kaputt.pdf", "c_ok.pdf")
    with Guard(store.root, timeout=30, target=TARGET) as g:
        res = {Path(r.file).name: r for r in scan_folder(d, store, guard=g)}
    assert res["a_absturz.pdf"].aborted == "fehler"
    assert "abgestuerzt" in res["a_absturz.pdf"].hold_reasons[0]
    assert res["b_kaputt.pdf"].aborted == "fehler"
    assert "defektes PDF" in res["b_kaputt.pdf"].hold_reasons[0]
    assert res["c_ok.pdf"].aborted == ""


def test_thread_mode_timeout(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "data")
    d = _files(tmp_path, "a.pdf")
    import _guard_helpers
    monkeypatch.setattr(_guard_helpers, "maybe_hang",
                        lambda path, store, use_llm=True: time.sleep(5))
    g = Guard(store.root, timeout=0.3, mode="thread", target=TARGET)
    res = g.process(d / "a.pdf")
    assert res.aborted == "zeitgrenze" and res.status == DEFERRED


def test_timeout_from_env_and_zero_disables(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_TIMEOUT", "7")
    assert Guard(tmp_path).timeout == 7
    monkeypatch.setenv("RECHNUNG_TIMEOUT", "0")
    store = Store(tmp_path / "data")
    d = _files(tmp_path, "a.pdf")
    res = Guard(store.root).process(d / "a.pdf")   # direkt, ohne Arbeitsprozess
    assert res.aborted == "" and res.duration_s >= 0
    monkeypatch.delenv("RECHNUNG_TIMEOUT")
    assert Guard(tmp_path).timeout == 120
