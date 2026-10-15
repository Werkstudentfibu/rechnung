"""Mandant aus dem Postfach (Entscheidung Aaron 15.10.2026): starker Hinweis
fuer den Geschaeftsbereich, nie eine stille Entscheidung (pipeline.py
``_mailbox_mandant_check``)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung.billing import BillingAddress
from rechnung.pipeline import DEFERRED, process
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

# make_invoice() zeichnet immer dieselbe feste Rechnungsempfaenger-Anschrift
# ("Beispiel Kunde AG" / "Bestellstr. 1" / "10115 Berlin").
BERLIN = BillingAddress(id="berlin", name="Werk Berlin",
                        match_terms=["Beispiel Kunde AG", "10115 Berlin"])
HAMBURG = BillingAddress(id="hamburg", name="Werk Hamburg",
                         match_terms=["Hamburger Spedition GmbH", "20099 Hamburg"])


def test_text_und_postfach_mandant_stimmen_ueberein_kein_zusaetzlicher_hold(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_address(BERLIN)
    inv = make_invoice(tmp_path / "a.pdf")
    doc_sha = __import__("rechnung.pdf", fromlist=["load"]).load(inv).sha256
    store.set_datei_mandant(doc_sha, "berlin")
    res = process(inv, store)
    assert res.billing_address_id == "berlin"
    assert not any("Postfach Mandant" in h for h in res.hold_reasons)


def test_text_erkennt_anderen_mandanten_als_postfach_wird_zurueckgestellt(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_address(BERLIN)
    store.save_address(HAMBURG)
    inv = make_invoice(tmp_path / "a.pdf")
    doc_sha = __import__("rechnung.pdf", fromlist=["load"]).load(inv).sha256
    # Postfach "Hamburg", aber die Rechnung selbst nennt eindeutig Berlin.
    store.set_datei_mandant(doc_sha, "hamburg")
    res = process(inv, store)
    assert res.status == DEFERRED
    assert res.billing_address_id == "berlin"   # Text gewinnt, wird nicht ueberschrieben
    grund = next(h for h in res.hold_reasons if "Postfach Mandant" in h)
    assert "Hamburg" in grund or "hamburg" in grund.lower()
    assert "Berlin" in grund or "berlin" in grund.lower()


def test_keine_rechnungsadresse_erkannt_postfach_mandant_ist_nur_vorschlag(tmp_path: Path):
    store = Store(tmp_path / "data")
    # Keine Rechnungsadresse passt auf den Text -> billing_address_id bleibt leer
    store.save_address(HAMBURG)
    inv = make_invoice(tmp_path / "a.pdf")
    doc_sha = __import__("rechnung.pdf", fromlist=["load"]).load(inv).sha256
    store.set_datei_mandant(doc_sha, "hamburg")
    res = process(inv, store)
    assert res.billing_address_id is None
    assert res.billing_address_suggestion_id == "hamburg"
    assert res.billing_address_suggestion_name == "Werk Hamburg"
    assert not any("Postfach Mandant" in h for h in res.hold_reasons)


def test_kein_postfach_bekannt_keine_aenderung(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_address(BERLIN)
    inv = make_invoice(tmp_path / "a.pdf")
    res = process(inv, store)
    assert res.billing_address_id == "berlin"
    assert res.billing_address_suggestion_id is None
