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
                 layout="a", items: list[dict] | None = None,
                 zahlungstext: str | None = None) -> Path:
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
    if zahlungstext is not None:
        # eigene Zahlungsbedingungen (Faelligkeit/Skonto-Tests), IBAN darunter
        c.drawString(20 * mm, 36 * mm, zahlungstext)
        c.drawString(20 * mm, 30 * mm, f"Bankverbindung IBAN {iban}")
    else:
        c.drawString(20 * mm, 30 * mm, f"Zahlbar bis 28.02.2026 auf IBAN {iban}")
    c.showPage()
    c.save()
    return path


# ---- Mehrseitige Rechnungen / Sammel-PDFs (Baustein 2/3) -------------------

def _draw_multipage(c: canvas.Canvas, *, lieferant: str, ustid: str, iban: str,
                    rechnungsnr: str, datum: str, seiten: int, satz: float,
                    waehrung: str, positionen_je_seite: int, zaehler: bool,
                    nummer_jede_seite: bool) -> dict:
    """Eine mehrseitige Rechnung auf ``c`` zeichnen (Kopf auf jeder Seite,
    Positionen ueber alle Seiten, Summenblock + Bank nur auf der letzten).
    -> Sollwerte (netto/ust/brutto)."""
    w, h = A4
    netto = 0.0
    pos = 0
    for k in range(1, seiten + 1):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(20 * mm, h - 20 * mm, lieferant)
        c.setFont("Helvetica", 9)
        c.drawString(20 * mm, h - 26 * mm, f"USt-IdNr.: {ustid}")
        if zaehler:
            c.drawRightString(190 * mm, h - 20 * mm, f"Seite {k} von {seiten}")
        if k == 1 or nummer_jede_seite:
            c.setFont("Helvetica", 10)
            c.drawString(120 * mm, h - 40 * mm, "Rechnungsnummer:")
            c.drawString(160 * mm, h - 40 * mm, rechnungsnr)
        if k == 1:
            c.drawString(120 * mm, h - 47 * mm, "Rechnungsdatum:")
            c.drawString(160 * mm, h - 47 * mm, datum)
            c.drawString(20 * mm, h - 50 * mm, "Beispiel Kunde AG, Bestellstr. 1, 10115 Berlin")
        y = h - 70 * mm
        c.setFont("Helvetica", 9)
        for _ in range(positionen_je_seite):
            pos += 1
            betrag = round(10 + pos * 1.25, 2)
            netto = round(netto + betrag, 2)
            c.drawString(20 * mm, y, f"Pos. {pos}  Artikel {pos:04d}")
            c.drawRightString(150 * mm, y, _de_amount(betrag))
            y -= 6 * mm
        if k < seiten:
            c.drawString(20 * mm, y - 4 * mm, f"Uebertrag {_de_amount(netto)}")
        else:
            ust = round(netto * satz / 100, 2)
            brutto = round(netto + ust, 2)
            ty = y - 10 * mm
            for label, val in (("Nettobetrag", netto), (f"Umsatzsteuer {satz:g}%", ust),
                               ("Gesamtbetrag", brutto)):
                c.setFont("Helvetica", 10)
                c.drawString(20 * mm, ty, label)
                c.drawString(120 * mm, ty, f"{_de_amount(val)} {waehrung}")
                ty -= 7 * mm
            c.setFont("Helvetica", 9)
            c.drawString(20 * mm, 30 * mm, f"Zahlbar innerhalb 14 Tagen auf IBAN {iban}")
        c.showPage()
    return {"rechnungsnummer": rechnungsnr, "netto": netto, "ust": ust, "brutto": brutto,
            "seiten": seiten}


def make_multipage_invoice(path: str | Path, *, seiten: int = 12,
                           lieferant="Muster Handels GmbH", ustid="DE123456789",
                           iban="DE89370400440532013000", rechnungsnr="RE-2026-00100",
                           datum="14.02.2026", satz: float = 19, waehrung="EUR",
                           positionen_je_seite: int = 8, zaehler: bool = True,
                           nummer_jede_seite: bool = True) -> dict:
    """Lange Rechnung (Standard 12 Seiten) -> Sollwerte inkl. ``path``."""
    path = Path(path)
    c = canvas.Canvas(str(path), pagesize=A4)
    soll = _draw_multipage(c, lieferant=lieferant, ustid=ustid, iban=iban,
                           rechnungsnr=rechnungsnr, datum=datum, seiten=seiten, satz=satz,
                           waehrung=waehrung, positionen_je_seite=positionen_je_seite,
                           zaehler=zaehler, nummer_jede_seite=nummer_jede_seite)
    c.save()
    return dict(soll, path=path)


def make_sammel_pdf(path: str | Path, rechnungen: list[dict], **common) -> list[dict]:
    """Mehrere (ggf. mehrseitige) Rechnungen in EINEM PDF. ``rechnungen``:
    je Rechnung die Parameter von ``make_multipage_invoice`` (mind.
    ``rechnungsnr``, ``seiten``). -> Sollwerte je Rechnung."""
    path = Path(path)
    c = canvas.Canvas(str(path), pagesize=A4)
    out = []
    defaults = dict(lieferant="Muster Handels GmbH", ustid="DE123456789",
                    iban="DE89370400440532013000", datum="14.02.2026", satz=19,
                    waehrung="EUR", positionen_je_seite=5, zaehler=True,
                    nummer_jede_seite=True)
    for r in rechnungen:
        kw = dict(defaults, **common, **r)
        out.append(_draw_multipage(c, **kw))
    c.save()
    return out
