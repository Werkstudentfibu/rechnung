"""Postfach-Uebersicht (ingest.bericht) -- nur lesen, mit Grund je Mail."""

from __future__ import annotations

from rechnung import ingest, lock

from test_postfach import FakeImap, _mail, env  # noqa: F401


class FakeBericht(FakeImap):
    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Spam"']

    def search(self, _charset, crit):
        if crit in ("ALL",) or crit.startswith("SINCE"):
            return "OK", [b" ".join(self.mails)]
        return super().search(_charset, crit)

    def fetch(self, num, what):
        self.fetch_cmds.append(what)
        fl = " ".join(sorted(self.flags[num]))
        return "OK", [(num + f" (FLAGS ({fl}) BODY[] {{1}}".encode(), self.mails[num]), b")"]


def test_bericht_zeigt_jede_mail_und_aendert_nichts(env, monkeypatch):
    cfg, store, inbox = env
    fake = FakeBericht({
        b"1": _mail(subject="Rechnung neu", attachments=[("RE-1.pdf", b"%PDF-1.4 a", "application/pdf")]),
        b"2": _mail(subject="Nur Text", body="Hallo"),
    })
    fake.flags[b"2"].add("\\Seen")
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    r = ingest.bericht(cfg, inbox)
    assert r["ok"] and r["anzahl_im_ordner"] == 2 and "Spam" in r["alle_ordner"]
    m = {x["betreff"]: x for x in r["mails"]}
    assert m["Rechnung neu"]["anhaenge"] == [{"name": "RE-1.pdf", "status": "neu"}]
    assert "abgeholt" in m["Rechnung neu"]["aktion"]
    assert m["Nur Text"]["gelesen"] is True
    assert fake.flags[b"1"] == set()            # nichts als gelesen markiert
    assert all("PEEK" in c for c in fake.fetch_cmds)
    assert not inbox.exists()


def test_app_abholung_wartet_nicht_auf_nachtsperre(env, monkeypatch):
    cfg, store, inbox = env
    lock.lock_path(store.root).parent.mkdir(parents=True, exist_ok=True)
    lock.lock_path(store.root).write_text("{}")
    fake = FakeImap({b"1": _mail(attachments=[("RE-1.pdf", b"%PDF-1.4 a", "application/pdf")])})
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    res = ingest.fetch(cfg, inbox, store=store, warten=False)
    assert "Nachtlauf" in res.error and res.saved == []
