"""Namens-Erkennung in der echten Verarbeitung (Aaron 09.11.2026): Lieferanten
aus dem Excel-Import (nur Name, keine Adresse, keine Kennungen) werden
erkannt, wenn GENAU EIN Lieferantenname als ganze Wortfolge auf der Rechnung
steht. Synthetische Daten (rechnung/synth.py)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import config
from rechnung.identify import Supplier, name_auf_rechnung
from rechnung.billing import BillingAddress
from rechnung.pipeline import (ENTWURF_HINWEIS, NAME_HINWEIS, NEW_SUPPLIER_HOLD, UNKNOWN,
                               process)
from rechnung.store import Store
from rechnung.textnorm import norm

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def _bibliothek(tmp_path: Path, *namen: str):
    store = Store(tmp_path / "data")
    for i, n in enumerate(namen):
        # wie der Excel-Import: nur Name, keine Adresse, bestaetigt
        store.save_supplier(Supplier(id=f"s{i}", name=n, bestaetigt=True))
    return store


def test_excel_lieferant_wird_ueber_den_namen_erkannt(tmp_path: Path):
    store = _bibliothek(tmp_path, "Muster Handels GmbH", "Ganz Andere Firma AG",
                        "Dritte Lieferanten KG")
    res = process(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels GmbH"), store)
    assert res.supplier_id == "s0"
    assert res.match_score == pytest.approx(0.2)
    assert res.status != UNKNOWN
    assert NEW_SUPPLIER_HOLD not in res.hold_reasons
    assert NAME_HINWEIS in res.hinweise


def test_abschaltbar(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_NAMENSERKENNUNG", "0")
    assert config.namenserkennung() is None
    store = _bibliothek(tmp_path, "Muster Handels GmbH", "Ganz Andere Firma AG")
    res = process(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels GmbH"), store)
    assert res.supplier_id is None
    assert res.status == UNKNOWN
    assert NAME_HINWEIS not in res.hinweise


def test_schwelle_einstellbar(monkeypatch):
    monkeypatch.setenv("RECHNUNG_NAMENSERKENNUNG", "0,3")
    assert config.namenserkennung() == pytest.approx(0.3)
    monkeypatch.setenv("RECHNUNG_NAMENSERKENNUNG", "1")
    assert config.namenserkennung() == pytest.approx(0.2)
    monkeypatch.delenv("RECHNUNG_NAMENSERKENNUNG")
    assert config.namenserkennung() == pytest.approx(0.2)


def test_zwei_namen_auf_der_rechnung_bleiben_beim_menschen(tmp_path: Path):
    """Verwechslungsschutz: "Muster Handels GmbH" und "Muster Handels" stehen
    beide (als Wortfolge) auf der Rechnung -> kein Treffer, Mensch."""
    store = _bibliothek(tmp_path, "Muster Handels GmbH", "Muster Handels")
    res = process(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels GmbH"), store)
    assert res.supplier_id is None
    assert res.status == UNKNOWN
    assert any("Lieferant" in g for g in res.hold_reasons)


def test_dummy_lieferant_zaehlt_nie_als_namenstreffer(tmp_path: Path):
    """Der Dummy-Lieferant (dummy.py) laesst sich nicht speichern -- deshalb
    direkt gegen identify_with_reason mit einem Dummy-Objekt."""
    from rechnung import pdf as pdfmod
    from rechnung.identify import identify_with_reason
    doc = pdfmod.load(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels GmbH"))
    dummy = Supplier(id="dummy", name="Muster Handels GmbH", bestaetigt=True, dummy=True)
    andere = Supplier(id="s1", name="Ganz Andere Firma AG", bestaetigt=True)
    match, why = identify_with_reason(doc, [dummy, andere], einzelkandidat_namensschwelle=0.2)
    assert match is None
    assert "passt" in why


def test_harte_kennung_schlaegt_namen(tmp_path: Path):
    """Steht die USt-IdNr eines anderen Lieferanten auf der Rechnung, gewinnt
    die harte Kennung -- der Namenstreffer aendert daran nichts."""
    store = _bibliothek(tmp_path, "Muster Handels GmbH")
    store.save_supplier(Supplier(id="hart", name="Echte Firma GmbH", bestaetigt=True,
                                 ustids=["DE123456789"]))
    res = process(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels GmbH",
                               ustid="DE123456789"), store)
    assert res.supplier_id == "hart"
    assert NAME_HINWEIS not in res.hinweise


def test_name_auf_rechnung_nur_als_ganze_wortfolge():
    assert name_auf_rechnung("Hotel Mueller GmbH", norm("Rechnung Hotel Müller GmbH Köln"))
    assert not name_auf_rechnung("Hotel", norm("Hotelbedarf Mueller GmbH"))
    assert name_auf_rechnung("Hotel", norm("Das Hotel am See"))
    assert not name_auf_rechnung("Gema", norm("Allgemeine Versorgung GmbH"))
    assert not name_auf_rechnung("ewz", norm("ewz Zuerich"))      # zu kurz
    assert not name_auf_rechnung("", norm("irgendwas"))
    assert not name_auf_rechnung(None, norm("irgendwas"))


def test_empfaenger_zaehlt_nie_als_lieferant(tmp_path: Path):
    """Die eigene Gesellschaft steht als Rechnungsadresse UND als Lieferant in
    der Bibliothek (Befund Trockenlauf 09.11.2026): ihr Name auf der Rechnung
    (als Empfaenger) darf sie nie zum Lieferanten machen."""
    store = _bibliothek(tmp_path, "Beispiel Kunde AG", "Ganz Andere Firma AG")
    store.save_address(BillingAddress(id="kunde", name="Beispiel Kunde AG",
                                      match_terms=["Beispiel Kunde AG"]))
    # Rechnung eines Lieferanten, der NICHT in der Bibliothek steht; der
    # Empfaenger "Beispiel Kunde AG" steht wie immer im Adressblock.
    res = process(make_invoice(tmp_path / "re.pdf", lieferant="Fremde Firma GmbH"), store)
    assert res.supplier_id is None
    assert res.status == UNKNOWN


def test_entwurf_bekommt_hinweis_auf_bestehenden_lieferanten(tmp_path: Path):
    """Praxisfall: die Rechnung haengt ueber die USt-IdNr an einem Entwurf
    "Unbekannt (DE...)", aber der Name genau EINES bestaetigten Excel-
    Lieferanten steht darauf -> Hinweis fuer den Menschen; der Entwurf bleibt
    (Stammdaten bestaetigt ein Mensch)."""
    store = _bibliothek(tmp_path, "Muster Handels GmbH", "Ganz Andere Firma AG")
    store.save_supplier(Supplier(id="neu-de123456789", name="Unbekannt (DE123456789)",
                                 bestaetigt=False, ustids=["DE123456789"]))
    res = process(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels GmbH",
                               ustid="DE123456789"), store)
    assert res.supplier_id == "neu-de123456789"
    assert NEW_SUPPLIER_HOLD in res.hold_reasons
    assert ENTWURF_HINWEIS.format(name="Muster Handels GmbH") in res.hinweise


def test_entwurf_ohne_eindeutigen_namen_bekommt_keinen_hinweis(tmp_path: Path):
    store = _bibliothek(tmp_path, "Muster Handels GmbH", "Muster Handels")
    store.save_supplier(Supplier(id="neu-de123456789", name="Unbekannt (DE123456789)",
                                 bestaetigt=False, ustids=["DE123456789"]))
    res = process(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels GmbH",
                               ustid="DE123456789"), store)
    assert res.supplier_id == "neu-de123456789"
    assert not any(h.startswith("Lieferanten-Entwurf:") for h in res.hinweise)


def test_abgeschnittener_adressname_zaehlt_als_empfaenger():
    """Excel-Import schneidet Adressnamen ab ("...gesellschaf"): trotzdem darf
    die volle Firma nicht als Lieferant durchgehen (Befund Trockenlauf 09.11.)."""
    from rechnung.identify import _ist_empfaenger, empfaenger_namen

    emp = empfaenger_namen([BillingAddress(id="abb", name="ABB - Althoff Beratungs- und Betreuungsgesellschaf",
                                           match_terms=["Althoff Beratungs- und Betreuungsgesellschaf"])])
    assert _ist_empfaenger(norm("Althoff Beratungs- und Betreuungsgesellschaft mbH"), emp)
    assert not _ist_empfaenger(norm("Althoff Catering GmbH"), emp)
    assert not _ist_empfaenger(norm("Muster Handels GmbH"), emp)
