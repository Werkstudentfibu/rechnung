from pathlib import Path

import pytest

from rechnung import pdf as pdfmod
from rechnung.identify import Supplier, identify, supplier_from_doc
from rechnung.pipeline import AUTO, NO_TEMPLATE, UNKNOWN, finalize, process
from rechnung.store import Store
from rechnung.template import Anchor, FieldSpec, Template, apply_template
from rechnung.textnorm import iban_valid, parse_amount, parse_date

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def _template() -> Template:
    t = Template(id="muster__standard", supplier_id="muster", secured_at=2, confirmations=2)
    t.fields = [
        FieldSpec("rechnungsnummer", "number", True, anchor=Anchor("rechnungsnummer", 0), rel=(60, -4, 240, 12)),
        FieldSpec("rechnungsdatum", "date", True, anchor=Anchor("rechnungsdatum", 0), rel=(50, -4, 240, 12)),
        FieldSpec("netto", "amount", True, anchor=Anchor("nettobetrag", 0), rel=(120, -4, 340, 12)),
        FieldSpec("ust_betrag", "amount", True, anchor=Anchor("umsatzsteuer", 0), rel=(120, -4, 340, 12)),
        FieldSpec("brutto", "amount", True, anchor=Anchor("gesamtbetrag", 0), rel=(120, -4, 340, 12)),
        FieldSpec("iban", "iban", False, anchor=Anchor("iban", 0), rel=(20, -4, 380, 12)),
    ]
    return t


def test_parsers():
    assert parse_amount("1.234,56 EUR") == 1234.56
    assert parse_amount("99,90") == 99.90
    assert parse_amount("1234.56") == 1234.56
    assert parse_date("14.02.2026").isoformat() == "2026-02-14"
    assert parse_date("1. Februar 2026").isoformat() == "2026-02-01"
    assert iban_valid("DE89 3704 0044 0532 0130 00")
    assert not iban_valid("DE00 1234")


def test_identify_and_extract(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "re1.pdf", rechnungsnr="RE-2026-1", netto=1000.0)

    doc = pdfmod.load(inv)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    assert identify(doc, store.suppliers())[0].id == "muster"

    store.save_template(_template())
    fields = apply_template(doc, _template())
    assert fields["rechnungsnummer"].value == "RE-2026-1"
    assert fields["netto"].value == 1000.0
    assert fields["ust_betrag"].value == 190.0
    assert fields["brutto"].value == 1190.0
    assert iban_valid(fields["iban"].value)


