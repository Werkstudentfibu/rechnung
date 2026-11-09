"""rechnung/postfach.py + Store-Methoden (mehrere Postfaecher, Entscheidung
Aaron 15.10.2026). ``test_postfach.py`` (bestehend) deckt den IMAP-Abholer
fuer EIN Postfach ab -- hier geht es um Verwaltung/Mandant-Zuordnung der
Postfach-STAMMDATEN, nicht ums Abholen selbst."""

from __future__ import annotations

from pathlib import Path

from rechnung.postfach import Postfach
from rechnung.store import Store


def test_save_and_load_postfach(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_postfach(Postfach(id="nord", mandant_id="nord", user="rechnung@nord.example"))
    pf = store.postfach("nord")
    assert pf is not None and pf.user == "rechnung@nord.example"
    assert pf.host == "imap.ionos.de" and pf.port == 993


def test_postfach_list_and_delete(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_postfach(Postfach(id="a", user="a@example.de"))
    store.save_postfach(Postfach(id="b", user="b@example.de"))
    assert {p.id for p in store.postfaecher()} == {"a", "b"}
    store.delete_postfach("a")
    assert {p.id for p in store.postfaecher()} == {"b"}


def test_upsert_does_not_duplicate(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_postfach(Postfach(id="a", user="alt@example.de"))
    store.save_postfach(Postfach(id="a", user="neu@example.de"))
    assert len(store.postfaecher()) == 1
    assert store.postfach("a").user == "neu@example.de"


def test_datei_mandant_mapping(tmp_path: Path):
    store = Store(tmp_path / "data")
    assert store.datei_mandant("abc") is None
    store.set_datei_mandant("abc", "nord")
    assert store.datei_mandant("abc") == "nord"


def test_postfach_to_dict_round_trip():
    pf = Postfach(id="x", mandant_id="m", mandant_name="Mandant M", user="u@example.de",
                  host="imap.ionos.de", port=993, source_folder="INBOX", done_folder="Erledigt")
    pf2 = Postfach.from_dict(pf.to_dict())
    assert pf2 == pf
