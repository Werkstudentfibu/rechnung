"""Schon gelesene Rechnungen sofort oeffnen (Befund Aaron 13.11.2026: eine
Rechnung, die die Vorverarbeitung im Hintergrund schon gelesen hat -- Zustand
"bereit zur Pruefung" --, wurde beim Anklicken noch einmal komplett gelesen:
"wird gelesen (Texterkennung/KI) ... s").

Ursache: die Pruefansicht (``/api/invoice``) stellte der KI andere Fragen als
die Vorverarbeitung (Vorlagen-Luecken mit anderem Text/anderer Feldliste), und
der KI-Gegencheck aenderte seine Frage nach jeder Korrektur. Beides verfehlte
den KI-Zwischenspeicher auf der Platte -- auf dem Laptop 30-90 s je Aufruf.

Die KI ist hier ein Zaehler (offline, deterministisch); gezaehlt wird jeder
echte Modellaufruf, also alles, was am Zwischenspeicher vorbeigeht. Nur
synthetische Rechnungen."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rechnung import learn, semantic
from rechnung import pdf as pdfmod
from rechnung import vorbereitung as vb
from rechnung.config import LlmConfig
from rechnung.identify import supplier_from_doc
from rechnung.store import Store
from rechnung.web.server import create_app

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

# Was die (gemockte) KI auf der Testrechnung liest -- alles im Text belegt
_KI_WERTE = {"rechnungsnummer": "RE-2026-00001", "rechnungsdatum": "2026-02-14",
             "netto": 1000.0, "ust_betrag": 190.0, "ust_satz": 19.0, "brutto": 1190.0}


@pytest.fixture
def ki(monkeypatch):
    """Wie im Einsatz: Vorverarbeitung an, Modus gruendlich, KI an -- aber jeder
    Modellaufruf landet in der zurueckgegebenen Liste statt bei Ollama."""
    monkeypatch.setenv("RECHNUNG_VORVERARBEITUNG", "1")
    monkeypatch.setenv("RECHNUNG_GRUENDLICH", "1")
    cfg = LlmConfig(enabled=True, base_url="http://127.0.0.1:11434", model="test-model")
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: cfg)
    monkeypatch.setattr(semantic, "available", lambda: True)
    aufrufe: list[str] = []

    def modell(cfg, prompt, schema=None):
        aufrufe.append(prompt)
        return json.dumps(_KI_WERTE)

    monkeypatch.setattr(semantic, "_call_ollama", modell)
    return aufrufe


def _mit_vorlage(tmp_path: Path):
    """Bekannter Lieferant mit junger Vorlage (Gegencheck aktiv), in der das
    Rechnungsdatum nicht angelernt ist -- eine Luecke, die die KI fuellt."""
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    src = make_invoice(samples / "a.pdf", rechnungsnr="RE-2026-00001")
    doc = pdfmod.load(src)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    tpl, _ = learn.learn_template(doc, "muster", {
        "rechnungsnummer": "RE-2026-00001", "netto": 1000.0, "ust_betrag": 190.0,
        "ust_satz": 19.0, "brutto": 1190.0})
    tpl.confirmations = 1
    store.save_template(tpl)
    return store, samples


def _ohne_vorlage(tmp_path: Path):
    """Unbekannter Lieferant: die Liste legt einen Entwurf an, keine Vorlage."""
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    make_invoice(samples / "a.pdf", rechnungsnr="RE-2026-00001")
    return store, samples


def _vorbereitet(store: Store, samples: Path):
    app = create_app(store, samples)
    assert app.state.vorverarbeiter.warte_bis_leer(60)
    assert app.state.liste.warte_bis_leer(60)
    sha = vb.datei_sha(samples / "a.pdf")
    assert (vb.marker(store.root, sha) or {}).get("ok"), "nicht vorbereitet"
    return app


def _oeffnen(c: TestClient) -> dict:
    r = c.get("/api/invoice", params={"file": "a.pdf"})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("setup", [_mit_vorlage, _ohne_vorlage], ids=["mit_vorlage", "ohne_vorlage"])
def test_vorbereitete_rechnung_oeffnet_ohne_erneutes_lesen(tmp_path, ki, setup):
    store, samples = setup(tmp_path)
    app = _vorbereitet(store, samples)
    assert ki, "die Vorverarbeitung hat die KI gefragt"
    gelesen = len(ki)
    semantic._resp_cache.clear()        # nur die Platte zaehlt (wie nach einem Neustart)

    c = TestClient(app)
    c.get("/api/scan")                  # Liste laden, dann anklicken
    d = _oeffnen(c)
    assert len(ki) == gelesen, ("beim Anklicken wurde die schon gelesene Rechnung "
                                f"noch einmal von der KI gelesen ({len(ki) - gelesen} Aufruf(e))")
    # ... und gezeigt wird trotzdem, was die KI im Hintergrund gelesen hat
    assert d["current"]["rechnungsdatum"]["value"] == "2026-02-14"
    assert d["current"]["rechnungsdatum"]["source"] == "llm"

    # Neustart des Programms: Merkzettel und Zwischenspeicher liegen auf der Platte
    semantic._resp_cache.clear()
    app2 = create_app(store, samples)
    assert app2.state.vorverarbeiter.warte_bis_leer(60)
    assert app2.state.vorverarbeiter.status["fertig"] == 0
    d2 = _oeffnen(TestClient(app2))
    assert len(ki) == gelesen, "nach dem Neustart wurde die Rechnung noch einmal gelesen"
    assert d2["current"]["rechnungsdatum"]["value"] == "2026-02-14"


def test_nach_korrektur_und_bestaetigung_aktuell_ohne_erneutes_lesen(tmp_path, ki):
    store, samples = _mit_vorlage(tmp_path)
    app = _vorbereitet(store, samples)
    c = TestClient(app)
    d = _oeffnen(c)
    gelesen = len(ki)

    # Mensch korrigiert den KI-Vorschlag: die Ansicht zeigt sofort den neuen Wert ...
    r = c.post("/api/correction", json={"file": "a.pdf", "field": "rechnungsdatum",
                                        "value": "2026-02-15", "template_id": d["template_id"]})
    assert r.status_code == 200, r.text
    d = _oeffnen(c)
    assert d["current"]["rechnungsdatum"]["value"] == "2026-02-15"
    assert d["current"]["rechnungsdatum"]["source"] == "manual"
    # ... ohne die Rechnung dafuer noch einmal lesen zu lassen
    assert len(ki) == gelesen, "nach der Korrektur wurde die Rechnung noch einmal gelesen"

    # "Wert stimmt" fuer ein anderes Feld: ebenso
    r = c.post("/api/feld-bestaetigen", json={"file": "a.pdf", "field": "netto",
                                              "value": "1000.0", "template_id": d["template_id"]})
    assert r.status_code == 200, r.text
    d = _oeffnen(c)
    assert d["current"]["netto"]["value"] == 1000.0
    assert d["current"]["rechnungsdatum"]["value"] == "2026-02-15"
    assert len(ki) == gelesen, "nach dem Bestaetigen wurde die Rechnung noch einmal gelesen"


def test_plausi_zwischenspeicher_haelt_gleichzeitige_abfrage_aus(tmp_path, monkeypatch):
    """Vorverarbeitung und Listen-Zwischenspeicher rechnen gleichzeitig. Fragt
    ein zweiter Thread, waehrend der erste die bestaetigten Rechnungen neu
    einliest, gab es vorher KeyError 'recs' -- die Vorbereitung endete als
    "Fehler", die Rechnung galt nicht als gelesen."""
    from rechnung import plausi, testsatz

    store = Store(tmp_path / "data")
    plausi._cache.clear()
    echt = testsatz.bestaetigte
    zweiter: list = []

    def bestaetigte(s):
        if not zweiter:
            zweiter.append(None)
            zweiter[0] = plausi._alle(s)        # "anderer Thread" mitten im Einlesen
        return echt(s)

    monkeypatch.setattr(testsatz, "bestaetigte", bestaetigte)
    assert plausi._alle(store) == []
    assert zweiter == [[]]


def test_gegencheck_fragt_nach_korrektur_gleich_prueft_korrektur_aber_nicht(tmp_path, monkeypatch):
    """Der Gegencheck stellt der KI vor und nach einer Korrektur dieselbe Frage
    (Treffer im Zwischenspeicher); ein vom Menschen korrigiertes Feld wird
    trotzdem weiterhin nicht gegen die KI gehalten."""
    from rechnung import pipeline
    from rechnung.template import FieldResult
    from tests.test_gegencheck import _KI_PASSEND, _setup

    store, inv = _setup(tmp_path)
    fragen: list[list[str]] = []

    def fake(text, names, hints, rejected=None):
        if hints:                       # Luecken-Fuellung, nicht der Gegencheck
            return {}
        fragen.append(list(names))
        if rejected is not None and "netto" in names:
            rejected["netto"] = "nicht im Text: 999"
        return {n: FieldResult(n, v, str(v), 0.6, "llm")
                for n, v in dict(_KI_PASSEND, netto=999.0).items() if n in names}

    monkeypatch.setattr(pipeline.semantic, "extract", fake)
    res = pipeline.process(inv, store)
    assert res.gegencheck_abweichungen["netto"]["ki"] == 999.0
    assert "netto" in res.llm_rejected

    store.save_correction(res.sha256, "netto", "200,00", "test")
    res2 = pipeline.process(inv, store)
    assert len(fragen) == 2 and fragen[0] == fragen[1]
    assert "netto" not in res2.gegencheck_abweichungen
    assert "netto" not in res2.llm_rejected
    assert not any("Gegencheck" in g for g in res2.hold_reasons)
