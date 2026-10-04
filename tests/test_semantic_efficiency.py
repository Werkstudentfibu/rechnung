"""Effizienz-Bausteine 1+2 fuer den semantischen Fallback (semantic.py):
gezielte Textausschnitte statt Volltext (Baustein 1) und ein echtes
JSON-Schema im Format-Feld statt pauschal "json" (Baustein 2).

Kein echtes Ollama noetig: wie in test_semantic.py werden Backend-Aufrufe
je Test ersetzt (offline, deterministisch)."""

from __future__ import annotations

import json

import pytest

from rechnung import semantic
from rechnung.config import LlmConfig


def _cfg(**overrides) -> LlmConfig:
    base = dict(enabled=True, base_url="http://127.0.0.1:11434",
                model="test-model", num_ctx=4096, timeout=5, max_confidence=0.6)
    base.update(overrides)
    return LlmConfig(**base)


# ---- _targeted_excerpt -------------------------------------------------
def test_targeted_excerpt_short_text_unchanged():
    text = "Rechnungsnummer RE-1 vom 01.02.2026, Gesamtbetrag 100,00 EUR"
    assert semantic._targeted_excerpt(text, ["rechnungsnummer"]) == text


def test_targeted_excerpt_keeps_only_keyword_window_in_long_text():
    head = "Musterfirma GmbH Musterstrasse 1 12345 Musterstadt " * 3   # ~18 Token
    filler = "Allgemeine Geschaeftsbedingungen gelten wie vereinbart. " * 80  # ~400 Token
    marker = "GEHEIMNIS-MITTE-DARF-NICHT-VORKOMMEN"
    field_line = "Rechnungsnummer: RE-2026-00099"
    tail = "Gesamtbetrag brutto: 500,00 EUR Zahlbar bis 01.03.2026 " * 3
    # marker liegt weit vom Kopf (> Kopf-Token-Fenster) UND weit von field_line
    # (> Wort-Fenster) entfernt -- nur field_line soll per Schluesselwort in
    # den Ausschnitt gelangen, marker nicht (weder Kopf/Fuss noch Fenster).
    text = f"{head} {filler} {marker} {filler} {field_line} {filler} {tail}"
    excerpt = semantic._targeted_excerpt(text, ["rechnungsnummer"], budget=2000)
    assert "RE-2026-00099" in excerpt
    assert marker not in excerpt
    assert len(excerpt) < len(text)


def test_targeted_excerpt_falls_back_to_head_and_tail_without_keywords():
    long_text = "Positionszeile " * 2000
    excerpt = semantic._targeted_excerpt(long_text, ["kostenstelle"], budget=100)
    # "kostenstelle" kommt im Text nicht vor -> Kopf+Fuss-Rueckfall, nicht leer
    assert excerpt
    assert len(excerpt) <= semantic._TEXT_BUDGET + 50


def test_targeted_excerpt_falls_back_when_dense_matches_exceed_budget():
    # Jedes Wort ist ein Treffer -> Fenster ueberlappen komplett -> Budget
    # garantiert ueberschritten -> Rueckfall auf den bewaehrten Zeichenschnitt.
    text = "netto " * 500
    excerpt = semantic._targeted_excerpt(text, ["netto"], budget=50)
    assert len(excerpt) <= semantic._TEXT_BUDGET + 50


# ---- _dynamic_num_ctx ---------------------------------------------------
def test_dynamic_num_ctx_small_for_short_prompt():
    assert semantic._dynamic_num_ctx("kurzer prompt", cap=4096) <= 1024


def test_dynamic_num_ctx_capped_by_config():
    long_prompt = "x" * 100_000
    assert semantic._dynamic_num_ctx(long_prompt, cap=4096) == 4096


def test_dynamic_num_ctx_never_exceeds_cap_even_when_small():
    assert semantic._dynamic_num_ctx("kurz", cap=512) == 512


# ---- Schema im Backend-Aufruf (Baustein 2) ------------------------------
def test_extract_sends_schema_to_call(monkeypatch):
    seen = {}

    def fake_call(cfg, prompt, schema=None):
        seen["schema"] = schema
        return json.dumps({"rechnungsnummer": "RE-1"})

    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_call", fake_call)
    out = semantic.extract("Rechnungsnummer RE-1", ["rechnungsnummer"], {})
    assert out["rechnungsnummer"].value == "RE-1"
    assert seen["schema"]["properties"] == {"rechnungsnummer": {"type": ["string", "number", "null"]}}


def test_extract_full_sends_schema_to_call(monkeypatch, tmp_path):
    pytest.importorskip("reportlab")
    from rechnung import pdf as pdfmod
    from rechnung.synth import make_invoice

    text = pdfmod.load(make_invoice(tmp_path / "a.pdf")).text
    seen = {}

    def fake_call(cfg, prompt, schema=None):
        seen["schema"] = schema
        return json.dumps({"rechnungsnummer": "RE-2026-00042"})

    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_call", fake_call)
    semantic.extract_full(text)
    assert seen["schema"] is not None
    assert "rechnungsnummer" in seen["schema"]["properties"]


