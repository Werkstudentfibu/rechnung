"""Bibliothek als einzige Quelle fuer Lieferant/Rechnungsadresse (SPEC §7l):
kein eindeutiger Treffer -> zurueckgestellt, Stammdaten haben Vorrang,
abweichende IBAN -> rote Pruefung (Betrugsschutz), nie "wuerde durchlassen"."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import learn, shadow
from rechnung import pdf as pdfmod
from rechnung.billing import BillingAddress
from rechnung.identify import IBAN_DEVIATES, Supplier, supplier_from_doc
from rechnung.pipeline import AUTO, DEFERRED, STATUS_LABELS, process
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

LIB_IBAN = "DE89370400440532013000"
FOREIGN_IBAN = "DE02120300000000202051"


def _secured(store: Store, inv: Path, sid: str = "muster") -> Supplier:
    doc = pdfmod.load(inv)
    sup = supplier_from_doc(doc, sid, "Muster Handels GmbH")
    sup.ibans = [LIB_IBAN]
    store.save_supplier(sup)
    tpl, _ = learn.learn_template(doc, sid, {
        "rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "2026-02-14",
        "netto": 1000.0, "ust_betrag": 190.0, "brutto": 1190.0})
    tpl.confirmations = 3
    store.save_template(tpl)
    return sup


def test_unknown_supplier_is_deferred(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf")
    res = process(inv, store)
    assert res.status == DEFERRED
    assert res.hold_reasons[0].startswith("Lieferant:")
    assert res.supplier_id is None and res.supplier_name is None
    assert res.shadow["would_pass"] is False
    d = res.to_dict()
    assert d["status_label"] == "zurückgestellt – menschlich bearbeiten"
    assert STATUS_LABELS[DEFERRED] == d["status_label"]


def test_ambiguous_hard_hits_are_deferred(tmp_path: Path):
    """USt-IdNr zeigt auf A, IBAN auf B -> nicht raten."""
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf")
    store.save_supplier(Supplier(id="a", name="A GmbH", ustids=["DE123456789"]))
    store.save_supplier(Supplier(id="b", name="B GmbH", ibans=[LIB_IBAN]))
    res = process(inv, store)
    assert res.status == DEFERRED
    assert "mehrdeutig" in res.hold_reasons[0]


def test_master_data_wins_and_matching_iban_passes(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf", iban=LIB_IBAN)
    _secured(store, inv)
    res = process(inv, store)
    assert res.status == AUTO
    chk = next(c for c in res.checks if c.name == "iban-stammdaten")
    assert chk.ok is True
    assert res.fields["iban"].source == "library"


def test_deviating_iban_is_red_deferred_and_never_would_pass(tmp_path: Path):
    store = Store(tmp_path / "data")
    src = make_invoice(tmp_path / "src.pdf", iban=LIB_IBAN)
    _secured(store, src)
    # Folgerechnung, gleiches Layout, aber andere Bankverbindung
    inv = make_invoice(tmp_path / "b.pdf", iban=FOREIGN_IBAN, rechnungsnr="RE-2026-00043")
    res = process(inv, store)
    assert res.supplier_id == "muster"                # per USt-IdNr erkannt
    chk = next(c for c in res.checks if c.name == "iban-stammdaten")
    assert chk.ok is False and chk.detail.startswith(IBAN_DEVIATES)
    assert FOREIGN_IBAN in chk.detail
    assert res.status == DEFERRED and IBAN_DEVIATES in res.hold_reasons
    assert res.fields["iban"].value == LIB_IBAN       # Stammdaten, nicht Rechnung
    assert res.shadow["would_pass"] is False

    # Auch nach Bestaetigung ohne jede Korrektur: keine "wuerde durchlassen"-Zaehlung
    store.mark_confirmed(res.sha256, "t")
    rec = shadow.record(store, res, "t")
    assert rec["would_pass"] is False


def test_additional_foreign_iban_next_to_master_iban_is_red(tmp_path: Path):
    store = Store(tmp_path / "data")
    src = make_invoice(tmp_path / "src.pdf", iban=LIB_IBAN)
    _secured(store, src)
    inv = make_invoice(tmp_path / "b.pdf", iban=f"{LIB_IBAN} oder {FOREIGN_IBAN}",
                       rechnungsnr="RE-2026-00044")
    res = process(inv, store)
    chk = next(c for c in res.checks if c.name == "iban-stammdaten")
    assert chk.ok is False and FOREIGN_IBAN in chk.detail
    assert res.status == DEFERRED


def test_manual_foreign_iban_correction_is_red(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf", iban=LIB_IBAN)
    _secured(store, inv)
    store.save_correction(pdfmod.load(inv).sha256, "iban", FOREIGN_IBAN)
    res = process(inv, store)
    assert next(c for c in res.checks if c.name == "iban-stammdaten").ok is False
    assert res.status == DEFERRED


def test_billing_address_from_library(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf", iban=LIB_IBAN)
    _secured(store, inv)

    # Ohne gepflegte Rechnungsadressen: nicht pruefbar, nicht zurueckgestellt
    res = process(inv, store)
    assert next(c for c in res.checks if c.name == "rechnungsadresse").ok is None
    assert res.status == AUTO

    # Gepflegt, aber keine passt -> zurueckgestellt
    store.save_address(BillingAddress(id="sued", name="Werk Sued",
                                      match_terms=["Werk Sued GmbH", "80331 Muenchen"]))
    res = process(inv, store)
    chk = next(c for c in res.checks if c.name == "rechnungsadresse")
    assert chk.ok is False
    assert res.status == DEFERRED and "Rechnungsadresse" in " ".join(res.hold_reasons)
    assert res.shadow["would_pass"] is False

    # Passende Adresse -> ok
    store.save_address(BillingAddress(id="berlin", name="Beispiel Kunde AG",
                                      match_terms=["Beispiel Kunde AG", "10115 Berlin"]))
    res = process(inv, store)
    assert next(c for c in res.checks if c.name == "rechnungsadresse").ok is True
    assert res.billing_address_id == "berlin"
    assert res.status != DEFERRED

    # Zwei gleich gut passende Adressen -> mehrdeutig -> zurueckgestellt
    store.save_address(BillingAddress(id="berlin2", name="Beispiel Kunde AG (alt)",
                                      match_terms=["Beispiel Kunde AG", "10115 Berlin"]))
    res = process(inv, store)
    assert res.status == DEFERRED and "mehrdeutig" in " ".join(res.hold_reasons)


def test_training_does_not_merge_invoice_iban_into_master_data(tmp_path: Path):
    """Anlernen auf einer Rechnung mit fremder IBAN darf diese nicht still in
    die Stammdaten uebernehmen."""
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    make_invoice(samples / "a.pdf", iban=FOREIGN_IBAN)
    store.save_supplier(Supplier(id="muster", name="Muster Handels GmbH",
                                 ustids=["DE123456789"], ibans=[LIB_IBAN]))
    client = TestClient(server_mod.create_app(store, samples))
    words = client.get("/api/invoice", params={"file": "a.pdf"}).json()["pages"][0]["words"]
    rn = next(w["i"] for w in words if w["text"] == "RE-2026-00042")
    r = client.post("/api/template", json={
        "file": "a.pdf", "supplier_id": "muster", "supplier_name": "Muster",
        "fields": [{"name": "rechnungsnummer", "value_type": "number",
                    "value_words": [rn], "page": 0}]})
    assert r.status_code == 200
    assert store.supplier("muster").ibans == [LIB_IBAN]

    body = client.get("/api/invoice", params={"file": "a.pdf"}).json()
    assert body["status"] == DEFERRED and IBAN_DEVIATES in body["hold_reasons"]
    assert body["current"]["iban"]["value"] == LIB_IBAN
