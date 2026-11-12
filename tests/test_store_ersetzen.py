"""Atomares Schreiben: ist die Zieldatei kurz gesperrt (Windows, anderer
Prozess liest gerade), wird erneut versucht statt abzubrechen
(Testrunde 12.11.2026)."""

from pathlib import Path

import pytest

from rechnung import store as store_mod


def test_gesperrte_datei_wird_nach_kurzer_pause_ersetzt(tmp_path, monkeypatch):
    ziel = tmp_path / "suppliers.json"
    ziel.write_text("alt", encoding="utf-8")
    echt = Path.replace
    fehlversuche = {"n": 0}

    def gesperrt(self, target):
        if Path(target) == ziel and fehlversuche["n"] < 3:
            fehlversuche["n"] += 1
            raise PermissionError(5, "Zugriff verweigert")
        return echt(self, target)

    monkeypatch.setattr(Path, "replace", gesperrt)
    monkeypatch.setattr(store_mod.time, "sleep", lambda s: None)
    store_mod._atomic_write(ziel, "neu")
    assert ziel.read_text(encoding="utf-8") == "neu"
    assert fehlversuche["n"] == 3


def test_dauerhaft_gesperrt_meldet_den_fehler(tmp_path, monkeypatch):
    ziel = tmp_path / "suppliers.json"
    ziel.write_text("alt", encoding="utf-8")
    echt = Path.replace

    def immer_gesperrt(self, target):
        if Path(target) == ziel:
            raise PermissionError(5, "Zugriff verweigert")
        return echt(self, target)

    monkeypatch.setattr(Path, "replace", immer_gesperrt)
    monkeypatch.setattr(store_mod.time, "sleep", lambda s: None)
    with pytest.raises(PermissionError):
        store_mod._atomic_write(ziel, "neu")
