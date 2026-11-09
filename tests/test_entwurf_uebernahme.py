"""Lieferanten-Entwurf in einen bestehenden Lieferanten uebernehmen
(entwurf_uebernahme.py, Entscheidung Aaron 09.11.2026): statt den Entwurf
"Unbekannt (DE...)" als eigenen Lieferanten zu bestaetigen (Dublette zum
Excel-Import ohne USt-IdNr/IBAN), wandern seine Kennungen in den bestehenden
Lieferanten -- nur auf Klick, nie automatisch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnung import entwurf_uebernahme, eventlog, human, shadow, vorlernen
from rechnung.entwurf_uebernahme import uebernehmen, zusammenfuehren
from rechnung.identify import NEW_SUPPLIER_HOLD, Supplier
from rechnung.pipeline import dispatch, process
from rechnung.store import Store
from rechnung.template import Template

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

USTID = "DE123456789"
IBAN = "DE89370400440532013000"


def _ziel(**kw) -> Supplier:
    """Lieferant wie aus dem Excel-Import: Name, Nummer, keine Kennungen."""
    return Supplier(id="muster-handels-gmbh", name="Muster Handels GmbH",
                    external_id="4711", bestaetigt=True, **kw)


def _entwurf(**kw) -> Supplier:
    return Supplier(id="neu-de123456789", name=f"Unbekannt ({USTID})",
                    ustids=[USTID], ibans=[IBAN], domains=["muster-handel.de"],
                    fingerprint=["muster", "handels"], bestaetigt=False, **kw)


# ---- Zusammenfuehren der Stammdaten -----------------------------------------
def test_zusammenfuehren_vereinigt_listen_und_behaelt_zielwerte():
    ziel = _ziel(ustids=["DE999999999"], bank_name="Zielbank", address_lines=["Zielweg 1"],
                 fixed_fields={"bestellnummer": "Z"}, currency="CHF")
    entwurf = _entwurf(bank_name="Entwurfsbank", bic="COBADEFFXXX",
                       address_lines=["Entwurfsweg 2"], fixed_fields={"bestellnummer": "E", "ust_satz": "7"})

    uebernommen = zusammenfuehren(ziel, entwurf)

    assert ziel.ustids == ["DE999999999", USTID]          # Ziel zuerst, dann neu
    assert ziel.ibans == [IBAN]
    assert ziel.domains == ["muster-handel.de"]
    assert ziel.fingerprint == ["muster", "handels"]
    assert ziel.bank_name == "Zielbank"                    # vorhandener Wert bleibt
    assert ziel.bic == "COBADEFFXXX"                       # leeres Feld gefuellt
    assert ziel.address_lines == ["Zielweg 1"]             # eine Anschrift, kein Mischen
    assert ziel.fixed_fields == {"bestellnummer": "Z", "ust_satz": "7"}
    assert ziel.currency == "CHF"
    assert ziel.name == "Muster Handels GmbH" and ziel.external_id == "4711"
    assert ziel.bestaetigt is True
    assert uebernommen == {"ustids": [USTID], "ibans": [IBAN], "domains": ["muster-handel.de"],
                           "fingerprint": ["muster", "handels"], "bic": "COBADEFFXXX",
                           "fixed_fields": {"ust_satz": "7"}}


def test_zusammenfuehren_adressen_leer_beim_ziel_bleibt_alle():
    ziel = _ziel()                                  # addresses leer = gilt fuer alle
    assert zusammenfuehren(ziel, _entwurf(addresses=["zuerich"])) .get("addresses") is None
    assert ziel.addresses == []

    ziel2 = _ziel(addresses=["berlin"])
    assert zusammenfuehren(ziel2, _entwurf(addresses=["zuerich", "berlin"]))["addresses"] == ["zuerich"]
    assert ziel2.addresses == ["berlin", "zuerich"]


def test_zusammenfuehren_ohne_neues_meldet_nichts():
    ziel = _ziel(ustids=[USTID], ibans=[IBAN], domains=["muster-handel.de"],
                 fingerprint=["muster", "handels"])
    assert zusammenfuehren(ziel, _entwurf()) == {}


# ---- Uebernehmen: Entwurf weg, Ziel bestaetigt, Audit mit Operator -------------
def test_uebernehmen_loescht_entwurf_und_fuellt_ziel(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(_ziel())
    store.save_supplier(_entwurf())

    erg = uebernehmen(store, "neu-de123456789", "muster-handels-gmbh", operator="aaron")

    assert store.supplier("neu-de123456789") is None
    ziel = store.supplier("muster-handels-gmbh")
    assert ziel.bestaetigt is True
    assert ziel.ustids == [USTID] and ziel.ibans == [IBAN]
    assert erg["ziel"].id == "muster-handels-gmbh"
    assert erg["uebernommen"]["ustids"] == [USTID]

    audit = [a for a in store.audit_log() if a["action"] == "lieferant_uebernommen"]
    assert len(audit) == 1
    assert audit[0]["operator"] == "aaron"
    assert audit[0]["detail"]["entwurf"] == "neu-de123456789"
    assert audit[0]["detail"]["ziel"] == "muster-handels-gmbh"
    assert audit[0]["detail"]["uebernommen"]["ibans"] == [IBAN]

    ev = [e for e in store.events() if e.get("type") == eventlog.TYPE_SUPPLIER]
    assert len(ev) == 1
    assert ev[0]["aktion"] == "uebernommen"
    assert ev[0]["supplier_id"] == "muster-handels-gmbh"
    assert ev[0]["entwurf"] == "neu-de123456789"
    assert ev[0]["operator"] == "aaron"
    # Nicht als "bestaetigt" eines neuen Lieferanten verbucht
    assert not [a for a in store.audit_log() if a["action"] == "lieferant_bestaetigt"]


def test_naechste_rechnung_findet_ziel_ohne_entwurfs_hold(tmp_path: Path):
    """Der Kern: nach der Uebernahme erkennt die Pipeline die Rechnung ueber
    die harte Kennung als den bestehenden Lieferanten -- kein neuer Entwurf,
    kein NEW_SUPPLIER_HOLD, keine Dublette."""
    store = Store(tmp_path / "data")
    store.save_supplier(_ziel())
    inv1 = make_invoice(tmp_path / "a.pdf", ustid=USTID, iban=IBAN, rechnungsnr="RE-1")
    res1 = process(inv1, store, create_draft_supplier=True)
    assert res1.supplier_id != "muster-handels-gmbh"
    assert store.supplier(res1.supplier_id).bestaetigt is False
    assert NEW_SUPPLIER_HOLD in res1.hold_reasons

    uebernehmen(store, res1.supplier_id, "muster-handels-gmbh", operator="aaron")

    inv2 = make_invoice(tmp_path / "b.pdf", ustid=USTID, iban=IBAN, rechnungsnr="RE-2")
    res2 = process(inv2, store, create_draft_supplier=True)
    assert res2.supplier_id == "muster-handels-gmbh"
    assert res2.supplier_name == "Muster Handels GmbH"
    assert NEW_SUPPLIER_HOLD not in res2.hold_reasons
    assert [s.id for s in store.suppliers()] == ["muster-handels-gmbh"]


# ---- Alles, was die supplier_id des Entwurfs gespeichert hat --------------------
def test_uebernehmen_haengt_rechnungen_und_protokolle_um(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(_ziel())
    inv = make_invoice(tmp_path / "a.pdf", ustid=USTID, iban=IBAN, rechnungsnr="RE-1")
    res = process(inv, store, create_draft_supplier=True)
    eid = res.supplier_id
    # Spuren wie im echten Betrieb: Ordner "Menschliche Bearbeitung" (dispatch,
    # vor dem Verarbeitungs-Log -- uebernommene Rechnungen legt er nicht ab),
    # Verarbeitungs-Log, Ereignis, Schatten, Vorlern-Gruppe, Lehrer-Protokoll
    dispatch(res, store)
    assert human.finde(store, res.sha256)[1]["lieferant"] == eid
    store.mark_processed({"file": "a.pdf", "sha256": res.sha256, "supplier_id": eid,
                          "supplier_name": res.supplier_name, "rechnungsnummer": "RE-1"})
    store.mark_processed({"file": "x.pdf", "sha256": "x" * 64, "supplier_id": "anderer",
                          "supplier_name": "Anderer", "rechnungsnummer": "RE-9"})
    eventlog.record(store, res, "aaron")
    shadow.record(store, res, "aaron")
    vorlernen._write_group(store, {"gruppe_id": "g1", "supplier_id": eid,
                                   "supplier_name": res.supplier_name, "variant": "standard"})
    (store.root / "lehrer.jsonl").write_text(
        json.dumps({"datei": "a.pdf", "lieferant": eid}) + "\n"
        + json.dumps({"datei": "x.pdf", "lieferant": "Anderer"}) + "\n", "utf-8")

    erg = uebernehmen(store, eid, "muster-handels-gmbh", operator="aaron")

    ziel_id, ziel_name = "muster-handels-gmbh", "Muster Handels GmbH"
    rows = store.processed()
    assert [r["supplier_id"] for r in rows] == [ziel_id, "anderer"]
    assert rows[0]["supplier_name"] == ziel_name
    assert store.seen_invoice(ziel_id, "RE-1")           # Dublettenpruefung kennt die Rechnung
    verarb = [e for e in store.events() if e.get("type") == eventlog.TYPE_PROCESSED]
    assert verarb and all(e["supplier_id"] == ziel_id and e["supplier_name"] == ziel_name
                          for e in verarb)
    assert all(r["supplier_id"] == ziel_id for r in store.shadow_log())
    side = human.finde(store, res.sha256)[1]
    assert side["lieferant"] == ziel_id and side["lieferant_name"] == ziel_name
    g = vorlernen.get_group(store, "g1")
    assert g["supplier_id"] == ziel_id and g["supplier_name"] == ziel_name
    lehrer = [json.loads(z) for z in (store.root / "lehrer.jsonl").read_text("utf-8").splitlines()]
    assert [r["lieferant"] for r in lehrer] == [ziel_name, "Anderer"]
    assert erg["umgehaengt"]["processed.jsonl"] == 1
    assert erg["umgehaengt"]["menschlich"] == 1
    assert erg["umgehaengt"]["vorlernen"] == 1
    assert erg["umgehaengt"]["lehrer.jsonl"] == 1
    # audit.jsonl bleibt append-only: der Entwurf steht dort weiterhin
    assert any(a["detail"].get("id") == eid or a["detail"].get("entwurf") == eid
               for a in store.audit_log())


def test_uebernehmen_haengt_vorlagen_um(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(_ziel())
    store.save_supplier(_entwurf())
    store.save_template(Template(id="neu-de123456789__standard", supplier_id="neu-de123456789",
                                 variant="standard", confirmations=2))
    store.save_template(Template(id="neu-de123456789__zuerich", supplier_id="neu-de123456789",
                                 variant="zuerich"))
    store.save_template(Template(id="muster-handels-gmbh__zuerich", supplier_id="muster-handels-gmbh",
                                 variant="zuerich", confirmations=5))

    erg = uebernehmen(store, "neu-de123456789", "muster-handels-gmbh", operator="aaron")

    ids = sorted(t.id for t in store.templates_for("muster-handels-gmbh"))
    assert ids == ["muster-handels-gmbh__standard", "muster-handels-gmbh__zuerich",
                   "muster-handels-gmbh__zuerich__v2"]
    assert store.template("muster-handels-gmbh__standard").confirmations == 2
    assert store.template("muster-handels-gmbh__zuerich").confirmations == 5   # Ziel-Vorlage bleibt
    assert store.templates_for("neu-de123456789") == []
    assert store.template("neu-de123456789__standard") is None
    assert dict(erg["vorlagen"]) == {"neu-de123456789__standard": "muster-handels-gmbh__standard",
                                     "neu-de123456789__zuerich": "muster-handels-gmbh__zuerich__v2"}


# ---- Verweigert ----------------------------------------------------------------
def test_uebernehmen_verweigert_falsche_faelle(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(_ziel())
    store.save_supplier(_entwurf())
    store.save_supplier(Supplier(id="entwurf2", name="Unbekannt (2)", bestaetigt=False))

    with pytest.raises(KeyError):
        uebernehmen(store, "gibts-nicht", "muster-handels-gmbh")
    with pytest.raises(KeyError):
        uebernehmen(store, "neu-de123456789", "gibts-nicht")
    with pytest.raises(ValueError):          # Ziel ist selbst ein Entwurf
        uebernehmen(store, "neu-de123456789", "entwurf2")
    with pytest.raises(ValueError):          # bestaetigter Lieferant ist kein Entwurf
        uebernehmen(store, "muster-handels-gmbh", "entwurf2")
    with pytest.raises(ValueError):          # derselbe
        uebernehmen(store, "neu-de123456789", "neu-de123456789")
    # nichts passiert
    assert store.supplier("neu-de123456789") is not None
    assert store.supplier("muster-handels-gmbh").ustids == []
    assert not [a for a in store.audit_log() if a["action"] == "lieferant_uebernommen"]


def test_uebernehmen_verweigert_dummy(tmp_path: Path):
    from rechnung.dummy import DUMMY_ID

    store = Store(tmp_path / "data")
    store.save_supplier(_entwurf())
    with pytest.raises(ValueError):
        uebernehmen(store, "neu-de123456789", DUMMY_ID)
    with pytest.raises(ValueError):
        uebernehmen(store, DUMMY_ID, "neu-de123456789")


def test_pipeline_ruft_uebernehmen_nie_selbst(tmp_path: Path):
    """Sicherheitsregel: Stammdaten bestaetigt ein Mensch. Auch wenn der Name
    des bestehenden Lieferanten auf der Rechnung steht, bleibt der Entwurf
    bestehen, bis jemand klickt."""
    store = Store(tmp_path / "data")
    store.save_supplier(_ziel())
    inv = make_invoice(tmp_path / "a.pdf", lieferant="Muster Handels GmbH",
                       ustid=USTID, iban=IBAN)
    res = process(inv, store, create_draft_supplier=True)
    assert store.supplier(res.supplier_id).bestaetigt is False
    assert len(store.suppliers()) == 2
    assert not [a for a in store.audit_log() if a["action"] == entwurf_uebernahme.AUDIT_AKTION]
    # Kein Modul ausser dem Endpunkt importiert die Uebernahme (Docstrings
    # duerfen sie erwaehnen, Code nicht aufrufen).
    src = Path(entwurf_uebernahme.__file__).parent
    for py in src.glob("*.py"):
        if py.name == "entwurf_uebernahme.py":
            continue
        text = py.read_text("utf-8")
        assert "import entwurf_uebernahme" not in text, py.name
        assert "from .entwurf_uebernahme" not in text, py.name
    assert "import entwurf_uebernahme" in (src / "web" / "server.py").read_text("utf-8")


# ---- Endpunkt -------------------------------------------------------------------
def _client(tmp_path: Path):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    app = server_mod.create_app(store, samples)
    return TestClient(app), store


def test_api_uebernehmen(tmp_path: Path):
    client, store = _client(tmp_path)
    store.save_supplier(_ziel())
    store.save_supplier(_entwurf())

    r = client.post("/api/suppliers/neu-de123456789/uebernehmen", json={"ziel_id": "muster-handels-gmbh"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["supplier"]["id"] == "muster-handels-gmbh"
    assert body["supplier"]["ustids"] == [USTID]
    assert body["uebernommen"]["ibans"] == [IBAN]
    assert store.supplier("neu-de123456789") is None
    ids = [s["id"] for s in client.get("/api/suppliers").json()]
    assert ids == ["muster-handels-gmbh"]


def test_api_uebernehmen_fehler(tmp_path: Path):
    client, store = _client(tmp_path)
    store.save_supplier(_ziel())
    store.save_supplier(_entwurf())

    assert client.post("/api/suppliers/gibts-nicht/uebernehmen",
                       json={"ziel_id": "muster-handels-gmbh"}).status_code == 404
    assert client.post("/api/suppliers/neu-de123456789/uebernehmen",
                       json={"ziel_id": "gibts-nicht"}).status_code == 404
    assert client.post("/api/suppliers/muster-handels-gmbh/uebernehmen",
                       json={"ziel_id": "neu-de123456789"}).status_code == 409
    assert client.post("/api/suppliers/neu-de123456789/uebernehmen", json={}).status_code == 422
    assert store.supplier("neu-de123456789") is not None
