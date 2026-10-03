"""Tests fuer den semantischen Fallback (semantic.py, SPEC-v1.md §7f).

Kein echtes Ollama noetig: _llm_cfg/_call_ollama/available werden je Test
gezielt ersetzt, damit die Tests offline und deterministisch laufen.
"""

from __future__ import annotations

import json

from rechnung import semantic
from rechnung.config import LlmConfig


def _cfg(**overrides) -> LlmConfig:
    base = dict(enabled=True, base_url="http://127.0.0.1:11434",
                model="test-model", num_ctx=4096, timeout=5, max_confidence=0.6)
    base.update(overrides)
    return LlmConfig(**base)


def test_extract_disabled_returns_empty(monkeypatch):
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg(enabled=False))
    out = semantic.extract("irgendein Text", ["rechnungsnummer"], {})
    assert out == {}


def test_extract_no_fields_returns_empty(monkeypatch):
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: True)
    out = semantic.extract("irgendein Text", [], {})
    assert out == {}


def test_extract_unreachable_returns_empty(monkeypatch):
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: False)
    out = semantic.extract("Rechnungsnummer RE-1 vom 01.02.2026", ["rechnungsnummer"], {})
    assert out == {}


def test_extract_parses_response_and_caps_confidence(monkeypatch):
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg(max_confidence=0.6))
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_call_ollama",
                        lambda cfg, prompt: json.dumps({
                            "rechnungsnummer": "RE-2026-042",
                            "rechnungsdatum": "03.03.2026",
                        }))
    out = semantic.extract(
        "Rechnung Nr. RE-2026-042 vom 03.03.2026",
        ["rechnungsnummer", "rechnungsdatum", "bestellnummer"],
        {},
    )
    assert set(out) == {"rechnungsnummer", "rechnungsdatum"}
    assert out["rechnungsnummer"].value == "RE-2026-042"
    assert out["rechnungsnummer"].source == "llm"
    assert out["rechnungsnummer"].confidence <= 0.6
    assert out["rechnungsdatum"].value == "2026-03-03"


def test_extract_ignores_taxblock_field(monkeypatch):
    """Positionen/USt-Aufstellung sind (noch) nicht Teil des Fallbacks (§7f)."""
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: True)
    called = {}

    def fake_call(cfg, prompt):
        called["prompt"] = prompt
        return json.dumps({"rechnungsnummer": "RE-1"})

    monkeypatch.setattr(semantic, "_call_ollama", fake_call)
    out = semantic.extract("Text", ["rechnungsnummer", "ust_aufstellung"], {})
    assert "ust_aufstellung" not in out
    assert "ust_aufstellung" not in called["prompt"]


def test_extract_drops_uncoercible_values(monkeypatch):
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_call_ollama",
                        lambda cfg, prompt: json.dumps({"netto": "nicht ein Betrag"}))
    out = semantic.extract("Text", ["netto"], {})
    assert out == {}


def test_extract_malformed_json_returns_empty(monkeypatch):
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_call_ollama", lambda cfg, prompt: "kein json")
    out = semantic.extract("Text", ["rechnungsnummer"], {})
    assert out == {}


def test_available_reflects_enabled_flag(monkeypatch):
    semantic._avail_cache = None
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg(enabled=False))
    assert semantic.available() is False


def test_available_true_when_reachable(monkeypatch):
    semantic._avail_cache = None
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg(enabled=True))

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(semantic.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert semantic.available() is True
    semantic._avail_cache = None


def test_head_and_tail_short_text_unchanged():
    text = "kurzer Text"
    assert semantic._head_and_tail(text) == text


def test_head_and_tail_keeps_end_of_long_invoice():
    head = "RECHNUNGSKOPF " * 500       # weit ueber dem Budget
    tail = "Gesamtbetrag brutto: 999,99 EUR"
    long_text = head + tail
    assert len(long_text) > semantic._TEXT_BUDGET
    result = semantic._head_and_tail(long_text)
    assert tail in result
    assert result.startswith("RECHNUNGSKOPF")
    assert len(result) < len(long_text)


def test_extract_finds_field_beyond_old_cutoff(monkeypatch):
    """Regression: vor dem Fix wurde bei langen Rechnungen nur der Anfang
    geschickt -- eine Summenzeile weit hinten kam beim Modell nie an."""
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: True)
    seen_prompts = []

    def fake_call(cfg, prompt):
        seen_prompts.append(prompt)
        # simuliert ein Modell, das den Wert nur zurueckgeben kann, wenn er
        # tatsaechlich im Prompt vorkam
        if "88888,88" in prompt:
            return json.dumps({"brutto": "88888,88"})
        return json.dumps({})

    monkeypatch.setattr(semantic, "_call_ollama", fake_call)
    long_text = ("Positionszeile " * 1000) + "Gesamtbetrag brutto: 88888,88 EUR"
    out = semantic.extract(long_text, ["brutto"], {})
    assert out["brutto"].value == 88888.88


def test_available_false_on_connection_error(monkeypatch):
    semantic._avail_cache = None
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg(enabled=True))

    def _raise(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(semantic.urllib.request, "urlopen", _raise)
    assert semantic.available() is False
    semantic._avail_cache = None
