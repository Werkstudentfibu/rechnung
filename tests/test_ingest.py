"""Tests fuer IONOS-Anhang-Parser (ingest) und JobRouter-Warteschlange."""

from __future__ import annotations

from email.message import EmailMessage

from rechnung.config import JobRouterConfig
from rechnung.ingest import attachments_from_bytes
from rechnung.jobrouter import enqueue, push_queue


def _mail(*, attachments: list[tuple[str, bytes, str]]) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Muster Lieferant <rechnung@muster-gmbh.de>"
    msg["Subject"] = "Ihre Rechnung RE-2026-00042"
    msg["Message-ID"] = "<abc@muster-gmbh.de>"
    msg.set_content("Rechnung im Anhang.")
    for name, data, subtype in attachments:
        maintype, _, sub = subtype.partition("/")
        msg.add_attachment(data, maintype=maintype, subtype=sub, filename=name)
    return msg.as_bytes()


def test_attachments_pdf_only():
    raw = _mail(attachments=[
        ("rechnung.pdf", b"%PDF-1.4 fake", "application/pdf"),
        ("logo.png", b"\x89PNG fake", "image/png"),
    ])
    atts = attachments_from_bytes(raw, (".pdf",))
    assert [a.filename for a in atts] == ["rechnung.pdf"]
    assert atts[0].content.startswith(b"%PDF")
    assert atts[0].from_addr.endswith("muster-gmbh.de>")
    assert "RE-2026-00042" in atts[0].subject


def test_attachments_skip_empty():
    raw = _mail(attachments=[("leer.pdf", b"", "application/pdf")])
    assert attachments_from_bytes(raw, (".pdf",)) == []


def test_attachment_name_is_sanitised():
    raw = _mail(attachments=[("../../etc/passwd.pdf", b"%PDF x", "application/pdf")])
    atts = attachments_from_bytes(raw, (".pdf",))
    assert atts and "/" not in atts[0].filename and ".." not in atts[0].filename


def test_jobrouter_queue_dry_run_roundtrip(tmp_path):
    data_root = tmp_path / "data"
    res = {"file": "re1.pdf", "sha256": "deadbeef" * 8, "status": "auto",
           "supplier_id": "muster", "fields": {"rechnungsnummer": {"value": "RE-1"}},
           "checks": []}
    assert enqueue(data_root, res) is not None
    # Zweites Einreihen derselben Rechnung ist erlaubt, solange nicht "done"
    out = push_queue(data_root, JobRouterConfig(), out_dir=data_root / "out")
    assert out["processed"] == 1 and out["dry_run"] is True
    # nach dem Lauf liegt sie unter queue/done/ und wird nicht erneut verarbeitet
    assert push_queue(data_root, JobRouterConfig(), out_dir=data_root / "out")["processed"] == 0
    assert enqueue(data_root, res) is None


def test_jobrouter_respects_allow_status(tmp_path):
    data_root = tmp_path / "data"
    res = {"file": "re2.pdf", "sha256": "c0ffee" * 8, "status": "no_template",
           "fields": {}, "checks": []}
    enqueue(data_root, res)
    cfg = JobRouterConfig(enabled=True, base_url="https://jr.example",
                          allow_status=("auto", "review"))
    out = push_queue(data_root, cfg, out_dir=data_root / "out")
    assert out["results"][0].get("skipped")
