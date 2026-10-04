"""Postfach-Baustein 7: CHF / Schweizer Rechnungen -- Waehrung sicher
erkennen, Schweizer Zahlenformat, CHE-UID gegen die Bibliothek, Swiss
QR-Rechnung (QR-Zahlteil). Keine Umrechnung, andere Waehrungen -> Mensch."""

from __future__ import annotations

import pytest

from rechnung import human, swissqr, textnorm, waehrung
from rechnung import pdf as pdfmod
from rechnung.identify import Supplier
from rechnung.pipeline import process
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


# ---- textnorm: Schweizer Zahlenformat / UID -------------------------------

@pytest.mark.parametrize("raw,erwartet", [
    ("1'234.50", 1234.50),
    ("1’234.50", 1234.50),   # typografisches Hochkomma
    ("12'345'678.90", 12345678.90),
    ("120.–", 120.0),         # "120.–" -- glatte Franken
])
def test_ch_zahlenformat(raw, erwartet):
    assert textnorm.parse_amount(raw) == erwartet


def test_che_pruefziffer():
    assert textnorm.che_valid("100000006") is True
    assert textnorm.che_valid("123456789") is False


def test_find_ustids_che():
    assert textnorm.find_ustids("MWST-Nr. CHE-100.000.006 MWST") == {"CHE100000006"}
    # falsche Pruefziffer -> kein Treffer (nicht geraten)
    assert textnorm.find_ustids("CHE-123.456.789 MWST") == set()


def test_norm_ustid():
    assert textnorm.norm_ustid("CHE-100.000.006 MWST") == "CHE100000006"
    assert textnorm.norm_ustid("DE 123 456 789") == "DE123456789"


# ---- waehrung.py: Waehrung aus dem Text -----------------------------------

@pytest.mark.parametrize("text,erwartet", [
    ("Betrag Fr. 120.00 netto", "CHF"),
    ("Preis 50.00 SFr.", "CHF"),
    ("Gesamtbetrag 1'234.50 CHF", "CHF"),
    ("Gesamtbetrag 1.234,50 EUR", "EUR"),
    ("Rechnungsbetrag: 500,00 Euro", "EUR"),
])
def test_waehrung_detect(text, erwartet):
    code, why = waehrung.detect(text)
    assert code == erwartet and why == ""


def test_waehrung_mehrdeutig():
    code, why = waehrung.detect("Auf dieser Rechnung stehen CHF und EUR nebeneinander")
    assert code is None and "nicht eindeutig" in why


def test_waehrung_fremd():
    code, why = waehrung.detect("Betrag 50.00 USD")
    assert code is None and "USD" in why


def test_waehrung_ohne_hinweis_kein_fehler():
    assert waehrung.detect("gar kein Waehrungshinweis hier") == (None, "")


# ---- swissqr.py: SPC bauen/lesen, Referenz-Pruefziffern -------------------

def test_spc_roundtrip():
    spc = swissqr.build_spc(iban="CH9300762011623852957", creditor_name="Muster AG",
                            creditor_city="8000 Zuerich", betrag=1081.35, waehrung="CHF",
                            debtor_name="Kunde AG", referenz_typ="QRR",
                            referenz="210000000003139471430009017")
    qr = swissqr.parse_spc(spc)
    assert qr is not None
    assert qr.iban == "CH9300762011623852957"
    assert qr.creditor_name == "Muster AG"
    assert qr.betrag == 1081.35
    assert qr.waehrung == "CHF"
    assert qr.referenz_typ == "QRR"
    assert qr.referenz == "210000000003139471430009017"


def test_spc_kein_spc_inhalt():
    assert swissqr.parse_spc("irgendein anderer QR-Code-Inhalt") is None


def test_qrr_pruefziffer():
    ref = swissqr.build_qrr_reference("210000000003139471430009017")
    assert swissqr.qrr_valid(ref) is True
    assert swissqr.qrr_valid(ref[:-1] + str((int(ref[-1]) + 1) % 10)) is False


def test_scor_pruefziffer():
    assert swissqr.scor_valid("RF18539007547034") is True
    assert swissqr.scor_valid("RF00539007547034") is False


# ---- Pipeline: Waehrung-Feld, CHE-UID, Swiss QR ---------------------------

def _store_ch(tmp_path, ustid="CHE-100.000.006 MWST", iban="CH9300762011623852957"):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="ch1", name="Schweizer Lieferant AG",
                                 ustids=[ustid], ibans=[iban], currency="CHF"))
    return store


def test_chf_rechnung_waehrung_und_che_uid(tmp_path):
    store = _store_ch(tmp_path)
    p = make_invoice(tmp_path / "ch.pdf", lieferant="Schweizer Lieferant AG",
                     ort="8000 Zuerich", ustid="CHE-100.000.006 MWST",
                     iban="CH9300762011623852957", rechnungsnr="CH-2026-1",
                     netto=1000.0, satz=8.1, waehrung="CHF")
    # ohne Vorlage/KI wird nur ueber Korrekturen etwas Pruefbares extrahiert
    # (der Punkt hier ist Waehrung/UID, nicht die Vorlagenerkennung)
    sha = pdfmod.load(p).sha256
    for f, v in (("rechnungsnummer", "CH-2026-1"), ("rechnungsdatum", "14.02.2026"),
                ("netto", "1000,00"), ("ust_betrag", "81,00"), ("brutto", "1081,00")):
        store.save_correction(sha, f, v)
    res = process(p, store, use_llm=False)
    assert res.fields["waehrung"].value == "CHF"
    chk = {c.name: c for c in res.checks}
    assert chk["waehrung"].ok is True
    assert chk["ustid-stammdaten"].ok is True
    assert human.gruende(res) == []


