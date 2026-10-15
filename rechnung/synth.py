"""Synthetische Beispielrechnungen (nur fuer Tests/Demo, bis echte PDFs da sind)."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from .swissqr import build_spc

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


def _ch_amount(v: float) -> str:
    """Schweizer Schreibweise mit Hochkomma als Tausendertrenner ('1'234.50')."""
    return f"{v:,.2f}".replace(",", "'")


def _draw_qr(c: canvas.Canvas, x_mm: float, y_mm: float, size_mm: float, spc: str) -> bool:
    """SPC-Inhalt als QR-Code auf die Seite zeichnen (``zxingcpp``, optionale
    Abhaengigkeit wie in swissqr.py) -> True wenn gezeichnet, False wenn
    ``zxingcpp`` nicht installiert ist (dann bleibt die Seite ohne QR-Code,
    kein Fehler)."""
    try:
        import zxingcpp
    except ImportError:
        return False
    from PIL import Image

    bc = zxingcpp.create_barcode(spc, zxingcpp.QRCode)
    img = zxingcpp.write_barcode_to_image(bc, scale=8)
    h_px, w_px = img.shape
    pil = Image.frombuffer("L", (w_px, h_px), bytes(memoryview(img)), "raw", "L", 0, 1)
    c.drawImage(ImageReader(pil), x_mm * mm, y_mm * mm, size_mm * mm, size_mm * mm)
    return True


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


# Englische Kopfzeile (Aaron 12.10.2026, Testsatz "englische Rechnung") --
# dieselben Spaltenschluessel wie _LI_HEADERS, nur die sichtbaren Labels
# uebersetzt.
_LI_HEADERS_EN = [
    ("Description", None),
    ("Qty", "menge"),
    ("Unit Price", "einzelpreis"),
    ("Amount", "gesamtpreis"),
    ("VAT", "mwst"),
    ("Currency", "waehrung"),
]


def _draw_items_table(c: canvas.Canvas, h: float, y_top: float, items: list[dict],
                      headers: list[tuple[str, str | None]] = _LI_HEADERS) -> float:
    """Zeichnet die Positionstabelle ab ``y_top`` (Punkte von unten) und gibt
    die y-Position unterhalb der letzten Zeile zurueck."""
    x0 = 20 * mm
    right_edge = 20 + _LI_RIGHT_EDGES["waehrung"]
    y = y_top
    c.setFont("Helvetica-Bold", 9)
    for label, key in headers:
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


def make_plain_doc(path: str | Path, *, titel: str, zeilen: list[str] | None = None,
                   seite2_titel: str | None = None) -> Path:
    """Einfaches PDF nur mit Titelzeile + ein paar Textzeilen -- fuer Tests
    rund um "Nicht-Rechnung aussortieren" (Lieferschein, Angebot, ...), ohne
    den ganzen Aufbau von ``make_invoice``. ``seite2_titel``: optional eine
    zweite Seite mit eigenem Titel (Test "Rechnung + Lieferschein in einem
    PDF" -- ``dubletten.title_text`` sieht ohnehin nur Seite 1)."""
    path = Path(path)
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20 * mm, h - 25 * mm, titel)
    c.setFont("Helvetica", 10)
    y = h - 45 * mm
    for zeile in zeilen or []:
        c.drawString(20 * mm, y, zeile)
        y -= 7 * mm
    c.showPage()
    if seite2_titel:
        c.setFont("Helvetica-Bold", 16)
        c.drawString(20 * mm, h - 25 * mm, seite2_titel)
        c.showPage()
    c.save()
    return path


