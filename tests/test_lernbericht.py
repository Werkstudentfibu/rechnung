"""Lernbericht an die Entwicklerin (Aaron 11.10.2026)."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from rechnung import lernbericht
from rechnung.web.server import create_app

from test_jobrouter_feedback import _secured_setup  # noqa: F401


class FakeSMTP:
    gesendet = []

    def __init__(self, host, port, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, u, p):
        self.u = u

    def send_message(self, msg):
        FakeSMTP.gesendet.append(msg)


def _bestaetigt(tmp_path):
    store, samples = _secured_setup(tmp_path)
    c = TestClient(create_app(store, samples))
    d = c.get("/api/invoice?file=a.pdf").json()
    c.post("/api/correction", json={"file": "a.pdf", "field": "rechnungsnummer",
                                    "value": "RE-2026-00001", "template_id": d["template_id"]})
    assert c.post("/api/senden", json={"file": "a.pdf", "template_id": d["template_id"]}).status_code == 200
    return store


def test_bericht_enthaelt_rechnung_und_korrektur(tmp_path: Path):
    store = _bestaetigt(tmp_path)
    b = lernbericht.erstellen(store)
    assert b["zusammenfassung"]["rechnungen"] == 1
    r = b["rechnungen"][0]
    assert r["lieferant"] == "Muster Handels GmbH" and "rechnungsnummer" in r["korrigiert"]
    assert any(k["field"] == "rechnungsnummer" for k in b["korrekturen"])


def test_senden_einmal_und_nur_neues(tmp_path: Path, monkeypatch):
    store = _bestaetigt(tmp_path)
    monkeypatch.setenv("RECHNUNG_BERICHT_AN", "emmy@example.org")
    monkeypatch.setenv("RECHNUNG_SMTP_USER", "ap@example.org")
    monkeypatch.setenv("RECHNUNG_SMTP_PASSWORD", "x")
    FakeSMTP.gesendet = []
    r = lernbericht.senden(store, smtp_factory=FakeSMTP)
    assert r["gesendet"] and r["rechnungen"] == 1
    msg = FakeSMTP.gesendet[0]
    anh = [p for p in msg.iter_attachments()]
    assert [a.get_filename() for a in anh] == ["lernbericht.json"]   # ohne PDF
    assert json.loads(anh[0].get_content())["zusammenfassung"]["rechnungen"] == 1
    assert lernbericht.senden(store, smtp_factory=FakeSMTP) == {"gesendet": False, "grund": "nichts Neues"}


def test_ohne_empfaenger_aus(tmp_path: Path, monkeypatch):
    store = _bestaetigt(tmp_path)
    monkeypatch.delenv("RECHNUNG_BERICHT_AN", raising=False)
    assert not lernbericht.faellig(store)
    assert lernbericht.senden(store)["gesendet"] is False