def test_fremde_waehrung_geht_an_mensch(tmp_path):
    store = _store_ch(tmp_path)
    p = make_invoice(tmp_path / "usd.pdf", lieferant="Schweizer Lieferant AG",
                     ort="8000 Zuerich", ustid="CHE-100.000.006 MWST",
                     iban="CH9300762011623852957", rechnungsnr="CH-2026-2",
                     netto=1000.0, satz=8.1, waehrung="USD")
    res = process(p, store, use_llm=False)
    assert "waehrung" not in res.fields
    chk = next(c for c in res.checks if c.name == "waehrung")
    assert chk.ok is False and "USD" in chk.detail
    assert any("waehrung" in g["grund"] for g in human.gruende(res))


def test_mehrdeutige_waehrung_geht_an_mensch(tmp_path):
    store = _store_ch(tmp_path)
    p = make_invoice(tmp_path / "amb.pdf", lieferant="Schweizer Lieferant AG",
                     ort="8000 Zuerich", ustid="CHE-100.000.006 MWST",
                     iban="CH9300762011623852957", rechnungsnr="CH-2026-3",
                     netto=1000.0, satz=8.1, waehrung="CHF",
                     zahlungstext="Frueherer Betrag 50,00 EUR storniert, Rest auf IBAN")
    res = process(p, store, use_llm=False)
    chk = next(c for c in res.checks if c.name == "waehrung")
    assert chk.ok is False and "nicht eindeutig" in chk.detail
    assert any("waehrung" in g["grund"] for g in human.gruende(res))


def test_ch_zahlenformat_auf_der_rechnung(tmp_path):
    """Betraege im Schweizer Format (Hochkomma-Tausendertrenner) werden
    trotzdem korrekt gelesen -- hier direkt ueber die Pruefung
    netto+ust=brutto (die Betraege selbst stehen per Korrektur, textnorm
    erkennt das Format unabhaengig davon schon auf Ebene der Regex)."""
    assert textnorm.parse_amount("12'500.00") == 12500.0
    assert textnorm.parse_amount("1'081.35") == 1081.35


def test_ohne_qr_zahlteil_keine_qr_pruefung(tmp_path):
    store = _store_ch(tmp_path)
    p = make_invoice(tmp_path / "ohne_qr.pdf", lieferant="Schweizer Lieferant AG",
                     ort="8000 Zuerich", ustid="CHE-100.000.006 MWST",
                     iban="CH9300762011623852957", rechnungsnr="CH-2026-4",
                     netto=1000.0, satz=8.1, waehrung="CHF")
    res = process(p, store, use_llm=False)
    assert res.qr is None
    assert not any(c.name.startswith("qr-") for c in res.checks)
    assert "zahlungsreferenz" not in res.fields


def test_swiss_qr_zahlteil_gelesen(tmp_path):
    zxingcpp = pytest.importorskip("zxingcpp")
    store = _store_ch(tmp_path)
    ref = swissqr.build_qrr_reference("210000000003139471430009017")
    p = make_invoice(tmp_path / "qr.pdf", lieferant="Schweizer Lieferant AG",
                     ort="8000 Zuerich", ustid="CHE-100.000.006 MWST",
                     iban="CH9300762011623852957", rechnungsnr="CH-2026-5",
                     netto=1000.0, satz=8.1, waehrung="CHF",
                     qr_zahlteil=True, qr_referenz_typ="QRR", qr_referenz=ref)
    res = process(p, store, use_llm=False)
    assert res.qr is not None
    assert res.qr["iban"] == "CH9300762011623852957"
    assert res.fields["zahlungsreferenz"].value == ref
    chk = {c.name: c for c in res.checks}
    assert chk["qr-referenz"].ok is True
    assert chk["qr-iban"].ok is True
    assert not any("qr-" in g["grund"] for g in human.gruende(res))


def test_swissqr_degradiert_ohne_zxingcpp(tmp_path, monkeypatch):
    """``zxingcpp`` fehlt (optionale Abhaengigkeit) -> ``read()`` gibt sauber
    ``None`` zurueck, kein Absturz; die Rechnung laeuft normal weiter."""
    import sys

    pytest.importorskip("zxingcpp")
    p = make_invoice(tmp_path / "qr_ohne_lib.pdf", lieferant="Schweizer Lieferant AG",
                     ort="8000 Zuerich", waehrung="CHF", qr_zahlteil=True,
                     qr_referenz_typ="NON")
    monkeypatch.setitem(sys.modules, "zxingcpp", None)
    assert swissqr.read(p) is None


def test_swiss_qr_iban_abweicht_geht_an_mensch(tmp_path):
    pytest.importorskip("zxingcpp")
    # Bibliothek fuehrt eine ANDERE IBAN als die im QR-Zahlteil gedruckte --
    # klassisches Betrugsmuster, wie bei jeder anderen Rechnung (Betrugsschutz).
    store = _store_ch(tmp_path, iban="CH3808888123456789012")
    ref = swissqr.build_qrr_reference("210000000003139471430009017")
    p = make_invoice(tmp_path / "qr_abweichung.pdf", lieferant="Schweizer Lieferant AG",
                     ort="8000 Zuerich", ustid="CHE-100.000.006 MWST",
                     iban="CH9300762011623852957", rechnungsnr="CH-2026-6",
                     netto=1000.0, satz=8.1, waehrung="CHF",
                     qr_zahlteil=True, qr_referenz_typ="QRR", qr_referenz=ref)
    res = process(p, store, use_llm=False)
    assert res.qr is not None
    chk = next(c for c in res.checks if c.name == "qr-iban")
    assert chk.ok is False
    assert any("qr-iban" in g["grund"] for g in human.gruende(res))
