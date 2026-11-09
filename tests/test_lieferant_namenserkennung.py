"""Teil A, Befund Aaron 06.11.2026 ("es erkennt garnichts"): Lieferanten aus
dem Excel-Stammdatenimport (stammdaten_import.py) haben oft NUR name (+
ggf. address_lines), aber keine IBAN/USt-IdNr/Fingerprint/Domain -- die
"harten Kennungen", die in identify.py ganz oben in der Rangfolge stehen.
Reproduziert mit SYNTHETISCHEN Daten (rechnung/synth.py), nie echten
Rechnungs-/Lieferantendaten."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung.identify import Supplier, identify_with_reason
from rechnung.pipeline import UNKNOWN, process
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402
from rechnung import pdf as pdfmod  # noqa: E402


def _einziger_kandidat(tmp_path: Path, lieferant_auf_rechnung: str,
                       name_in_bibliothek: str):
    """Baut genau das Szenario aus Aarons Befund: EIN Lieferant in der
    Bibliothek (nur name, keine harten Kennungen), EINE Rechnungsadresse,
    der ausschliesslich dieser Lieferant zugeordnet ist -- nach
    ``kandidaten()`` bleibt also exakt 1 Kandidat, keine Verwechslungsgefahr."""
    doc = pdfmod.load(make_invoice(tmp_path / "re.pdf", lieferant=lieferant_auf_rechnung))
    sup = Supplier(id="muster", name=name_in_bibliothek, addresses=["adr-1"])
    return identify_with_reason(doc, [sup], address_id="adr-1", has_addresses=True,
                                address_name="Beispiel Kunde AG")


def test_single_candidate_name_only_supplier_not_matched(tmp_path: Path):
    """Kernbefund: trotz GENAU EINEM Kandidaten (keine Verwechslungsgefahr)
    und exaktem Namens-Teiltreffer bleibt der Treffer aus, weil ein reiner
    Namens-Score (0.2) die globale Schwelle (0.5) nicht erreicht -- die
    Bibliothek wird so praktisch nie genutzt, wenn Stammdaten nur den Namen
    enthalten."""
    match, why = _einziger_kandidat(tmp_path, "Muster Handels GmbH", "Muster Handels GmbH")
    assert match is None
    assert "passt" in why


def test_case_and_whitespace_variants_are_normalized_but_score_still_too_low(tmp_path: Path):
    """norm() (textnorm.py) gleicht Gross-/Kleinschreibung und Leer-/
    Sonderzeichen problemlos aus -- der Teiltreffer entsteht trotzdem, scheitert
    aber weiterhin an der Schwelle (zeigt: das Problem ist nicht die
    Normalisierung, sondern die Gewichtung/Schwelle)."""
    match, why = _einziger_kandidat(tmp_path, "MUSTER   HANDELS GMBH", "muster-handels gmbh")
    assert match is None
    assert "passt" in why


def test_missing_rechtsform_on_invoice_breaks_substring_match_entirely(tmp_path: Path):
    """Schlimmer als die reine Schwelle: fehlt auf der Rechnung der in der
    Bibliothek hinterlegte Rechtsformzusatz (hier: Bibliothek 'Muster Handels
    GmbH', Rechnung druckt nur 'Muster Handels'), scheitert schon der
    Teilstring-Vergleich (``norm(s.name) in text_n``) komplett -- der
    Namens-Score faellt nicht nur knapp, sondern auf 0."""
    doc = pdfmod.load(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels"))
    sup = Supplier(id="muster", name="Muster Handels GmbH", addresses=["adr-1"])
    match, why = identify_with_reason(doc, [sup], address_id="adr-1", has_addresses=True,
                                      address_name="Beispiel Kunde AG")
    assert match is None
    assert "passt" in why


def test_different_rechtsform_breaks_substring_match_entirely(tmp_path: Path):
    """Abweichender Rechtsformzusatz (Bibliothek 'Muster Handels GmbH',
    Rechnung zeigt 'Muster Handels AG' -- z.B. nach Umwandlung/Tippfehler im
    Stammdatenimport) scheitert ebenso komplett am Teilstring-Vergleich."""
    doc = pdfmod.load(make_invoice(tmp_path / "re.pdf", lieferant="Muster Handels AG"))
    sup = Supplier(id="muster", name="Muster Handels GmbH", addresses=["adr-1"])
    match, why = identify_with_reason(doc, [sup], address_id="adr-1", has_addresses=True,
                                      address_name="Beispiel Kunde AG")
    assert match is None
    assert "passt" in why


def test_library_name_as_substring_of_invoice_name_still_only_scores_name_part(tmp_path: Path):
    """Umgekehrter Fall (Bibliothek ohne Rechtsform, Rechnung mit): der
    Teilstring-Vergleich findet den Treffer (Bibliotheksname steckt in der
    Rechnung), trotzdem bleibt der Gesamt-Score bei 0.2 -- ebenfalls unter
    der Schwelle."""
    match, why = _einziger_kandidat(tmp_path, "Muster Handels GmbH", "Muster Handels")
    assert match is None
    assert "passt" in why


# ---- Teil 1, Zusatzfrage: welche Lieferantenfelder fehlen bei einem
# Nicht-Treffer, weil sie nur aus der Bibliothek kaemen? --------------------
def test_no_match_means_library_master_fields_are_never_merged_in(tmp_path: Path, monkeypatch):
    # Seit 09.11.2026 wuerde die Namens-Erkennung (config.namenserkennung)
    # diesen Fall treffen -- hier geht es um das Verhalten OHNE Treffer.
    monkeypatch.setenv("RECHNUNG_NAMENSERKENNUNG", "0")
    """Erklaert Aarons zweite Beobachtung ("die Daten sind nicht
    vollstaendig"): IBAN/USt-IdNr/Adresse eines Lieferanten kommen NUR per
    ``pipeline._inject_library_fields`` aus der Bibliothek, und die wird nur
    aufgerufen, wenn ``identify_with_reason`` zuvor einen Treffer meldet
    (pipeline.py, Aufrufstellen nach "supplier, score = match"). Matcht der
    Name-only-Lieferant nicht (siehe Tests oben), bleiben auch dann, wenn die
    Bibliothek fuer ihn bereits eine Adresse pflegt, 'adresse'/'iban'/'ustid'
    OHNE source='library' -- die angezeigten Werte kommen bestenfalls aus der
    rohen Extraktion der Rechnung, nicht aus den gepflegten Stammdaten."""
    store = Store(tmp_path / "data")
    samples = tmp_path / "samples"
    samples.mkdir()
    a = make_invoice(samples / "a.pdf", lieferant="Muster Handels GmbH", rechnungsnr="RE-1")

    sup = Supplier(id="muster", name="Muster Handels GmbH")
    # Bibliothek pflegt eine Adresse -- aber keine IBAN/USt-IdNr/Fingerprint/
    # Domain (typischer Excel-Stammdatenimport, siehe Modulbeschreibung oben).
    sup.address_lines = ["Musterstr. 1", "50667 Koeln"]
    store.save_supplier(sup)

    res = process(a, store)
    assert res.status == UNKNOWN
    assert res.supplier_id is None
    adresse = res.fields.get("adresse")
    assert adresse is None or adresse.source != "library"
    for feld in ("iban", "ustid"):
        fr = res.fields.get(feld)
        assert fr is None or fr.source != "library"


def test_match_merges_library_master_fields_in_for_contrast(tmp_path: Path):
    """Gegenstueck zum Test oben: hat die Bibliothek zusaetzlich IBAN/USt-IdNr
    (also harte Kennungen, die einen Treffer ermoeglichen), kommen Adresse/
    IBAN/USt-IdNr sehr wohl mit source='library' -- der Unterschied ist
    ausschliesslich der fehlende Treffer, nicht ein Extraktionsproblem."""
    store = Store(tmp_path / "data")
    samples = tmp_path / "samples"
    samples.mkdir()
    a = make_invoice(samples / "a.pdf", lieferant="Muster Handels GmbH", rechnungsnr="RE-1",
                     ustid="DE123456789", iban="DE89370400440532013000")

    sup = Supplier(id="muster", name="Muster Handels GmbH",
                   ustids=["DE123456789"], ibans=["DE89370400440532013000"])
    sup.address_lines = ["Musterstr. 1", "50667 Koeln"]
    store.save_supplier(sup)

    res = process(a, store)
    assert res.supplier_id == "muster"
    assert res.fields["adresse"].source == "library"
    assert res.fields["iban"].source == "library"
    assert res.fields["ustid"].source == "library"
