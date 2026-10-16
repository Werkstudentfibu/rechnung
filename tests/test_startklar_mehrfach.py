"""Startklar-Check zeigt je konfiguriertem Postfach eine eigene Zeile
(Entscheidung Aaron 15.10.2026) -- ein kaputtes Postfach darf im Ueberblick
nicht in einer Sammelzeile untergehen."""

from __future__ import annotations

from pathlib import Path

from rechnung import startklar
from rechnung.postfach import Postfach
from rechnung.store import Store


def test_je_postfach_eine_zeile(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_IMAP_USER", raising=False)
    store = Store(tmp_path / "data")
    store.save_postfach(Postfach(id="nord", mandant_name="Nord", user="n@example.de",
                                 host="imap.invalid"))
    store.save_postfach(Postfach(id="sued", mandant_name="Süd", user="s@example.de",
                                 host="imap.invalid"))
    punkte = startklar.pruefen(store, tmp_path / "samples")
    namen = [p["name"] for p in punkte if p["name"].startswith("Postfach:")]
    assert len(namen) == 2
    assert any("Nord" in n for n in namen)
    assert any("Süd" in n for n in namen)
    # Beide nicht erreichbar (host ungueltig) -- trotzdem je eine eigene Zeile
    assert all(p["status"] == "fehler" for p in punkte if p["name"].startswith("Postfach:"))
