"""Postfach-Uebersicht zeigt ALLE Postfaecher (nicht nur das ENV-Testpostfach)
und die IMAP-Verbindung hat eine Zeitgrenze (Befund 02.11.2026)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from rechnung import ingest
from rechnung.config import ImapConfig
from rechnung.store import Store
from rechnung.web.server import create_app


def _client(tmp_path: Path, monkeypatch) -> TestClient:
    store = Store(tmp_path / "data")
    samples = tmp_path / "inbox"
    samples.mkdir()
    return TestClient(create_app(store, samples))


def test_uebersicht_listet_alle_postfaecher_mit_grund(tmp_path: Path, monkeypatch):
    c = _client(tmp_path, monkeypatch)
    c.post("/api/postfaecher", json={"id": "a", "user": "a@example.invalid",
                                     "host": "imap.invalid", "passwort": "x"})
    c.post("/api/postfaecher", json={"id": "b", "user": "b@example.invalid",
                                     "host": "imap.invalid"})
    monkeypatch.setattr(ingest, "bericht", lambda cfg=None, dest=None, anzahl=15: {
        "ok": True, "postfach": cfg.user if cfg else "", "mails": [], "ordner": "INBOX"})
    r = c.get("/api/postfach-bericht").json()
    namen = {p["adresse"]: p for p in r["postfaecher"]}
    assert set(namen) >= {"a@example.invalid", "b@example.invalid"}
    assert namen["a@example.invalid"]["nr"] is not None
    assert "Passwort" in namen["b@example.invalid"]["hinweis"]
    r2 = c.get(f"/api/postfach-bericht?nr={namen['a@example.invalid']['nr']}").json()
    assert r2["postfach"] == "a@example.invalid"


def test_verbindung_hat_zeitgrenze(monkeypatch):
    gesehen = {}

    def fake(host, port, timeout=None):
        gesehen["timeout"] = timeout
        raise TimeoutError("haengt")

    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", fake)
    monkeypatch.setenv("RECHNUNG_IMAP_TIMEOUT", "7")
    cfg = ImapConfig(host="imap.invalid", user="u", password="p")
    res = ingest.check_connection(cfg)
    assert gesehen["timeout"] == 7.0 and res["ok"] is False
