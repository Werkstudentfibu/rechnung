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


def test_fortschritt_laufend_sichtbar(tmp_path: Path, monkeypatch):
    """Aaron 05.11.2026: erste lange Abholung ueber ~50 Postfaecher -- die
    Uebersicht zeigt schon waehrend des Laufs, was erledigt ist."""
    mailboxes = [(f"Postfach {x}", ImapConfig(host="x", user=f"{x}@example.de", password="p"), x)
                 for x in "abc"]
    a = Abholer(tmp_path, 5, mailboxes=mailboxes)
    a.status["postfaecher"] = [{"postfach": "Postfach c", "adresse": "c@example.de",
                                "ok": True, "fehler": "", "neu": 0, "mails": 3}]
    gesehen = []

    def fake_fetch(cfg=None, **_k):
        gesehen.append((a.status["fortschritt"], a.status["aktuelles_postfach"],
                        [p["adresse"] for p in a.status["postfaecher"]]))
        return FetchResult([], 0, False)

    import rechnung.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "fetch", fake_fetch)
    st = a.einmal()
    assert gesehen[0] == ("0/3", "Postfach a", ["c@example.de"])   # altes Ergebnis von c bleibt
    assert gesehen[1] == ("1/3", "Postfach b", ["a@example.de", "c@example.de"])
    assert gesehen[2][:2] == ("2/3", "Postfach c")
    assert st["fortschritt"] == "" and len(st["postfaecher"]) == 3
