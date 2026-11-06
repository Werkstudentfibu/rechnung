"""Anlernphase mit Claude als Lehrer (lehrer.py) -- Claude-API gemockt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnung import human, lehrer
from rechnung import pdf as pdfmod
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import finalize, process
from rechnung.store import Store

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

RICHTIG = {"rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "2026-02-14",
           "netto": "1000.00", "ust_betrag": "190.00", "ust_satz": "19", "brutto": "1190.00",
           "faelligkeit": "2026-02-28", "waehrung": "EUR"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RECHNUNG_CLAUDE_LEHRER", "1")
    monkeypatch.setenv("RECHNUNG_CLAUDE_STICHPROBE", "0")


def _store(tmp_path: Path, bestaetigt: bool = True):
    store = Store(tmp_path / "data")
    ordner = tmp_path / "in"
    ordner.mkdir()
    src = make_invoice(ordner / "a.pdf")
    sup = supplier_from_doc(pdfmod.load(src), "muster", "Muster Handels GmbH")
    sup.bestaetigt = bestaetigt
    store.save_supplier(sup)
    return store, ordner, src


def _claude(werte: dict, sicher: bool = True, calls: list | None = None):
    def post(body):
        if calls is not None:
            calls.append(body)
        felder = {k: {"wert": v, "sicher": sicher, "beleg": str(v)} for k, v in werte.items()}
        return {"content": [{"type": "text", "text": json.dumps({"felder": felder})}]}
    return post


def test_claude_bestaetigt_und_programm_lernt_vorlage(tmp_path):
    store, ordner, src = _store(tmp_path)
    calls = []
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG, calls=calls))
    assert r["ergebnis"] == lehrer.ERG_BESTAETIGT, r
    assert calls and calls[0]["messages"][0]["content"][0]["type"] == "document"
    assert r["gelernt"]["saved"]
    assert store.templates_for("muster")
    # nur Claude hat bestaetigt -> darf nicht in die Buecher / an JobRouter
    res = process(src, store)
    assert lehrer.angelernt(store, res.sha256) and not store.is_confirmed(res.sha256)
    out = finalize(res, store, to_jobrouter=True)
    assert out == {"finalized": False, "error": "not_confirmed", "status": res.status}
    assert not store.seen_sha(res.sha256)
    # Schattenbetrieb zaehlt wie bei einem Menschen, Operator claude
    assert store.shadow_log()[-1]["operator"] == "claude"


def test_naechste_rechnung_nutzt_gelernte_vorlage(tmp_path):
    store, ordner, src = _store(tmp_path)
    lehrer.anlernen_datei(store, src, post=_claude(RICHTIG))
    nxt = make_invoice(ordner / "b.pdf", rechnungsnr="RE-2026-00050", netto=420.0)
    res = process(nxt, store, use_llm=False)
    assert res.template_id and res.fields["netto"].value == 420.0


def test_claude_korrigiert_falschen_programmwert(tmp_path):
    store, ordner, src = _store(tmp_path)
    sha = pdfmod.load(src).sha256
    store.save_correction(sha, "netto", "999,00", "jemand")  # Programm "liegt falsch"
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG))
    assert r["ergebnis"] == lehrer.ERG_BESTAETIGT, r
    assert "netto" in r["korrigiert"]
    assert store.corrections(sha)["netto"] == "1000.00"
    assert any(a["action"] == "correct" and a["operator"] == "claude" for a in store.audit_log())


def test_erfundener_wert_geht_zum_menschen(tmp_path):
    store, ordner, src = _store(tmp_path)
    falsch = dict(RICHTIG, rechnungsnummer="RE-9999-ERFUNDEN")
    r = lehrer.anlernen_datei(store, src, post=_claude(falsch))
    assert r["ergebnis"] == lehrer.ERG_MENSCH
    assert "nicht im PDF gefunden" in r["gruende"][0]
    assert not lehrer.angelernt(store, pdfmod.load(src).sha256)
    assert human.offen(store)


def test_unsicherer_claude_geht_zum_menschen(tmp_path):
    store, ordner, src = _store(tmp_path)
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG, sicher=False))
    assert r["ergebnis"] == lehrer.ERG_MENSCH
    assert "unsicher" in r["gruende"][0]


def test_lieferanten_entwurf_entscheidet_mensch_ohne_api(tmp_path):
    store, ordner, src = _store(tmp_path, bestaetigt=False)
    calls = []
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG, calls=calls))
    assert r["ergebnis"] == lehrer.ERG_MENSCH and not calls


def test_stichprobe_und_bilanz(tmp_path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_CLAUDE_STICHPROBE", "100")
    store, ordner, src = _store(tmp_path)
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG))
    assert r["ergebnis"] == lehrer.ERG_MENSCH and r.get("stichprobe")
    sha = r["sha256"]
    # Mensch kontrolliert: korrigiert brutto (Claude lag falsch) und bestaetigt
    store.save_correction(sha, "brutto", "1.190,01", "mensch")
    store.mark_confirmed(sha, "mensch")
    b = lehrer.bilanz(store)
    assert b["lehrer"]["stichprobe_kontrolliert"] == 1
    assert b["lehrer"]["claude_lag_falsch"] == 1 and b["lehrer"]["felder"] == {"brutto": 1}
    # vom Menschen bestaetigt -> darf jetzt regulaer weiter
    assert store.is_confirmed(sha)


def test_antwort_wird_gemerkt(tmp_path):
    store, ordner, src = _store(tmp_path)
    calls = []
    post = _claude(RICHTIG, calls=calls)
    lehrer.anlernen(store, ordner, post=post)
    lehrer.anlernen(store, ordner, post=post)  # zweiter Lauf: schon bestaetigt
    assert len(calls) == 1


def test_api_fehler_ohne_seiteneffekt(tmp_path):
    store, ordner, src = _store(tmp_path)

    def kaputt(body):
        raise OSError("keine Verbindung")
    r = lehrer.anlernen_datei(store, src, post=kaputt)
    assert r["ergebnis"] == lehrer.ERG_FEHLER
    assert not lehrer.angelernt(store, r["sha256"]) and not human.offen(store)


def test_aus_ist_standard(monkeypatch):
    monkeypatch.delenv("RECHNUNG_CLAUDE_LEHRER")
    assert not lehrer.an()


def test_kommandozeile_bilanz(tmp_path, capsys):
    store, ordner, src = _store(tmp_path)
    lehrer.anlernen_datei(store, src, post=_claude(RICHTIG))
    assert lehrer.main(["--data", str(store.root), "--bilanz"]) == 0
    assert "von Claude bestaetigt: 1" in capsys.readouterr().out
