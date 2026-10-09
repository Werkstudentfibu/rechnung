"""Postfach-Baustein 8: Anhaenge nach Beschriftung auswaehlen -- nur
Beleg-Anhaenge (Rechnung/Gutschrift/...) werden verarbeitet, andere
beschriftete Anhaenge (AGB, Lieferschein, ...) haengen als Anlage am Beleg
derselben Mail; kein Beleg oder ein unzuordenbarer Anhang -> Mensch, nie
stillschweigend verworfen."""

from __future__ import annotations

import hashlib
import io

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from rechnung import anlagen, human, ingest, jobrouter
from rechnung.config import ImapConfig
from rechnung.pipeline import process
from rechnung.store import Store

from test_postfach import FakeImap, _mail

CFG = ImapConfig(host="h", user="u", password="p", extensions=(".pdf", ".xml"))


def _pdf(text="Beispieltext") -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(60, 780, text)
    c.save()
    return buf.getvalue()


def test_rechnung_mit_agb_und_lieferschein_wird_ein_beleg_mit_zwei_anlagen(tmp_path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    rechnung_bytes = _pdf("Rechnung")
    raw = _mail(attachments=[
        ("RE-1.pdf", rechnung_bytes, "application/pdf"),
        ("AGB.pdf", _pdf("Allgemeine Geschaeftsbedingungen"), "application/pdf"),
        ("Lieferschein.pdf", _pdf("Lieferschein"), "application/pdf"),
    ])
    fake = FakeImap({b"1": raw})
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    res = ingest.fetch(CFG, tmp_path / "inbox", store=store)

    assert sorted(p.name for p in (tmp_path / "inbox").iterdir()) == ["RE-1.pdf"]
    assert human.offen(store) == []
    assert res.human == []

    sha = hashlib.sha256(rechnung_bytes).hexdigest()
    info = anlagen.info(store, sha)
    assert {a["dateiname"] for a in info} == {"AGB.pdf", "Lieferschein.pdf"}
    assert {a["art"] for a in info} == {"AGB", "Lieferschein"}
    files = anlagen.files(store, sha)
    assert len(files) == 2 and all(f.exists() for f in files)


def test_mail_nur_mit_agb_geht_an_mensch(tmp_path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    raw = _mail(attachments=[("AGB.pdf", _pdf("Allgemeine Geschaeftsbedingungen"),
                              "application/pdf")])
    fake = FakeImap({b"1": raw})
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    ingest.fetch(CFG, tmp_path / "inbox", store=store)

    assert not (tmp_path / "inbox").exists() or list((tmp_path / "inbox").iterdir()) == []
    offen = human.offen(store)
    assert len(offen) == 1
    assert ingest.MAIL_OHNE_BELEG_ANHANG in offen[0]["gruende"][0]["grund"]
    assert "AGB" in offen[0]["gruende"][0]["grund"]


def test_unbeschrifteter_anhang_wird_als_rechnung_verarbeitet(tmp_path, monkeypatch):
    """Rechnungs-Postfach (09.10.2026): einziger, nicht beschrifteter Anhang
    -> in den Eingang (Pipeline entscheidet), nicht als .eml an den Menschen."""
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    raw = _mail(attachments=[("dokument123.pdf", _pdf("Nur ein Textblock ohne Beschriftung"),
                              "application/pdf")])
    fake = FakeImap({b"1": raw})
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    ingest.fetch(CFG, tmp_path / "inbox", store=store)

    assert [p.name for p in (tmp_path / "inbox").iterdir()] == ["dokument123.pdf"]
    assert human.offen(store) == []


def test_unbeschriftet_neben_agb_geht_weiter_an_mensch(tmp_path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    raw = _mail(attachments=[
        ("AGB.pdf", _pdf("Allgemeine Geschaeftsbedingungen"), "application/pdf"),
        ("dokument123.pdf", _pdf("Nur ein Textblock"), "application/pdf")])
    fake = FakeImap({b"1": raw})
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    ingest.fetch(CFG, tmp_path / "inbox", store=store)

    assert not (tmp_path / "inbox").exists() or list((tmp_path / "inbox").iterdir()) == []
    assert len(human.offen(store)) == 1


def test_unzuordenbarer_anhang_neben_beleg_geht_einzeln_an_mensch(tmp_path, monkeypatch):
    """Rechnung + ein Anhang, der weder Beleg noch bekannte Anlage ist: die
    Rechnung wird normal verarbeitet, der unklare Anhang geht EINZELN an den
    Menschen (nicht die ganze Mail)."""
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    raw = _mail(attachments=[
        ("RE-1.pdf", _pdf("Rechnung"), "application/pdf"),
        ("dokument123.pdf", _pdf("Nur ein Textblock ohne Beschriftung"), "application/pdf"),
    ])
    fake = FakeImap({b"1": raw})
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    ingest.fetch(CFG, tmp_path / "inbox", store=store)

    assert sorted(p.name for p in (tmp_path / "inbox").iterdir()) == ["RE-1.pdf"]
    offen = human.offen(store)
    assert len(offen) == 1 and offen[0]["datei"] == "dokument123.pdf"
    assert ingest.ANHANG_NICHT_ZUZUORDNEN in offen[0]["gruende"][0]["grund"]


def test_anlagenart_aus_dem_dokumenttitel_wenn_dateiname_nichts_sagt(tmp_path, monkeypatch):
    """Ohne sprechenden Dateinamen entscheidet der Titel der ersten Seite."""
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    rechnung_bytes = _pdf("Rechnung")
    raw = _mail(attachments=[
        ("RE-1.pdf", rechnung_bytes, "application/pdf"),
        ("anhang2.pdf", _pdf("Lieferschein"), "application/pdf"),
    ])
    fake = FakeImap({b"1": raw})
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    ingest.fetch(CFG, tmp_path / "inbox", store=store)

    assert human.offen(store) == []
    sha = hashlib.sha256(rechnung_bytes).hexdigest()
    info = anlagen.info(store, sha)
    assert info == [{"dateiname": "anhang2.pdf", "art": "Lieferschein"}]


def test_anlagen_gehen_mit_an_jobrouter(tmp_path, monkeypatch):
    """Die Anlagen eines Belegs (anlagen.py) kommen bei der Verarbeitung
    wieder heraus und landen im JobRouter-Datensatz als "attachments"."""
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    rechnung_bytes = _pdf("Rechnung")
    raw = _mail(attachments=[
        ("RE-1.pdf", rechnung_bytes, "application/pdf"),
        ("AGB.pdf", _pdf("Allgemeine Geschaeftsbedingungen"), "application/pdf"),
    ])
    fake = FakeImap({b"1": raw})
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    ingest.fetch(CFG, tmp_path / "inbox", store=store)

    res = process(tmp_path / "inbox" / "RE-1.pdf", store, use_llm=False)
    assert res.anlagen == [{"dateiname": "AGB.pdf", "art": "AGB"}]
    body = jobrouter.payload_from_result(res.to_dict())
    assert body["attachments"] == [{"dateiname": "AGB.pdf", "art": "AGB"}]
