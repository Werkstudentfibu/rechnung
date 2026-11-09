"""Teil C, Vorschlag fuer bessere Erkennung (NICHT automatisch aktiv, braucht
Aarons Bestaetigung an echten Daten -- siehe
worker/bericht-lieferant-erkennung-1106.md): ``identify_with_reason`` bekommt
einen rein opt-in Parameter ``einzelkandidat_namensschwelle``. pipeline.py
ruft die Funktion weiterhin OHNE dieses Argument auf (Standard ``None``) --
dieser Test belegt sowohl, dass der Vorschlag das Kernproblem (Teil A) loest,
WENN man ihn einschaltet, als auch, dass er ohne Einschalten nichts aendert
und die bestehende Mehrdeutigkeits-/Betrugsschutz-Pruefung bei zwei oder mehr
Kandidaten unberuehrt laesst."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung.identify import Supplier, identify_with_reason

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402
from rechnung import pdf as pdfmod  # noqa: E402


def test_default_unverandert_ohne_flag(tmp_path: Path):
    """Ohne das neue Argument (so wie pipeline.py es tatsaechlich aufruft)
    aendert sich nichts -- derselbe Fall wie in
    test_lieferant_namenserkennung.test_single_candidate_name_only_supplier_not_matched."""
    doc = pdfmod.load(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels GmbH"))
    sup = Supplier(id="muster", name="Muster Handels GmbH", addresses=["adr-1"])
    match, why = identify_with_reason(doc, [sup], address_id="adr-1", has_addresses=True)
    assert match is None
    assert "passt" in why


def test_flag_loest_den_einzelkandidat_fall(tmp_path: Path):
    """Mit eingeschaltetem Vorschlag matcht genau der Fall aus Teil A: ein
    einziger Kandidat (nach Adress-Einschraenkung), dessen Name als
    Teilstring auf der Rechnung steht, obwohl der reine Namens-Score (0.2)
    unter der globalen Schwelle (0.5) liegt."""
    doc = pdfmod.load(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels GmbH"))
    sup = Supplier(id="muster", name="Muster Handels GmbH", addresses=["adr-1"])
    match, why = identify_with_reason(doc, [sup], address_id="adr-1", has_addresses=True,
                                      einzelkandidat_namensschwelle=0.2)
    assert match is not None
    assert match[0].id == "muster"
    assert why == ""


def test_flag_verhindert_keinen_treffer_ohne_jede_uebereinstimmung(tmp_path: Path):
    """Der Vorschlag rät nicht blind: zeigt die Rechnung nicht einmal den
    Namen des einzigen Kandidaten, bleibt es bei 'kein Treffer' -- auch mit
    eingeschaltetem Flag."""
    doc = pdfmod.load(make_invoice(tmp_path / "re.pdf", lieferant="Ganz Andere Firma AG"))
    sup = Supplier(id="muster", name="Muster Handels GmbH", addresses=["adr-1"])
    match, why = identify_with_reason(doc, [sup], address_id="adr-1", has_addresses=True,
                                      einzelkandidat_namensschwelle=0.2)
    assert match is None
    assert "passt" in why


def test_flag_lockert_mehrdeutigkeit_bei_zwei_namenstreffern_nicht(tmp_path: Path):
    """Kernbedingung: stehen ZWEI Lieferantennamen auf der Rechnung, bleibt
    die normale threshold-/min_margin-Pruefung unveraendert streng -- die
    Namens-Erkennung darf die Mehrdeutigkeits-Erkennung nicht aufweichen.
    (Seit 09.11.2026 zaehlt nicht mehr "genau ein Kandidat", sondern "genau
    ein Namenstreffer": Excel-Lieferanten ohne Adresse gelten global, die
    Kandidatenmenge ist in der Praxis immer die ganze Bibliothek.)"""
    doc = pdfmod.load(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels GmbH"))
    a = Supplier(id="a", name="Muster Handels GmbH", addresses=["adr-1"])
    b = Supplier(id="b", name="Muster Handels", addresses=["adr-1"])
    match, why = identify_with_reason(doc, [a, b], address_id="adr-1", has_addresses=True,
                                      einzelkandidat_namensschwelle=0.2)
    assert match is None
    assert "passt" in why   # beide unter der Schwelle, keiner gewinnt


def test_flag_zwei_kandidaten_nur_einer_steht_auf_der_rechnung(tmp_path: Path):
    """Der Praxisfall: viele Kandidaten (Excel-Import, alle ohne Adresse),
    aber nur EIN Name steht auf der Rechnung -> dieser gilt als erkannt."""
    doc = pdfmod.load(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels GmbH"))
    a = Supplier(id="a", name="Muster Handels GmbH")
    b = Supplier(id="b", name="Ganz Andere Firma AG")
    c = Supplier(id="c", name="Muster Handels Nord GmbH")
    match, why = identify_with_reason(doc, [a, b, c], einzelkandidat_namensschwelle=0.2)
    assert match is not None and match[0].id == "a"
    assert why == ""


def test_flag_aendert_betrugsschutz_bei_widerspruechlichen_kennungen_nicht(tmp_path: Path):
    """Der bestehende 'mehrdeutig: USt-IdNr/IBAN passen zu mehreren
    Lieferanten'-Pfad (Betrugsschutz) wird VOR dem Fallback-Scoring
    entschieden und bleibt vom neuen Parameter vollstaendig unberuehrt."""
    doc = pdfmod.load(make_invoice(tmp_path / "re.pdf"))
    from rechnung.identify import extract_identifiers

    ids = extract_identifiers(doc)
    iban = next(iter(ids["ibans"] or {"DE89370400440532013000"}))
    a = Supplier(id="a", name="A GmbH", ibans=[iban])
    b = Supplier(id="b", name="B GmbH", ustids=list(ids["ustids"]) or ["DE999999999"])
    if not ids["ustids"]:
        pytest.skip("Testrechnung enthaelt keine USt-IdNr")
    match, why = identify_with_reason(doc, [a, b], einzelkandidat_namensschwelle=0.2)
    assert match is None
    assert "mehrdeutig" in why


def test_flag_hebt_schwelle_nie_ueber_die_globale(tmp_path: Path):
    """Sicherheitsnetz: ein versehentlich zu hoch gesetzter Vorschlagswert
    (> globale threshold) darf die Erkennung nie strenger machen, als sie
    ohne den Vorschlag waere (``min(threshold, ...)``)."""
    doc = pdfmod.load(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels GmbH"))
    sup = Supplier(id="muster", name="Muster Handels GmbH", addresses=["adr-1"])
    match, why = identify_with_reason(doc, [sup], address_id="adr-1", has_addresses=True,
                                      einzelkandidat_namensschwelle=0.9)
    # Namens-Score 0.2 < globale threshold 0.5 -- bleibt kein Treffer,
    # NICHT weil 0.9 > 0.2 waere, sondern weil min(0.5, 0.9) == 0.5 gilt.
    assert match is None
