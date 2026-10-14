"""SPEC Baustein 5 "Rangfolge" (Aaron 12.10.2026, Grundsatz: "Wissen ueber
DENSELBEN Lieferanten wiegt staerker als uebertragenes"): eigene (ggf.
veraltete) Spec dieses Lieferanten vor Layout-Familie (aehnlicher
Lieferant) vor eingebauten Schluesselwoertern/Spaltenwoerterbuch --
zentral in ``pipeline._tabelle_rangfolge``."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import learn, pipeline
from rechnung import pdf as pdfmod
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import NO_TEMPLATE, process
from rechnung.store import Store
from rechnung.template import Template

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def _gelernte_spec(doc):
    """``learn_line_items`` erwartet bereits KANONISCHE Spaltennamen (wie
    eine bestaetigte Position sie hat) -- anders als ``synth.make_invoice``s
    eigenes ``items``-Schema (``einzelpreis``/``mwst``)."""
    rows = [{"beschreibung": "Ersatzteile", "menge": 2, "einzelpreis_netto": 100.0,
            "gesamt_netto": 200.0, "mwst_satz": 19.0, "waehrung": "EUR"}]
    return rows, learn.learn_line_items(doc, rows)


def test_eigene_veraltete_spec_gewinnt_gegen_schluesselwoerter(tmp_path: Path):
    src = make_invoice(tmp_path / "src.pdf",
                       items=[{"beschreibung": "Ersatzteile", "menge": 2,
                              "einzelpreis": 100.0, "mwst": 19, "waehrung": "EUR"}],
                       waehrung="EUR")
    doc = pdfmod.load(src)
    rows, spec = _gelernte_spec(doc)
    assert spec is not None

    store = Store(tmp_path / "data")
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    store.save_template(Template(id="muster__standard", supplier_id="muster",
                                 line_items=spec, stale=True))

    nxt = make_invoice(tmp_path / "nxt.pdf",
                       items=[{"beschreibung": "Montagezubehoer", "menge": 3,
                              "einzelpreis": 50.0, "mwst": 19, "waehrung": "EUR"}],
                       rechnungsnr="RE-2026-00099", waehrung="EUR")
    res = process(nxt, store, use_llm=False)
    assert res.status == NO_TEMPLATE
    assert res.line_items
    # Herkunft: die (veraltete) eigene Spec hat entschieden, nicht der
    # allgemeine Fallback (tabelle.extract waere "tabelle").
    assert res.line_items[0]["_quelle"] == "tabelle_eigene_spec"
    assert any("frueher gelernter Vorlage" in h for h in res.hinweise)
    assert round(sum(it["gesamt_netto"] for it in res.line_items), 2) == 150.0


def test_layout_familie_schlaegt_eingebaute_spec_vor(tmp_path: Path):
    """``_tabelle_rangfolge`` direkt: ein unbekannter Lieferant (kein
    Treffer in der Bibliothek) mit einer zu einem gelernten Lieferanten
    AEHNLICHEN Kopfzeile (Fingerprint-Jaccard) bekommt dessen Spec als
    Vorschlag -- nur wenn sie die Rechen-Pruefung besteht."""
    quelle = make_invoice(tmp_path / "quelle.pdf",
                          items=[{"beschreibung": "Ersatzteile", "menge": 2,
                                 "einzelpreis": 100.0, "mwst": 19, "waehrung": "EUR"}],
                          waehrung="EUR")
    doc_a = pdfmod.load(quelle)
    _rows, spec = _gelernte_spec(doc_a)
    assert spec is not None

    store = Store(tmp_path / "data")
    sup_a = supplier_from_doc(doc_a, "lieferant-a", "Lieferant A GmbH")
    store.save_supplier(sup_a)
    store.save_template(Template(id="lieferant-a__standard", supplier_id="lieferant-a",
                                 line_items=spec))

    unbekannt = make_invoice(tmp_path / "unbekannt.pdf",
                             items=[{"beschreibung": "Montagezubehoer", "menge": 4,
                                    "einzelpreis": 25.0, "mwst": 19, "waehrung": "EUR"}],
                             rechnungsnr="RE-2026-00123", waehrung="EUR")
    doc_b = pdfmod.load(unbekannt)
    # Kopf-Fingerprint von B kuenstlich mit A ueberlappen lassen (SPEC:
    # "sehr aehnliche Kopfzeile/Spaltenlage" -- hier direkt am Lieferanten-
    # Fingerprint simuliert statt zwei Rechnungen mit identischem Briefkopf
    # zu bauen, die identify() sonst selbst schon zuordnen wuerde).
    from rechnung.identify import extract_identifiers
    fp_b = extract_identifiers(doc_b)["fingerprint"]
    sup_a.fingerprint = sorted(fp_b)
    store.save_supplier(sup_a)

    items, herkunft = pipeline._tabelle_rangfolge(doc_b, None, store, None, None, doc_b.text, None)
    assert items is not None
    assert items[0]["_quelle"] == "tabelle_layout_familie"
    assert "Lieferant A GmbH" in herkunft
    assert round(sum(it["gesamt_netto"] for it in items), 2) == 100.0


def test_layout_familie_wird_abgelehnt_wenn_pruefung_nicht_aufgeht(tmp_path: Path):
    """Eine fremde Spec, die auf dieser Rechnung eine falsche/unplausible
    Tabelle liefern wuerde (hier: Spalten vertauscht -- Menge/MwSt-Satz
    mit Betragsspalten verwechselt), darf nicht als Vorschlag durchgehen --
    dann bleibt es beim eingebauten Fallback (Schluesselwoerter), der auf
    DIESER Rechnung unabhaengig funktioniert."""
    from rechnung.identify import extract_identifiers
    from rechnung.template import Anchor, Column, LineItemSpec

    unbekannt = make_invoice(tmp_path / "unbekannt.pdf",
                             items=[{"beschreibung": "Montagezubehoer", "menge": 4,
                                    "einzelpreis": 25.0, "mwst": 19, "waehrung": "EUR"}],
                             rechnungsnr="RE-2026-00321", waehrung="EUR")
    doc_b = pdfmod.load(unbekannt)

    # Eine absichtlich falsch gebaute Spec: "menge" zeigt auf die MwSt-Satz-
    # Spalte und umgekehrt -- liefert auf dieser Rechnung eine Zeile, die
    # die Rechen-Pruefung (menge * einzelpreis = gesamt) nicht erfuellt.
    kaputt = LineItemSpec(
        header_anchor=Anchor(text="beschreibung", page=0),
        columns=[
            Column(name="beschreibung", value_type="text", x0=0.0, x1=0.355),
            Column(name="mwst_satz", value_type="number", x0=0.355, x1=0.5887),
            Column(name="einzelpreis_netto", value_type="amount", x0=0.5887, x1=0.7066),
            Column(name="gesamt_netto", value_type="amount", x0=0.7066, x1=0.8207),
            Column(name="menge", value_type="number", x0=0.8207, x1=0.903),
            Column(name="waehrung", value_type="text", x0=0.903, x1=1.0),
        ],
        stop_texts=["nettobetrag"],
    )
    store = Store(tmp_path / "data")
    sup_a = supplier_from_doc(doc_b, "lieferant-a", "Lieferant A GmbH")
    fp_b = extract_identifiers(doc_b)["fingerprint"]
    sup_a.fingerprint = sorted(fp_b)
    store.save_supplier(sup_a)
    store.save_template(Template(id="lieferant-a__standard", supplier_id="lieferant-a",
                                 line_items=kaputt))

    items, herkunft = pipeline._tabelle_rangfolge(doc_b, None, store, None, None, doc_b.text, None)
    # Die kaputte Spec darf nicht gewonnen haben -- der eingebaute Fallback
    # (Schluesselwoerter) findet die Tabelle auf dieser Rechnung trotzdem.
    assert items is not None
    assert items[0]["_quelle"] != "tabelle_layout_familie"
    assert "Lieferant A GmbH" not in herkunft
    assert round(sum(it["gesamt_netto"] for it in items), 2) == 100.0


def test_eigene_specs_neueste_zuerst(tmp_path: Path):
    src = make_invoice(tmp_path / "src.pdf",
                       items=[{"beschreibung": "Ersatzteile", "menge": 2,
                              "einzelpreis": 100.0, "mwst": 19, "waehrung": "EUR"}],
                       waehrung="EUR")
    doc = pdfmod.load(src)
    _rows, spec = _gelernte_spec(doc)
    store = Store(tmp_path / "data")
    store.save_template(Template(id="muster__standard", supplier_id="muster", line_items=spec,
                                 stale=True))
    store.save_template(Template(id="muster__standard__v2", supplier_id="muster",
                                 line_items=spec, stale=True))
    store.save_template(Template(id="muster__standard__v3", supplier_id="muster",
                                 line_items=spec, stale=False))
    geordnet = pipeline._eigene_tabellen_specs(store, "muster")
    assert [t.id for t in geordnet] == [
        "muster__standard__v3", "muster__standard__v2", "muster__standard"]
