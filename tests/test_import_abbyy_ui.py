"""Abbyy-Listen ueber die Oberflaeche einlesen (Aaron 11.10.2026)."""

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from rechnung.store import Store
from rechnung.web.server import create_app

BU = "Id;Name;VATID;Street;ZIP;City;CountryCode\n019;AMERON Hotelgesellschaft HH mbH;DE192954984;Am Sandtorkai 4;20457;Hamburg;DE\n"
VE = ("Id;BusinessUnitId;Name;VATID;Street;ZIP;City;CountryCode;IBAN\n"
      "70368;019;Andronaco GmbH & Co. KG;DE257545868;Halskestraße 48;22113;Hamburg;DE;DE13100100100794615103\n"
      "1;999;Ohne Bereich GmbH;;;;;;\n")


def _b64(t: str, enc="utf-8") -> str:
    return base64.b64encode(t.encode(enc)).decode()


def test_einlesen_ueber_oberflaeche(tmp_path: Path):
    store = Store(tmp_path / "data")
    c = TestClient(create_app(store, tmp_path))
    r = c.post("/api/import-abbyy", json={"art": "geschaeftsbereiche", "inhalt": _b64(BU)})
    assert r.status_code == 200 and r.json()["anzahl"] == 1
    r = c.post("/api/import-abbyy", json={"art": "lieferanten", "inhalt": _b64(VE, "cp1252")})
    assert r.status_code == 200 and r.json()["anzahl"] == 2 and r.json()["fehlend"] == ["999"]
    sup = store.supplier("70368@019")
    assert sup.name == "Andronaco GmbH & Co. KG" and sup.addresses == ["019"]
    assert store.address("019").name.startswith("AMERON")


def test_falsche_art_und_leere_datei(tmp_path: Path):
    c = TestClient(create_app(Store(tmp_path / "data"), tmp_path))
    assert c.post("/api/import-abbyy", json={"art": "x", "inhalt": _b64(BU)}).status_code == 400
    assert c.post("/api/import-abbyy", json={"art": "lieferanten", "inhalt": ""}).status_code == 400
