"""Vorschlaege fuer noch unbekannte Lieferanten/Rechnungsadressen (09.10.2026,
Vorfuehrung im Finance-Buero ohne Lieferantenliste): die KI liest Lieferanten-
namen und Rechnungsempfaenger mit Beleg-Pflicht, das Programm fuellt sie zur
Pruefung vor -- angelegt wird eine Adresse nur per Klick."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import semantic
from rechnung.billing import BillingAddress
from rechnung.identify import Supplier
from rechnung.pipeline import process
from rechnung.store import Store

from test_semantic_full import GOOD, _mock

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402
from rechnung import pdf as pdfmod  # noqa: E402

EMPF = dict(GOOD, empfaenger_name="Beispiel Kunde AG", empfaenger_anschrift="10115 Berlin")


def test_recipient_read_with_proof(monkeypatch, tmp_path: Path):
    text = pdfmod.load(make_invoice(tmp_path / "a.pdf")).text
    _mock(monkeypatch, EMPF)
    full = semantic.extract_full(text)
    assert full.empfaenger == {"name": "Beispiel Kunde AG", "lines": ["10115 Berlin"]}


def test_recipient_not_in_text_is_rejected(monkeypatch, tmp_path: Path):
    text = pdfmod.load(make_invoice(tmp_path / "a.pdf")).text
    _mock(monkeypatch, dict(GOOD, empfaenger_name="Hotel Erfunden AG",
                            empfaenger_anschrift="Phantomweg 9, 10115 Berlin"))
    full = semantic.extract_full(text)
    assert "name" not in full.empfaenger
    assert full.empfaenger.get("lines") == ["10115 Berlin"]   # nur belegte Teile
    assert "empfaenger_name" in full.rejected
    assert "empfaenger_anschrift" in full.rejected


def test_draft_gets_name_from_ki_instead_of_placeholder(monkeypatch, tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf", ustid="DE123456789",
                       iban="DE89370400440532013000")
    _mock(monkeypatch, EMPF)

    res = process(inv, store, create_draft_supplier=True)

    sup = store.supplier(res.supplier_id)
    assert sup.bestaetigt is False
    assert sup.name == "Muster Handels GmbH"
    assert res.ki_empfaenger["name"] == "Beispiel Kunde AG"


def test_preview_does_not_rename_draft(monkeypatch, tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="neu-de123456789", name="Unbekannt (DE123456789)",
                                 ustids=["DE123456789"], bestaetigt=False))
    inv = make_invoice(tmp_path / "a.pdf", ustid="DE123456789")
    _mock(monkeypatch, EMPF)

    process(inv, store)   # Vorschau: create_draft_supplier=False

    assert store.supplier("neu-de123456789").name == "Unbekannt (DE123456789)"


def test_hand_named_draft_keeps_its_name(monkeypatch, tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="neu-x", name="Von Hand benannt",
                                 ustids=["DE123456789"], bestaetigt=False))
    inv = make_invoice(tmp_path / "a.pdf", ustid="DE123456789")
    _mock(monkeypatch, EMPF)

    process(inv, store, create_draft_supplier=True)

    assert store.supplier("neu-x").name == "Von Hand benannt"


def test_addressless_draft_adopts_address_once_known(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf", ustid="DE123456789",
                       iban="DE89370400440532013000")
    res1 = process(inv, store, create_draft_supplier=True)
    assert store.supplier(res1.supplier_id).addresses == []

    # Rechnungsadresse wird nachtraeglich angelegt (z.B. per Klick im UI)
    store.save_address(BillingAddress(id="kunde", name="Beispiel Kunde AG",
                                      match_terms=["Beispiel Kunde AG"]))
    res2 = process(inv, store, create_draft_supplier=True)

    assert res2.billing_address_id == "kunde"
    assert res2.supplier_id == res1.supplier_id
    assert len(store.suppliers()) == 1
    assert store.supplier(res1.supplier_id).addresses == ["kunde"]