def test_call_ollama_uses_schema_in_format_field(monkeypatch):
    seen = {}

    class _Resp:
        def read(self):
            return json.dumps({"response": "{}"}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(semantic.urllib.request, "urlopen", fake_urlopen)
    schema = {"type": "object", "properties": {"a": {"type": ["string", "null"]}}}
    cfg = _cfg()
    semantic._call_ollama(cfg, "prompt", schema)
    assert seen["body"]["format"] == schema


def test_call_ollama_falls_back_to_plain_json_on_schema_failure(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        calls.append(body["format"])
        if body["format"] != "json":
            raise OSError("Schema nicht unterstuetzt")

        class _Resp:
            def read(self):
                return json.dumps({"response": "{}"}).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp()

    monkeypatch.setattr(semantic.urllib.request, "urlopen", fake_urlopen)
    schema = {"type": "object", "properties": {}}
    raw = semantic._call_ollama(_cfg(), "prompt", schema)
    assert raw == "{}"
    assert calls == [schema, "json"]


def test_call_ollama_without_schema_uses_plain_json_string(monkeypatch):
    seen = {}

    class _Resp:
        def read(self):
            return json.dumps({"response": "{}"}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(semantic.urllib.request, "urlopen", fake_urlopen)
    semantic._call_ollama(_cfg(), "prompt")
    assert seen["body"]["format"] == "json"


def test_call_openai_uses_json_schema_response_format(monkeypatch):
    seen = {}

    class _Resp:
        def __init__(self, payload):
            self._p = json.dumps(payload).encode("utf-8")

        def read(self):
            return self._p

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(semantic.urllib.request, "urlopen", fake_urlopen)
    schema = {"type": "object", "properties": {}}
    raw = semantic._call_openai(_cfg(backend="openai"), "prompt", schema)
    assert raw == "{}"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["response_format"]["json_schema"]["schema"] == schema


def test_call_openai_falls_back_to_json_object_on_schema_failure(monkeypatch):
    attempts = []

    class _Resp:
        def __init__(self, payload):
            self._p = json.dumps(payload).encode("utf-8")

        def read(self):
            return self._p

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        body = json.loads(req.data.decode("utf-8"))
        attempts.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            raise OSError("unterstuetzt kein json_schema")
        return _Resp({"choices": [{"message": {"content": "{}"}}]})

    monkeypatch.setattr(semantic.urllib.request, "urlopen", fake_urlopen)
    raw = semantic._call_openai(_cfg(backend="openai"), "prompt", {"type": "object"})
    assert raw == "{}"
    assert attempts == ["json_schema", "json_object"]


# ---- Belegpflicht-Nachfassen mit vollem Text (Baustein 1) ---------------
def test_extract_full_retries_with_full_text_when_required_field_missing(monkeypatch, tmp_path):
    pytest.importorskip("reportlab")
    from rechnung import pdf as pdfmod
    from rechnung.synth import make_invoice

    text = pdfmod.load(make_invoice(tmp_path / "a.pdf", rechnungsnr="RE-2026-00042")).text
    # Den Ausschnitt kuenstlich winzig machen, damit er garantiert nicht die
    # Rechnungsnummer enthaelt -- erzwingt den Nachfass-Pfad.
    monkeypatch.setattr(semantic, "_targeted_excerpt", lambda *a, **k: "irrelevanter Kopf")
    calls = []

    def fake_call(cfg, prompt, schema=None):
        calls.append(prompt)
        if "RE-2026-00042" in prompt:
            return json.dumps({"rechnungsnummer": "RE-2026-00042"})
        return json.dumps({})

    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_call_ollama", fake_call)
    full = semantic.extract_full(text)
    assert full.fields["rechnungsnummer"].value == "RE-2026-00042"
    assert len(calls) == 2    # einmal Ausschnitt, einmal voller Text


def test_extract_full_does_not_retry_when_all_required_fields_found(monkeypatch, tmp_path):
    pytest.importorskip("reportlab")
    from rechnung import pdf as pdfmod
    from rechnung.synth import make_invoice

    text = pdfmod.load(make_invoice(tmp_path / "a.pdf")).text
    calls = []

    def fake_call(cfg, prompt, schema=None):
        calls.append(prompt)
        return json.dumps({
            "rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "14.02.2026",
            "netto": "1000,00", "ust_betrag": "190,00", "brutto": "1190,00",
        })

    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_call_ollama", fake_call)
    full = semantic.extract_full(text)
    assert full.fields["rechnungsnummer"].value == "RE-2026-00042"
    assert len(calls) == 1    # kein Nachfassen noetig


def test_extract_full_does_not_retry_for_missing_optional_field(monkeypatch, tmp_path):
    pytest.importorskip("reportlab")
    from rechnung import pdf as pdfmod
    from rechnung.synth import make_invoice

    text = pdfmod.load(make_invoice(tmp_path / "a.pdf")).text
    calls = []

    def fake_call(cfg, prompt, schema=None):
        calls.append(prompt)
        # bestellnummer (optional) fehlt bewusst -- alle Pflichtfelder da
        return json.dumps({
            "rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "14.02.2026",
            "netto": "1000,00", "ust_betrag": "190,00", "brutto": "1190,00",
        })

    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg())
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_call_ollama", fake_call)
    semantic.extract_full(text)
    assert len(calls) == 1
