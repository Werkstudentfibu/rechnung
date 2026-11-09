"""Zwischenspeicher des Excel-Imports: Listen/Antworten ueberleben das
Schliessen des Programms, Passwoerter nie im Klartext in der Entwurfsdatei."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import secrets, stammdaten_entwurf as se
from rechnung.store import Store

PW = "NurTest-keinEchtesPasswort-123!"


def _slots(passwort: str = PW, mapping_pw: str = "passwort") -> dict:
    return {"postfaecher": {"datei": "pf.xlsx", "warnung": "", "bestaetigt": True, "sheets": {
        "Blatt|1": {
            "headers": ["MDT", "E-Mail", "Passwort"],
            "rows": [["001", "a@example.com", passwort], ["002", "b@example.com", ""]],
            "mapping": {"MDT": "mandant_verweis", "E-Mail": "email", "Passwort": mapping_pw},
            "art": "postfach", "art_vorschlag": "postfach", "gelernt": False,
        }}}}


def _daten(**kw) -> dict:
    return {"slots": _slots(**kw), "antworten": {"Blatt|1:2|email": {"antwort": "so übernehmen"}},
            "zielfelder": ["email", "passwort"], "nurOffen": False}


def test_roundtrip_restores_everything_including_password(tmp_path: Path):
    store = Store(tmp_path / "data")
    se.speichern(store, _daten())
    e = se.laden(store)
    assert e is not None
    assert e["slots"] == _slots()
    assert e["antworten"] == _daten()["antworten"]
    assert e["zielfelder"] == ["email", "passwort"]
    assert e["nurOffen"] is False
    assert e["passwoerter_fehlen"] == 0


def test_password_not_in_entwurf_file(tmp_path: Path):
    store = Store(tmp_path / "data")
    se.speichern(store, _daten())
    text = (store.root / se.ENTWURF_DATEI).read_text("utf-8")
    assert PW not in text
    assert "a@example.com" in text      # normale Daten sind dagegen im Entwurf


def test_password_column_masked_even_if_mapping_changed_to_ignore(tmp_path: Path):
    store = Store(tmp_path / "data")
    se.speichern(store, _daten(mapping_pw="ignorieren"))
    assert PW not in (store.root / se.ENTWURF_DATEI).read_text("utf-8")
    assert se.laden(store)["slots"]["postfaecher"]["sheets"]["Blatt|1"]["rows"][0][2] == PW


def test_lost_secret_is_reported_not_hidden(tmp_path: Path):
    store = Store(tmp_path / "data")
    se.speichern(store, _daten())
    secrets.delete_password(store, se.GEHEIMNIS_ID)
    e = se.laden(store)
    assert e["passwoerter_fehlen"] == 1
    assert e["slots"]["postfaecher"]["sheets"]["Blatt|1"]["rows"][0][2] == ""


def test_unchanged_password_is_not_rewritten_on_every_autosave(tmp_path: Path):
    store = Store(tmp_path / "data")
    for _ in range(3):
        se.speichern(store, _daten())
    gesetzt = [a for a in store.audit_log() if a.get("action") == "postfach_passwort_gesetzt"]
    assert len(gesetzt) == 1


def test_loeschen_removes_file_and_secret(tmp_path: Path):
    store = Store(tmp_path / "data")
    se.speichern(store, _daten())
    se.loeschen(store)
    assert se.laden(store) is None
    assert not (store.root / se.ENTWURF_DATEI).exists()
    assert not secrets.has_password(store, se.GEHEIMNIS_ID)
    se.loeschen(store)   # zweimal ist kein Fehler


def test_saving_without_lists_deletes_old_entwurf(tmp_path: Path):
    store = Store(tmp_path / "data")
    se.speichern(store, _daten())
    se.speichern(store, {"slots": {}, "antworten": {}})
    assert se.laden(store) is None


def test_invalid_input_is_rejected(tmp_path: Path):
    store = Store(tmp_path / "data")
    with pytest.raises(ValueError):
        se.speichern(store, {"slots": {"x": {"sheets": {"B": {"headers": "kaputt", "rows": []}}}}})
    with pytest.raises(ValueError):
        se.speichern(store, {"slots": "kaputt"})
    assert se.laden(store) is None


def test_corrupt_entwurf_file_is_ignored(tmp_path: Path):
    store = Store(tmp_path / "data")
    (store.root).mkdir(parents=True, exist_ok=True)
    (store.root / se.ENTWURF_DATEI).write_text("{kaputt", "utf-8")
    assert se.laden(store) is None