def make_invoice(path: str | Path, *, lieferant="Muster Handels GmbH",
                 strasse="Industriestr. 5", ort="50667 Koeln",
                 ustid="DE123456789", iban="DE89370400440532013000",
                 domain="muster-handel.de", rechnungsnr="RE-2026-00042",
                 datum="14.02.2026", netto=1000.00, satz=19, waehrung="EUR",
                 layout="a", items: list[dict] | None = None,
                 zahlungstext: str | None = None,
                 ch_zahlenformat: bool = False,
                 qr_zahlteil: bool = False, qr_referenz_typ: str = "NON",
                 qr_referenz: str = "", sprache: str = "de") -> Path:
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

    # Englische Rechnung (Aaron 12.10.2026, Testsatz): nur die sichtbaren
    # Labels uebersetzt, Layout/Positionen unveraendert.
    en = sprache == "en"
    nr_label_a, nr_label_b = ("Invoice No.:", "Invoice No.") if en else \
        ("Rechnungsnummer:", "Rechnung Nr.")
    datum_label_a, datum_label_b = ("Invoice Date:", "Date") if en else \
        ("Rechnungsdatum:", "Datum")
    netto_label = "Net amount" if en else "Nettobetrag"
    ust_label = "VAT" if en else "Umsatzsteuer"
    brutto_label = "Total amount" if en else "Gesamtbetrag"
    headers = _LI_HEADERS_EN if en else _LI_HEADERS

    if layout == "a":
        x_label, x_val, y = 120 * mm, 160 * mm, h - 60 * mm
        rows = [
            (nr_label_a, rechnungsnr),
            (datum_label_a, datum),
        ]
        table_top = h - 100 * mm
    else:  # zweite Layout-Variante fuer Wiederholungs-Tests
        x_label, x_val, y = 20 * mm, 70 * mm, h - 90 * mm
        rows = [
            (nr_label_b, rechnungsnr),
            (datum_label_b, datum),
        ]
        table_top = h - 120 * mm
    c.setFont("Helvetica", 10)
    for label, val in rows:
        c.drawString(x_label, y, label)
        c.drawString(x_val, y, str(val))
        y -= 7 * mm

    table_bottom = _draw_items_table(c, h, table_top, items, headers=headers)

    fmt = _ch_amount if ch_zahlenformat else (lambda v: f"{v:.2f}")
    c.setFont("Helvetica", 10)
    ty = table_bottom - 8 * mm
    for label, val in [
        (netto_label, f"{fmt(netto)} {waehrung}"),
        (f"{ust_label} {satz}%" if not custom_items else ust_label, f"{fmt(ust)} {waehrung}"),
        (brutto_label, f"{fmt(brutto)} {waehrung}"),
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

    if qr_zahlteil:
        # Swiss QR-Rechnung (Postfach-Baustein 7): "Zahlteil" als Text (damit
        # swissqr.likely() den QR-Code ueberhaupt sucht) + der QR-Code selbst.
        c.setFont("Helvetica-Bold", 11)
        c.drawString(120 * mm, 60 * mm, "Zahlteil")
        spc = build_spc(iban=iban, creditor_name=lieferant, creditor_city=ort,
                        betrag=brutto, waehrung=waehrung,
                        debtor_name="Beispiel Kunde AG", debtor_city="10115 Berlin",
                        referenz_typ=qr_referenz_typ, referenz=qr_referenz)
        _draw_qr(c, 120, 10, 46, spc)
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


def make_multipage_table_invoice(path: str | Path, *, lieferant="Muster Handels GmbH",
                                 ustid="DE123456789", iban="DE89370400440532013000",
                                 rechnungsnr="RE-2026-00200", datum="14.02.2026",
                                 seiten: int = 3, positionen_je_seite: int = 4,
                                 satz: float = 19, waehrung="EUR") -> dict:
    """Mehrseitige Rechnung MIT echter Tabellen-Kopfzeile (wie
    ``_draw_items_table``, auf jeder Seite wiederholt) und "Uebertrag" am
    Ende jeder Seite ausser der letzten -- fuer den Testsatz von Baustein 2
    (``tabelle.py``) ueber Seitenwechsel hinweg (Aaron 12.10.2026)."""
    path = Path(path)
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    netto = 0.0
    pos = 0
    for k in range(1, seiten + 1):
        c.setFont("Helvetica-Bold", 14)
        c.drawString(20 * mm, h - 20 * mm, lieferant)
        c.setFont("Helvetica", 9)
        c.drawString(20 * mm, h - 26 * mm, f"USt-IdNr.: {ustid}")
        if k == 1:
            c.setFont("Helvetica", 10)
            c.drawString(120 * mm, h - 40 * mm, "Rechnungsnummer:")
            c.drawString(160 * mm, h - 40 * mm, rechnungsnr)
            c.drawString(120 * mm, h - 47 * mm, "Rechnungsdatum:")
            c.drawString(160 * mm, h - 47 * mm, datum)
        items = []
        for _ in range(positionen_je_seite):
            pos += 1
            preis = round(10 + pos * 1.25, 2)
            items.append({"beschreibung": f"Artikel {pos:04d}", "menge": 1,
                         "einzelpreis": preis, "gesamtpreis": preis, "mwst": satz,
                         "waehrung": waehrung})
            netto = round(netto + preis, 2)
        table_bottom = _draw_items_table(c, h, h - 70 * mm, items)
        if k < seiten:
            c.setFont("Helvetica", 9)
            c.drawString(20 * mm, table_bottom - 4 * mm,
                        f"Uebertrag {_de_amount(netto)} {waehrung}")
        else:
            ust = round(netto * satz / 100, 2)
            brutto = round(netto + ust, 2)
            ty = table_bottom - 8 * mm
            c.setFont("Helvetica", 10)
            for label, val in (("Nettobetrag", netto), (f"Umsatzsteuer {satz:g}%", ust),
                               ("Gesamtbetrag", brutto)):
                c.drawString(20 * mm, ty, label)
                c.drawString(120 * mm, ty, f"{_de_amount(val)} {waehrung}")
                ty -= 7 * mm
            c.setFont("Helvetica", 9)
            c.drawString(20 * mm, 30 * mm, f"Zahlbar innerhalb 14 Tagen auf IBAN {iban}")
        c.showPage()
    c.save()
    ust_ges = round(netto * satz / 100, 2)
    return {"rechnungsnummer": rechnungsnr, "netto": netto, "ust": ust_ges,
           "brutto": round(netto + ust_ges, 2), "seiten": seiten, "path": path}


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
