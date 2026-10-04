"""API-Tests fuer server.py: Merge-by-Name bei /api/template, Ablösen einer
manuellen Korrektur durch einen frisch trainierten Anker, und dass der
semantische Fallback bereits in /api/invoice sichtbar ist (nicht erst beim
Bestätigen) -- siehe Konversation vom 2026-09-18."""

from pathlib import Path

import pytest

from rechnung import pdf as pdfmod
from rechnung.store import Store
from rechnung.template import FieldResult
from rechnung.textnorm import norm
from rechnung.web import server as server_mod

reportlab = pytest.importorskip("reportlab")
fastapi_testclient = pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402


def _client(tmp_path: Path):
    from rechnung.synth import make_invoice

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    inv = make_invoice(samples / "a.pdf", rechnungsnr="RE-2026-00099", netto=800.0)
    app = server_mod.create_app(store, samples)
    return TestClient(app), store, inv


def _word_index(words: list[dict], contains: str) -> int:
    target = norm(contains)
    for w in words:
        if target in norm(w["text"]):
            return w["i"]
    raise AssertionError(f"Wort mit '{contains}' nicht gefunden")


def test_template_save_merges_instead_of_replacing(tmp_path: Path):
    client, store, inv = _client(tmp_path)
    r = client.get("/api/invoice", params={"file": "a.pdf"})
    words = r.json()["pages"][0]["words"]

    rn_anchor = _word_index(words, "rechnungsnummer")
    rn_val = _word_index(words, "RE-2026-00099")
    netto_anchor = _word_index(words, "nettobetrag")
    netto_val = _word_index(words, "800.00")

    body = {
        "file": "a.pdf", "supplier_id": "muster", "supplier_name": "Muster",
        "variant": "standard",
        "fields": [
            {"name": "rechnungsnummer", "value_type": "number", "required": True,
             "anchor_word": rn_anchor, "value_words": [rn_val], "page": 0},
        ],
        "line_items": None,
    }
    r1 = client.post("/api/template", json=body)
    assert r1.status_code == 200
    tid = r1.json()["template_id"]
    assert {f.name for f in store.template(tid).fields} == {"rechnungsnummer"}

    # Zweiter Aufruf traegt nur "netto" nach -- "rechnungsnummer" darf dabei
    # nicht verloren gehen (das war der Bug: /api/template hat vorher die
    # komplette Feldliste ersetzt statt zu mergen).
    body2 = {
        "file": "a.pdf", "supplier_id": "muster", "supplier_name": "Muster",
        "variant": "standard",
        "fields": [
            {"name": "netto", "value_type": "amount", "required": True,
             "anchor_word": netto_anchor, "value_words": [netto_val], "page": 0},
        ],
        "line_items": None,
    }
    r2 = client.post("/api/template", json=body2)
    assert r2.status_code == 200
    names = {f.name for f in store.template(tid).fields}
    assert names == {"rechnungsnummer", "netto"}, names


def test_fresh_anchor_replaces_manual_correction(tmp_path: Path):
    client, store, inv = _client(tmp_path)
    doc = pdfmod.load(inv)

    client.post("/api/correction", json={
        "file": "a.pdf", "field": "bestellnummer", "value": "PO-HANDGETIPPT",
    })
    assert store.corrections(doc.sha256).get("bestellnummer") == "PO-HANDGETIPPT"

    r = client.get("/api/invoice", params={"file": "a.pdf"})
    words = r.json()["pages"][0]["words"]
    # Kein echtes "bestellnummer"-Wort auf der Testrechnung -- irgendein
    # Wort reicht, es geht nur um den Mechanismus (Anker fuer dieses Feld
    # erzeugen), nicht um einen inhaltlich sinnvollen Treffer.
    any_word = words[0]["i"]

    r2 = client.post("/api/template", json={
        "file": "a.pdf", "supplier_id": "muster", "supplier_name": "Muster",
        "variant": "standard",
        "fields": [
            {"name": "bestellnummer", "value_type": "text", "required": False,
             "anchor_word": None, "value_words": [any_word], "page": 0},
        ],
        "line_items": None,
    })
    assert r2.status_code == 200
    assert "bestellnummer" not in store.corrections(doc.sha256)


def test_scan_endpoint_skips_semantic_fallback(tmp_path: Path, monkeypatch):
    """/api/scan verarbeitet ggf. viele offene Rechnungen auf einmal --
    der LLM-Fallback (30-50s pro Datei mit Luecken) darf dort nicht laufen,
    weil er wegen der gedeckelten Konfidenz (pipeline.CONF_MIN) den
    auto/review-Status ohnehin nie aendern kann. Siehe Konversation vom
    2026-09-18 (Effizienz-Anfrage)."""
    client, store, inv = _client(tmp_path)

    def boom(*a, **k):
        raise AssertionError("semantic.extract() darf in /api/scan nicht aufgerufen werden")

    monkeypatch.setattr(server_mod.semantic, "extract", boom)

    r = client.get("/api/scan")
    assert r.status_code == 200
    assert len(r.json()["results"]) == 1


