"""Postfach-Baustein 11c: Fortschritt messbar.

Jede Korrektur traegt Vorlage/vorherige Quelle ins Ereignisprotokoll;
Fehlerbericht zeigt je Lieferant "Korrekturen pro Rechnung" (letzte 10 vs.
davor) und die am haeufigsten korrigierten Felder. Wird dasselbe Feld
einer GESICHERTEN Vorlage wiederholt in Folge korrigiert (Schwelle
konfigurierbar, Default 2), wird die Vorlage ueber den bestehenden Stale-/
Versions-Mechanismus neu gelernt -- nie still umgeschrieben."""

from __future__ import annotations

from pathlib import Path

from rechnung import eventlog, learn
from rechnung.store import Store
from rechnung.template import FieldSpec, Template


def _tpl(**kw) -> Template:
    kw.setdefault("id", "muster__standard")
    kw.setdefault("supplier_id", "muster")
    kw.setdefault("confirmations", 3)
    kw.setdefault("fields", [FieldSpec("rechnungsnummer", "number", True),
                             FieldSpec("bestellnummer", "number", False)])
    return Template(**kw)


class _FakeRes:
    def __init__(self, sha256: str = "s1"):
        self.sha256 = sha256


# ---- Ereignisprotokoll: Vorlage/vorherige Quelle --------------------------

def test_record_correction_mit_vorlage_und_vorheriger_quelle(tmp_path: Path):
    store = Store(tmp_path / "data")
    rec = eventlog.record_correction(store, "sha1", "netto", None, 123.0, "op",
                                     supplier_id="muster", template_id="muster__standard",
                                     quelle_vorher="template-anchor")
    assert rec["template_id"] == "muster__standard"
    assert rec["quelle_vorher"] == "template-anchor"


# ---- Fehlerbericht: Korrekturen pro Rechnung ueber die Zeit ---------------

def test_korrekturen_trend_letzte_10_vs_davor(tmp_path: Path):
    store = Store(tmp_path / "data")
    for i in range(5):
        eventlog.record_correction(store, f"alt{i}", "netto", None, 1, "op", supplier_id="muster")
        eventlog.record_correction(store, f"alt{i}", "brutto", None, 1, "op", supplier_id="muster")
    for i in range(10):
        eventlog.record_correction(store, f"neu{i}", "netto", None, 1, "op", supplier_id="muster")

    rep = eventlog.report(store)
    trend = next(t for t in rep["korrekturen_trend"] if t["lieferant"] == "muster")
    assert trend["rechnungen_mit_korrektur"] == 15
    # letzte 10 Rechnungen: je 1 Korrektur -- davor: je 2 -- Verbesserung sichtbar
    assert trend["korrekturen_pro_rechnung_letzte_10"] == 1.0
    assert trend["korrekturen_pro_rechnung_davor"] == 2.0
    assert trend["haeufigste_felder"][0]["feld"] == "netto"


def test_korrekturen_trend_ohne_genug_rechnungen_bleibt_davor_leer(tmp_path: Path):
    store = Store(tmp_path / "data")
    eventlog.record_correction(store, "sha1", "netto", None, 1, "op", supplier_id="muster")
    rep = eventlog.report(store)
    trend = next(t for t in rep["korrekturen_trend"] if t["lieferant"] == "muster")
    assert trend["korrekturen_pro_rechnung_davor"] is None
    assert trend["korrekturen_pro_rechnung_letzte_10"] == 1.0


# ---- Dasselbe Feld einer gesicherten Vorlage wiederholt korrigiert --------

def test_feld_zweimal_in_folge_korrigiert_macht_vorlage_veraltet(tmp_path: Path):
    store = Store(tmp_path / "data")
    tpl = _tpl()
    store.save_template(tpl)

    out1 = learn.LearnOutcome(skipped={"bestellnummer": "gesichertes Template liefert anderen Wert"})
    learn._track_field_corrections(store, tpl, out1, _FakeRes("s1"))
    assert tpl.field_correction_streak.get("bestellnummer") == 1
    assert tpl.stale is False

    out2 = learn.LearnOutcome(skipped={"bestellnummer": "gesichertes Template liefert anderen Wert"})
    learn._track_field_corrections(store, tpl, out2, _FakeRes("s2"))
    assert tpl.field_correction_streak.get("bestellnummer") == 2
    assert tpl.stale is True
    assert store.template(tpl.id).stale is True
    actions = [a["action"] for a in store.audit_log()]
    assert "template_stale_feld" in actions


def test_streak_reset_wenn_feld_wieder_passt(tmp_path: Path):
    store = Store(tmp_path / "data")
    tpl = _tpl()
    store.save_template(tpl)
    out1 = learn.LearnOutcome(skipped={"bestellnummer": "gesichertes Template liefert anderen Wert"})
    learn._track_field_corrections(store, tpl, out1, _FakeRes("s1"))
    assert tpl.field_correction_streak.get("bestellnummer") == 1

    out2 = learn.LearnOutcome(kept=["bestellnummer"])
    learn._track_field_corrections(store, tpl, out2, _FakeRes("s2"))
    assert tpl.field_correction_streak.get("bestellnummer") == 0
    assert tpl.stale is False


def test_konfigurierbare_schwelle(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_STALE_FELD_SCHWELLE", "1")
    store = Store(tmp_path / "data")
    tpl = _tpl()
    store.save_template(tpl)
    out1 = learn.LearnOutcome(skipped={"bestellnummer": "gesichertes Template liefert anderen Wert"})
    learn._track_field_corrections(store, tpl, out1, _FakeRes("s1"))
    assert tpl.stale is True


def test_unbetroffenes_feld_bleibt_unbeeinflusst(tmp_path: Path):
    store = Store(tmp_path / "data")
    tpl = _tpl()
    store.save_template(tpl)
    out = learn.LearnOutcome(kept=["rechnungsnummer"])
    learn._track_field_corrections(store, tpl, out, _FakeRes("s1"))
    assert tpl.field_correction_streak == {}
    assert tpl.stale is False
