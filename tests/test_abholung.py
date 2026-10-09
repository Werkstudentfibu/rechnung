"""Regelmaessige Postfach-Abholung in der App (abholung.py, 09.10.2026)."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from rechnung.abholung import Abholer, aus_konfiguration
from rechnung.ingest import FetchResult
from rechnung.store import Store
from rechnung.web.server import create_app


def _fake(saved_per_call):
    calls = []

    def fetch(dest):
        calls.append(dest)
        n = saved_per_call[min(len(calls) - 1, len(saved_per_call) - 1)]
        return FetchResult([f"r{i}.pdf" for i in range(n)], n, False)
    return fetch, calls


def test_einmal_counts_new_invoices(tmp_path: Path):
    fetch, calls = _fake([2, 0, 1])
    a = Abholer(tmp_path, 5, fetch=fetch)
    a.einmal(); a.einmal(); st = a.einmal()
    assert len(calls) == 3
    assert st["neu_gesamt"] == 3 and st["zuletzt_neu"] == 1
    assert st["letzte_pruefung"] and st["fehler"] == ""


def test_error_is_reported_not_raised(tmp_path: Path):
    def kaputt(dest):
        raise OSError("Netz weg")
    st = Abholer(tmp_path, 5, fetch=kaputt).einmal()
    assert "Netz weg" in st["fehler"]


def test_thread_polls_and_can_be_triggered(tmp_path: Path):
    fetch, calls = _fake([1])
    a = Abholer(tmp_path, 60, fetch=fetch)   # Takt 1 h -> nur Start + Anstoss
    a.start()
    try:
        for _ in range(100):
            if calls: break
            time.sleep(0.02)
        a.anstossen()
        for _ in range(100):
            if len(calls) >= 2: break
            time.sleep(0.02)
    finally:
        a.stop()
    assert len(calls) >= 2


def test_zero_minutes_never_starts(tmp_path: Path):
    fetch, calls = _fake([1])
    a = Abholer(tmp_path, 0, fetch=fetch)
    a.start(); time.sleep(0.05)
    assert calls == [] and a._thread is None


def test_not_configured_no_poller(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("RECHNUNG_IMAP_USER", raising=False)
    monkeypatch.delenv("RECHNUNG_IMAP_PASSWORD", raising=False)
    assert aus_konfiguration(tmp_path) is None
    c = TestClient(create_app(Store(tmp_path / "data"), tmp_path))
    assert c.get("/api/abholung").json()["eingerichtet"] is False
    assert c.post("/api/abholung").json()["eingerichtet"] is False
