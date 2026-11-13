"""Befund Aaron 13.11.2026: Feld bestaetigt (✓) oder korrigiert, danach ein
ANDERES Feld derselben Rechnung gespeichert -> das erste Feld sprang wieder
auf rot bzw. verlor die Bestaetigung. Ursache: ``/api/invoice`` lieferte die
Bestaetigungen nicht mit (``STATE.bestaetigt`` wurde bei jedem Neuladen
geleert), und beim Neuzeichnen faerbten die alten roten Pruefungen auch
korrigierte Felder wieder rot."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rechnung.pipeline import FieldResult
from rechnung.store import Store
from rechnung.validate import rechen_pruefungen
from rechnung.web import server as server_mod

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

INDEX = Path(server_mod.__file__).parent / "static" / "index.html"


def _client(tmp_path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "eingang"
    samples.mkdir()
    make_invoice(samples / "a.pdf", rechnungsnr="302-1234567-1234567", netto=5.80)
    return TestClient(server_mod.create_app(store, samples)), store


def _laden(client):
    r = client.get("/api/invoice?file=a.pdf")
    assert r.status_code == 200
    return r.json()


def test_bestaetigung_und_korrektur_ueberstehen_speichern_eines_anderen_feldes(tmp_path):
    client, _ = _client(tmp_path)
    # 1. Rechnungsnummer korrigieren (Bestellnummer gelesen -> echte Nummer)
    client.post("/api/correction", json={"file": "a.pdf", "field": "rechnungsnummer",
                                         "value": "DE43MCFWABEI"})
    # 2. Nettobetrag per Haken bestaetigen
    assert client.post("/api/feld-bestaetigen", json={
        "file": "a.pdf", "field": "netto", "value": "5,80"}).status_code == 200
    # 3. ein ANDERES Feld speichern, danach neu laden (wie die Pruefmaske)
    client.post("/api/correction", json={"file": "a.pdf", "field": "rechnungsdatum",
                                         "value": "14.02.2026"})
    d = _laden(client)

    assert d["current"]["rechnungsnummer"]["value"] == "DE43MCFWABEI"
    assert d["current"]["rechnungsnummer"]["source"] == "manual"
    assert "netto" in d["bestaetigte_felder"]
    # erneutes Bestaetigen bleibt moeglich
    assert client.post("/api/feld-bestaetigen", json={
        "file": "a.pdf", "field": "rechnungsnummer", "value": "DE43MCFWABEI"}).status_code == 200
    assert {"netto", "rechnungsnummer"} <= set(_laden(client)["bestaetigte_felder"])


def test_pruefmaske_uebernimmt_bestaetigungen_und_faerbt_gepruefte_felder_nicht_rot():
    html = INDEX.read_text(encoding="utf-8")
    assert "d.bestaetigte_felder" in html                      # nicht mehr bei jedem Laden leer
    assert "bestaetigt: {}," not in html
    assert "(da && cur.source === 'manual')" in html            # Korrektur gilt als geprueft
    assert "if (geprueft && da) st = 'gut';" in html


def test_leerer_steuersatz_block_macht_keine_pruefung_rot():
    """Amazon: 5,80 + 1,10 = 6,90 (19 %), daneben ein leerer 7-%-Block."""
    rows = [{"satz": 19, "netto": 5.80, "ust": 1.10}, {"satz": 7, "netto": None, "ust": None}]
    f = {n: FieldResult(n, v, str(v), 1.0, "template") for n, v in
         dict(netto=5.80, ust_betrag=1.10, brutto=6.90, ust_satz=19, waehrung="EUR",
              ust_aufstellung=rows).items()}
    r = rechen_pruefungen(f, [{"beschreibung": "x", "menge": 1, "einzelpreis": 5.80,
                               "gesamt": 5.80, "ust_satz": 19}])
    assert [c for c in r["checks"] if c["ok"] is False] == []
