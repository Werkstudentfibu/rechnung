"""JobRouter-Uebergabe fuer den echten Anschluss vorbereiten (SPEC §7s):
Prozess-/Benutzerangaben konfigurierbar, Feldzuordnung ueber eine
Mapping-Datei statt fest im Code, Original-PDF als Multipart-Anhang,
`rechnung jobrouter-test`/`imap-test` pruefen Erreichbarkeit ohne etwas
anzulegen/abzuholen."""

from __future__ import annotations

import email
import json
import urllib.error
from pathlib import Path

import pytest

from rechnung import cli, ingest, jobrouter
from rechnung.config import ImapConfig, JobRouterConfig


class _Resp:
    def __init__(self, status=200, body=b"ok"):
        self.status = status
        self._body = body

    def read(self, *_a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ---- load_mapping / payload_from_result ------------------------------------
def test_load_mapping_reads_file(tmp_path: Path):
    p = tmp_path / "mapping.json"
    p.write_text(json.dumps({"rechnungsnummer": "InvoiceNumber"}), "utf-8")
    cfg = JobRouterConfig(mapping_file=str(p))
    assert jobrouter.load_mapping(cfg) == {"rechnungsnummer": "InvoiceNumber"}


def test_load_mapping_missing_file_returns_empty(tmp_path: Path):
    cfg = JobRouterConfig(mapping_file=str(tmp_path / "nope.json"))
    assert jobrouter.load_mapping(cfg) == {}


def test_load_mapping_ignores_non_string_values(tmp_path: Path):
    p = tmp_path / "mapping.json"
    p.write_text(json.dumps({"netto": "NetAmount", "weird": 5}), "utf-8")
    cfg = JobRouterConfig(mapping_file=str(p))
    assert jobrouter.load_mapping(cfg) == {"netto": "NetAmount"}


def test_load_mapping_default_path_uses_data_dir():
    cfg = JobRouterConfig()
    assert cfg.mapping_path == Path("data/jobrouter-mapping.json")


def test_payload_from_result_includes_process_info_and_mapped_fields(tmp_path: Path):
    p = tmp_path / "mapping.json"
    p.write_text(json.dumps({"rechnungsnummer": "InvoiceNumber"}), "utf-8")
    cfg = JobRouterConfig(process="Rechnungseingang", process_version="3", step="Pruefung",
                          user="svc-rechnung", role="Sachbearbeiter", mapping_file=str(p))
    res = {"file": "a.pdf", "sha256": "abc", "status": "auto", "supplier_id": "muster",
          "fields": {"rechnungsnummer": {"value": "RE-1"}, "netto": {"value": 100.0}},
          "checks": []}
    body = jobrouter.payload_from_result(res, cfg)
    assert body["process"] == {"name": "Rechnungseingang", "version": "3", "step": "Pruefung"}
    assert body["user"] == "svc-rechnung" and body["role"] == "Sachbearbeiter"
    assert body["fields"] == {"InvoiceNumber": "RE-1", "netto": 100.0}


def test_payload_from_result_without_mapping_keeps_own_field_names(tmp_path: Path):
    cfg = JobRouterConfig(mapping_file=str(tmp_path / "nope.json"))
    res = {"file": "a.pdf", "fields": {"netto": {"value": 1.0}}, "checks": []}
    body = jobrouter.payload_from_result(res, cfg)
    assert body["fields"] == {"netto": 1.0}


def test_payload_from_result_includes_lieferantennummer_when_present(tmp_path: Path):
    """Supplier.external_id (Abbyy-'Id') geht als eigenes Feld mit --
    unabhaengig von unserer (ggf. um die BusinessUnitId erweiterten) internen
    supplierId (Lieferant je Rechnungsadresse, 09.10.2026)."""
    cfg = JobRouterConfig(mapping_file=str(tmp_path / "nope.json"))
    res = {"file": "a.pdf", "fields": {}, "checks": [],
          "supplier_id": "9001@zuerich", "supplier_external_id": "9001"}
    body = jobrouter.payload_from_result(res, cfg)
    assert body["supplierId"] == "9001@zuerich"
    assert body["lieferantennummer"] == "9001"


def test_payload_from_result_omits_lieferantennummer_when_absent(tmp_path: Path):
    cfg = JobRouterConfig(mapping_file=str(tmp_path / "nope.json"))
    res = {"file": "a.pdf", "fields": {}, "checks": [], "supplier_id": "muster"}
    body = jobrouter.payload_from_result(res, cfg)
    assert "lieferantennummer" not in body


# ---- Herkunft je Feld/Position (SPEC Baustein 3) ---------------------------

def test_payload_from_result_herkunft_je_feld(tmp_path: Path):
    cfg = JobRouterConfig(mapping_file=str(tmp_path / "nope.json"))
    res = {"file": "a.pdf", "checks": [], "fields": {
        "netto": {"value": 336.13, "herkunft": "errechnet"},
        "rechnungsnummer": {"value": "RE-1", "herkunft": "gelesen"},
        "iban": {"value": "DE..", "herkunft": "gelesen"},
    }}
    body = jobrouter.payload_from_result(res, cfg)
    assert body["herkunft"] == {"netto": "errechnet", "rechnungsnummer": "gelesen",
                                "iban": "gelesen"}


def test_payload_from_result_lineitems_und_taxbreakdown_ohne_unterstrich(tmp_path: Path):
    cfg = JobRouterConfig(mapping_file=str(tmp_path / "nope.json"))
    res = {"file": "a.pdf", "checks": [], "fields": {},
          "line_items": [{"beschreibung": "A", "gesamt_netto": 10.0,
                          "_herkunft": {"gesamt_netto": "errechnet"}}],
          "tax_breakdown": [{"satz": 19, "netto": 10.0, "ust": 1.9,
                             "_herkunft": {"ust": "errechnet"}}]}
    body = jobrouter.payload_from_result(res, cfg)
    assert body["lineItems"] == [{"beschreibung": "A", "gesamt_netto": 10.0,
                                  "herkunft": {"gesamt_netto": "errechnet"}}]
    assert body["taxBreakdown"] == [{"satz": 19, "netto": 10.0, "ust": 1.9,
                                     "herkunft": {"ust": "errechnet"}}]


# ---- Multipart-Anhang --------------------------------------------------------
def test_build_multipart_is_parseable_with_file_and_field():
    data, content_type = jobrouter._build_multipart(
        {"data": json.dumps({"a": 1})}, [("file", "re.pdf", b"%PDF-1.4 fake", "application/pdf")])
    msg = email.message_from_bytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + data)
    parts = list(msg.walk())[1:]
    names = [p.get_param("name", header="Content-Disposition") for p in parts]
    assert "data" in names and "file" in names
    file_part = next(p for p in parts if p.get_param("name", header="Content-Disposition") == "file")
    assert file_part.get_filename() == "re.pdf"
    assert file_part.get_payload(decode=True) == b"%PDF-1.4 fake"


