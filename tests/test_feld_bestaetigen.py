"""Knopf "Wert stimmt" (Aaron 05.11.2026): ein Feld ausdruecklich bestaetigen.
Das ist KEINE Korrektur -- Wert und Quelle bleiben, es zaehlt als "richtig
erkannt" im Ereignisprotokoll, im Testsatz und im Lernbericht."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rechnung import eventlog, lernbericht, pipeline, testsatz
from rechnung.store import Store
from rechnung.web import server as server_mod

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def _client(tmp_path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "eingang"
    samples.mkdir()
    inv = make_invoice(samples / "a.pdf", rechnungsnr="RE-9")
    return TestClient(server_mod.create_app(store, samples)), store, inv


def _bestaetigen(client, feld="rechnungsnummer", wert="RE-9", quelle="llm"):
    return client.post("/api/feld-bestaetigen", json={
        "file": "a.pdf", "field": feld, "value": wert, "quelle_vorher": quelle})


def test_bestaetigen_ist_keine_korrektur(tmp_path):
    client, store, inv = _client(tmp_path)
    sha = pipeline.process(inv, store, use_llm=False).sha256

    r = _bestaetigen(client)

    assert r.status_code == 200 and r.json()["programm_richtig"] is True
    assert store.corrections(sha) == {}                       # nichts als Korrektur gespeichert
    assert store.confirmed_fields(sha) == {"rechnungsnummer": "RE-9"}
    typen = [e["type"] for e in store.events()]
    assert eventlog.TYPE_FELD_BESTAETIGT in typen and eventlog.TYPE_CORRECTION not in typen
    assert eventlog.report(store)["korrekturen"] == []        # Fehlerbericht bleibt leer


def test_bestaetigen_eigener_eingabe_zaehlt_nicht_als_programmerfolg(tmp_path):
    client, store, _ = _client(tmp_path)
    r = _bestaetigen(client, quelle="manual")
    assert r.json()["programm_richtig"] is False


def test_spaetere_korrektur_hebt_bestaetigung_auf(tmp_path):
    client, store, inv = _client(tmp_path)
    sha = pipeline.process(inv, store, use_llm=False).sha256
    _bestaetigen(client)

    client.post("/api/correction", json={"file": "a.pdf", "field": "rechnungsnummer",
                                         "value": "RE-10"})

    assert store.confirmed_fields(sha) == {}
    assert store.corrections(sha) == {"rechnungsnummer": "RE-10"}


def test_bestaetigen_gegen_abweichende_korrektur_wird_abgelehnt(tmp_path):
    client, store, inv = _client(tmp_path)
    client.post("/api/correction", json={"file": "a.pdf", "field": "rechnungsnummer",
                                         "value": "RE-10"})
    assert _bestaetigen(client, wert="RE-9").status_code == 409


def test_testsatz_und_lernbericht_zaehlen_bestaetigte_felder(tmp_path):
    client, store, inv = _client(tmp_path)
    sha = pipeline.process(inv, store, use_llm=False).sha256
    for f, v in dict(rechnungsnummer="RE-9", rechnungsdatum="14.02.2026", netto="1000,00").items():
        store.save_correction(sha, f, v, "t")
    res = pipeline.process(inv, store, use_llm=False)
    store.mark_confirmed(sha, "t")

    # ein vom Programm (hier: Regel/Tabelle, nicht manual) gelesenes Feld bestaetigen
    quelle = next((n for n, fr in res.fields.items() if fr.source not in ("manual", "missing")
                   and fr.value not in (None, "")), None)
    assert quelle, "Testdaten brauchen ein maschinell gelesenes Feld"
    store.save_confirmed_field(sha, quelle, str(res.fields[quelle].value), "t")
    testsatz.merken(store, res, "t")

    b = lernbericht.erstellen(store)
    assert b["rechnungen"][0]["bestaetigt_felder"] == [quelle]
    assert b["zusammenfassung"]["ausdruecklich_richtig"] == 1
    assert quelle not in b["rechnungen"][0]["korrigiert"]
