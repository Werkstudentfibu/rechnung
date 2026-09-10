"""Synthetische Beispielrechnungen (nur fuer Tests/Demo, bis echte PDFs da sind)."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def make_invoice(path: str | Path, *, lieferant="Muster Handels GmbH",
                 strasse="Industriestr. 5", ort="50667 Koeln",
                 ustid="DE123456789", iban="DE89370400440532013000",
                 domain="muster-handel.de", rechnungsnr="RE-2026-00042",
                 datum="14.02.2026", netto=1000.00, satz=19, layout="a") -> Path:
    path = Path(path)
    ust = round(netto * satz / 100, 2)
    brutto = round(netto + ust, 2)
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4

    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, h - 25 * mm, lieferant)
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, h - 31 * mm, f"{strasse} - {ort}")
    c.drawString(20 * mm, h - 36 * mm, f"E-Mail: buchhaltung@{domain}")
    c.drawString(20 * mm, h - 41 * mm, f"USt-IdNr.: {ustid}")

    c.setFont("Helvetica", 10)
    c.drawString(20 * mm, h - 60 * mm, "Beispiel Kunde AG")
    c.drawString(20 * mm, h - 65 * mm, "Bestellstr. 1")
    c.drawString(20 * mm, h - 70 * mm, "10115 Berlin")

    if layout == "a":
        x_label, x_val, y = 120 * mm, 160 * mm, h - 60 * mm
        rows = [
            ("Rechnungsnummer:", rechnungsnr),
            ("Rechnungsdatum:", datum),
            ("Bestellnummer:", "PO-77123"),
        ]
    else:  # zweite Layout-Variante fuer Wiederholungs-Tests
        x_label, x_val, y = 20 * mm, 70 * mm, h - 90 * mm
        rows = [
            ("Rechnung Nr.", rechnungsnr),
            ("Datum", datum),
            ("Ihre Bestellung", "PO-77123"),
        ]
    c.setFont("Helvetica", 10)
    for label, val in rows:
        c.drawString(x_label, y, label)
        c.drawString(x_val, y, str(val))
        y -= 7 * mm

    c.setFont("Helvetica", 10)
    ty = h - 150 * mm
    for label, val in [
        ("Nettobetrag", f"{netto:.2f} EUR"),
        (f"Umsatzsteuer {satz}%", f"{ust:.2f} EUR"),
        ("Gesamtbetrag", f"{brutto:.2f} EUR"),
    ]:
        c.drawString(20 * mm, ty, label)
        c.drawString(120 * mm, ty, val)
        ty -= 7 * mm

    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, 30 * mm, f"Zahlbar bis 28.02.2026 auf IBAN {iban}")
    c.showPage()
    c.save()
    return path
