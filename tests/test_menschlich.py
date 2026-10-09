"""Ordner "Menschliche Bearbeitung" (human.py, SPEC §7u): unsichere
Rechnungen werden abgelegt statt geraten, verlassen den Ordner nach
Bearbeitung+Bestaetigung, das Programm lernt daraus."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnung import human
from rechnung import pdf as pdfmod
from rechnung.identify import IBAN_DEVIATES, supplier_from_doc
from rechnung.pipeline import DEFERRED, REVIEW, Result, dispatch, scan_folder
from rechnung.store import Store
from rechnung.template import FieldResult
from rechnung.validate import Check

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

FULL = {"rechnungsnummer": "RE-1", "rechnungsdatum": "2026-02-14", "netto": 100.0,
        "ust_betrag": 19.0, "brutto": 119.0}


def _res(conf=0.92, source="template-anchor", checks=None, **override) -> Result:
    vals = dict(FULL, **override)
    r = Result(file="/x/a.pdf", sha256="ab" * 32, supplier_id="muster",
               supplier_name="Muster", status=REVIEW)
    r.fields = {n: FieldResult(n, v, str(v), conf, source)
                for n, v in vals.items() if v is not None}
    r.checks = checks or [Check("netto+ust=brutto", True, "")]
    return r


# ---- Wann ist das Programm nicht sicher? ------------------------------------
def test_safe_result_has_no_reasons():
    assert human.gruende(_res()) == []
    assert human.ist_sicher(_res())


def test_missing_required_field():
    g = human.gruende(_res(brutto=None))
    assert [x["art"] for x in g] == ["pflichtfeld"]
    assert g[0]["felder"] == ["brutto"]


def test_red_check_names_affected_fields():
    g = human.gruende(_res(checks=[Check("netto+ust=brutto", False, "Abweichung 1.00"),
                                   Check("keine-dublette", None, "")]))
    assert g == [{"art": "pruefung", "grund": "Pruefung rot: netto+ust=brutto (Abweichung 1.00)",
                  "felder": ["netto", "ust_betrag", "brutto"]}]


def test_low_confidence_value_is_unsure_not_guessed():
    g = human.gruende(_res(conf=0.6, source="llm"))
    assert [x["art"] for x in g] == ["unsicher"]
    assert set(g[0]["felder"]) == set(FULL)


def test_library_and_manual_values_are_not_unsure():
    r = _res()
    r.fields["iban"] = FieldResult("iban", "DE89", "DE89", 0.5, "library")
    r.fields["netto"] = FieldResult("netto", 100.0, "100", 0.5, "manual")
    assert human.gruende(r) == []


def test_ocr_unsure_check():
    g = human.gruende(_res(checks=[Check("ocr-konfidenz", False, "Ø 55")]))
    assert g[0]["art"] == "ocr" and "OCR unsicher" in g[0]["grund"]


def test_iban_deviation_reported_once():
    r = _res(checks=[Check("iban-stammdaten", False, f"{IBAN_DEVIATES}: DE02")])
    r.status = DEFERRED
    r.hold_reasons = [IBAN_DEVIATES]
    g = human.gruende(r)
    assert [x["art"] for x in g] == ["iban"]
    assert g[0]["felder"] == ["iban"]


def test_timeout_only_reports_timeout():
    r = Result(file="a.pdf", sha256="cd" * 32, status=DEFERRED, aborted="zeitgrenze",
               hold_reasons=["Zeitgrenze 120 s ueberschritten -- Verarbeitung abgebrochen"])
    assert [x["art"] for x in human.gruende(r)] == ["zeitgrenze"]


def test_file_name_handles_windows_paths():
    assert human.file_name(r"C:\Rechnungen\eingang\re 1.pdf") == "re 1.pdf"
    assert human.file_name("/tmp/x/re.pdf") == "re.pdf"


# ---- Ordner -------------------------------------------------------------------
def _unknown_supplier_invoice(tmp_path: Path) -> tuple[Store, Path]:
    store = Store(tmp_path / "data")
    samples = tmp_path / "samples"
    samples.mkdir()
    make_invoice(samples / "re.pdf")
    return store, samples


def test_scan_places_unsure_invoice_with_sidecar(tmp_path: Path):
    store, samples = _unknown_supplier_invoice(tmp_path)
    [res] = scan_folder(samples, store, guard=_NoGuard(store))
    folder = human.human_dir(store.root)
    assert (folder / "re.pdf").read_bytes() == (samples / "re.pdf").read_bytes()
    side = json.loads((folder / ("re.pdf" + human.SIDECAR_SUFFIX)).read_text("utf-8"))
    assert side["sha256"] == res.sha256
    arten = {g["art"] for g in side["gruende"]}
    # scan_folder legt bei unbekanntem Lieferanten einen Entwurf an
    # (Entscheidung Aaron 09.10.2026) -- der Grund ist jetzt "neuer
    # Lieferant wartet auf Bestaetigung" statt "kein Lieferant...".
    assert "neuer_lieferant" in arten
    assert res.supplier_id and res.supplier_name
    assert res.supplier_id.startswith("neu-")
    assert side["abgelegt"] and "vorschlaege" in side
    # Original im Eingangsordner bleibt unveraendert liegen
    assert (samples / "re.pdf").exists()
    assert any(a["action"] == "menschlich_abgelegt" for a in store.audit_log())
    placed = [e for e in store.events() if e.get("type") == "menschlich"]
    assert len(placed) == 1 and placed[0]["aktion"] == "abgelegt"

    # Erneuter Scan: Begleitdatei aktualisiert, kein zweites Ereignis
    scan_folder(samples, store, guard=_NoGuard(store))
    assert len([e for e in store.events() if e.get("type") == "menschlich"]) == 1
    assert len(human.offen(store)) == 1
    assert human.offen(store)[0]["abgelegt"] == side["abgelegt"]


def test_name_clash_with_other_invoice_does_not_overwrite(tmp_path: Path):
    store = Store(tmp_path / "data")
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(), b.mkdir()
    make_invoice(a / "re.pdf", rechnungsnr="RE-1")
    make_invoice(b / "re.pdf", rechnungsnr="RE-2")
    scan_folder(a, store, guard=_NoGuard(store))
    scan_folder(b, store, guard=_NoGuard(store))
    names = sorted(r["datei"] for r in human.offen(store))
    assert len(names) == 2 and "re.pdf" in names
    assert any(n.endswith("_re.pdf") for n in names)


def test_already_processed_or_confirmed_is_not_placed(tmp_path: Path):
    store, samples = _unknown_supplier_invoice(tmp_path)
    from rechnung.pipeline import process

    res = process(samples / "re.pdf", store)
    store.mark_confirmed(res.sha256, "tester")
    assert dispatch(res, store)["aktion"] == "mensch"
    assert human.offen(store) == []       # bestaetigt -> nicht (wieder) ablegen
    store.mark_processed({"sha256": res.sha256, "file": "re.pdf"})
    assert dispatch(res, store) == {"aktion": "bereits_uebernommen"}


class _NoGuard:
    """Ohne Arbeitsprozess (schneller, deterministisch) -- wie Guard(timeout=0)."""

    def __init__(self, store):
        from rechnung.guard import Guard
        self._g = Guard(store.root, timeout=0)

    def process(self, p, use_llm=True, create_draft_supplier=False):
        return self._g.process(p, use_llm=use_llm, create_draft_supplier=create_draft_supplier)


# ---- Bearbeitung im Training-UI -> verlaesst den Ordner, lernt -------------
def test_ui_confirm_moves_out_learns_and_next_invoice_is_safe(tmp_path: Path):
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    first = make_invoice(samples / "re1.pdf", rechnungsnr="RE-2026-00042")
    store.save_supplier(supplier_from_doc(pdfmod.load(first), "muster", "Muster Handels GmbH"))
    client = TestClient(server_mod.create_app(store, samples))

    # Lieferant bekannt, noch keine Vorlage -> Pflichtfelder fehlen -> Mensch
    client.get("/api/scan")
    offen = client.get("/api/menschlich").json()["offen"]
    assert [r["datei"] for r in offen] == ["re1.pdf"]
    assert any(g["art"] == "pflichtfeld" for g in offen[0]["gruende"])

    # Mensch traegt die Werte ein (aus der Arbeitskopie im Ordner) und bestaetigt
    (samples / "re1.pdf").unlink()           # Eingangsordner z.B. schon geleert
    for f, v in {"rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "14.02.2026",
                 "netto": "1.000,00", "ust_betrag": "190,00", "ust_satz": "19",
                 "brutto": "1.190,00"}.items():
        assert client.post("/api/correction", json={"file": "re1.pdf", "field": f,
                                                     "value": v}).status_code == 200
    r = client.post("/api/confirm", json={"file": "re1.pdf", "template_id": None})
    assert r.status_code == 200, r.text
    assert r.json()["learned"]["saved"]

    assert client.get("/api/menschlich").json()["offen"] == []
    folder = human.human_dir(store.root)
    assert not (folder / "re1.pdf").exists()
    done = json.loads(next((folder / human.DONE_DIR).glob("*.json")).read_text("utf-8"))
    assert done["bearbeiter"] and "netto" in done["korrigiert"]
    assert [e["aktion"] for e in store.events() if e.get("type") == "menschlich"] == \
        ["abgelegt", "erledigt"]

    # Naechste Rechnung desselben Lieferanten: Vorlage greift, nichts unsicher
    make_invoice(samples / "re2.pdf", rechnungsnr="RE-2026-00043", netto=500.0)
    client.get("/api/scan")
    assert client.get("/api/menschlich").json()["offen"] == []
