"""Postfach-Passwoerter: nie im Klartext in den normalen Stammdaten, auf
POSIX klar markierter 0600-Fallback mit Warnung; Windows prueft DPAPI
statt der dort absichtlich nicht vorhandenen Klartext-Fallbackdatei."""

from __future__ import annotations

import json
import stat
import sys

import pytest
from pathlib import Path

from rechnung import secrets
from rechnung.postfach import Postfach
from rechnung.store import Store


def test_password_roundtrip(tmp_path: Path):
    store = Store(tmp_path / "data")
    secrets.set_password(store, "pf1", "geheim123", operator="aaron")
    assert secrets.has_password(store, "pf1")
    assert secrets.get_password(store, "pf1") == "geheim123"


def test_unset_password_is_empty_and_not_has_password(tmp_path: Path):
    store = Store(tmp_path / "data")
    assert secrets.get_password(store, "unbekannt") == ""
    assert not secrets.has_password(store, "unbekannt")


def test_delete_password(tmp_path: Path):
    store = Store(tmp_path / "data")
    secrets.set_password(store, "pf1", "x")
    secrets.delete_password(store, "pf1", operator="aaron")
    assert not secrets.has_password(store, "pf1")


@pytest.mark.skipif(sys.platform == "win32", reason="Windows nutzt DPAPI, keinen POSIX-Klartext-Fallback")
def test_linux_fallback_file_has_0600_permissions(tmp_path: Path):
    store = Store(tmp_path / "data")
    secrets.set_password(store, "pf1", "geheim")
    pfad = store.root / secrets.UNSICHER_DATEI
    assert pfad.exists()
    mode = stat.S_IMODE(pfad.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_linux_fallback_filename_marks_it_as_unsafe(tmp_path: Path):
    """Dateiname ist bewusst als unsicher erkennbar -- kein falsches
    Sicherheitsgefuehl, falls jemand die data/-Struktur durchsieht."""
    assert "unsicher" in secrets.UNSICHER_DATEI


def test_password_never_in_postfach_to_dict(tmp_path: Path):
    """Postfach.to_dict() kennt gar kein Passwortfeld -- strukturell
    unmoeglich, es versehentlich mit auszugeben/zu loggen."""
    store = Store(tmp_path / "data")
    secrets.set_password(store, "pf1", "geheim123")
    p = Postfach(id="pf1", user="rechnung@kunde-a.de")
    store.save_postfach(p)
    d = store.postfach("pf1").to_dict()
    assert "geheim123" not in json.dumps(d)
    assert "passwort" not in d and "password" not in d


def test_password_never_in_audit_log(tmp_path: Path):
    store = Store(tmp_path / "data")
    secrets.set_password(store, "pf1", "ganzGeheimesPasswort!")
    log_text = (store.root / "audit.jsonl").read_text("utf-8")
    assert "ganzGeheimesPasswort!" not in log_text


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI ist nur unter Windows verfuegbar")
def test_windows_dpapi_file_is_encrypted_and_no_plaintext_fallback(tmp_path: Path):
    store = Store(tmp_path / "data")
    password = "NurTest-keinEchtesPasswort-123!"
    secrets.set_password(store, "pf1", password)
    pfad = store.root / secrets.WINDOWS_DATEI
    assert pfad.exists()
    assert not (store.root / secrets.UNSICHER_DATEI).exists()
    daten = json.loads(pfad.read_text("utf-8"))
    assert daten["pf1"] and daten["pf1"] != password
    assert password not in pfad.read_text("utf-8")
    assert secrets.has_password(store, "pf1")
    assert secrets.get_password(store, "pf1") == password