def test_invoice_endpoint_prefills_library_fields_without_template(tmp_path: Path):
    """Ein per Bulk-Import bekannter Lieferant (IBAN/USt-IdNr passen), fuer den
    aber noch kein Layout-Template trainiert ist, soll IBAN/USt-IdNr/Adresse
    schon in der allerersten Rechnung im Review sehen -- nicht erst nach dem
    ersten manuellen Anlernen. Siehe Konversation vom 2026-09-18."""
    from rechnung.identify import supplier_from_doc

    client, store, inv = _client(tmp_path)
    doc = pdfmod.load(inv)

    sup = supplier_from_doc(doc, "muster", "Muster Handels GmbH")
    sup.address_lines = ["Musterstr. 1", "50667 Koeln"]
    store.save_supplier(sup)

    r = client.get("/api/invoice", params={"file": "a.pdf"})
    body = r.json()
    assert body["template_id"] is None  # bewusst: noch kein Template trainiert
    current = body["current"]
    assert current["adresse"]["source"] == "library"
    assert current["adresse"]["value"] == "Musterstr. 1, 50667 Koeln"
    assert current["iban"]["source"] == "library"


def test_invoice_endpoint_exposes_identifiers_for_unknown_supplier(tmp_path: Path):
    """Grundlage fuer den 'Neuer Lieferant erkannt'-Kasten im Frontend: auch
    ohne Treffer muss /api/invoice die aus dem PDF gelesenen Kennungen
    (USt-IdNr/IBAN/Domain) mitliefern, sonst gibt es nichts zum Uebernehmen."""
    client, store, inv = _client(tmp_path)
    r = client.get("/api/invoice", params={"file": "a.pdf"})
    body = r.json()
    assert body["match"] is None
    assert body["identifiers"]["ustids"] == ["DE123456789"]


def test_confirming_new_supplier_makes_it_recognized_next_time(tmp_path: Path):
    """Kern des 'Kartei'-Wunsches: Lieferant unbekannt -> Mensch klickt
    'Als neuen Lieferanten anlegen' (Frontend ruft dafuer /api/suppliers mit
    den aus der Rechnung gelesenen Kennungen auf) -> ab sofort erkannt,
    ganz ohne dass vorher ein Layout-Template angelernt wurde. Nichts davon
    darf automatisch/ungefragt passieren -- deshalb ein eigener POST, kein
    Seiteneffekt von GET /api/invoice."""
    client, store, inv = _client(tmp_path)
    r = client.get("/api/invoice", params={"file": "a.pdf"})
    ids = r.json()["identifiers"]
    assert r.json()["match"] is None

    r2 = client.post("/api/suppliers", json={
        "id": "muster", "name": "Muster Handels GmbH",
        "ustids": ids["ustids"], "ibans": ids["ibans"], "domains": ids["domains"],
        "currency": ids.get("currency", "EUR"),
    })
    assert r2.status_code == 200

    r3 = client.get("/api/invoice", params={"file": "a.pdf"})
    assert r3.json()["match"] is not None
    assert r3.json()["match"]["id"] == "muster"
    # Kein Template noetig, damit IBAN/USt-IdNr/Adresse als Stammdaten
    # schon jetzt vorbefuellt waeren (siehe test_invoice_endpoint_
    # prefills_library_fields_without_template) -- hier reicht die reine
    # Erkennung.
    assert r3.json()["template_id"] is None


def test_invoice_endpoint_surfaces_semantic_fallback(tmp_path: Path, monkeypatch):
    """Der LLM-Vorschlag muss schon in /api/invoice auftauchen (Review vor
    dem Bestaetigen), nicht erst bei /api/confirm -- sonst sieht der Mensch
    ihn nie, bevor er bestaetigt (widerspraeche §7c)."""
    client, store, inv = _client(tmp_path)
    doc = pdfmod.load(inv)

    from rechnung.identify import supplier_from_doc
    from rechnung.template import Anchor, FieldSpec, Template

    sup = supplier_from_doc(doc, "muster", "Muster Handels GmbH")
    store.save_supplier(sup)
    tpl = Template(id="muster__standard", supplier_id="muster")
    tpl.fields = [
        FieldSpec("rechnungsnummer", "number", True,
                 anchor=Anchor("rechnungsnummer", 0), rel=(60, -4, 240, 12)),
        # "bestellnummer" bewusst nicht angelernt -- Luecke fuer den Fallback
    ]
    store.save_template(tpl)

    def fake_extract(text, field_names, hints, rejected=None):
        assert "bestellnummer" in field_names
        return {"bestellnummer": FieldResult("bestellnummer", "PO-42", "PO-42", 0.6, "llm")}

    monkeypatch.setattr(server_mod.semantic, "extract", fake_extract)

    r = client.get("/api/invoice", params={"file": "a.pdf"})
    current = r.json()["current"]
    assert current["bestellnummer"]["source"] == "llm"
    assert current["bestellnummer"]["value"] == "PO-42"
