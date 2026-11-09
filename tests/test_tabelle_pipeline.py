"""Baustein 2 in der Pipeline verdrahtet (Aaron 12.10.2026, Schwerpunkt
Positionen): findet ``tabelle.py`` die Positionen ohne KI, fragt die KI gar
nicht erst danach (kleinerer Prompt) -- und die deterministisch gefundene
Tabelle gewinnt gegen einen KI-Vorschlag."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnung import pipeline, semantic
from rechnung.config import LlmConfig
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

GOOD = {
    "rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "2026-02-14",
    "netto": "1.000,00", "ust_betrag": 190, "ust_satz": "19", "brutto": "1190.00",
}


def _cfg(**overrides) -> LlmConfig:
    base = dict(enabled=True, base_url="http://127.0.0.1:11434", model="test-model",
               num_ctx=4096, timeout=5, max_confidence=0.6, line_items=True)
    base.update(overrides)
    return LlmConfig(**base)


def _mock(monkeypatch, answer):
    calls: list[str] = []

    def fake_call(c, prompt, schema=None):
        calls.append(prompt)
        return json.dumps(answer)

    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_call_ollama", fake_call)
    return calls


def test_gefundene_tabelle_spart_ki_anfrage_nach_positionen(tmp_path, monkeypatch):
    calls = _mock(monkeypatch, GOOD)  # Antwort OHNE "positionen"
    p = make_invoice(tmp_path / "a.pdf", netto=1000.0, satz=19)
    store = Store(tmp_path / "data")

    res = pipeline.process(p, store, use_llm=True)

    # Positionen kommen aus der Tabelle, nicht aus der KI
    assert res.line_items
    assert all(it.get("_quelle") == "tabelle" for it in res.line_items)
    assert round(sum(it["gesamt_netto"] for it in res.line_items), 2) == 1000.0
    # der grosse Prompt hat gar nicht erst nach Positionen gefragt
    assert all("positionen" not in c for c in calls)


def test_tabelle_gewinnt_gegen_ki_vorschlag(tmp_path, monkeypatch):
    """Selbst wenn die KI (z.B. weil ``skip_items`` nicht gegriffen hat)
    eigene Positionen vorschlaegt, gewinnt die deterministisch gefundene
    Tabelle -- Beleg-Pflicht/Kein-Raten-Prinzip."""
    answer = dict(GOOD, positionen=[
        {"beschreibung": "Erfunden", "menge": 1, "netto": "999,00", "ust_satz": 19},
    ])
    calls = _mock(monkeypatch, answer)
    p = make_invoice(tmp_path / "b.pdf", netto=1000.0, satz=19)
    store = Store(tmp_path / "data")

    res = pipeline.process(p, store, use_llm=True)

    assert all(it.get("_quelle") == "tabelle" for it in res.line_items)
    assert round(sum(it["gesamt_netto"] for it in res.line_items), 2) == 1000.0
    assert not any("Erfunden" in str(it.get("beschreibung")) for it in res.line_items)


def test_ohne_tabelle_fragt_ki_wie_bisher(tmp_path, monkeypatch):
    """Das Alt-Format ohne echte Kopfzeile (``make_multipage_invoice``) hat
    keine von Baustein 2 erkennbare Tabelle -- die KI wird weiterhin nach
    Positionen gefragt (kein Rueckschritt)."""
    from rechnung.synth import make_multipage_invoice

    answer = dict(GOOD, positionen=[
        {"beschreibung": "Artikel 0001", "menge": 1, "netto": "11,25", "ust_satz": 19},
    ])
    calls = _mock(monkeypatch, answer)
    soll = make_multipage_invoice(tmp_path / "c.pdf", seiten=2, positionen_je_seite=3)
    store = Store(tmp_path / "data")

    pipeline.process(soll["path"], store, use_llm=True)

    assert any("positionen" in c for c in calls)


def test_variante_b_fragt_positionen_separat(tmp_path, monkeypatch):
    """Variante B (Baustein 3): findet ``tabelle.py`` den Tabellenausschnitt
    (auch wenn die volle Summenpruefung scheitert), geht die Positions-Frage
    als EIGENER kleiner Prompt raus -- der grosse Prompt fragt dann nicht
    mehr danach."""
    answer_gross = dict(GOOD)
    answer_positionen = {"positionen": [
        {"beschreibung": "Ersatzteile Serie X200", "menge": 1, "einzelpreis": "550,00",
         "netto": "550,00", "ust_satz": 19},
        {"beschreibung": "Montagezubehoer", "menge": 1, "einzelpreis": "300,00",
         "netto": "300,00", "ust_satz": 19},
        {"beschreibung": "Versand- und Verpackungskosten", "menge": 1, "einzelpreis": "150,00",
         "netto": "150,00", "ust_satz": 19},
    ]}
    calls: list[str] = []

    def fake_call(c, prompt, schema=None):
        calls.append(prompt)
        if "Tabellenausschnitt" in prompt:
            return json.dumps(answer_positionen)
        return json.dumps(answer_gross)

    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_call_ollama", fake_call)

    # Baustein 2 selbst muss hier scheitern (sonst fragt niemand die KI) --
    # ein falscher netto-Hinweis plus eine nicht pruefbare Zeile (keine
    # Menge/Einzelpreis) reicht dafuer nicht, also eine Position mit
    # widerspruechlicher Zeilen-Mathematik erzwingen.
    from rechnung import tabelle as tabelle_mod
    monkeypatch.setattr(tabelle_mod, "extract", lambda *a, **k: None)

    p = make_invoice(tmp_path / "d.pdf", netto=1000.0, satz=19)
    store = Store(tmp_path / "data")

    res = pipeline.process(p, store, use_llm=True, variante="B")

    gross_prompts = [c for c in calls if "Tabellenausschnitt" not in c]
    assert all("positionen" not in c for c in gross_prompts)
    assert any("Tabellenausschnitt" in c for c in calls)
    assert round(sum(it["gesamt_netto"] for it in res.line_items), 2) == 1000.0
