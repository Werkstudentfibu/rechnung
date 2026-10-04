"""Layoutwechsel erkennen (SPEC §7p): eine gesicherte Vorlage, die wiederholt
Pflichtfelder nicht findet oder Kernpruefungen nicht aufgehen laesst, wird
als "veraltet" markiert (nicht geloescht) -- naechste Rechnungen laufen ueber
die KI-Stufe/Handeingabe, aus der naechsten Bestaetigung lernt das Programm
automatisch eine neue Vorlage/Variante."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import learn
from rechnung import pdf as pdfmod
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import NO_TEMPLATE, process
from rechnung.store import Store
from rechnung.template import FieldResult, FieldSpec, Template
from rechnung.validate import Check

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


class _FakeRes:
    def __init__(self, sources, checks, sha256="s1"):
        self.shadow = {"sources": sources}
        self.checks = checks
        self.sha256 = sha256


def _tpl(**kw) -> Template:
    kw.setdefault("id", "muster__standard")
    kw.setdefault("supplier_id", "muster")
    kw.setdefault("confirmations", 3)
    kw.setdefault("fields", [FieldSpec("rechnungsnummer", "number", True),
                             FieldSpec("netto", "amount", True)])
    return Template(**kw)


# ---- _template_ok ---------------------------------------------------------
def test_template_ok_when_clean():
    tpl = _tpl()
    res = _FakeRes({"rechnungsnummer": "template-anchor", "netto": "template-anchor"}, [])
    ok, reason = learn._template_ok(tpl, res)
    assert ok and reason == ""


def test_template_ok_false_on_missing_required_field():
    tpl = _tpl()
    res = _FakeRes({"rechnungsnummer": "template-anchor"}, [])  # netto fehlt
    ok, reason = learn._template_ok(tpl, res)
    assert not ok and "netto" in reason


def test_template_ok_false_on_red_core_check():
    tpl = _tpl()
    res = _FakeRes({"rechnungsnummer": "template-anchor", "netto": "template-anchor"},
                   [Check("netto+ust=brutto", False, "Abweichung 5.00")])
    ok, reason = learn._template_ok(tpl, res)
    assert not ok and "netto+ust=brutto" in reason


def test_template_ok_ignores_unrelated_red_check():
    """Eine rote Pruefung ohne Layout-Bezug (z.B. Bibliotheks-Abgleich) darf
    eine intakte Vorlage nicht faelschlich als veraltet markieren."""
    tpl = _tpl()
    res = _FakeRes({"rechnungsnummer": "template-anchor", "netto": "template-anchor"},
                   [Check("iban-stammdaten", False, "IBAN weicht ab")])
    ok, _ = learn._template_ok(tpl, res)
    assert ok


# ---- track_template_health -------------------------------------------------
def test_track_template_health_marks_stale_after_threshold(tmp_path: Path):
    store = Store(tmp_path / "data")
    tpl = _tpl()
    store.save_template(tpl)

    bad = _FakeRes({"rechnungsnummer": "template-anchor"}, [], sha256="s1")
    learn.track_template_health(store, tpl, bad)
    assert tpl.consecutive_failures == 1 and not tpl.stale
    assert store.template(tpl.id).consecutive_failures == 1

    bad2 = _FakeRes({"rechnungsnummer": "template-anchor"}, [], sha256="s2")
    learn.track_template_health(store, tpl, bad2)
    assert tpl.stale is True
    assert store.template(tpl.id).stale is True
    actions = [a["action"] for a in store.audit_log()]
    assert "template_stale" in actions


def test_track_template_health_resets_on_success(tmp_path: Path):
    store = Store(tmp_path / "data")
    tpl = _tpl()
    store.save_template(tpl)
    bad = _FakeRes({"rechnungsnummer": "template-anchor"}, [])
    learn.track_template_health(store, tpl, bad)
    assert tpl.consecutive_failures == 1

    good = _FakeRes({"rechnungsnummer": "template-anchor", "netto": "template-anchor"}, [])
    learn.track_template_health(store, tpl, good)
    assert tpl.consecutive_failures == 0 and not tpl.stale


def test_track_template_health_ignores_unsecured_template(tmp_path: Path):
    store = Store(tmp_path / "data")
    tpl = _tpl(confirmations=1)  # nicht gesichert (secured_at=3 Default)
    store.save_template(tpl)
    bad = _FakeRes({}, [])
    learn.track_template_health(store, tpl, bad)
    learn.track_template_health(store, tpl, bad)
    assert tpl.consecutive_failures == 0 and not tpl.stale


# ---- Versionierte Neuanlage -------------------------------------------------
def test_next_template_id_versions_around_stale(tmp_path: Path):
    store = Store(tmp_path / "data")
    assert learn._next_template_id(store, "muster", "standard") == "muster__standard"
    store.save_template(_tpl(stale=True))
    assert learn._active_template(store, "muster", "standard") is None
    assert learn._next_template_id(store, "muster", "standard") == "muster__standard__v2"
    store.save_template(_tpl(id="muster__standard__v2", stale=False))
    assert learn._active_template(store, "muster", "standard").id == "muster__standard__v2"
    assert learn._next_template_id(store, "muster", "standard") == "muster__standard__v3"


# ---- End-to-End: Pipeline waehlt eine veraltete Vorlage nicht mehr ---------
def _secured_setup(tmp_path: Path):
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


def _client(store, samples):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod
    return TestClient(server_mod.create_app(store, samples))


def test_stale_template_is_not_selected_and_relearned(tmp_path: Path, monkeypatch):
    store, samples = _secured_setup(tmp_path)
    make_invoice(samples / "b.pdf", rechnungsnr="RE-2026-00002", netto=300.0)
    make_invoice(samples / "c.pdf", rechnungsnr="RE-2026-00003", netto=400.0)
    make_invoice(samples / "d.pdf", rechnungsnr="RE-2026-00004", netto=500.0)
    tid = "muster__standard"

    # Layoutwechsel simulieren: "netto" wird in der Pipeline nie mehr gefunden
    # (ohne eine neue PDF-Variante bauen zu muessen) -- der Mensch korrigiert
    # trotzdem weiter, die Vorlage selbst liefert das Feld aber nicht mehr.
    from rechnung import template as template_mod
    real_apply = template_mod.apply_template

    def broken_apply(doc, tpl):
        out = real_apply(doc, tpl)
        if "netto" in out:
            out["netto"] = FieldResult("netto", None, "", 0.0, "missing")
        return out

    monkeypatch.setattr("rechnung.pipeline.apply_template", broken_apply)

    client = _client(store, samples)
    for name in ("b.pdf", "c.pdf"):
        client.post("/api/correction", json={"file": name, "field": "netto",
                                             "value": "300.00" if name == "b.pdf" else "400.00"})
        r = client.post("/api/confirm", json={"file": name, "template_id": tid})
        assert r.status_code == 200

    tpl = store.template(tid)
    assert tpl.stale is True
    assert any(a["action"] == "template_stale" for a in store.audit_log())

    # Naechste Rechnung desselben Lieferanten: Vorlage nicht mehr automatisch
    # gewaehlt -- faellt auf die KI-Stufe/Handeingabe zurueck.
    res = process(samples / "d.pdf", store, use_llm=False)
    assert res.status == NO_TEMPLATE and res.template_id is None

    # Aus der naechsten Bestaetigung lernt das Programm automatisch eine neue
    # Vorlage/Variante -- unter einer neuen Id, die alte bleibt erhalten.
    for field, value in [("rechnungsnummer", "RE-2026-00004"), ("rechnungsdatum", "2026-02-14"),
                        ("netto", "500.00"), ("ust_betrag", "95.00"), ("brutto", "595.00")]:
        client.post("/api/correction", json={"file": "d.pdf", "field": field, "value": value})
    r = client.post("/api/confirm", json={"file": "d.pdf"})
    assert r.status_code == 200
    learned_id = r.json()["learned"]["template_id"]
    assert learned_id == "muster__standard__v2"
    new_tpl = store.template(learned_id)
    assert new_tpl is not None and not new_tpl.stale
    assert store.template(tid).stale is True  # unveraendert erhalten, nicht geloescht
