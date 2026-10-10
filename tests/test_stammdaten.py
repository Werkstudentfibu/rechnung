"""Stammdaten aus der Bibliothek gehen mit (Wunsch Aaron 10.10.2026,
Abbyy-Kopf: Geschaeftsbereich + Lieferant mit ID, USt-ID, Anschrift, Bank)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import jobrouter, stammdaten
from rechnung.billing import BillingAddress
from rechnung.identify import Supplier

from test_jobrouter_feedback import _confirmed_and_queued  # noqa: F401


def test_split_adresse_de_ch():
    assert stammdaten.split_adresse(["Am Sandtorkai 4", "20457 Hamburg"]) == \
        {"strasse": "Am Sandtorkai 4", "plz": "20457", "ort": "Hamburg", "land": ""}
    assert stammdaten.split_adresse(["Gessnerallee 3", "CH-8001 Zürich"])["land"] == "CH"
    assert stammdaten.split_adresse(["Halskestr. 48", "22113 Hamburg", "Deutschland"])["land"] == "DE"


def test_geschaeftsbereich_wie_abbyy():
    a = BillingAddress(id="019", name="AMERON Hotelgesellschaft HH mbH",
                       lines=["Am Sandtorkai 4", "20457 Hamburg"],
                       notes="USt-IdNr: DE192954984", cost_center="4711")
    g = stammdaten.geschaeftsbereich(a)
    assert g == {"id": "019", "name": "AMERON Hotelgesellschaft HH mbH", "ustid": "DE192954984",
                 "strasse": "Am Sandtorkai 4", "plz": "20457", "ort": "Hamburg", "land": "DE",
                 "kostenstelle": "4711", "mandant": ""}


def test_lieferant_wie_abbyy_mit_gelesener_iban():
    s = Supplier(id="70368@019", name="Andronaco GmbH & Co. KG", external_id="70368",
                 ustids=["DE257545868"], ibans=["DE11111111111111111111", "DE13100100100794615103"],
                 address_lines=["Halskestraße 48", "22113 Hamburg"], bic="PBNKDEFFXXX")
    l = stammdaten.lieferant(s, "DE13 1001 0010 0794 6151 03")
    assert l["id"] == "70368" and l["iban"] == "DE13100100100794615103"
    assert (l["strasse"], l["plz"], l["ort"], l["land"]) == ("Halskestraße 48", "22113", "Hamburg", "DE")
    assert l["bic"] == "PBNKDEFFXXX" and l["ustid"] == "DE257545868"
    assert stammdaten.lieferant(s, None)["iban"] == "DE11111111111111111111"


def test_freigabe_schickt_stammdaten_an_jobrouter(tmp_path: Path):
    store, res = _confirmed_and_queued(tmp_path)
    import json
    done = list((store.root / "queue" / "done").glob("*.json"))
    assert done
    payload = json.loads(done[0].read_text("utf-8"))
    body = jobrouter.payload_from_result(payload)
    assert body["lieferantStammdaten"]["name"] == "Muster Handels GmbH"
    assert body["lieferantStammdaten"]["iban"] == "DE89370400440532013000"
    assert "geschaeftsbereich" in body
