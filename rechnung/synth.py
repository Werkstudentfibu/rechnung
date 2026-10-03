"""Synthetische Beispielrechnungen (nur fuer Tests/Demo, bis echte PDFs da sind)."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

# Rechte Kante je Spalte der Positionstabelle, als mm-Offset ab dem linken
# Rand (20mm). "Beschreibung" ist die einzige linksbuendige Spalte.
_LI_RIGHT_EDGES = {
    "menge": 98, "einzelpreis": 126, "gesamtpreis": 150, "mwst": 163, "waehrung": 190,
}
_LI_HEADERS = [
    ("Beschreibung", None),
    ("Menge", "menge"),
    ("Einzelpreis", "einzelpreis"),
    ("Gesamtpreis", "gesamtpreis"),
    ("MwSt", "mwst"),
    ("Währung", "waehrung"),
]


def _de_amount(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _default_items(netto: float, satz: float, waehrung: str) -> list[dict]:
    """Realistische Standard-Positionen, deren Summe exakt ``netto`` ergibt --
    die letzte Position nimmt den Rundungsrest auf, damit z.B. netto=99.9
    weiterhin exakt aufgeht."""
    plan = [
        ("Ersatzteile Serie X200", 0.55),
        ("Montagezubehoer", 0.30),
        ("Versand- und Verpackungskosten", 0.15),
    ]
    items: list[dict] = []
    used = 0.0
    for i, (name, frac) in enumerate(plan):
        amt = round(netto * frac, 2) if i < len(plan) - 1 else round(netto - used, 2)
        used = round(used + amt, 2)
        items.append({"beschreibung": name, "menge": 1, "einzelpreis": amt,
                      "gesamtpreis": amt, "mwst": satz, "waehrung": waehrung})
    return items


def _draw_items_table(c: canvas.Canvas, h: float, y_top: float, items: list[dict]) -> float:
    """Zeichnet die Positionstabelle ab ``y_top`` (Punkte von unten) und gibt
    die y-Position unterhalb der letzten Zeile zurueck."""
    x0 = 20 * mm
    right_edge = 20 + _LI_RIGHT_EDGES["waehrung"]
    y = y_top
    c.setFont("Helvetica-Bold", 9)
    for label, key in _LI_HEADERS:
        if key is None:
            c.drawString(x0, y, label)
        else:
            c.drawRightString((20 + _LI_RIGHT_EDGES[key]) * mm, y, label)
    y -= 2 * mm
    c.line(x0, y, right_edge * mm, y)
    y -= 5 * mm

    c.setFont("Helvetica", 9)
    for it in items:
        menge = it.get("menge", 1)
        einzelpreis = it["einzelpreis"]
        gesamtpreis = it.get("gesamtpreis", round(menge * einzelpreis, 2))
        c.drawString(x0, y, str(it["beschreibung"]))
        c.drawRightString((20 + _LI_RIGHT_EDGES["menge"]) * mm, y, f"{menge:g}")
        c.drawRightString((20 + _LI_RIGHT_EDGES["einzelpreis"]) * mm, y, _de_amount(einzelpreis))
        c.drawRightString((20 + _LI_RIGHT_EDGES["gesamtpreis"]) * mm, y, _de_amount(gesamtpreis))
        c.drawRightString((20 + _LI_RIGHT_EDGES["mwst"]) * mm, y, f"{it['mwst']:g}%")
        c.drawRightString(right_edge * mm, y, str(it.get("waehrung", "EUR")))
        y -= 6 * mm
    y -= 1 * mm
    c.line(x0, y, right_edge * mm, y)
    return y - 4 * mm


def make_invoice(path: str | Path, *, lieferant="Muster Handels GmbH",
                 strasse="Industriestr. 5", ort="50667 Koeln",
                 ustid="DE123456789", iban="DE89370400440532013000",
                 domain="muster-handel.de", rechnungsnr="RE-2026-00042",
                 datum="14.02.2026", netto=1000.00, satz=19, waehrung="EUR",
                 layout="a", items: list[dict] | None = None) -> Path:
    path = Path(path)
    custom_items = items is not None
    if items is None:
        items = _default_items(netto, satz, waehrung)

    if custom_items:
        # Bei frei vorgegebenen Positionen (z.B. mehrere MwSt-Saetze in einer
        # Rechnung) ergeben sich Netto/USt/Brutto aus den Positionen selbst,
        # nicht mehr aus dem einzelnen ``satz``-Parameter.
        by_rate: dict[float, float] = {}
        for it in items:
            g = it.get("gesamtpreis", round(it.get("menge", 1) * it["einzelpreis"], 2))
            by_rate[it["mwst"]] = round(by_rate.get(it["mwst"], 0.0) + g, 2)
        netto = round(sum(by_rate.values()), 2)
        ust = round(sum(round(v * r / 100, 2) for r, v in by_rate.items()), 2)
    else:
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
        ]
        table_top = h - 100 * mm
    else:  # zweite Layout-Variante fuer Wiederholungs-Tests
        x_label, x_val, y = 20 * mm, 70 * mm, h - 90 * mm
        rows = [
            ("Rechnung Nr.", rechnungsnr),
            ("Datum", datum),
        ]
        table_top = h - 120 * mm
    c.setFont("Helvetica", 10)
    for label, val in rows:
        c.drawString(x_label, y, label)
        c.drawString(x_val, y, str(val))
        y -= 7 * mm

    table_bottom = _draw_items_table(c, h, table_top, items)

    c.setFont("Helvetica", 10)
    ty = table_bottom - 8 * mm
    for label, val in [
        ("Nettobetrag", f"{netto:.2f} {waehrung}"),
        (f"Umsatzsteuer {satz}%" if not custom_items else "Umsatzsteuer", f"{ust:.2f} {waehrung}"),
        ("Gesamtbetrag", f"{brutto:.2f} {waehrung}"),
    ]:
        c.drawString(20 * mm, ty, label)
        c.drawString(120 * mm, ty, val)
        ty -= 7 * mm

    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, 30 * mm, f"Zahlbar bis 28.02.2026 auf IBAN {iban}")
    c.showPage()
    c.save()
    return path
