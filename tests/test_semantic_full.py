"""Stufe 2: KI-Extraktion ohne Template (semantic.extract_full) + Wiring.

Kein echtes Modell noetig: Konfiguration, Erreichbarkeit und Backend-Aufruf
werden je Test ersetzt (offline, deterministisch)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnung import pdf as pdfmod
from rechnung import pipeline, semantic
from rechnung.config import LlmConfig
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import EXTRACT_LLM, NO_TEMPLATE, UNKNOWN, process
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

GOOD = {
    "rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "2026-02-14",
    "netto": "1.000,00", "ust_betrag": 190, "ust_satz": "19", "brutto": "1190.00",
    "bestellnummer": None,
    "lieferant_name": "Muster Handels GmbH",
    "lieferant_iban": "DE89 3704 0044 0532 0130 00",
    "lieferant_ustid": "DE123456789",
}


def _cfg(**overrides) -> LlmConfig:
    base = dict(enabled=True, base_url="http://127.0.0.1:11434", model="test-model",
                num_ctx=4096, timeout=5, max_confidence=0.6)
    base.update(overrides)
    return LlmConfig(**base)


def _mock(monkeypatch, answer, **cfg):
    calls = []

    def fake_call(c, prompt):
        calls.append(prompt)
        return answer if isinstance(answer, str) else json.dumps(answer)

    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg(**cfg))
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_call_ollama", fake_call)
    return calls


@pytest.fixture
def text(tmp_path: Path) -> str:
    return pdfmod.load(make_invoice(tmp_path / "a.pdf")).text


# ---- extract_full ---------------------------------------------------------
def test_full_disabled_by_default(text):
    assert LlmConfig().enabled is False
    assert semantic.extract_full(text).fields == {}


def test_full_switch_off(monkeypatch, text):
    _mock(monkeypatch, GOOD, full_extraction=False)
    assert semantic.extract_full(text).fields == {}


def test_full_reads_all_fields_verified(monkeypatch, text):
    _mock(monkeypatch, GOOD)
    full = semantic.extract_full(text)
    got = {k: v.value for k, v in full.fields.items()}
    assert got == {"rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "2026-02-14",
                   "netto": 1000.0, "ust_betrag": 190.0, "ust_satz": 19.0,
                   "brutto": 1190.0, "iban": "DE89370400440532013000",
                   "ustid": "DE123456789"}
    assert all(f.source == "llm" and f.confidence <= 0.6 for f in full.fields.values())
    assert full.supplier == {"name": "Muster Handels GmbH",
                             "iban": "DE89370400440532013000", "ustid": "DE123456789"}
    assert full.rejected == {}


def test_full_rejects_values_not_in_pdf(monkeypatch, text):
    """Halluzinationsschutz: Werte, die nicht im Text stehen, fliegen raus."""
    _mock(monkeypatch, dict(GOOD, brutto="9.999,99", rechnungsnummer="RE-2026-99999",
                            rechnungsdatum="01.01.2026",
                            lieferant_name="Erfundene AG",
                            lieferant_ustid="DE999999999",
                            lieferant_iban="DE02120300000000202051"))
    full = semantic.extract_full(text)
    for name in ("brutto", "rechnungsnummer", "rechnungsdatum", "iban", "ustid"):
        assert name not in full.fields
    assert "nicht im PDF gefunden" in full.rejected["brutto"]
    assert set(full.rejected) >= {"brutto", "rechnungsnummer", "rechnungsdatum",
                                  "lieferant_name", "lieferant_ustid", "lieferant_iban"}
    assert full.supplier == {}
    assert full.fields["netto"].value == 1000.0  # belegte Werte bleiben


def test_full_number_must_be_read_completely(monkeypatch, text):
    # "RE 2026 00042" wuerde von coerce() zu "RE" -- kein eindeutiger Wert
    _mock(monkeypatch, dict(GOOD, rechnungsnummer="RE 2026 00042"))
    full = semantic.extract_full(text)
    assert "rechnungsnummer" not in full.fields
    assert full.rejected["rechnungsnummer"].startswith("Nummer nicht eindeutig")


def test_full_schema_validation(monkeypatch, text):
    _mock(monkeypatch, dict(GOOD, netto=["1000"], ust_betrag=True, unbekannt="x",
                            lieferant_name=42))
    full = semantic.extract_full(text)
    assert full.rejected["netto"].startswith("Schema")
    assert full.rejected["lieferant_name"].startswith("Schema")
    assert "ust_betrag" not in full.fields      # bool ist keine Zahl
    assert "unbekannt" not in full.rejected and "unbekannt" not in full.fields
    assert full.fields["brutto"].value == 1190.0


@pytest.mark.parametrize("answer", ["kein json", "[1, 2]", ""])
def test_full_unusable_answer(monkeypatch, text, answer):
    _mock(monkeypatch, answer)
    assert semantic.extract_full(text).fields == {}


def test_full_accepts_json_in_code_block(monkeypatch, text):
    _mock(monkeypatch, "```json\n" + json.dumps({"netto": "1000.00"}) + "\n```")
    assert semantic.extract_full(text).fields["netto"].value == 1000.0


def test_full_line_items_only_when_enabled_and_verified(monkeypatch, text):
    answer = dict(GOOD, positionen=[
        {"beschreibung": "Ersatzteile Serie X200", "menge": 1, "einzelpreis": "550,00",
         "netto": "550,00", "ust_satz": 19},
        {"beschreibung": "Erfunden", "menge": 2, "netto": "4711,00"},
        "kein objekt",
    ])
    calls = _mock(monkeypatch, answer)
    assert semantic.extract_full(text).line_items == []    # Standard: aus
    assert "positionen" not in calls[-1]

    semantic._resp_cache.clear()
    calls = _mock(monkeypatch, answer, line_items=True)
    full = semantic.extract_full(text)
    assert "positionen" in calls[-1]
    assert full.line_items == [{"beschreibung": "Ersatzteile Serie X200", "menge": 1.0,
                                "einzelpreis": 550.0, "netto": 550.0, "ust_satz": 19.0,
                                "_quelle": "llm"}]
    assert "1 Position" in full.rejected["positionen"]


def test_gap_fill_also_verifies_against_text(monkeypatch):
    _mock(monkeypatch, {"rechnungsnummer": "RE-77", "brutto": "12,50"})
    rejected: dict = {}
    out = semantic.extract("Rechnung RE-77 Summe 99,00", ["rechnungsnummer", "brutto"],
                           {}, rejected)
    assert set(out) == {"rechnungsnummer"}
    assert rejected["brutto"].startswith("nicht im PDF gefunden")


def test_response_cache_avoids_second_model_call(monkeypatch, text):
    calls = _mock(monkeypatch, GOOD)
    a = semantic.extract_full(text)
    b = semantic.extract_full(text)
    assert len(calls) == 1
    assert {k: v.value for k, v in a.fields.items()} == {k: v.value for k, v in b.fields.items()}


# ---- OpenAI-kompatibles Backend ---------------------------------------
class _Resp:
    def __init__(self, payload: dict):
        self._p = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_openai_backend_request_and_parse(monkeypatch, text):
    cfg = _cfg(backend="openai", base_url="http://llm.intern:8000/v1",
               model="qwen2.5-14b", api_key="geheim")
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: cfg)
    monkeypatch.setattr(semantic, "available", lambda: True)
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp({"choices": [{"message": {"content": json.dumps(GOOD)}}]})

    monkeypatch.setattr(semantic.urllib.request, "urlopen", fake_urlopen)
    full = semantic.extract_full(text)
    assert seen["url"] == "http://llm.intern:8000/v1/chat/completions"
    assert seen["auth"] == "Bearer geheim"
    assert seen["body"]["model"] == "qwen2.5-14b"
    assert seen["body"]["temperature"] == 0.0
    assert full.fields["brutto"].value == 1190.0


def test_openai_backend_broken_answer(monkeypatch, text):
    monkeypatch.setattr(semantic, "_llm_cfg", lambda: _cfg(backend="openai"))
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resp({"error": "kaputt"}))
    assert semantic.extract_full(text).fields == {}


def test_openai_available_probes_models(monkeypatch):
    semantic._avail_cache = None
    monkeypatch.setattr(semantic, "_llm_cfg",
                        lambda: _cfg(backend="openai", base_url="http://llm.intern:8000/v1"))
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req.full_url if hasattr(req, "full_url") else req)
        return _Resp({})

    monkeypatch.setattr(semantic.urllib.request, "urlopen", fake_urlopen)
    assert semantic.available() is True
    assert seen == ["http://llm.intern:8000/v1/models"]
    semantic._avail_cache = None


# ---- Pipeline ---------------------------------------------------------
def test_pipeline_unknown_supplier_full_extraction(monkeypatch, tmp_path: Path):
    _mock(monkeypatch, dict(GOOD, brutto="5,00"))
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf")
    res = process(inv, store)
    assert res.status == UNKNOWN                      # bleibt "anlernen"
    assert res.extraction == EXTRACT_LLM
    assert res.supplier_name == "Muster Handels GmbH"
    d = res.to_dict()
    assert d["fields"]["netto"]["origin"] == "llm"
    assert d["fields"]["iban"]["origin"] == "llm"
    assert "brutto" not in d["fields"] and "brutto" in d["llm_rejected"]
    assert any(c.name == "netto+ust=brutto" for c in res.checks)

    # Korrektur gewinnt gegen das Modell
    store.save_correction(res.sha256, "brutto", "1.190,00")
    res2 = process(inv, store)
    assert res2.to_dict()["fields"]["brutto"]["origin"] == "manual"
    assert all(c.ok is not False for c in res2.checks)


def test_pipeline_known_supplier_master_data_beats_model(monkeypatch, tmp_path: Path):
    _mock(monkeypatch, GOOD)
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf")
    store.save_supplier(supplier_from_doc(pdfmod.load(inv), "muster", "Muster"))
    res = process(inv, store)
    assert res.status == NO_TEMPLATE
    assert res.fields["iban"].source == "library"
    assert res.fields["netto"].source == "llm"
    assert res.supplier_name == "Muster"              # Stammdaten, nicht Modell


def test_pipeline_scan_without_llm(monkeypatch, tmp_path: Path):
    calls = _mock(monkeypatch, GOOD)
    store = Store(tmp_path / "data")
    res = process(make_invoice(tmp_path / "a.pdf"), store, use_llm=False)
    assert calls == [] and res.fields == {}


def test_origin_mapping():
    assert pipeline.origin("template-anchor") == "template"
    assert pipeline.origin("template-abs") == "template"
    assert pipeline.origin("missing") is None
    for o in pipeline.ORIGINS:
        assert pipeline.origin(o) == o


def test_server_llm_review_confirm_learns(monkeypatch, tmp_path: Path):
    """Ganze Kette: KI liest unbekanntes Layout -> Mensch sieht es in
    /api/invoice -> bestaetigt -> Stufe 1 lernt die Vorlage daraus."""
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod

    calls = _mock(monkeypatch, GOOD)
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    a = make_invoice(samples / "a.pdf")
    store.save_supplier(supplier_from_doc(pdfmod.load(a), "muster", "Muster"))
    client = TestClient(server_mod.create_app(store, samples))

    body = client.get("/api/invoice", params={"file": "a.pdf"}).json()
    assert body["extraction"] == EXTRACT_LLM
    assert body["current"]["netto"]["origin"] == "llm"
    assert body["current"]["iban"]["origin"] == "library"

    r = client.post("/api/confirm", json={"file": "a.pdf"})
    assert r.status_code == 200, r.text
    learned = r.json()["learned"]
    assert learned["saved"] and set(learned["learned"]) >= {"netto", "brutto",
                                                            "rechnungsnummer"}
    assert len(calls) == 1          # Bestaetigen nutzt die gesehene Antwort
    assert store.template("muster__standard").confirmations == 0
