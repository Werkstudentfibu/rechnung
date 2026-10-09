"""Unbekannte Lieferanten: automatischer Entwurf statt reiner Vorschlag
(Entscheidung Aaron, 09.10.2026, SPEC §7ab) -- ersetzt die fruehere Regel
"Lieferanten werden nie automatisch angelegt" (§7h)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import human, modus
from rechnung.billing import BillingAddress
from rechnung.identify import NEW_SUPPLIER_HOLD, Supplier, kandidaten
from rechnung.jobrouter import queue_dir
from rechnung.messen import messen
from rechnung.pipeline import DEFERRED, confirm_supplier, discard_supplier, dispatch, process
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

ZUERICH = BillingAddress(id="zuerich", name="Werk Zuerich",
                         match_terms=["Beispiel Kunde AG", "10115 Berlin"])


# ---- Entwurf wird angelegt -------------------------------------------------
def test_unknown_supplier_gets_draft_with_fields_from_invoice(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf", ustid="DE123456789",
                       iban="DE89370400440532013000")

    res = process(inv, store, create_draft_supplier=True)

    assert res.supplier_id is not None
    sup = store.supplier(res.supplier_id)
    assert sup is not None
    assert sup.bestaetigt is False
    assert sup.ustids == ["DE123456789"]
    assert sup.ibans == ["DE89370400440532013000"]
    assert sup.entwurf_quelle == res.sha256
    assert sup.entwurf_am
    assert NEW_SUPPLIER_HOLD in res.hold_reasons
    assert res.status == DEFERRED


def test_unknown_supplier_without_name_gets_readable_placeholder(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf", ustid="DE555555555")

    res = process(inv, store, create_draft_supplier=True)

    sup = store.supplier(res.supplier_id)
    assert "DE555555555" in sup.name
    assert sup.name.startswith("Unbekannt")


def test_draft_assigned_to_detected_billing_address(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_address(ZUERICH)
    inv = make_invoice(tmp_path / "a.pdf")   # matcht "zuerich"

    res = process(inv, store, create_draft_supplier=True)

    assert res.billing_address_id == "zuerich"
    sup = store.supplier(res.supplier_id)
    assert sup.addresses == ["zuerich"]


# ---- keine Duplikate -------------------------------------------------------
def test_second_invoice_of_same_unknown_supplier_reuses_draft_no_duplicate(tmp_path: Path):
    store = Store(tmp_path / "data")
    make_invoice(tmp_path / "a.pdf", ustid="DE123456789", iban="DE89370400440532013000",
                rechnungsnr="RE-1")
    make_invoice(tmp_path / "b.pdf", ustid="DE123456789", iban="DE89370400440532013000",
                rechnungsnr="RE-2")

    res1 = process(tmp_path / "a.pdf", store, create_draft_supplier=True)
    res2 = process(tmp_path / "b.pdf", store, create_draft_supplier=True)

    assert res1.supplier_id == res2.supplier_id
    assert len(store.suppliers()) == 1


def test_different_unknown_suppliers_get_different_drafts(tmp_path: Path):
    store = Store(tmp_path / "data")
    make_invoice(tmp_path / "a.pdf", ustid="DE111111111", iban="DE89370400440532013000",
                lieferant="Erste Firma")
    make_invoice(tmp_path / "b.pdf", ustid="DE222222222", iban="DE02120300000000202051",
                lieferant="Zweite Firma")

    res1 = process(tmp_path / "a.pdf", store, create_draft_supplier=True)
    res2 = process(tmp_path / "b.pdf", store, create_draft_supplier=True)

    assert res1.supplier_id != res2.supplier_id
    assert len(store.suppliers()) == 2


# ---- Entwurf ohne Adresse gilt nicht global -------------------------------
def test_draft_without_address_does_not_match_invoice_with_recognized_address(tmp_path: Path):
    store = Store(tmp_path / "data")
    draft = Supplier(id="neu-de123456789", name="Unbekannt (DE123456789)",
                     ustids=["DE123456789"], bestaetigt=False)
    confirmed_global = Supplier(id="confirmed-global", name="Bestaetigt Global",
                                ustids=["DE999999999"], bestaetigt=True)
    store.save_supplier(draft)
    store.save_supplier(confirmed_global)
    store.save_address(ZUERICH)

    # Mit erkannter Adresse ist der adresslose Entwurf KEIN Kandidat mehr,
    # ein bestaetigter globaler Lieferant bleibt rueckwaertskompatibel einer.
    cands = kandidaten(store.suppliers(), "zuerich", has_addresses=True)
    assert draft not in cands
    assert confirmed_global in cands
    # Ohne erkannte Adresse ist der Entwurf weiter Kandidat (globale Suche).
    cands_no_addr = kandidaten(store.suppliers(), None, has_addresses=True)
    assert draft in cands_no_addr


# ---- Rechnung eines unbestaetigten Lieferanten bleibt beim Menschen ------
def test_invoice_of_draft_supplier_stays_with_human_and_never_jobrouter(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf")

    res = process(inv, store, create_draft_supplier=True)
    assert res.status == DEFERRED
    assert human.gruende(res)

    out = dispatch(res, store)
    assert out["aktion"] == "mensch"
    assert not list(queue_dir(store.root).glob("*.json"))


def test_invoice_of_draft_supplier_stays_with_human_even_in_autonomous_mode(tmp_path: Path):
    store = Store(tmp_path / "data")
    modus.set_mode(store, modus.AUTONOM, operator="tester")
    inv = make_invoice(tmp_path / "a.pdf")

    res = process(inv, store, create_draft_supplier=True)
    out = dispatch(res, store)

    assert out["aktion"] == "mensch"
    assert not list(queue_dir(store.root).glob("*.json"))
    assert not store.processed()


# ---- Bestaetigen / Verwerfen ----------------------------------------------
def test_confirm_supplier_makes_next_invoice_run_without_hold(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv1 = make_invoice(tmp_path / "a.pdf", ustid="DE123456789",
                        iban="DE89370400440532013000", rechnungsnr="RE-1")
    res1 = process(inv1, store, create_draft_supplier=True)
    assert NEW_SUPPLIER_HOLD in res1.hold_reasons

    sup = confirm_supplier(store, res1.supplier_id, operator="tester")
    assert sup.bestaetigt is True
    assert store.supplier(res1.supplier_id).bestaetigt is True

    inv2 = make_invoice(tmp_path / "b.pdf", ustid="DE123456789",
                        iban="DE89370400440532013000", rechnungsnr="RE-2")
    res2 = process(inv2, store, create_draft_supplier=True)
    assert res2.supplier_id == res1.supplier_id
    assert NEW_SUPPLIER_HOLD not in res2.hold_reasons

    audit_actions = [a["action"] for a in store.audit_log()]
    assert "lieferant_bestaetigt" in audit_actions
    supplier_events = [e for e in store.events() if e.get("type") == "lieferant"]
    assert any(e["aktion"] == "bestaetigt" for e in supplier_events)


def test_confirm_already_confirmed_supplier_is_a_noop(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="s", name="S", bestaetigt=True))
    confirm_supplier(store, "s", operator="tester")
    assert not [a for a in store.audit_log() if a["action"] == "lieferant_bestaetigt"]


def test_discard_draft_supplier_deletes_it(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="entwurf", name="Entwurf", bestaetigt=False))
    discard_supplier(store, "entwurf", operator="tester")
    assert store.supplier("entwurf") is None
    assert any(a["action"] == "lieferant_verworfen" for a in store.audit_log())


def test_discard_confirmed_supplier_is_rejected(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="s", name="S", bestaetigt=True))
    with pytest.raises(ValueError):
        discard_supplier(store, "s", operator="tester")
    assert store.supplier("s") is not None


def test_confirm_discard_unknown_supplier_raises(tmp_path: Path):
    store = Store(tmp_path / "data")
    with pytest.raises(ValueError):
        confirm_supplier(store, "nichts", operator="tester")
    with pytest.raises(ValueError):
        discard_supplier(store, "nichts", operator="tester")


# ---- Vorschau/messen/vorlernen legen nichts an ----------------------------
def test_process_default_does_not_create_draft(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf")

    res = process(inv, store)   # create_draft_supplier nicht gesetzt -> False

    assert res.supplier_id is None
    assert store.suppliers() == []


def test_messen_does_not_create_draft(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    make_invoice(samples / "a.pdf")

    messen(samples, store)

    assert store.suppliers() == []


def test_vorlernen_scan_does_not_create_draft(tmp_path: Path):
    from rechnung import vorlernen

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    make_invoice(samples / "a.pdf")

    vorlernen.scan(samples, store)

    assert store.suppliers() == []


def test_api_invoice_preview_does_not_create_draft(tmp_path: Path):
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    make_invoice(samples / "a.pdf")

    app = server_mod.create_app(store, samples)
    client = TestClient(app)
    r = client.get("/api/invoice", params={"file": "a.pdf"})
    assert r.status_code == 200
    assert store.suppliers() == []


# ---- Widerspruch bestaetigt/unbestaetigt -> Mensch ------------------------
def test_confirmed_and_draft_sharing_identifiers_is_ambiguous(tmp_path: Path):
    """Zeigen USt-IdNr/IBAN auf einen bestaetigten UND einen unbestaetigten
    Lieferanten (hier: versehentlich doppelt gepflegte USt-IdNr), bleibt es
    'mehrdeutig' -> Mensch, keiner gewinnt stillschweigend."""
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="bestaetigt", name="Bestaetigt",
                                 ustids=["DE123456789"], bestaetigt=True))
    store.save_supplier(Supplier(id="entwurf", name="Entwurf",
                                 ustids=["DE123456789"], bestaetigt=False))
    inv = make_invoice(tmp_path / "a.pdf", ustid="DE123456789")

    res = process(inv, store, create_draft_supplier=True)

    assert res.supplier_id is None
    assert any("mehrdeutig" in g for g in res.hold_reasons)
    # Kein dritter Lieferant wurde trotz "kein Treffer" angelegt.
    assert len(store.suppliers()) == 2


def test_confirmed_supplier_wins_over_draft_on_close_fuzzy_score(tmp_path: Path):
    """Ohne harten USt-IdNr/IBAN-Treffer: ein bestaetigter Lieferant gewinnt
    gegen einen Entwurf innerhalb der normalen Mehrdeutigkeits-Spanne, statt
    dass der Entwurf einen bestaetigten Lieferanten verdraengt."""
    from rechnung.identify import identify_with_reason
    from rechnung.pdf import load as load_pdf

    store = Store(tmp_path / "data")
    inv_path = make_invoice(tmp_path / "a.pdf", lieferant="Muster Handels GmbH")
    doc = load_pdf(inv_path)
    fp = sorted(store.suppliers() and [] or [])  # placeholder, siehe unten

    confirmed = Supplier(id="bestaetigt", name="Muster Handels GmbH", bestaetigt=True)
    draft = Supplier(id="entwurf", name="Muster Handels GmbH", bestaetigt=False)
    match, why = identify_with_reason([confirmed, draft][0:0] or doc.text and None, [])  # unused
