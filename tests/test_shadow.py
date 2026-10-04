"""Schattenbetrieb-Messung (shadow.py): "haette ich selbst durchgelassen?"
gegen das, was der Mensch beim Bestaetigen tatsaechlich korrigiert hat."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnung import cli, learn, shadow
from rechnung import pdf as pdfmod
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import AUTO, finalize, process
from rechnung.store import Store
from rechnung.template import FieldResult
from rechnung.validate import Check

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def _fr(name, value, source="template-anchor"):
    return FieldResult(name, value, str(value), 0.92, source)


FULL = {"rechnungsnummer": "RE-1", "rechnungsdatum": "2026-02-14", "netto": 100.0,
        "ust_betrag": 19.0, "brutto": 119.0}


def _fields(source="template-anchor", **override):
    vals = dict(FULL, **override)
    return {n: _fr(n, v, source) for n, v in vals.items() if v is not None}


# ---- Entscheidung -------------------------------------------------------
def test_decide_passes_when_complete_and_green():
    d = shadow.decide(_fields(), [Check("netto+ust=brutto", True, "")], new_supplier=False)
    assert d["would_pass"] and d["reasons"] == []
    assert d["values"]["netto"] == 100.0


def test_decide_missing_required_field():
    d = shadow.decide(_fields(brutto=None), [], new_supplier=False)
    assert not d["would_pass"] and d["reasons"] == ["fehlt: brutto"]


def test_decide_red_check():
    d = shadow.decide(_fields(), [Check("iban", False, ""), Check("datum", None, "")],
                      new_supplier=False)
    assert not d["would_pass"] and d["reasons"] == ["Pruefung rot: iban"]


def test_decide_llm_only_blocks_new_supplier_only():
    llm = _fields(source="llm")
    assert not shadow.decide(llm, [], new_supplier=True)["would_pass"]
    assert "nur KI" in shadow.decide(llm, [], new_supplier=True)["reasons"][0]
    assert shadow.decide(llm, [], new_supplier=False)["would_pass"]


# ---- Pipeline/Server ---------------------------------------------------
def _secured_setup(tmp_path: Path):
    """Lieferant 'muster' mit gesichertem (gelerntem) Template."""
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    src = make_invoice(samples / "a.pdf", rechnungsnr="RE-2026-00001")
    doc = pdfmod.load(src)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    tpl, _ = learn.learn_template(doc, "muster", {
        "rechnungsnummer": "RE-2026-00001", "rechnungsdatum": "2026-02-14",
        "netto": 1000.0, "ust_betrag": 190.0, "ust_satz": 19.0, "brutto": 1190.0})
    tpl.confirmations = 3
    store.save_template(tpl)
    return store, samples


def test_pipeline_records_machine_decision_before_corrections(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    p = samples / "a.pdf"
    res = process(p, store)
    assert res.status == AUTO and res.shadow["would_pass"]

    store.save_correction(res.sha256, "rechnungsnummer", "RE-2026-00001-K")
    res2 = process(p, store)
    # Entscheidung bezieht sich auf den maschinellen Stand, nicht die Korrektur
    assert res2.shadow["values"]["rechnungsnummer"] == "RE-2026-00001"
    assert res2.fields["rechnungsnummer"].value == "RE-2026-00001-K"
    assert "values" not in res2.to_dict()["shadow"]
    assert shadow.corrected_fields(res2, store) == ["rechnungsnummer"]


def test_would_pass_never_bypasses_confirmation(tmp_path: Path):
    """Bestaetigungspflicht bleibt: auch would_pass=True wird ohne Klick nicht
    archiviert/geloggt/eingereiht."""
    store, samples = _secured_setup(tmp_path)
    res = process(samples / "a.pdf", store)
    assert res.shadow["would_pass"]
    out = finalize(res, store)
    assert out == {"finalized": False, "error": "not_confirmed", "status": AUTO}
    assert store.processed() == [] and store.shadow_log() == []


def test_untemplated_llm_new_supplier_not_passed(tmp_path: Path, monkeypatch):
    from rechnung import semantic
    from rechnung.config import LlmConfig

    monkeypatch.setattr(semantic, "_llm_cfg", lambda: LlmConfig(enabled=True))
    monkeypatch.setattr(semantic, "available", lambda: True)
    monkeypatch.setattr(semantic, "_call_ollama", lambda c, p: json.dumps({
        "rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "14.02.2026",
        "netto": "1000.00", "ust_betrag": "190.00", "brutto": "1190.00"}))
    store = Store(tmp_path / "data")
    res = process(make_invoice(tmp_path / "x.pdf"), store)
    assert not res.shadow["would_pass"] and res.shadow["new_supplier"]
    assert any("nur KI" in r for r in res.shadow["reasons"])


def _client(store, samples):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod
    return TestClient(server_mod.create_app(store, samples))


def test_server_confirm_counts_per_supplier(tmp_path: Path, capsys):
    store, samples = _secured_setup(tmp_path)
    make_invoice(samples / "b.pdf", rechnungsnr="RE-2026-00002", netto=300.0)
    make_invoice(samples / "c.pdf", rechnungsnr="RE-2026-00003", netto=50.0)
    client = _client(store, samples)
    tid = "muster__standard"

    # a: sauber durch -> haette durchgelassen, ohne Korrektur
    assert client.post("/api/confirm", json={"file": "a.pdf", "template_id": tid}).status_code == 200
    # b: Maschine haette durchgelassen, Mensch korrigiert -> Fehlentscheidung
    client.post("/api/correction", json={"file": "b.pdf", "field": "rechnungsnummer",
                                         "value": "RE-2026-00002-X"})
    assert client.post("/api/confirm", json={"file": "b.pdf", "template_id": tid}).status_code == 200
    # c: zweimal bestaetigt -> zaehlt einmal (erste Bestaetigung gilt)
    for _ in range(2):
        assert client.post("/api/confirm", json={"file": "c.pdf", "template_id": tid}).status_code == 200

    data = client.get("/api/shadow").json()
    assert data["rows"] == [{"lieferant": "muster", "name": "Muster Handels GmbH",
                             "rechnungen": 3, "wuerde_durchlassen": 3,
                             "ohne_korrektur": 2, "fehlentscheidungen": 1}]
    assert data["totals"]["fehlentscheidungen"] == 1
    wrong = [r for r in store.shadow_log() if r["wrong"]]
    assert [r["file"] for r in wrong] == ["b.pdf"]
    assert wrong[0]["corrected"] == ["rechnungsnummer"]

    # CLI zeigt dieselbe Tabelle
    assert cli.main(["--data", str(store.root), "schatten"]) == 0
    out = capsys.readouterr().out
    assert "muster (Muster Handels GmbH)" in out and "Summe" in out
    assert cli.main(["--data", str(store.root), "schatten", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["rechnungen"] == 3


def test_anchor_training_on_invoice_counts_as_correction(tmp_path: Path):
    """Hat der Mensch an genau dieser Rechnung einen Anker gesetzt, ist der
    maschinelle Stand kein eigener Verdienst -- zaehlt als Korrektur."""
    store, samples = _secured_setup(tmp_path)
    client = _client(store, samples)
    words = client.get("/api/invoice", params={"file": "a.pdf"}).json()["pages"][0]["words"]
    idx = next(w["i"] for w in words if w["text"] == "1000.00")
    anchor = next(w["i"] for w in words if w["text"] == "Nettobetrag")
    r = client.post("/api/template", json={
        "file": "a.pdf", "supplier_id": "muster", "supplier_name": "Muster Handels GmbH",
        "variant": "standard", "line_items": None,
        "fields": [{"name": "netto", "value_type": "amount", "required": True,
                    "anchor_word": anchor, "value_words": [idx], "page": 0}]})
    assert r.status_code == 200
    client.post("/api/confirm", json={"file": "a.pdf", "template_id": "muster__standard"})
    rec = store.shadow_log()[-1]
    assert rec["corrected"] == ["netto"] and rec["wrong"] is rec["would_pass"]


def test_einvoice_and_untemplated_confirm_are_recorded(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    a = make_invoice(samples / "a.pdf")
    store.save_supplier(supplier_from_doc(pdfmod.load(a), "muster", "Muster"))
    client = _client(store, samples)
    for k, v in {"rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "14.02.2026",
                 "netto": "1000,00", "ust_betrag": "190,00", "brutto": "1190,00"}.items():
        client.post("/api/correction", json={"file": "a.pdf", "field": k, "value": v})
    assert client.post("/api/confirm", json={"file": "a.pdf"}).status_code == 200
    rec = store.shadow_log()[-1]
    # ohne Template hatte die Maschine nichts -> nicht durchgelassen, kein Fehler
    assert rec["would_pass"] is False and rec["wrong"] is False
    assert set(rec["corrected"]) == {"rechnungsnummer", "rechnungsdatum", "netto",
                                     "ust_betrag", "brutto"}
    assert shadow.stats(store)["rows"][0]["wuerde_durchlassen"] == 0
