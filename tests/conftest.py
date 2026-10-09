"""Synthetische Test-Rechnungen (digitale PDFs mit Textlayer)."""

from __future__ import annotations

import os
from pathlib import Path

# Die Tests pruefen die Standardwerte. Auf dem Laptop laedt der Starter vor dem
# Selbst-Update die echte rechnung.env (KI an, OCR aus, Postfach ...) -- ohne
# das hier fielen dort Tests durch und jedes Update wuerde zurueckgerollt.
# Muss vor dem ersten Import von rechnung.* passieren (Config liest beim Import).
for _k in [k for k in os.environ if k.startswith("RECHNUNG_")]:
    del os.environ[_k]
# Die bestehenden Tests pruefen den vollen Feldumfang; das Ausblenden einzelner
# Felder (Standard im Einsatz) testet tests/test_felder_aus.py gezielt.
os.environ["RECHNUNG_FELDER_AUS"] = ""
# Keine Hintergrund-Vorverarbeitung in Tests (nebenlaeufige KI-Mocks)
os.environ["RECHNUNG_VORVERARBEITUNG"] = "0"

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

W, H = A4


def make_invoice(path: Path, *, supplier: str, domain: str, ustid: str, iban: str,
                 invoice_no: str, datum: str, netto: str, ust: str, brutto: str,
                 ust_satz: str = "19,00", layout: str = "a") -> Path:
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, H - 60, supplier)
    c.setFont("Helvetica", 9)
    c.drawString(40, H - 74, f"{supplier} - Musterstrasse 1 - 12345 Musterstadt")
    c.drawString(40, H - 86, f"E-Mail: rechnung@{domain}")

    c.setFont("Helvetica", 10)
    if layout == "a":
        y = H - 160
        c.drawString(40, y, "Rechnungsnummer")
        c.drawString(200, y, invoice_no)
        c.drawString(40, y - 16, "Rechnungsdatum")
        c.drawString(200, y - 16, datum)
        ty = y - 90
        c.drawString(40, ty, "Nettobetrag")
        c.drawRightString(300, ty, netto + " EUR")
        c.drawString(40, ty - 16, "Umsatzsteuer")
        c.drawRightString(300, ty - 16, ust_satz + " %")
        c.drawRightString(300, ty - 30, ust + " EUR")
        c.drawString(40, ty - 48, "Rechnungsbetrag")
        c.drawRightString(300, ty - 48, brutto + " EUR")
    else:  # layout b: Werte rechts, andere Labels
        y = H - 200
        c.drawString(320, y, "Invoice No.")
        c.drawString(430, y, invoice_no)
        c.drawString(320, y - 16, "Date")
        c.drawString(430, y - 16, datum)
        ty = y - 80
        c.drawString(320, ty, "Net")
        c.drawRightString(520, ty, netto)
        c.drawString(320, ty - 16, "VAT " + ust_satz)
        c.drawRightString(520, ty - 16, ust)
        c.drawString(320, ty - 32, "Total")
        c.drawRightString(520, ty - 32, brutto)

    c.setFont("Helvetica", 8)
    c.drawString(40, 70, f"USt-IdNr: {ustid}")
    c.drawString(40, 58, f"IBAN: {iban}")
    c.drawString(40, 46, "Zahlbar innerhalb 14 Tagen ohne Abzug.")
    c.showPage()
    c.save()
    return path


SUPPLIERS = {
    "alpha": dict(supplier="Alpha Werkzeug GmbH", domain="alpha-werkzeug.de",
                  ustid="DE123456789", iban="DE89370400440532013000", layout="a"),
    "beta": dict(supplier="Beta Buerobedarf AG", domain="beta-buero.de",
                 ustid="DE987654321", iban="DE02120300000000202051", layout="b"),
}


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    d = tmp_path / "samples"
    d.mkdir()
    rows = [
        ("alpha", "AL-2026-0001", "05.01.2026", "1.000,00", "190,00", "1.190,00"),
        ("alpha", "AL-2026-0002", "11.02.2026", "2.500,00", "475,00", "2.975,00"),
        ("alpha", "AL-2026-0003", "03.03.2026", "800,00", "152,00", "952,00"),
        ("alpha", "AL-2026-0004", "09.04.2026", "1.234,56", "234,57", "1.469,13"),
        ("beta", "B-77012", "07.01.2026", "300,00", "57,00", "357,00"),
        ("beta", "B-77108", "19.02.2026", "90,00", "17,10", "107,10"),
        ("beta", "B-77233", "22.03.2026", "540,00", "102,60", "642,60"),
    ]
    for key, no, datum, netto, ust, brutto in rows:
        s = SUPPLIERS[key]
        make_invoice(d / f"{key}_{no}.pdf".replace("/", "-"),
                     invoice_no=no, datum=datum, netto=netto, ust=ust, brutto=brutto,
                     **s)
    return d


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture(autouse=True)
def _clear_llm_cache():
    """semantic.py cached Modellantworten je Prompt im Prozess -- zwischen
    Tests mit unterschiedlich gemockten Antworten darf nichts haengen bleiben."""
    from rechnung import semantic
    semantic._resp_cache.clear()
    semantic.set_cache_dir(None)
    yield
    semantic._resp_cache.clear()
    semantic.set_cache_dir(None)
