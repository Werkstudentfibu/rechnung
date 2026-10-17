"""Der eine Dummy-Lieferant (Aaron 17.10.2026): unbekannter Lieferant -> Senden
mit Dummy statt Blockade; echte Daten gehen als Bemerkung mit; der Dummy wird
nie gelernt/erkannt/importiert. Nur synthetische Daten."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rechnung import dummy, jobrouter, startklar
from rechnung import stammdaten_import as si
from rechnung.identify import Supplier
from rechnung.store import Store
from rechnung.web.server import create_app

from test_jobrouter_feedback import _secured_setup  # noqa: F401
from test_stammdaten_excel_robust import CH, DE, KOPF, _k, _sheets  # noqa: F401

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def _entwurf_setup(tmp_path: Path):
    """Wie _secured_setup, aber der Lieferant ist nur ein unbestaetigter Entwurf
    mit erkannten Daten."""
    store, samples = _secured_setup(tmp_path)
    sup = store.supplier("muster")
    sup.bestaetigt = False
    sup.ustids = ["DE123456789"]
    sup.ibans = ["DE02120300000000202051"]
    sup.address_lines = ["Musterweg 1", "50667 Köln"]
    store.save_supplier(sup)
    return store, samples


def _queue(store):
    return [json.loads(p.read_text("utf-8")) for p in jobrouter.queue_dir(store.root).glob("*.json")]


# ---- 1. Anlegen / Platzhalter ------------------------------------------------
def test_dummy_wird_beim_start_angelegt_und_ist_stabil(tmp_path: Path):
    store = Store(tmp_path / "d")
    assert store.dummy_lieferant() is None
    create_app(store, tmp_path)
    d = store.dummy_lieferant()
    assert d.id == "dummy-lieferant" and d.name == "Dummy Lieferant"
    assert d.dummy and d.bestaetigt
    # neutral: keine echt aussehenden Kennungen
    assert not d.ibans and not d.ustids and not d.creditor_id and not d.bic
    create_app(store, tmp_path)     # idempotent
    assert len([s for s in store._alle_lieferanten() if s.dummy]) == 1


def test_dummy_env_ueberschreibt_name_und_nummer(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_DUMMY_LIEFERANT_NAME", "Unbekannt GmbH")
    monkeypatch.setenv("RECHNUNG_DUMMY_LIEFERANT_NR", "99999")
    store = Store(tmp_path / "d")
    d = dummy.sicherstellen(store)
    assert (d.name, d.external_id, d.id) == ("Unbekannt GmbH", "99999", "dummy-lieferant")
    monkeypatch.delenv("RECHNUNG_DUMMY_LIEFERANT_NAME")
    assert dummy.sicherstellen(store).name == "Dummy Lieferant"


def test_dummy_ist_unsichtbar_unveraenderbar_und_ueberlebt_speichern(tmp_path: Path):
    store = Store(tmp_path / "d")
    dummy.sicherstellen(store)
    store.save_supplier(Supplier(id="echt", name="Echt GmbH"))
    assert [s.id for s in store.suppliers()] == ["echt"]
    assert store.supplier("dummy-lieferant") is None
    assert store.dummy_lieferant() is not None          # Speichern hat ihn nicht verloren
    store.delete_supplier("dummy-lieferant")
    store.delete_supplier("echt")
    assert store.dummy_lieferant() is not None
    with pytest.raises(ValueError):
        store.save_supplier(Supplier(id="dummy-lieferant", name="X"))
    with pytest.raises(ValueError):
        store.save_supplier(Supplier(id="anders", name="X", dummy=True))


# ---- 2. Senden -----------------------------------------------------------------
def test_senden_mit_entwurf_geht_als_dummy_raus_mit_erkannten_daten(tmp_path: Path):
    store, samples = _entwurf_setup(tmp_path)
    c = TestClient(create_app(store, samples))
    d = c.get("/api/invoice?file=a.pdf").json()
    assert d["supplier"]["bestaetigt"] is False and d["dummy_name"] == "Dummy Lieferant"
    r = c.post("/api/senden", json={"file": "a.pdf", "template_id": d["template_id"]})
    assert r.status_code == 200, r.text
    assert r.json()["als_dummy"] is True
    (q,) = _queue(store)
    assert q["supplier_id"] == "dummy-lieferant" and q["supplier_name"] == "Dummy Lieferant"
    assert q["stammdaten"]["lieferant"]["name"] == "Dummy Lieferant"
    assert q["stammdaten"]["lieferant"]["iban"] == ""
    erk = q["erkannter_lieferant"]
    assert erk["name"] == "Muster Handels GmbH" and erk["iban"] == "DE02120300000000202051"
    assert erk["ustid"] == "DE123456789" and erk["ort"] == "Köln"
    p = jobrouter.payload_from_result(q)
    assert p["supplier"] == "Dummy Lieferant" and p["lieferantIstDummy"] is True
    assert p["erkannterLieferant"]["name"] == "Muster Handels GmbH"
    assert "Muster Handels GmbH" in p["bemerkung"] and "DE123456789" in p["bemerkung"]
    assert p["lieferantStammdaten"]["dummy"] is True
    # Journal bleibt beim internen Entwurf (Dubletten/Lernen)
    assert store.processed()[-1]["supplier_id"] == "muster"


def test_echter_bestaetigter_lieferant_hat_vorrang(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    c = TestClient(create_app(store, samples))
    d = c.get("/api/invoice?file=a.pdf").json()
    r = c.post("/api/senden", json={"file": "a.pdf", "template_id": d["template_id"]})
    assert r.status_code == 200 and r.json()["als_dummy"] is False
    (q,) = _queue(store)
    assert q["supplier_id"] == "muster" and "erkannter_lieferant" not in q
    p = jobrouter.payload_from_result(q)
    assert "erkannterLieferant" not in p and "bemerkung" not in p


def test_pflichtpruefungen_bleiben_trotz_dummy(tmp_path: Path):
    samples = tmp_path / "eingang"; samples.mkdir()
    make_invoice(samples / "R.pdf")
    store = Store(tmp_path / "d")
    c = TestClient(create_app(store, samples))
    r = c.post("/api/senden", json={"file": "R.pdf", "template_id": None})
    assert r.status_code == 400 and "rechnungsnummer" in r.json()["detail"]
    assert "Lieferant" not in r.json()["detail"]
    assert (samples / "R.pdf").exists() and not _queue(store)


def test_geschaeftsbereich_bleibt_pflicht(tmp_path: Path):
    from rechnung.billing import BillingAddress
    store, samples = _entwurf_setup(tmp_path)
    store.save_address(BillingAddress(id="x", name="Fremd AG", lines=["Weg 1", "10115 Berlin"]))
    c = TestClient(create_app(store, samples))
    r = c.post("/api/senden", json={"file": "a.pdf", "template_id": None})
    assert r.status_code == 400 and "Geschäftsbereich" in r.json()["detail"]


# ---- 4. Lernen -------------------------------------------------------------------
def test_dummy_wird_nie_gelernt_oder_erkannt(tmp_path: Path):
    store, samples = _entwurf_setup(tmp_path)
    c = TestClient(create_app(store, samples))
    d = c.get("/api/invoice?file=a.pdf").json()
    assert c.post("/api/senden", json={"file": "a.pdf", "template_id": d["template_id"]}).status_code == 200
    dsup = store.dummy_lieferant()
    # unveraendert: keine Kennungen, Domains, Fingerabdruck angelernt
    assert not (dsup.ibans or dsup.ustids or dsup.domains or dsup.fingerprint or dsup.addresses)
    assert store.templates_for("dummy-lieferant") == []
    assert not any("dummy-lieferant" in f.name for f in (store.root / "templates").iterdir())
    # der Entwurf lernt weiter
    assert store.templates_for("muster")
    for rec in store.processed():
        assert rec["supplier_id"] != "dummy-lieferant"
    # Lieferantenerkennung kennt den Dummy nicht, auch nicht per Name/IBAN
    from rechnung import pdf as pdfmod
    from rechnung.identify import identify
    m = identify(pdfmod.load(samples / "a.pdf") if (samples / "a.pdf").exists()
                 else pdfmod.load(next((store.root / "erledigt").iterdir())),
                 store.suppliers(), index=store.supplier_index(None))
    assert m is None or m[0].id != "dummy-lieferant"
    assert all(s.id != "dummy-lieferant" for s in store.suppliers())


def test_lernbericht_und_anlernwege_schliessen_dummy_aus(tmp_path: Path):
    store, samples = _entwurf_setup(tmp_path)
    c = TestClient(create_app(store, samples))
    z = c.get("/api/lernbericht").json()["zuordnung"]
    assert all(x["name"] != "Dummy Lieferant" for x in z)
    assert c.get("/api/suppliers").json() and all(
        s["id"] != "dummy-lieferant" for s in c.get("/api/suppliers").json())
    r = c.post("/api/suppliers", json={"id": "dummy-lieferant", "name": "Gefaelscht"})
    assert r.status_code == 400
    assert store.dummy_lieferant().name == "Dummy Lieferant"
    assert c.delete("/api/suppliers/dummy-lieferant").status_code in (200, 204)
    assert store.dummy_lieferant() is not None
    # neuer Entwurf mit Dummy-Namen bekommt nie die Dummy-ID
    from rechnung.pipeline import _unique_supplier_id
    assert _unique_supplier_id(store, "dummy-lieferant") != "dummy-lieferant"


# ---- 5. Import ---------------------------------------------------------------------
def test_import_ignoriert_dummy_zeile_ohne_rueckfrage(tmp_path: Path):
    store = Store(tmp_path / "d")
    dummy.sicherstellen(store)
    sheets = _sheets({DE: [KOPF, _k("70001", "Echt GmbH"), _k("70002", "Dummy Lieferant"),
                           _k("dummy-lieferant", "Irgendwas")]})
    vor = si.vorschau(store, sheets)
    zeilen = vor["blaetter"][DE]["zeilen"]
    assert [z["werte"]["name"] for z in zeilen] == ["Echt GmbH"]
    assert all(not z["fragen"] for z in zeilen)
    assert len(vor["automatisch"]["dummy"]) == 2
    assert vor["zusammenfassung"]["dummy_zeilen_ignoriert"] == 2
    erg = si.anwenden(store, sheets, {})
    assert erg["kreditoren"] == 1
    assert [s.name for s in store.suppliers()] == ["Echt GmbH"]
    d = store.dummy_lieferant()
    assert d.name == "Dummy Lieferant" and d.external_id == "DUMMY" and not d.ibans


# ---- 6. Startklar ---------------------------------------------------------------------
def test_startklar_zeile_dummy_vorhanden(tmp_path: Path):
    store = Store(tmp_path / "d")
    zeile = lambda: next(p for p in startklar.pruefen(store, tmp_path / "s")
                         if p["name"] == "Dummy-Lieferant vorhanden")
    assert zeile()["status"] == startklar.FEHLER
    dummy.sicherstellen(store)
    assert zeile()["status"] == startklar.OK
