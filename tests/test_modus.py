"""Betriebsmodus Testphase -> Autonomie (modus.py, SPEC §7v): Fortschritt,
Umschalten nur per Befehl mit Audit, autonome Uebernahme nur sicherer
Rechnungen, IBAN-Abweichung mensch|warnen, optionale Stichprobe/Rueckfall."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnung import cli, human, jobrouter, learn, modus
from rechnung import pdf as pdfmod
from rechnung.identify import supplier_from_doc
from rechnung.nightly import run_nightly
from rechnung.pipeline import DEFERRED, dispatch, finalize, process
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

CONFIRMED = {
    "rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "2026-02-14",
    "netto": 1000.0, "ust_betrag": 190.0, "ust_satz": 19.0, "brutto": 1190.0,
}
FOREIGN_IBAN = "DE02120300000000202051"


def _setup(tmp_path: Path) -> tuple[Store, Path]:
    """Lieferant 'muster' mit gelernter (noch nicht gesicherter) Vorlage."""
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    first = make_invoice(tmp_path / "re1.pdf")
    doc = pdfmod.load(first)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    tpl, _ = learn.learn_template(doc, "muster", CONFIRMED)
    store.save_template(tpl)
    return store, samples


def _safe_invoice(samples: Path, n: int = 43, **kw) -> Path:
    return make_invoice(samples / f"re{n}.pdf", rechnungsnr=f"RE-2026-{n:05d}",
                        netto=500.0 + n, **kw)


def _shadow(store, sha, supplier, would_pass=True, corrected=(), wrong=None):
    store.shadow_append({"sha256": sha, "file": f"{sha}.pdf", "supplier_id": supplier,
                         "would_pass": would_pass, "corrected": list(corrected),
                         "wrong": would_pass and bool(corrected) if wrong is None else wrong})


# ---- Modus + Umschalten ------------------------------------------------------
def test_default_is_testphase_and_switch_needs_operator(tmp_path: Path):
    store = Store(tmp_path / "data")
    assert modus.get(store) == modus.TESTPHASE
    with pytest.raises(ValueError):
        modus.set_mode(store, modus.AUTONOM, "")
    with pytest.raises(ValueError):
        modus.set_mode(store, "vollgas", "aaron")
    res = modus.set_mode(store, modus.AUTONOM, "aaron", grund="Test")
    assert res["alt"] == modus.TESTPHASE and modus.get(store) == modus.AUTONOM
    audit = [a for a in store.audit_log() if a["action"] == "betriebsmodus"]
    assert audit[-1]["operator"] == "aaron"
    assert audit[-1]["detail"]["neu"] == "autonom"
    assert audit[-1]["detail"]["richtwert_erreicht"] is False


def test_cli_modus_requires_operator_and_switches(tmp_path: Path, capsys):
    data = str(tmp_path / "data")
    assert cli.main(["--data", data, "modus", "autonom"]) == 2
    assert modus.get(Store(data)) == modus.TESTPHASE
    assert cli.main(["--data", data, "modus", "autonom", "--operator", "aaron"]) == 0
    out = capsys.readouterr().out
    assert "testphase -> autonom" in out and "Richtwert" in out
    assert cli.main(["--data", data, "modus"]) == 0
    assert "Modus: autonom" in capsys.readouterr().out


def test_never_switches_automatically(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_TESTPHASE_IN_FOLGE", "2")
    monkeypatch.setenv("RECHNUNG_TESTPHASE_LIEFERANTEN", "1")
    store = Store(tmp_path / "data")
    for i in range(5):
        _shadow(store, f"s{i}", "a")
    assert modus.fortschritt(store)["erreicht"]
    assert modus.get(store) == modus.TESTPHASE


# ---- Fortschritt -------------------------------------------------------------
def test_progress_counts_streak_and_suppliers(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_TESTPHASE_IN_FOLGE", "4")
    monkeypatch.setenv("RECHNUNG_TESTPHASE_LIEFERANTEN", "3")
    store = Store(tmp_path / "data")
    _shadow(store, "s1", "a")
    _shadow(store, "s2", "b", corrected=["netto"])          # Fehlentscheidung -> von vorn
    _shadow(store, "s3", "a")
    _shadow(store, "s4", "x", would_pass=False)              # unsicher -> zaehlt nicht
    _shadow(store, "s5", "b")
    _shadow(store, "s6", "a")
    fp = modus.fortschritt(store)
    assert fp["in_folge"] == 3 and fp["lieferanten_in_folge"] == 2
    assert not fp["erreicht"]
    assert fp["letzte_fehlentscheidung"]["korrigiert"] == ["netto"]
    _shadow(store, "s7", "c")
    fp = modus.fortschritt(store)
    assert fp["in_folge"] == 4 and fp["lieferanten_in_folge"] == 3 and fp["erreicht"]

    # Spaetere JobRouter-Korrektur einer Rechnung mitten in der Folge
    from rechnung import shadow
    shadow.record_feedback_correction(store, "s5", ["brutto"], "jobrouter")
    fp = modus.fortschritt(store)
    assert fp["in_folge"] == 2 and not fp["erreicht"]


def test_progress_defaults():
    assert modus.ziel_in_folge() == 50 and modus.ziel_lieferanten() == 10
    assert modus.stichprobe_prozent() == 0 and not modus.rueckfall_aktiv()


# ---- Verteilen ---------------------------------------------------------------
def test_testphase_safe_invoice_waits_for_click(tmp_path: Path):
    store, samples = _setup(tmp_path)
    res = process(_safe_invoice(samples), store)
    assert human.gruende(res) == [], human.gruende(res)
    assert dispatch(res, store) == {"aktion": "bestaetigung"}
    assert finalize(res, store, autonomous=True)["error"] == "not_autonomous"
    assert not store.seen_sha(res.sha256)


def test_autonomous_safe_invoice_goes_to_jobrouter_queue(tmp_path: Path):
    store, samples = _setup(tmp_path)
    modus.set_mode(store, modus.AUTONOM, "aaron")
    res = process(_safe_invoice(samples), store)
    out = dispatch(res, store)
    assert out["aktion"] == "autonom" and out["finalize"]["freigabe"] == "autonom"
    assert res.verteilung == out
    q = json.loads(Path(out["finalize"]["queued"]).read_text("utf-8"))
    assert q["freigabe"] == "autonom"
    assert jobrouter.payload_from_result(q)["approval"] == "autonom"
    assert store.seen_sha(res.sha256) and not store.is_confirmed(res.sha256)
    fin = [a for a in store.audit_log() if a["action"] == "finalize"]
    assert fin[-1]["operator"] == "autonom" and fin[-1]["detail"]["freigabe"] == "autonom"
    assert [r["operator"] for r in store.shadow_log()] == ["autonom"]
    # Zweiter Lauf: bereits uebernommen, nichts doppelt
    assert dispatch(process(samples / "re43.pdf", store), store)["aktion"] == "bereits_uebernommen"


def test_autonomous_unsafe_invoice_goes_to_human(tmp_path: Path):
    store, samples = _setup(tmp_path)
    modus.set_mode(store, modus.AUTONOM, "aaron")
    res = process(_safe_invoice(samples, iban=FOREIGN_IBAN), store)
    assert res.status == DEFERRED
    assert dispatch(res, store)["aktion"] == "mensch"
    assert finalize(res, store, autonomous=True)["error"] == "unsicher"
    assert not store.seen_sha(res.sha256)
    assert [r["sha256"] for r in human.offen(store)] == [res.sha256]


def test_optional_sample_check_sends_safe_invoice_to_human(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_AUTONOM_STICHPROBE", "100")
    store, samples = _setup(tmp_path)
    modus.set_mode(store, modus.AUTONOM, "aaron")
    res = process(_safe_invoice(samples), store)
    assert dispatch(res, store)["gruende"][0]["art"] == "stichprobe"
    assert not store.seen_sha(res.sha256)


# ---- IBAN-Abweichung: mensch | warnen ---------------------------------------
def test_iban_deviation_warn_mode_passes_with_warning(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_IBAN_ABWEICHUNG", "warnen")
    store, samples = _setup(tmp_path)
    modus.set_mode(store, modus.AUTONOM, "aaron")
    res = process(_safe_invoice(samples, iban=FOREIGN_IBAN), store)
    assert res.status != DEFERRED
    assert res.warnings and FOREIGN_IBAN in res.warnings[0]
    assert next(c for c in res.checks if c.name == "iban-stammdaten").ok is False
    assert res.shadow["would_pass"]
    out = dispatch(res, store)
    assert out["aktion"] == "autonom"
    payload = jobrouter.payload_from_result(
        json.loads(Path(out["finalize"]["queued"]).read_text("utf-8")))
    assert "ACHTUNG" in payload["fields"]["warnung"] and FOREIGN_IBAN in payload["warnings"][0]
    # Stammdaten-IBAN bleibt der Zahlungs-Wert
    assert payload["fields"]["iban"] != FOREIGN_IBAN


def test_unknown_iban_mode_falls_back_to_human(monkeypatch):
    from rechnung.config import iban_mode

    monkeypatch.setenv("RECHNUNG_IBAN_ABWEICHUNG", "egal")
    assert iban_mode() == "mensch"


# ---- Bekannt gewordene Fehler (JobRouter-Rueckmeldung) -----------------------
def _autonomous_then_feedback(tmp_path: Path) -> tuple[Store, str, list]:
    store, samples = _setup(tmp_path)
    modus.set_mode(store, modus.AUTONOM, "aaron")
    res = process(_safe_invoice(samples), store)
    dispatch(res, store)
    jobrouter.push_queue(store.root)
    fb = jobrouter.Feedback(sha256=res.sha256, file="re43.pdf",
                            fields={"netto": "543,00"})
    return store, res.sha256, jobrouter.apply_feedback(store, [fb])


def test_feedback_logs_and_relearns_without_fallback(tmp_path: Path):
    store, sha, applied = _autonomous_then_feedback(tmp_path)
    assert modus.get(store) == modus.AUTONOM           # kein Rueckfall per Standard
    assert applied[0]["learned"] is not None            # Neu-Lernen angestossen
    assert any(e.get("type") == "korrektur" and e["sha256"] == sha for e in store.events())
    assert modus.fortschritt(store)["letzte_fehlentscheidung"]["korrigiert"] == ["netto"]


def test_optional_fallback_to_testphase(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_AUTONOM_RUECKFALL", "1")
    store, _sha, _ = _autonomous_then_feedback(tmp_path)
    assert modus.get(store) == modus.TESTPHASE
    last = [a for a in store.audit_log() if a["action"] == "betriebsmodus"][-1]
    assert last["operator"] == "jobrouter" and "Rueckfall" in last["detail"]["grund"]


# ---- Nachtbericht ------------------------------------------------------------
def test_nightly_report_contains_progress(tmp_path: Path):
    store = Store(tmp_path / "data")
    _shadow(store, "s1", "a")
    bericht = run_nightly(store)
    b = bericht["betrieb"]
    assert b["modus"] == "testphase" and b["in_folge"] == 1
    assert b["ziel_in_folge"] == 50 and "1/50" in b["text"]
    assert b["menschlich_offen"] == 0


def test_daily_report_counts_human_and_autonomous(tmp_path: Path):
    from rechnung.nightly import tagesbericht

    store, samples = _setup(tmp_path)
    modus.set_mode(store, modus.AUTONOM, "aaron")
    dispatch(process(_safe_invoice(samples), store), store)
    dispatch(process(_safe_invoice(samples, n=44, iban=FOREIGN_IBAN), store), store)
    tb = tagesbericht(store)
    assert tb["autonom_uebernommen"] == 1 and tb["menschlich_abgelegt"] == 1


def test_api_modus_is_read_only(tmp_path: Path):
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    (tmp_path / "s").mkdir()
    client = TestClient(server_mod.create_app(store, tmp_path / "s"))
    d = client.get("/api/modus").json()
    assert d["modus"] == "testphase" and "0/50" in d["text"]
    assert client.post("/api/modus", json={"modus": "autonom"}).status_code == 405
