"""Selbstlernen 2, Baustein 3: Herkunft sichtbar.

UI (``/api/invoice``), CSV (``store.write_output``) und JobRouter-Payload
zeigen je Feld/Zelle die Herkunft (gelesen/errechnet/kopf/korrigiert) --
Baustein-3-Tests fuer die eigentliche Oberflaeche sind in
tests/test_jobrouter_setup.py (Payload) und tests/test_pipeline.py/
tests/test_positionen.py (CSV); hier die Live-Editor-Integration
(``/api/invoice`` wendet dieselbe Rechenstufe wie die Pipeline an, auch
fuer eine bereits trainierte Vorlage)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import pdf as pdfmod
from rechnung.identify import Supplier
from rechnung.store import Store
from rechnung.template import Template, build_field_spec

pytest.importorskip("reportlab")
pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402

from rechnung.synth import make_invoice  # noqa: E402
from rechnung.web import server as server_mod  # noqa: E402


def _client(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="muster", name="Muster Handels GmbH",
                                 ustids=["DE123456789"], ibans=["DE89370400440532013000"]))
    samples = tmp_path / "s"
    samples.mkdir()
    return TestClient(server_mod.create_app(store, samples)), store, samples


def _word(words, text):
    return next(w for w in words if w.text == text)


def test_invoice_endpoint_zeigt_errechnetes_netto_mit_herkunft(tmp_path: Path):
    """Vorlage kennt nur Brutto + USt-Satz (Gesamtbetrag/Umsatzsteuer-Label)
    -- Netto/USt-Betrag fehlen als Anker, die Rechenstufe ergaenzt sie auch
    schon im Live-Editor (nicht erst beim Bestaetigen)."""
    client, store, samples = _client(tmp_path)
    p = make_invoice(samples / "a.pdf", rechnungsnr="RE-2026-00042", netto=1000.0, satz=19)
    doc = pdfmod.load(p)
    words = doc.all_words()
    pg = doc.pages[0]

    brutto_spec = build_field_spec("brutto", "amount", True, _word(words, "Gesamtbetrag"),
                                   [_word(words, "1190.00")], pg.width, pg.height)
    satz_spec = build_field_spec("ust_satz", "amount", False, _word(words, "Umsatzsteuer"),
                                 [_word(words, "19%")], pg.width, pg.height)
    tpl = Template(id="muster__standard", supplier_id="muster",
                   fields=[brutto_spec, satz_spec])
    store.save_template(tpl)

    d = client.get("/api/invoice", params={"file": "a.pdf"}).json()
    assert d["template_id"] == "muster__standard"
    cur = d["current"]
    assert cur["brutto"]["value"] == 1190.0 and cur["brutto"]["herkunft"] == "gelesen"
    assert cur["netto"]["value"] == 1000.0
    assert cur["netto"]["source"] == "errechnet"
    assert cur["netto"]["herkunft"] == "errechnet"
    assert cur["ust_betrag"]["value"] == 190.0
    assert cur["ust_betrag"]["herkunft"] == "errechnet"
    # Formel im Rohwert fuer den Tooltip (SPEC Baustein 3)
    assert "1190.00" in cur["netto"]["raw"]


def test_manuelle_korrektur_ueberschreibt_herkunft_mit_korrigiert(tmp_path: Path):
    client, store, samples = _client(tmp_path)
    make_invoice(samples / "a.pdf", rechnungsnr="RE-2026-00043", netto=1000.0, satz=19)
    client.post("/api/correction", json={"file": "a.pdf", "field": "netto", "value": "1,00"})
    d = client.get("/api/invoice", params={"file": "a.pdf"}).json()
    assert d["current"]["netto"]["source"] == "manual"
    assert d["current"]["netto"]["herkunft"] == "korrigiert"
