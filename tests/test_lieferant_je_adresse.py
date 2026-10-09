"""Lieferantensuche je Rechnungsadresse (wie Abbyy FlexiCapture): ein
Lieferant wird nur unter der Rechnungsadresse (BusinessUnit) gesucht, der er
zugeordnet ist, nie uebergreifend (Entscheidung Aaron, 09.10.2026)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung.billing import BillingAddress
from rechnung.identify import Supplier, kandidaten
from rechnung.jobrouter import payload_from_result
from rechnung.pipeline import AUTO, DEFERRED, process
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

# make_invoice() zeichnet immer dieselbe feste Rechnungsempfaenger-Anschrift
# ("Beispiel Kunde AG" / "Bestellstr. 1" / "10115 Berlin") -- die "Zuerich"-
# Adresse unten matcht genau diesen Text, "Hamburg" bewusst etwas anderes.
ZUERICH = BillingAddress(id="zuerich", name="Werk Zuerich",
                         match_terms=["Beispiel Kunde AG", "10115 Berlin"])
HAMBURG = BillingAddress(id="hamburg", name="Werk Hamburg",
                         match_terms=["Hamburger Spedition GmbH", "20099 Hamburg"])


# ---- identify.kandidaten (reine Filterlogik, ohne PDF) --------------------
def _sup(id_, addresses=()) -> Supplier:
    return Supplier(id=id_, name=id_, addresses=list(addresses))


def test_kandidaten_without_any_addresses_in_library_returns_all():
    a, b = _sup("a"), _sup("b", addresses=["zuerich"])
    assert kandidaten([a, b], None, has_addresses=False) == [a, b]
    assert kandidaten([a, b], "zuerich", has_addresses=False) == [a, b]


def test_kandidaten_address_not_recognized_returns_only_global():
    global_sup = _sup("global")
    scoped = _sup("scoped", addresses=["zuerich"])
    out = kandidaten([global_sup, scoped], None, has_addresses=True)
    assert out == [global_sup]


def test_kandidaten_address_recognized_includes_global_and_matching_scoped():
    global_sup = _sup("global")
    zuerich_sup = _sup("zuerich-sup", addresses=["zuerich"])
    hamburg_sup = _sup("hamburg-sup", addresses=["hamburg"])
    out = kandidaten([global_sup, zuerich_sup, hamburg_sup], "zuerich", has_addresses=True)
    assert out == [global_sup, zuerich_sup]


# ---- Pipeline: Erkennung nur unter der passenden Rechnungsadresse --------
def test_zurich_invoice_never_finds_hamburg_supplier_even_with_same_ustid(tmp_path: Path):
    """Zuerich-Rechnung darf einen nur Hamburg zugeordneten Lieferanten NICHT
    finden -- auch wenn die USt-IdNr zufaellig uebereinstimmt (z.B. dieselbe
    Abbyy-Id unter zwei BusinessUnits, importers.py)."""
    store = Store(tmp_path / "data")
    store.save_address(ZUERICH)
    store.save_address(HAMBURG)
    store.save_supplier(Supplier(id="9001@hamburg", name="Reiniger Hamburg",
                                 ustids=["DE123456789"], addresses=["hamburg"]))
    inv = make_invoice(tmp_path / "a.pdf")  # matcht "zuerich", nicht "hamburg"

    res = process(inv, store)
    assert res.billing_address_id == "zuerich"
    assert res.supplier_id is None
    assert any("Lieferant:" in g for g in res.hold_reasons)
    assert res.status == DEFERRED


def test_same_ustid_under_two_addresses_is_not_ambiguous(tmp_path: Path):
    """Dieselbe USt-IdNr unter zwei verschiedenen Rechnungsadressen darf NICHT
    als 'mehrdeutig' gelten, solange die Suche auf eine Adresse beschraenkt
    ist -- der jeweils andere Lieferant ist gar kein Kandidat."""
    store = Store(tmp_path / "data")
    store.save_address(ZUERICH)
    store.save_address(HAMBURG)
    store.save_supplier(Supplier(id="9001@zuerich", name="Reiniger Zuerich",
                                 ustids=["DE123456789"], addresses=["zuerich"]))
    store.save_supplier(Supplier(id="9001@hamburg", name="Reiniger Hamburg",
                                 ustids=["DE123456789"], addresses=["hamburg"]))
    inv = make_invoice(tmp_path / "a.pdf")  # matcht "zuerich"

    res = process(inv, store)
    assert res.supplier_id == "9001@zuerich"
    assert not any("mehrdeutig" in g for g in res.hold_reasons)


def test_address_not_recognized_only_searches_global_suppliers(tmp_path: Path):
    """Gibt es Rechnungsadressen in der Bibliothek, wurde aber fuer DIESE
    Rechnung keine erkannt, wird nur unter den globalen Lieferanten
    (``addresses`` leer) gesucht -- nie stillschweigend die ganze
    Bibliothek."""
    store = Store(tmp_path / "data")
    # Keine der beiden gepflegten Adressen passt zur Testrechnung.
    store.save_address(BillingAddress(id="anderswo", name="Anderswo",
                                      match_terms=["Nirgendwo GmbH", "99999 Nirgendwo"]))
    store.save_address(HAMBURG)
    store.save_supplier(Supplier(id="global-sup", name="Global GmbH",
                                 ustids=["DE123456789"]))  # addresses leer = global
    store.save_supplier(Supplier(id="scoped-sup", name="Scoped GmbH",
                                 ustids=["DE999999999"], addresses=["hamburg"]))
    inv = make_invoice(tmp_path / "a.pdf")

    res = process(inv, store)
    assert res.billing_address_id is None
    assert res.supplier_id == "global-sup"
    assert not any("Lieferant:" in g for g in res.hold_reasons)


def test_address_not_recognized_reason_mentions_address_scoped_search(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_address(BillingAddress(id="anderswo", name="Anderswo",
                                      match_terms=["Nirgendwo GmbH", "99999 Nirgendwo"]))
    store.save_supplier(Supplier(id="scoped-sup", name="Scoped GmbH",
                                 addresses=["anderswo2"]))  # nicht die erkannte Adresse
    inv = make_invoice(tmp_path / "a.pdf")

    res = process(inv, store)
    lieferant_grund = next(g for g in res.hold_reasons if g.startswith("Lieferant:"))
    assert "nur je Adresse gesucht" in lieferant_grund


# ---- Store.supplier_index(): je Kandidatenmenge, invalidiert auch bei Adressen
def test_store_supplier_index_is_scoped_per_address(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_address(ZUERICH)
    store.save_address(HAMBURG)
    store.save_supplier(Supplier(id="z", name="Z", ustids=["DE111111111"],
                                 addresses=["zuerich"]))
    store.save_supplier(Supplier(id="h", name="H", ustids=["DE111111111"],
                                 addresses=["hamburg"]))

    idx_z = store.supplier_index("zuerich")
    idx_h = store.supplier_index("hamburg")
    assert idx_z[("ustid", "DE111111111")] == {"z"}
    assert idx_h[("ustid", "DE111111111")] == {"h"}


def test_store_supplier_index_invalidated_on_address_change(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="a", name="A", ustids=["DE111111111"]))
    idx_before = store.supplier_index(None)      # noch keine Adressen -> global = alle
    store.save_address(ZUERICH)
    idx_after = store.supplier_index(None)       # jetzt: nur globale Lieferanten
    assert idx_before is not idx_after


# ---- Alte Lieferanten-JSON ohne neue Felder laedt weiter -----------------
def test_legacy_supplier_json_without_new_fields_still_loads(tmp_path: Path):
    data = tmp_path / "data"
    store = Store(data)
    (data / "suppliers.json").write_text(
        '[{"id": "alt", "name": "Alter Lieferant", "ustids": ["DE123456789"], '
        '"ibans": [], "domains": [], "fingerprint": []}]',
        "utf-8",
    )
    sup = store.supplier("alt")
    assert sup is not None
    assert sup.addresses == []
    assert sup.external_id == ""


# ---- UI-Uebersteuerung: /api/invoice?address=... -------------------------
def test_ui_address_override_reruns_supplier_search_under_chosen_address(tmp_path: Path):
    """Waehlt der Mensch im Reiter '1 Rechnungsadresse' eine andere Adresse,
    laeuft die Lieferantensuche unter dieser Adresse neu -- auch wenn sie
    nicht die automatisch erkannte ist."""
    TestClient = pytest.importorskip("fastapi.testclient").TestClient
    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    store.save_address(ZUERICH)
    store.save_address(HAMBURG)
    store.save_supplier(Supplier(id="9001@zuerich", name="Reiniger Zuerich",
                                 ustids=["DE123456789"], addresses=["zuerich"]))
    store.save_supplier(Supplier(id="9001@hamburg", name="Reiniger Hamburg",
                                 ustids=["DE123456789"], addresses=["hamburg"]))
    make_invoice(samples / "a.pdf")  # erkannt wird "zuerich"

    app = server_mod.create_app(store, samples)
    client = TestClient(app)

    r = client.get("/api/invoice", params={"file": "a.pdf"})
    d = r.json()
    assert d["match"]["id"] == "9001@zuerich"
    assert d["variant"] == "zuerich"

    r2 = client.get("/api/invoice", params={"file": "a.pdf", "address": "hamburg"})
    d2 = r2.json()
    assert d2["match"]["id"] == "9001@hamburg"
    assert d2["variant"] == "hamburg"


# ---- Export: external_id als 'lieferantennummer' --------------------------
def test_result_carries_supplier_external_id_for_export(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="9001@zuerich", name="Reiniger Zuerich",
                                 ustids=["DE123456789"], external_id="9001"))
    inv = make_invoice(tmp_path / "a.pdf")

    res = process(inv, store)
    assert res.supplier_id == "9001@zuerich"
    assert res.supplier_external_id == "9001"
    body = payload_from_result(res.to_dict())
    assert body["lieferantennummer"] == "9001"
