"""Effizienz-Baustein 4: Lieferant zuerst ueber den IBAN-/USt-IdNr-Index
(identify.build_identifier_index, Store.supplier_index) statt immer die
ganze Bibliothek per Fingerprint zu scoren."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung.identify import Supplier, build_identifier_index, identify_with_reason
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402
from rechnung import pdf as pdfmod  # noqa: E402


def _supplier(id_, name, ustids=(), ibans=()) -> Supplier:
    return Supplier(id=id_, name=name, ustids=list(ustids), ibans=list(ibans))


# ---- build_identifier_index ------------------------------------------------
def test_index_maps_ustid_and_iban_to_supplier_id():
    a = _supplier("a", "A GmbH", ustids=["DE111111111"], ibans=["DE89370400440532013000"])
    b = _supplier("b", "B GmbH", ustids=["DE222222222"])
    idx = build_identifier_index([a, b])
    assert idx[("ustid", "DE111111111")] == {"a"}
    assert idx[("iban", "DE89370400440532013000")] == {"a"}
    assert idx[("ustid", "DE222222222")] == {"b"}
    assert ("iban", "nichtvorhanden") not in idx


def test_index_collects_multiple_suppliers_per_identifier():
    a = _supplier("a", "A GmbH", ustids=["DE111111111"])
    b = _supplier("b", "B GmbH", ustids=["DE111111111"])  # Datenfehler/Dublette
    idx = build_identifier_index([a, b])
    assert idx[("ustid", "DE111111111")] == {"a", "b"}


def test_index_excludes_steuernummer_deliberately():
    """Bewusste Entscheidung: die Steuernummer ist uneinheitlich formatiert
    (je Bundesland) -- eine Freitext-Erkennung waere riskant, deshalb bleibt
    sie ausserhalb des schnellen Index (reiner Stammdaten-Abgleich)."""
    a = _supplier("a", "A GmbH")
    a.creditor_id = "12/345/67890"
    idx = build_identifier_index([a])
    assert not any(k[0] == "steuernummer" for k in idx)


# ---- identify_with_reason: Index nutzen, Verhalten unveraendert -----------
def test_unique_hard_hit_wins_even_with_precomputed_index(tmp_path: Path):
    doc = pdfmod.load(make_invoice(tmp_path / "a.pdf"))
    from rechnung.identify import supplier_from_doc

    sup = supplier_from_doc(doc, "muster", "Muster Handels GmbH")
    index = build_identifier_index([sup])
    match, why = identify_with_reason(doc, [sup], index=index)
    assert why == ""
    assert match[0].id == "muster"


def test_identify_builds_index_itself_when_not_given(tmp_path: Path):
    doc = pdfmod.load(make_invoice(tmp_path / "a.pdf"))
    from rechnung.identify import supplier_from_doc

    sup = supplier_from_doc(doc, "muster", "Muster Handels GmbH")
    match, why = identify_with_reason(doc, [sup])
    assert why == ""
    assert match[0].id == "muster"


def test_ambiguous_hard_hits_still_detected_via_index(tmp_path: Path):
    """Betrugsschutz bleibt: zeigen USt-IdNr und IBAN auf verschiedene
    Lieferanten, ist das ein Widerspruch -> Mensch, auch mit dem Index."""
    doc = pdfmod.load(make_invoice(tmp_path / "a.pdf"))
    from rechnung.identify import extract_identifiers

    ids = extract_identifiers(doc)
    iban = next(iter(ids["ibans"] or {"DE89370400440532013000"}))
    a = _supplier("a", "A GmbH", ibans=[iban])
    b = _supplier("b", "B GmbH", ustids=list(ids["ustids"]) or ["DE999999999"])
    if not ids["ustids"]:
        pytest.skip("Testrechnung enthaelt keine USt-IdNr")
    match, why = identify_with_reason(doc, [a, b])
    assert match is None
    assert "mehrdeutig" in why


def test_empty_library_reports_reason():
    from rechnung.identify import Document

    match, why = identify_with_reason(Document(Path("x"), [], "sha"), [])
    assert match is None
    assert "leer" in why


def test_no_hard_hit_falls_back_to_fingerprint_scoring(tmp_path: Path):
    """Ohne USt-IdNr/IBAN-Treffer entscheidet weiterhin Fingerprint/Name/
    Domain -- der Index aendert daran nichts."""
    doc = pdfmod.load(make_invoice(tmp_path / "a.pdf"))
    from rechnung.identify import supplier_from_doc

    sup = supplier_from_doc(doc, "muster", "Muster Handels GmbH")
    sup.ustids = []
    sup.ibans = []
    match, why = identify_with_reason(doc, [sup])
    assert why == ""
    assert match[0].id == "muster"


# ---- Store.supplier_index(): gecacht, invalidiert bei Aenderung -----------
def test_store_supplier_index_is_cached(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(_supplier("a", "A GmbH", ustids=["DE111111111"]))
    idx1 = store.supplier_index()
    idx2 = store.supplier_index()
    assert idx1 is idx2    # derselbe Cache-Treffer


def test_store_supplier_index_invalidated_on_save(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(_supplier("a", "A GmbH", ustids=["DE111111111"]))
    idx1 = store.supplier_index()
    store.save_supplier(_supplier("b", "B GmbH", ustids=["DE222222222"]))
    idx2 = store.supplier_index()
    assert idx1 is not idx2
    assert ("ustid", "DE222222222") in idx2


def test_store_supplier_index_invalidated_on_delete(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(_supplier("a", "A GmbH", ustids=["DE111111111"]))
    store.supplier_index()
    store.delete_supplier("a")
    idx = store.supplier_index()
    assert ("ustid", "DE111111111") not in idx
