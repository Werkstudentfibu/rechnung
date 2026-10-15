"""Mehrere Postfaecher nacheinander abholen (Entscheidung Aaron 15.10.2026,
~50 IONOS-Postfaecher): sequentiell (nicht parallel, schwacher Laptop), ein
kaputtes Postfach blockiert die anderen nicht. Ergaenzt ``test_abholung.py``
(ein einzelnes ENV-Postfach, unveraendertes Verhalten)."""

from __future__ import annotations

from pathlib import Path

from rechnung.abholung import Abholer, aus_konfiguration
from rechnung.config import ImapConfig
from rechnung.ingest import FetchResult
from rechnung.postfach import Postfach
from rechnung.store import Store


def test_mehrere_postfaecher_nacheinander_kaputtes_blockiert_nicht_die_anderen(tmp_path: Path):
    calls: list[str] = []

    def fake_fetch(cfg=None, dest=None, dry_run=False, limit=0, store=None,
                  warten=True, mandant_address_id=None):
        label = cfg.user
        calls.append(label)
        if label == "kaputt@example.de":
            return FetchResult([], 0, False, "Login fehlgeschlagen")
        return FetchResult([f"{label}.pdf"], 1, False)

    mailboxes = [
        ("Postfach A", ImapConfig(host="x", user="a@example.de", password="p"), "a"),
        ("Postfach kaputt", ImapConfig(host="x", user="kaputt@example.de", password="p"), "kaputt"),
        ("Postfach C", ImapConfig(host="x", user="c@example.de", password="p"), "c"),
    ]
    a = Abholer(tmp_path, 5, mailboxes=mailboxes)

    import rechnung.ingest as ingest_mod
    real_fetch = ingest_mod.fetch
    ingest_mod.fetch = fake_fetch
    try:
        st = a.einmal()
    finally:
        ingest_mod.fetch = real_fetch

    assert calls == ["a@example.de", "kaputt@example.de", "c@example.de"]
    assert st["zuletzt_neu"] == 2   # a + c, kaputt zaehlt nicht
    je_postfach = {p["postfach"]: p for p in st["postfaecher"]}
    assert je_postfach["Postfach A"]["ok"] is True
    assert je_postfach["Postfach kaputt"]["ok"] is False
    assert "Login fehlgeschlagen" in je_postfach["Postfach kaputt"]["fehler"]
    assert je_postfach["Postfach C"]["ok"] is True
    assert "Postfach kaputt" in st["fehler"]


def test_aus_konfiguration_nutzt_store_postfaecher(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_IMAP_USER", raising=False)
    monkeypatch.delenv("RECHNUNG_IMAP_PASSWORD", raising=False)
    store = Store(tmp_path / "data")
    store.save_postfach(Postfach(id="nord", mandant_id="nord", user="nord@example.de"))
    a = aus_konfiguration(tmp_path, store=store)
    assert a is not None
    assert a.mailboxes and a.mailboxes[0][2] == "nord"


def test_aus_konfiguration_ohne_store_bleibt_einzelpostfach_verhalten(tmp_path: Path, monkeypatch):
    """Ohne ``store`` (z.B. ein Aufrufer, der die Bibliothek noch nicht
    kennt) bleibt das Verhalten exakt wie vor der Mehrfach-Postfach-
    Erweiterung -- ein einzelnes ENV-Postfach ueber den alten ``Abholer``-
    Pfad ohne ``mailboxes``. ``ImapConfig``-Felder lesen ihre Defaults beim
    Modul-Import aus den ENV-Variablen (siehe config.py) -- hier direkt eine
    konfigurierte Instanz vorgeben, statt fragil ENV + Modul-Reload zu
    mischen."""
    from rechnung import config as config_mod

    monkeypatch.setattr(config_mod, "ImapConfig",
                        lambda: ImapConfig(host="x", user="u", password="p"))
    a = aus_konfiguration(tmp_path)
    assert a is not None and a.mailboxes is None
