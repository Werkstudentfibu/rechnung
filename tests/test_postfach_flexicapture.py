"""ABBYY FlexiCapture liest die Postfaecher und verschiebt jede Mail sofort
nach Done/Error -- INBOX ist immer leer (Befund Aaron 05.11.2026). Das
Programm liest deshalb Done/Error mit, NUR LESEND."""

from __future__ import annotations

import dataclasses

from rechnung import config, ingest
from rechnung.postfach import Postfach

from test_postfach import FakeImap, _mail, _run, env  # noqa: F401


class FakeOrdner(FakeImap):
    """Mehrere Ordner, je eigene Mails/Nummern; merkt sich SELECTs."""

    def __init__(self, ordner: dict[str, dict[bytes, bytes]]):
        super().__init__({})
        self.ordner = ordner
        self.aktuell = ""
        self.selects: list[tuple[str, bool]] = []
        self.flags_je = {o: {k: set() for k in m} for o, m in ordner.items()}

    def select(self, name="INBOX", readonly=False):
        self.selects.append((name, readonly))
        if name not in self.ordner:
            return "NO", [b"nicht vorhanden"]
        self.aktuell = name
        self.mails = self.ordner[name]
        self.flags = self.flags_je[name]
        return "OK", [str(len(self.mails)).encode()]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" Done', b'(\\HasNoChildren) "/" "INBOX/FlexiCapture12Processing"',
                      b'(\\HasNoChildren) "/" Error']

    def search(self, _charset, crit):
        if crit == "ALL" or crit.startswith("SINCE"):
            return "OK", [b" ".join(self.mails)]
        return super().search(_charset, crit)

    def fetch(self, num, what):
        if "MESSAGE-ID" in what:
            mid = f"<{self.aktuell}-{num.decode()}@x>".encode()
            return "OK", [(num + b" (BODY[HEADER] {1}", b"Message-ID: " + mid + b"\r\n\r\n"), b")"]
        return super().fetch(num, what)


def _pdf(name):
    return _mail(subject=f"Rechnung {name}", body="Rechnung",
                 attachments=[(name, b"%PDF-1.4 " + name.encode(), "application/pdf")])


def test_liest_done_und_error_nur_lesend(env, monkeypatch):
    cfg, store, inbox = env
    cfg = dataclasses.replace(cfg, source_folder="Done,Error", nur_lesen=True)
    fake = FakeOrdner({"Done": {b"1": _pdf("RE-done.pdf")}, "Error": {b"1": _pdf("RE-error.pdf")}})
    fake.flags_je["Done"][b"1"].add("\\Seen")
    res = _run(fake, cfg, store, inbox, monkeypatch)
    assert res.mails == 2, res.error
    assert (inbox / "RE-done.pdf").exists() and (inbox / "RE-error.pdf").exists()
    assert fake.selects == [("Done", True), ("Error", True)]
    assert fake.flags_je["Error"][b"1"] == set()      # nichts markiert
    assert fake.copied == []                          # nichts verschoben
    # zweiter Lauf: alles bekannt, auch die ungelesene Mail in Error
    assert _run(fake, cfg, store, inbox, monkeypatch).mails == 0


def test_fehlender_ordner_blockiert_die_anderen_nicht(env, monkeypatch):
    cfg, store, inbox = env
    cfg = dataclasses.replace(cfg, source_folder="Gibtsnicht,Done", nur_lesen=True)
    fake = FakeOrdner({"Done": {b"1": _pdf("RE-done.pdf")}})
    res = _run(fake, cfg, store, inbox, monkeypatch)
    assert res.mails == 1 and "Gibtsnicht" in res.error


def test_postfaecher_nutzen_ordner_aus_env(env, monkeypatch):
    cfg, store, inbox = env
    store.save_postfach(Postfach(id="p1", user="ap@firma.de"))
    store.save_postfach(Postfach(id="p2", user="eigen@firma.de", source_folder="Rechnungen"))
    monkeypatch.setenv("RECHNUNG_IMAP_USER", "")
    monkeypatch.setenv("RECHNUNG_POSTFACH_ORDNER", " Done , Error ")
    je = {c.user: c for _l, c, _m in config.postfaecher(store)}
    assert je["ap@firma.de"].source_folder == "Done,Error" and je["ap@firma.de"].nur_lesen
    assert je["eigen@firma.de"].source_folder == "Rechnungen"     # eigener Ordner bleibt
    assert not je["eigen@firma.de"].nur_lesen
    monkeypatch.delenv("RECHNUNG_POSTFACH_ORDNER")
    je = {c.user: c for _l, c, _m in config.postfaecher(store)}
    assert je["ap@firma.de"].source_folder == "INBOX" and not je["ap@firma.de"].nur_lesen


def test_bericht_ueber_beide_ordner_und_saubere_ordnernamen(env, monkeypatch):
    cfg, store, inbox = env
    cfg = dataclasses.replace(cfg, source_folder="Done,Error", nur_lesen=True)
    fake = FakeOrdner({"Done": {b"1": _pdf("RE-a.pdf"), b"2": _pdf("RE-b.pdf")},
                       "Error": {b"1": _pdf("RE-c.pdf")}})
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    r = ingest.bericht(cfg, inbox)
    assert r["ok"], r["fehler"]
    assert r["je_ordner"] == {"Done": 2, "Error": 1} and r["anzahl_im_ordner"] == 3
    assert r["alle_ordner"] == ["Done", "INBOX/FlexiCapture12Processing", "Error"]
    assert r["nur_lesen"] is True
    assert sorted(m["ordner"] for m in r["mails"]) == ["Done", "Done", "Error"]
    assert all(ro for _n, ro in fake.selects)


def test_ordnername_varianten():
    assert ingest._ordnername(b'(\\HasNoChildren) "/" Done') == "Done"
    assert ingest._ordnername(b'(\\HasChildren) "/" "INBOX"') == "INBOX"
    assert ingest._ordnername(b'(\\Noselect) NIL "Gesendete Objekte"') == "Gesendete Objekte"
