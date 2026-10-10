"""Teil B: Positionsmodus je Lieferant (Entscheidung Aaron 10.10.2026).

Normalfall "einzeln" (unveraendert); "eine_position" bildet die Positionen
aus der USt-Aufstellung (eine Zeile je Steuersatz, nie zusammengefasst);
"zusammenfassung" liest/lernt die Uebersichtstabelle am Rechnungsende wie
eine normale Positionstabelle (dafuer ist nichts Besonderes noetig, nur die
KI bekommt ohne Vorlage gezielt nur die Schlussseiten)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import pdf as pdfmod
from rechnung import positionen
from rechnung.identify import POSITIONSMODUS_EINE_POSITION, POSITIONSMODUS_EINZELN, Supplier
from rechnung.pipeline import _LANGER_BELEG_HINWEIS, process, set_positionsmodus
from rechnung.store import Store
from rechnung.template import Template, build_field_spec

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice, make_multipage_invoice  # noqa: E402


def _word(words, text):
    return next(w for w in words if w.text == text)


# ---- positionen.aus_summen --------------------------------------------

def test_aus_summen_einzelsatz():
    rows = [{"satz": 19.0, "netto": 1000.0, "ust": 190.0, "brutto": 1190.0}]
    out = positionen.aus_summen(rows, "Strom", "EUR")
    assert len(out) == 1
    it = out[0]
    assert it["beschreibung"] == "Strom"
    assert it["menge"] == 1
    assert it["einzelpreis_netto"] == 1000.0
    assert it["gesamt_netto"] == 1000.0
    assert it["mwst_satz"] == 19.0
    assert it["waehrung"] == "EUR"
    assert all(v == positionen.HERKUNFT_EINE_POSITION for v in it["_herkunft"].values())


def test_aus_summen_mischsaetze_nie_zusammengefasst():
    rows = [{"satz": 19.0, "netto": 300.0, "ust": 57.0},
           {"satz": 7.0, "netto": 500.0, "ust": 35.0}]
    out = positionen.aus_summen(rows, "Strom", "EUR")
    assert len(out) == 2
    assert [it["mwst_satz"] for it in out] == [7.0, 19.0]   # sortiert
    assert {it["gesamt_netto"] for it in out} == {500.0, 300.0}


def test_aus_summen_zeile_ohne_netto_wird_ausgelassen():
    rows = [{"satz": 19.0, "netto": None, "ust": None}]
    assert positionen.aus_summen(rows, "Strom", "EUR") == []


# ---- pipeline: Modus "eine_position" ----------------------------------

def _template_mit_brutto_und_satz(doc, supplier_id: str) -> Template:
    """Minimale Vorlage (nur Brutto + USt-Satz als Anker, wie
    test_herkunft.py) -- reicht, damit ``rechnen.rechne`` Netto/USt
    ergaenzt und damit eine USt-Aufstellung entsteht, aus der
    ``positionen.aus_summen`` im Modus 'eine_position' die Positionszeile(n)
    bilden kann."""
    words = doc.all_words()
    pg = doc.pages[0]
    brutto_spec = build_field_spec("brutto", "amount", True, _word(words, "Gesamtbetrag"),
                                   [_word(words, "1190.00")], pg.width, pg.height)
    satz_spec = build_field_spec("ust_satz", "amount", False, _word(words, "Umsatzsteuer"),
                                 [_word(words, "19%")], pg.width, pg.height)
    return Template(id=f"{supplier_id}__standard", supplier_id=supplier_id,
                    fields=[brutto_spec, satz_spec])


def test_pipeline_eine_position_bildet_zeile_aus_summen(tmp_path: Path):
    store = Store(tmp_path / "data")
    sup = Supplier(id="stadtwerke", name="Stadtwerke Musterstadt",
                   ustids=["DE123456789"], ibans=["DE89370400440532013000"],
                   positionsmodus=POSITIONSMODUS_EINE_POSITION,
                   positionsbezeichnung="Strom", positionsmodus_gesetzt=True)
    store.save_supplier(sup)
    p = make_invoice(tmp_path / "a.pdf", lieferant="Stadtwerke Musterstadt",
                     ustid="DE123456789", iban="DE89370400440532013000",
                     netto=1000.0, satz=19)
    store.save_template(_template_mit_brutto_und_satz(pdfmod.load(p), "stadtwerke"))

    res = process(p, store, use_llm=False)

    assert res.supplier_id == "stadtwerke"
    assert res.fields["brutto"].value == 1190.0
    assert len(res.line_items) == 1
    it = res.line_items[0]
    assert it["beschreibung"] == "Strom"
    assert it["mwst_satz"] == 19
    assert it["gesamt_netto"] == 1000.0
    assert it["_herkunft"]["gesamt_netto"] == positionen.HERKUNFT_EINE_POSITION


def test_pipeline_einzeln_bleibt_unveraendert(tmp_path: Path):
    # Normalfall: ohne Modus-Aenderung (Standard "einzeln") bleibt das
    # bisherige Verhalten -- keine synthetischen Positionen, auch wenn (wie
    # hier) sonst dieselbe Vorlage greift.
    store = Store(tmp_path / "data")
    sup = Supplier(id="stadtwerke", name="Stadtwerke Musterstadt",
                   ustids=["DE123456789"], ibans=["DE89370400440532013000"])
    assert sup.positionsmodus == POSITIONSMODUS_EINZELN
    store.save_supplier(sup)
    p = make_invoice(tmp_path / "a.pdf", lieferant="Stadtwerke Musterstadt",
                     ustid="DE123456789", iban="DE89370400440532013000",
                     netto=1000.0, satz=19)
    store.save_template(_template_mit_brutto_und_satz(pdfmod.load(p), "stadtwerke"))

    res = process(p, store, use_llm=False)
    assert res.fields["brutto"].value == 1190.0
    assert all(it.get("_herkunft", {}).get("gesamt_netto") != positionen.HERKUNFT_EINE_POSITION
              for it in res.line_items)


# ---- pipeline: Hinweis bei langem Beleg --------------------------------

def test_hinweis_bei_langem_beleg_ohne_gesetzten_modus(tmp_path: Path):
    store = Store(tmp_path / "data")
    soll = make_multipage_invoice(tmp_path / "lang.pdf", seiten=8, ustid="DE123456789",
                                  iban="DE89370400440532013000")
    res = process(soll["path"], store, use_llm=False, create_draft_supplier=True)
    assert _LANGER_BELEG_HINWEIS in res.hinweise


def test_kein_hinweis_wenn_modus_bereits_gesetzt(tmp_path: Path):
    store = Store(tmp_path / "data")
    sup = Supplier(id="stadtwerke", name="Muster Handels GmbH",
                   ustids=["DE123456789"], ibans=["DE89370400440532013000"],
                   positionsmodus_gesetzt=True)
    store.save_supplier(sup)
    soll = make_multipage_invoice(tmp_path / "lang.pdf", seiten=8, ustid="DE123456789",
                                  iban="DE89370400440532013000")
    res = process(soll["path"], store, use_llm=False)
    assert _LANGER_BELEG_HINWEIS not in res.hinweise


def test_kein_hinweis_bei_kurzem_beleg(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf", netto=1000.0, satz=19)
    res = process(inv, store, use_llm=False, create_draft_supplier=True)
    assert _LANGER_BELEG_HINWEIS not in res.hinweise


# ---- set_positionsmodus / Audit ----------------------------------------

def test_set_positionsmodus_speichert_und_markiert_gesetzt(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="stadtwerke", name="Stadtwerke Musterstadt"))

    sup = set_positionsmodus(store, "stadtwerke", POSITIONSMODUS_EINE_POSITION, "Strom", "tester")

    assert sup.positionsmodus == POSITIONSMODUS_EINE_POSITION
    assert sup.positionsbezeichnung == "Strom"
    assert sup.positionsmodus_gesetzt is True
    reloaded = store.supplier("stadtwerke")
    assert reloaded.positionsmodus == POSITIONSMODUS_EINE_POSITION
    audit = store.audit_log()
    assert any(a["action"] == "lieferant_positionsmodus" for a in audit)


def test_set_positionsmodus_bezeichnung_verworfen_wenn_nicht_eine_position(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="stadtwerke", name="Stadtwerke Musterstadt"))
    sup = set_positionsmodus(store, "stadtwerke", "zusammenfassung", "Strom")
    assert sup.positionsbezeichnung == ""


def test_set_positionsmodus_unbekannter_lieferant_wirft(tmp_path: Path):
    store = Store(tmp_path / "data")
    with pytest.raises(ValueError):
        set_positionsmodus(store, "nicht-da", POSITIONSMODUS_EINZELN)


def test_set_positionsmodus_ungueltiger_modus_wirft(tmp_path: Path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="stadtwerke", name="Stadtwerke Musterstadt"))
    with pytest.raises(ValueError):
        set_positionsmodus(store, "stadtwerke", "quatsch")


# ---- API-Endpunkt -------------------------------------------------------

def test_api_positionsmodus_endpoint(tmp_path: Path):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="stadtwerke", name="Stadtwerke Musterstadt"))
    samples = tmp_path / "s"
    samples.mkdir()
    client = TestClient(server_mod.create_app(store, samples))

    r = client.post("/api/suppliers/stadtwerke/positionsmodus",
                    json={"modus": "eine_position", "bezeichnung": "Strom"})
    assert r.status_code == 200
    assert r.json()["supplier"]["positionsmodus"] == "eine_position"
    assert r.json()["supplier"]["positionsbezeichnung"] == "Strom"

    listed = client.get("/api/suppliers").json()
    sup = next(s for s in listed if s["id"] == "stadtwerke")
    assert sup["positionsmodus"] == "eine_position"
    assert sup["positionsmodus_gesetzt"] is True


def test_api_invoice_live_preview_zeigt_eine_position(tmp_path: Path):
    """Der Live-Editor (/api/invoice) baut Positionen bei vorhandener
    Vorlage separat zusammen (eigener Code in server.py, nicht
    pipeline.process) -- auch dort muss 'eine_position' greifen, sonst
    sieht der Mensch beim Pruefen etwas anderes als das, was er bestaetigt."""
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    sup = Supplier(id="stadtwerke", name="Stadtwerke Musterstadt",
                   ustids=["DE123456789"], ibans=["DE89370400440532013000"],
                   positionsmodus=POSITIONSMODUS_EINE_POSITION,
                   positionsbezeichnung="Strom", positionsmodus_gesetzt=True)
    store.save_supplier(sup)
    samples = tmp_path / "s"
    samples.mkdir()
    p = make_invoice(samples / "a.pdf", lieferant="Stadtwerke Musterstadt",
                     ustid="DE123456789", iban="DE89370400440532013000",
                     netto=1000.0, satz=19)
    store.save_template(_template_mit_brutto_und_satz(pdfmod.load(p), "stadtwerke"))

    client = TestClient(server_mod.create_app(store, samples))
    d = client.get("/api/invoice", params={"file": "a.pdf"}).json()

    assert d["template_id"] == "stadtwerke__standard"
    assert len(d["line_items"]) == 1
    it = d["line_items"][0]
    assert it["beschreibung"] == "Strom" and it["mwst_satz"] == 19


def test_api_positionsmodus_ungueltiger_wert(tmp_path: Path):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="stadtwerke", name="Stadtwerke Musterstadt"))
    samples = tmp_path / "s"
    samples.mkdir()
    client = TestClient(server_mod.create_app(store, samples))

    r = client.post("/api/suppliers/stadtwerke/positionsmodus", json={"modus": "quatsch"})
    assert r.status_code == 400