def test_submit_with_attachment_sends_multipart(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        captured["data"] = req.data
        return _Resp(200, b"created")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    pdf = tmp_path / "re.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    cfg = JobRouterConfig(enabled=True, base_url="https://jr.example", mapping_file="")
    res = {"file": "re.pdf", "sha256": "abc", "status": "auto", "fields": {}, "checks": []}

    r = jobrouter.submit(res, tmp_path / "out", cfg, pdf_path=pdf)
    assert r["sent"] is True
    assert captured["headers"]["Content-type"].startswith("multipart/form-data")
    assert b"%PDF-1.4 fake" in captured["data"]


def test_submit_without_pdf_path_sends_plain_json(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _Resp(200, b"created")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    cfg = JobRouterConfig(enabled=True, base_url="https://jr.example")
    res = {"file": "re.pdf", "sha256": "abc", "status": "auto", "fields": {}, "checks": []}
    r = jobrouter.submit(res, tmp_path / "out", cfg)
    assert r["sent"] is True
    assert captured["headers"]["Content-type"] == "application/json"


def test_push_queue_attaches_archived_original(tmp_path: Path, monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _Resp(200, b"created")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    root = tmp_path / "data"
    (root / "archive").mkdir(parents=True)
    (root / "archive" / ("s" * 64 + ".pdf")).write_bytes(b"%PDF-1.4 archived")
    res = {"file": "re.pdf", "sha256": "s" * 64, "status": "auto", "fields": {}, "checks": []}
    jobrouter.enqueue(root, res)

    cfg = JobRouterConfig(enabled=True, base_url="https://jr.example")
    out = jobrouter.push_queue(root, cfg, out_dir=root / "out")
    assert out["sent"] == 1
    assert captured["headers"]["Content-type"].startswith("multipart/form-data")


# ---- check_connection (jobrouter) -------------------------------------------
def test_jobrouter_check_connection_disabled():
    res = jobrouter.check_connection(JobRouterConfig(enabled=False))
    assert res["ok"] is None


def test_jobrouter_check_connection_no_url():
    res = jobrouter.check_connection(JobRouterConfig(enabled=True, base_url=""))
    assert res["ok"] is False


def test_jobrouter_check_connection_success(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp(200))
    res = jobrouter.check_connection(JobRouterConfig(enabled=True, base_url="https://jr.example"))
    assert res["ok"] is True and "200" in res["detail"]


def test_jobrouter_check_connection_auth_error(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.HTTPError("https://jr.example", 401, "unauthorized", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    res = jobrouter.check_connection(JobRouterConfig(enabled=True, base_url="https://jr.example"))
    assert res["ok"] is False and "401" in res["detail"]


def test_jobrouter_check_connection_unreachable(monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("kein Netz")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    res = jobrouter.check_connection(JobRouterConfig(enabled=True, base_url="https://jr.example"))
    assert res["ok"] is False


# ---- check_connection (ingest/IMAP) ------------------------------------------
def test_imap_check_connection_not_configured(monkeypatch):
    for var in ("RECHNUNG_IMAP_HOST", "RECHNUNG_IMAP_USER", "RECHNUNG_IMAP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    res = ingest.check_connection(ImapConfig(host="", user="", password=""))
    assert res["ok"] is None


class _FakeImap:
    def __init__(self, *a, **k):
        pass

    def login(self, user, password):
        return "OK", [b"login"]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Verarbeitet"']

    def select(self, folder, readonly=False):
        assert readonly is True
        return "OK", [b"3"]

    def search(self, charset, criterion):
        return "OK", [b"1 2"]

    def logout(self):
        return "BYE", [b"logout"]


def test_imap_check_connection_success(monkeypatch):
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", _FakeImap)
    cfg = ImapConfig(host="imap.example", user="u", password="p")
    res = ingest.check_connection(cfg)
    assert res["ok"] is True
    assert len(res["folders"]) == 2 and res["unseen"] == 2


def test_imap_check_connection_login_failure(monkeypatch):
    class _Failing(_FakeImap):
        def login(self, user, password):
            raise ingest.imaplib.IMAP4.error("bad credentials")

    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", _Failing)
    cfg = ImapConfig(host="imap.example", user="u", password="wrong")
    res = ingest.check_connection(cfg)
    assert res["ok"] is False


# ---- CLI ---------------------------------------------------------------------
def test_cli_jobrouter_test_text_and_json(capsys, monkeypatch):
    monkeypatch.delenv("RECHNUNG_JOBROUTER_ENABLED", raising=False)
    assert cli.main(["jobrouter-test"]) == 0
    out = capsys.readouterr().out
    assert "[-]" in out or "Trockenlauf" in out

    assert cli.main(["jobrouter-test", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is None


def test_cli_imap_test_text_and_json(capsys, monkeypatch):
    for var in ("RECHNUNG_IMAP_HOST", "RECHNUNG_IMAP_USER", "RECHNUNG_IMAP_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    assert cli.main(["imap-test"]) == 0
    assert "nicht konfiguriert" in capsys.readouterr().out

    assert cli.main(["imap-test", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is None
