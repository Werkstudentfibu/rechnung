"""Startklar-Check, Datensicherung, JobRouter-Vorschau (Aaron 11.10.2026)."""

import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from rechnung import sicherung, startklar
from rechnung.web.server import create_app

from test_jobrouter_feedback import _secured_setup  # noqa: F401


def test_startklar_liefert_alle_punkte(tmp_path: Path, monkeypatch):
    for v in ("RECHNUNG_IMAP_USER", "RECHNUNG_BERICHT_AN", "RECHNUNG_SICHERUNG_ZIEL"):
        monkeypatch.delenv(v, raising=False)
    store, samples = _secured_setup(tmp_path)
    punkte = startklar.pruefen(store, samples)
    namen = [p["name"] for p in punkte]
    for n in ("Postfach (IONOS)", "JobRouter", "Texterkennung (Scans)", "KI", "Datensicherung", "Stammdaten"):
        assert n in namen
    assert all(p["status"] in ("ok", "warn", "fehler", "aus") for p in punkte)
    c = TestClient(create_app(store, samples))
    assert c.get("/api/startklar").status_code == 200


def test_sicherung_ohne_caches_und_mit_rotation(tmp_path: Path, monkeypatch):
    store, samples = _secured_setup(tmp_path)
    (store.root / "ocr").mkdir(exist_ok=True); (store.root / "ocr" / "x.pdf").write_bytes(b"x")
    (store.root / "ki-cache").mkdir(exist_ok=True); (store.root / "ki-cache" / "k.json").write_text("{}")
    ziel = tmp_path / "netz"
    monkeypatch.setenv("RECHNUNG_SICHERUNG_ZIEL", str(ziel))
    monkeypatch.setenv("RECHNUNG_SICHERUNG_ANZAHL", "2")
    for alt in ("20260101", "20260102", "20260103"):
        ziel.mkdir(exist_ok=True); (ziel / f"rechnung-daten-{alt}.zip").write_bytes(b"")
    assert sicherung.faellig()
    r = sicherung.sichern(store.root)
    assert r["ok"]
    namen = zipfile.ZipFile(r["datei"]).namelist()
    assert "suppliers.json" in namen and not any(n.startswith(("ocr/", "ki-cache/")) for n in namen)
    assert len(list(ziel.glob("rechnung-daten-*.zip"))) == 2
    assert not sicherung.faellig()


def test_jobrouter_vorschau(tmp_path: Path, monkeypatch):
    store, samples = _secured_setup(tmp_path)
    monkeypatch.setenv("RECHNUNG_DATA", str(store.root))   # wie auf dem Laptop
    (store.root / "jobrouter-mapping.json").write_text(json.dumps({"rechnungsnummer": "InvoiceNo"}))
    c = TestClient(create_app(store, samples))
    d = c.get("/api/jobrouter-vorschau?file=a.pdf").json()
    rn = [f for f in d["felder"] if f["feld"] == "rechnungsnummer"][0]
    assert rn["jobrouter"] == "InvoiceNo" and rn["zugeordnet"] and rn["wert"] == "RE-2026-00001"
    assert d["nutzlast"]["lieferantStammdaten"]["name"] == "Muster Handels GmbH"
    assert (samples / "a.pdf").exists()            # Vorschau sendet/verschiebt nichts
