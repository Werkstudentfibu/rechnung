"""Auch im Webmail schon GELESENE Mails abholen, aber jede nur einmal
(Befund Aaron 09.10.2026: neue Mail kam nach "Postfach pruefen" nicht an)."""

from __future__ import annotations

from rechnung import ingest

from test_postfach import FakeImap, _mail, _run, env  # noqa: F401


class FakeImapSince(FakeImap):
    def search(self, _charset, crit):
        if crit.startswith("SINCE"):
            return "OK", [b" ".join(self.mails)]
        return super().search(_charset, crit)


def _pdf_mail(name, inhalt, subject):
    return _mail(body=subject, subject=subject,
                 attachments=[(name, inhalt, "application/pdf")])


def test_gelesene_neue_mail_wird_abgeholt(env, monkeypatch):
    cfg, store, inbox = env
    fake = FakeImapSince({b"1": _pdf_mail("RE-1.pdf", b"%PDF-1.4 eins", "Rechnung 1")})
    fake.flags[b"1"].add("\\Seen")          # im Webmail geoeffnet
    res = _run(fake, cfg, store, inbox, monkeypatch)
    assert res.mails == 1 and len(res.saved) == 1
    assert (inbox / "RE-1.pdf").exists()


def test_jede_mail_nur_einmal(env, monkeypatch):
    cfg, store, inbox = env
    fake = FakeImapSince({b"1": _pdf_mail("RE-1.pdf", b"%PDF-1.4 eins", "Rechnung 1")})
    _run(fake, cfg, store, inbox, monkeypatch)
    (inbox / "RE-1.pdf").unlink()          # z.B. aus dem Eingang entfernt
    res2 = _run(fake, cfg, store, inbox, monkeypatch)
    assert res2.mails == 0 and res2.saved == []
    assert not (inbox / "RE-1.pdf").exists()


def test_gleicher_anhang_nicht_doppelt(env, monkeypatch):
    cfg, store, inbox = env
    fake = FakeImapSince({b"1": _pdf_mail("RE-1.pdf", b"%PDF-1.4 gleich", "Rechnung A"),
                          b"2": _pdf_mail("RE-1 Kopie.pdf", b"%PDF-1.4 gleich", "Rechnung B")})
    res = _run(fake, cfg, store, inbox, monkeypatch)
    assert res.mails == 2 and len(res.saved) == 1
    assert len(list(inbox.iterdir())) == 1
