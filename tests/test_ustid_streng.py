"""USt-IdNr-Erkennung streng: nur Formate echter EU-/CH-USt-IdNrn, nie BICs
oder beliebige Grossbuchstaben-Ziffern-Folgen.

Befund Aaron 09.11.2026 (Trockenlauf ueber echte Uebungsrechnungen): die
alte Regel "2 Buchstaben + 8-12 Zeichen mit >= 2 Ziffern" hielt BICs
(COKSDE33XXX, MALADE51WOR, DEUTDEDB384), Filtermasse (AB1000X450MM) oder
"TELEFON0511" fuer USt-IdNrn. Folge: ein Entwurf "Unbekannt (COKSDE33XXX)"
buendelte Rechnungen VERSCHIEDENER Lieferanten, die zufaellig bei derselben
Sparkasse sind -- ein Bestaetigen haette sie alle einem Lieferanten
zugeordnet (gefaehrlich fuer die IBAN-Pruefung). Alle Texte hier sind
synthetisch (rechnung/synth.py), keine echten Rechnungen."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import textnorm
from rechnung.identify import extract_identifiers
from rechnung.pdf import Document, Page, Word
from rechnung.store import Store


def _doc(text: str) -> Document:
    """Document direkt aus Text (ohne PDF) -- extract_identifiers liest nur
    ``doc.text`` und die Woerter der ersten Seite."""
    words = [Word(t, 0.0, 10.0, 0.0, 10.0, 0) for t in text.split()]
    return Document(Path("synthetisch.pdf"), [Page(0, 595.0, 842.0, words)], "sha-synth")


# ---- echte USt-IdNrn werden weiterhin erkannt ------------------------------
@pytest.mark.parametrize("text,erwartet", [
    ("USt-IdNr.: DE123456789", "DE123456789"),
    ("USt-IdNr. DE 123 456 789", "DE123456789"),        # gruppiert gedruckt
    ("UID-Nr. ATU12345678", "ATU12345678"),
    ("MWST-Nr. CHE-100.000.006 MWST", "CHE100000006"),  # gueltige Pruefziffer
    ("N TVA FR52327460341", "FR52327460341"),
    ("BTW-nr. NL862219267B01", "NL862219267B01"),
    ("BTW BE0123456789", "BE0123456789"),
    ("Partita IVA IT12345678901", "IT12345678901"),
    ("NIF ESB12345678", "ESB12345678"),
    ("TVA LU12345678", "LU12345678"),
    ("VAT Reg. No. GB123456789", "GB123456789"),
])
def test_echte_ustidnrn_werden_erkannt(text, erwartet):
    assert textnorm.find_ustids(text) == {erwartet}
    assert extract_identifiers(_doc(text))["ustids"] == {erwartet}


def test_ustidnr_zwischen_anderen_kennungen():
    text = ("Muster Handels GmbH USt-IdNr.: DE123456789 "
            "IBAN DE89 3704 0044 0532 0130 00 BIC COKSDE33XXX Filter AB1000X450MM")
    assert textnorm.find_ustids(text) == {"DE123456789"}


# ---- BICs und beliebige Buchstaben-Ziffern-Folgen sind keine USt-IdNr ------
@pytest.mark.parametrize("text", [
    "BIC: COKSDE33XXX",                 # Kreissparkasse Koeln
    "BIC COLSDE33XXX",                  # Sparkasse KoelnBonn
    "SWIFT-BIC: MALADE51WOR",           # Sparkasse Worms (8+3)
    "BIC DEUTDEDB384",                  # Deutsche Bank -- beginnt mit DE!
    "Bank: COKSEDE33XXX",               # Tippfehler-Variante aus dem Befund
    "Bankverbindung COBADEFFXXX",        # 11-stellig ohne Beschriftung
    "Bankverbindung COBADEFF",           # 8-stellig ohne Beschriftung
    "Filter AB1000X450MM",              # Masse eines Filters
    "TELEFON0511 123456",
    "BUCHENWIRAM18",
    "Referenz RFLUS5ET2UNGI",
    "Art.-Nr. DE12345678",              # DE mit 8 Ziffern: keine USt-IdNr
    "Art.-Nr. DE1234567890",            # DE mit 10 Ziffern: keine USt-IdNr
    "Kundennr. DE1234ABCD5",            # DE mit Buchstaben: keine USt-IdNr
    "IBAN DE89370400440532013000",      # IBAN bleibt IBAN
    "CHE-123.456.789 MWST",             # falsche Pruefziffer
    "Lieferschein LS2026000123",
])
def test_bic_und_zufallsfolgen_sind_keine_ustidnr(text):
    assert textnorm.find_ustids(text) == set()
    assert extract_identifiers(_doc(text))["ustids"] == set()


def test_bic_beschriftung_schlaegt_auch_passendes_format():
    """Steht BIC/SWIFT davor, gilt die Folge nie als USt-IdNr -- selbst wenn
    sie zufaellig wie eine aussaehe."""
    assert textnorm.find_ustids("SWIFT: LU12345678") == set()
    assert textnorm.find_ustids("BIC/SWIFT LU12345678") == set()
    assert textnorm.find_ustids("TVA LU12345678") == {"LU12345678"}


def test_looks_like_bic():
    assert textnorm.looks_like_bic("COKSDE33XXX") is True
    assert textnorm.looks_like_bic("COBADEFF") is True
    assert textnorm.looks_like_bic("MALADE51WOR") is True
    assert textnorm.looks_like_bic("DE123456789") is False
    assert textnorm.looks_like_bic("ATU12345678") is False
    assert textnorm.looks_like_bic("ABCDXX11") is False     # XX ist kein Land


# ---- KI-Wert vom Typ ustid wird an den strengen Formaten gemessen ----------
def test_coerce_ustid_lehnt_bic_ab():
    assert textnorm.coerce("COKSDE33XXX", "ustid")[1] is False
    assert textnorm.coerce("DE 123 456 789", "ustid") == ("DE123456789", True)


# ---- Entwurf: nie allein aus einer BIC --------------------------------------
reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402
from rechnung.pipeline import process  # noqa: E402


def test_entwurf_entsteht_nicht_mit_bic_als_kennung(tmp_path: Path):
    """Rechnung ohne USt-IdNr und ohne IBAN, aber mit BIC im Text: der
    Entwurf darf die BIC nicht als USt-IdNr tragen, sonst landen alle
    Kunden derselben Sparkasse im selben Entwurf (Befund 09.11.2026)."""
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf", ustid="", iban="",
                       zahlungstext="Ueberweisung an BIC: COKSDE33XXX")

    res = process(inv, store, create_draft_supplier=True)

    assert res.supplier_id is not None
    sup = store.supplier(res.supplier_id)
    assert sup.bestaetigt is False
    assert sup.ustids == []
    assert sup.ibans == []
    assert "COKSDE33XXX" not in sup.name
    assert "coksde33xxx" not in sup.id
    # ohne Kennung: Schluessel "neu", Name "Unbekannt" (kein Buendeln ueber
    # eine BIC moeglich -- der Entwurf hat keine harte Kennung)
    assert sup.id.startswith("neu")
    assert sup.name == "Unbekannt"


def test_zwei_lieferanten_derselben_sparkasse_bekommen_getrennte_entwuerfe(tmp_path: Path):
    """Zwei unbekannte Lieferanten mit verschiedener IBAN, aber derselben BIC
    -> zwei Entwuerfe, geschluesselt nach IBAN (neu-<iban>), nie nach BIC."""
    store = Store(tmp_path / "data")
    a = make_invoice(tmp_path / "a.pdf", lieferant="Alpha Technik GmbH", ustid="",
                     iban="DE89370400440532013000", domain="alpha-technik.de",
                     zahlungstext="BIC COKSDE33XXX")
    b = make_invoice(tmp_path / "b.pdf", lieferant="Beta Reinigung GmbH", ustid="",
                     iban="DE02120300000000202051", domain="beta-reinigung.de",
                     zahlungstext="BIC COKSDE33XXX", rechnungsnr="RE-2026-00043")

    ra = process(a, store, create_draft_supplier=True)
    rb = process(b, store, create_draft_supplier=True)

    assert ra.supplier_id != rb.supplier_id
    sa, sb = store.supplier(ra.supplier_id), store.supplier(rb.supplier_id)
    assert sa.ustids == [] and sb.ustids == []
    assert sa.ibans == ["DE89370400440532013000"]
    assert sb.ibans == ["DE02120300000000202051"]
    assert sa.id == "neu-de89370400440532013000"
    assert sb.id == "neu-de02120300000000202051"
