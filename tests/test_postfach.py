"""Postfach-Abholung (Baustein 0): BODY.PEEK[] statt RFC822, jede Mail fuehrt
zu einem Ergebnis -- Anhaenge im Eingang ODER Eintrag im Ordner
"Menschliche Bearbeitung" mit Grund + .eml. Erst danach \\Seen."""

from __future__ import annotations

import json
from email.message import EmailMessage

import pytest

from rechnung import human, ingest
from rechnung.config import ImapConfig
from rechnung.store import Store


def _mail(body="Hallo,\nanbei.", attachments=(), subject="Ihre Rechnung", html=None) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Lieferant <rechnung@lieferant.de>"
    msg["Subject"] = subject
    msg["Message-ID"] = f"<{abs(hash((body, subject)))}@lieferant.de>"
    msg.set_content(body)
    if html:
        msg.add_alternative(html, subtype="html")
    for name, data, ctype in attachments:
        main, _, sub = ctype.partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return msg.as_bytes()


class FakeImap:
    """Minimaler IMAP-Server im Speicher: merkt sich FETCH-Befehle und Flags."""

    def __init__(self, mails: dict[bytes, bytes]):
        self.mails = mails
        self.flags: dict[bytes, set] = {k: set() for k in mails}
        self.fetch_cmds: list[str] = []
        self.copied: list = []

    def login(self, *a):
        return "OK", [b""]

    def select(self, *a, **k):
        return "OK", [b"1"]

    def search(self, _charset, crit):
        ids = [k for k in self.mails if "\\Seen" not in self.flags[k]]
        return "OK", [b" ".join(ids)]

    def fetch(self, num, what):
        self.fetch_cmds.append(what)
        if "RFC822" in what or ("BODY[" in what and "PEEK" not in what):
            self.flags[num].add("\\Seen")   # wie ein echter Server
        return "OK", [(num + b" (BODY[] {1}", self.mails[num]), b")"]

    def store(self, num, op, flag):
        self.flags[num].add(flag)
        return "OK", [b""]

    def copy(self, num, folder):
        self.copied.append((num, folder))
        return "OK", [b""]

    def expunge(self):
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("RECHNUNG_DATA", str(data))
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    cfg = ImapConfig(host="imap.test", user="u", password="p", ssl=True,
                     extensions=(".pdf", ".xml"))
    return cfg, Store(data), tmp_path / "inbox"


def _run(fake, cfg, store, inbox, monkeypatch):
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    return ingest.fetch(cfg, inbox, store=store)


def test_mail_ohne_anhang_geht_an_mensch_nicht_verloren(env, monkeypatch):
    """Regression: Mail ohne Anhang wurde per RFC822 als gelesen markiert und
    dann uebersprungen -> nie wieder gesehen."""
    cfg, store, inbox = env
    fake = FakeImap({b"1": _mail("Guten Tag, kurze Rueckfrage zu Ihrem Angebot.",
                                 subject="Rueckfrage")})
    res = _run(fake, cfg, store, inbox, monkeypatch)
    assert all("PEEK" in c for c in fake.fetch_cmds)
    assert res.mails == 1 and res.saved == []
    offen = human.offen(store)
    assert len(offen) == 1
    rec = offen[0]
    assert rec["datei"].endswith(".eml")
    assert rec["gruende"][0]["grund"] == ingest.MAIL_OHNE_ANHANG
    assert rec["gruende"][0]["art"] == human.ART_MAIL
    eml = human.human_dir(store.root) / rec["datei"]
    assert eml.read_bytes() == fake.mails[b"1"]
    # erst nach dem Ablegen gelesen markiert
    assert "\\Seen" in fake.flags[b"1"]


def test_mail_mit_rechnung_im_anhang(env, monkeypatch):
    cfg, store, inbox = env
    fake = FakeImap({b"7": _mail(attachments=[("RE-1.pdf", b"%PDF-1.4 x", "application/pdf")])})
    res = _run(fake, cfg, store, inbox, monkeypatch)
    assert len(res.saved) == 1 and (inbox / "RE-1.pdf").exists()
    assert human.offen(store) == []
    assert "\\Seen" in fake.flags[b"7"]