def test_process_status(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "samples"
    samples.mkdir()
    a = make_invoice(samples / "a.pdf", rechnungsnr="RE-1", netto=500.0)

    # unbekannter Lieferant
    assert process(a, store).status == UNKNOWN

    doc = pdfmod.load(a)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    assert process(a, store).status == NO_TEMPLATE

    store.save_template(_template())
    assert process(a, store).status == AUTO


def test_library_fields_prefilled_even_without_template(tmp_path: Path):
    """Ein per Bulk-Import (importers.py) bekannter Lieferant -- IBAN/USt-IdNr
    passen, aber noch kein Layout-Template trainiert -- soll IBAN/USt-IdNr/
    Adresse trotzdem schon bei der allerersten Rechnung zeigen, nicht erst
    nach dem ersten manuellen Anlernen. Siehe Konversation vom 2026-09-18."""
    store = Store(tmp_path / "data")
    samples = tmp_path / "samples"
    samples.mkdir()
    a = make_invoice(samples / "a.pdf", rechnungsnr="RE-1", netto=500.0)
    doc = pdfmod.load(a)

    sup = supplier_from_doc(doc, "muster", "Muster Handels GmbH")
    sup.address_lines = ["Musterstr. 1", "50667 Koeln"]
    sup.creditor_id = "DE99STNR12345"
    store.save_supplier(sup)

    res = process(a, store)
    assert res.status == NO_TEMPLATE
    assert res.fields["adresse"].source == "library"
    assert res.fields["adresse"].value == "Musterstr. 1, 50667 Koeln"
    assert res.fields["steuernummer"].source == "library"
    assert res.fields["iban"].source == "library"

    store.save_template(_template())
    r = process(a, store)
    assert r.status == AUTO
    assert r.overall_confidence >= 0.7
    assert all(c.ok is not False for c in r.checks)


def test_write_output_json_and_csv(tmp_path: Path):
    store = Store(tmp_path / "data")
    p = store.write_output("re1", {"rechnungsnummer": "RE-9", "netto": 100.0, "iban": None,
                                   "_herkunft": {"netto": "errechnet"}})
    assert p.exists()
    csv_path = p.with_suffix(".csv")
    assert csv_path.exists()
    lines = csv_path.read_text("utf-8-sig").splitlines()
    assert lines[0] == "feld;wert;herkunft"   # SPEC Baustein 3: Herkunft je Feld
    assert "rechnungsnummer;RE-9;" in lines
    assert "netto;100.0;errechnet" in lines
    assert "iban;;" in lines


def test_finalize_requires_confirmation_even_when_auto(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "samples"
    samples.mkdir()
    a = make_invoice(samples / "a.pdf", rechnungsnr="RE-CONFIRM", netto=500.0)
    doc = pdfmod.load(a)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    store.save_template(_template())

    r = process(a, store)
    assert r.status == AUTO  # Template ist gesichert, Konfidenz/Prüfungen ok ...

    # ... trotzdem darf ohne expliziten Bestaetigungs-Klick nichts uebernommen werden.
    out = finalize(r, store, to_jobrouter=False)
    assert out == {"finalized": False, "error": "not_confirmed", "status": AUTO}
    assert not store.processed()
    assert not (store.root / "archive" / f"{r.sha256}.pdf").exists()

    store.mark_confirmed(r.sha256, "aaron")
    out2 = finalize(r, store, to_jobrouter=False)
    assert out2["finalized"] is True
    assert len(store.processed()) == 1
    assert (store.root / "archive" / f"{r.sha256}.pdf").exists()


def test_identify_rejects_ambiguous_fingerprint_match(tmp_path: Path):
    a = make_invoice(tmp_path / "a.pdf", lieferant="Muster Handels GmbH",
                     ustid="DE111111111", domain="muster-handel.de")
    doc = pdfmod.load(a)
    # Zwei Lieferanten mit identischem Fingerprint (z.B. gleiche Rechnungs-
    # Software), aber keiner davon hat die tatsaechliche USt-IdNr/IBAN aus
    # der Rechnung hinterlegt -> keine harten Identifikatoren, nur der
    # unspezifische Kopf-Fingerprint entscheidet. Das darf nicht in eine
    # stille falsche Zuordnung muenden.
    fp = supplier_from_doc(doc, "", "").fingerprint
    x = Supplier(id="x", name="X GmbH", fingerprint=fp)
    y = Supplier(id="y", name="Y GmbH", fingerprint=fp)
    assert identify(doc, [x, y]) is None


def test_identify_trusts_hard_identifier_despite_close_fingerprint_scores(tmp_path: Path):
    a = make_invoice(tmp_path / "a.pdf", lieferant="Muster Handels GmbH",
                     ustid="DE111111111", domain="muster-handel.de")
    doc = pdfmod.load(a)
    fp = supplier_from_doc(doc, "", "").fingerprint
    real = Supplier(id="real", name="Muster Handels GmbH",
                    ustids=["DE111111111"], fingerprint=fp)
    lookalike = Supplier(id="lookalike", name="Lookalike GmbH", fingerprint=fp)
    match = identify(doc, [real, lookalike])
    assert match is not None
    assert match[0].id == "real"


def test_ambiguous_anchor_lowers_confidence_below_auto_threshold(tmp_path: Path):
    # Ankertext "rechnungsnummer" kommt zweimal vor: einmal als echtes Label,
    # einmal versteckt in einer zweiten Textzeile -- der beim Anlernen
    # gespeicherte occurrence-Index (0) kann dadurch auf das falsche
    # Vorkommen zeigen. apply_template soll das an der Konfidenz erkennbar
    # machen statt 0.92 zu vergeben.
    a = make_invoice(tmp_path / "a.pdf", rechnungsnr="RE-1", netto=500.0)
    doc = pdfmod.load(a)
    from rechnung.pdf import Word
    doc.pages[0].words.append(Word("Rechnungsnummer:", 20.0, 90.0, 400.0, 410.0, 0))

    tpl = _template()
    fields = apply_template(doc, tpl)
    assert fields["rechnungsnummer"].confidence < 0.7


def test_duplicate_detection(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "samples"
    samples.mkdir()
    a = make_invoice(samples / "a.pdf", rechnungsnr="RE-DUP", netto=500.0)
    doc = pdfmod.load(a)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    store.save_template(_template())

    store.mark_processed({"supplier_id": "muster", "rechnungsnummer": "RE-DUP"})
    r = process(a, store)
    dup = next(c for c in r.checks if c.name == "keine-dublette")
    assert dup.ok is False