def test_nur_link_im_mailtext(env, monkeypatch):
    cfg, store, inbox = env
    fake = FakeImap({b"2": _mail("Ihre Rechnung steht bereit: https://portal.example/rechnung/42",
                                 subject="Neue Rechnung")})
    _run(fake, cfg, store, inbox, monkeypatch)
    rec = human.offen(store)[0]
    assert rec["gruende"][0]["grund"] == ingest.MAIL_NUR_LINK


def test_rechnung_im_mailtext_als_pdf_gesichert(env, monkeypatch):
    cfg, store, inbox = env
    html = ("<html><body><h1>Rechnung 2026-17</h1><table><tr><td>Beratung</td>"
            "<td>1.190,00 EUR</td></tr></table><p>Gesamtbetrag 1.190,00 EUR</p></body></html>")
    fake = FakeImap({b"3": _mail("Rechnung 2026-17\nGesamtbetrag 1.190,00 EUR", html=html,
                                 subject="Rechnung 2026-17")})
    _run(fake, cfg, store, inbox, monkeypatch)
    rec = human.offen(store)[0]
    assert rec["gruende"][0]["grund"] == ingest.MAIL_TEXT_RECHNUNG
    folder = human.human_dir(store.root)
    pdf = folder / rec["datei"]
    assert pdf.suffix == ".pdf" and pdf.read_bytes().startswith(b"%PDF")
    assert (folder / (rec["datei"] + ".eml")).exists()
    from rechnung import pdf as pdfmod
    assert "1.190,00" in pdfmod.load(pdf).text
    # von Hand abschliessen: Kopie + .eml weg, Vermerk unter erledigt/
    done = human.erledigt_sha(store, rec["sha256"], "tester", "manuell erfasst")
    assert done and not pdf.exists() and not (folder / (rec["datei"] + ".eml")).exists()
    assert json.loads((folder / "erledigt" / f"{rec['sha256']}.json").read_text())["notiz"]


def test_nicht_verwertbarer_anhang_wird_genannt(env, monkeypatch):
    cfg, store, inbox = env
    fake = FakeImap({b"4": _mail(attachments=[("angebot.docx", b"PK..", "application/msword")],
                                 subject="Angebot")})
    _run(fake, cfg, store, inbox, monkeypatch)
    grund = human.offen(store)[0]["gruende"][0]["grund"]
    assert grund.startswith(ingest.MAIL_OHNE_ANHANG) and "angebot.docx" in grund


def test_verschluesselte_mail(env, monkeypatch):
    cfg, store, inbox = env
    msg = EmailMessage()
    msg["From"] = "x@y.de"
    msg["Subject"] = "Rechnung"
    msg.set_content(b"\x30\x80garbage", maintype="application", subtype="pkcs7-mime",
                    params={"smime-type": "enveloped-data"}, filename="smime.p7m")
    fake = FakeImap({b"5": msg.as_bytes()})
    _run(fake, cfg, store, inbox, monkeypatch)
    assert human.offen(store)[0]["gruende"][0]["grund"] == ingest.MAIL_VERSCHLUESSELT


def test_fehler_beim_sichern_laesst_mail_ungelesen(env, monkeypatch):
    cfg, store, inbox = env
    fake = FakeImap({b"6": _mail(attachments=[("RE-2.pdf", b"%PDF-1.4 y", "application/pdf")])})

    def boom(*a, **k):
        raise OSError("Platte voll")
    monkeypatch.setattr(ingest, "_save", boom)
    res = _run(fake, cfg, store, inbox, monkeypatch)
    assert "Platte voll" in res.error
    assert "\\Seen" not in fake.flags[b"6"]


def test_gleiche_mail_zweimal_nur_ein_eintrag(env, monkeypatch):
    cfg, store, inbox = env
    raw = _mail("Nur ein Gruss.", subject="Hallo")
    for _ in range(2):
        _run(FakeImap({b"1": raw}), cfg, store, inbox, monkeypatch)
    assert len(human.offen(store)) == 1
