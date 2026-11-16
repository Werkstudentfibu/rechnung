"""Aaron 16.11.2026: "Tabellen werden oft gar nicht angelegt. Aber die, die
angelegt wurden, sind meistens richtig." -- Die Kopfzeile wurde nur ueber
ganze, fest eingebaute Woerter erkannt ("Artikelbezeichnung",
"Leistungsbeschreibung", "Stk.", "E-Preis" fielen durch), brauchte drei
Spalten (kein "Bezeichnung | Betrag") und es zaehlte nur der erste
Kopf-Kandidat. Ohne erkennbaren Kopf gab es nie eine Tabelle. Dazu wurden
Summenzeilen wie "Summe"/"Netto" als weitere Position mitgelesen.

Alles mit erfundenen Rechnungen; jede neue Tabelle muss weiter die Rechen-
Pruefung bestehen (kein Raten)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rechnung import pipeline, tabelle
from rechnung.pdf import load
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

POSITIONEN = [("Tomaten passiert 2,5kg", 4, 3.95), ("Olivenoel extra vergine 5l", 2, 38.50),
              ("Mehl Type 405 25kg", 1, 21.90)]
NETTO = 114.70


def _de(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _rechnung(path: Path, spalten, *, kopf=True, summe="Nettobetrag", vorspann=None) -> Path:
    """``spalten``: (Kopftext, Art, rechte Kante in mm oder None = links
    buendig) -- Art: pos/artnr/text/menge/ep/gp/datum."""
    c = canvas.Canvas(str(path), pagesize=A4)
    _w, h = A4
    c.setFont("Helvetica-Bold", 14)
    c.drawString(20 * mm, h - 25 * mm, "Muster Lebensmittel GmbH")
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, h - 32 * mm, "Musterweg 1, 12345 Musterstadt  USt-IdNr. DE123456789")
    c.drawString(20 * mm, h - 50 * mm, "Rechnung Nr. 4711   Datum 03.11.2026")
    y = h - 62 * mm
    for zeile in vorspann or []:
        c.drawString(20 * mm, y, zeile[0])
        c.drawRightString(185 * mm, y, zeile[1])
        y -= 6 * mm
    y -= 6 * mm

    def _setze(text, art, kante):
        if kante is None:
            c.drawString((50 if art == "text" else 20) * mm, y, text)
        else:
            c.drawRightString(kante * mm, y, text)

    if kopf:
        c.setFont("Helvetica-Bold", 9)
        for label, art, kante in spalten:
            _setze(label, art, kante)
        y -= 7 * mm
    c.setFont("Helvetica", 9)
    for i, (txt, menge, ep) in enumerate(POSITIONEN, 1):
        werte = {"pos": str(i), "artnr": f"10{i}2{i}", "text": txt, "menge": f"{menge:g}",
                 "ep": _de(ep), "gp": _de(round(menge * ep, 2)), "datum": f"0{i}.11.2026"}
        for _label, art, kante in spalten:
            _setze(werte[art], art, kante)
        y -= 6 * mm
    y -= 4 * mm
    c.drawString(120 * mm, y, summe)
    c.drawRightString(190 * mm, y, _de(NETTO) + " EUR")
    y -= 5 * mm
    c.drawString(120 * mm, y, "Umsatzsteuer 7%")
    c.drawRightString(190 * mm, y, _de(round(NETTO * 0.07, 2)) + " EUR")
    y -= 5 * mm
    c.drawString(120 * mm, y, "Rechnungsbetrag")
    c.drawRightString(190 * mm, y, _de(round(NETTO * 1.07, 2)) + " EUR")
    c.drawString(20 * mm, 30 * mm, "IBAN DE89 3704 0044 0532 0130 00")
    c.save()
    return path


def _kurz(items):
    return [(it.get("beschreibung"), it.get("menge"), it.get("einzelpreis_netto"),
             it.get("gesamt_netto")) for it in items]


SOLL = [("Tomaten passiert 2,5kg", 4.0, 3.95, 15.8),
        ("Olivenoel extra vergine 5l", 2.0, 38.5, 77.0),
        ("Mehl Type 405 25kg", 1.0, 21.9, 21.9)]


@pytest.mark.parametrize("name,spalten", [
    ("artikelbezeichnung", [("Artikel-Nr.", "artnr", None), ("Artikelbezeichnung", "text", None),
                            ("Menge", "menge", 120), ("Preis", "ep", 150), ("Betrag", "gp", 185)]),
    ("leistungsbeschreibung", [("Leistungsbeschreibung", "text", None), ("Anzahl", "menge", 120),
                               ("E-Preis", "ep", 150), ("Gesamt", "gp", 185)]),
    ("stk_epreis", [("Stk.", "menge", 30), ("Beschreibung", "text", None),
                    ("E-Preis", "ep", 150), ("G-Preis", "gp", 185)]),
    ("netto_spalten", [("Art.-Nr.", "artnr", None), ("Warenbezeichnung", "text", None),
                       ("Liefermenge", "menge", 120), ("Nettopreis", "ep", 150),
                       ("Nettowert", "gp", 185)]),
    ("artikel_neben_bezeichnung", [("Artikel", "artnr", None), ("Bezeichnung", "text", None),
                                   ("Menge", "menge", 120), ("VK-Preis", "ep", 150),
                                   ("Wert", "gp", 185)]),
    ("englisch_mehrwortig", [("Item description", "text", None), ("Qty", "menge", 120),
                             ("Unit price", "ep", 150), ("Line total", "gp", 185)]),
])
def test_zusammengesetzte_und_abgekuerzte_kopfwoerter(tmp_path, name, spalten):
    doc = load(_rechnung(tmp_path / f"{name}.pdf", spalten))
    items = tabelle.extract(doc, text=doc.text)
    assert items is not None, name
    # Artikelnummer bleibt eine eigene (verworfene) Spalte -- nicht in der
    # Beschreibung, nicht als Positionsnummer
    assert _kurz(items) == SOLL
    assert [it["pos"] for it in items] == [1, 2, 3]


@pytest.mark.parametrize("spalten", [
    [("Bezeichnung", "text", None), ("Betrag", "gp", 185)],
    [("Datum", "datum", None), ("Text", "text", None), ("Betrag", "gp", 185)],
])
def test_zwei_spalten_nur_mit_passender_summe(tmp_path, spalten):
    """"Bezeichnung | Betrag", Hotel-Folio "Datum | Text | Betrag": keine
    Zeilen-Mathematik moeglich -> nur, wenn die Summe zum Netto passt."""
    doc = load(_rechnung(tmp_path / "zwei.pdf", spalten))
    assert tabelle.extract(doc, text=doc.text) is None
    assert tabelle.extract(doc, netto=120.0, text=doc.text) is None
    items = tabelle.extract(doc, netto=NETTO, text=doc.text)
    assert [(it["beschreibung"], it["gesamt_netto"]) for it in items] == [
        (b, g) for b, _m, _e, g in SOLL]


@pytest.mark.parametrize("summe", ["Summe", "Summe netto", "Netto", "Netto-Betrag",
                                   "Gesamt netto", "Endsumme", "Positionen gesamt"])
def test_summenzeile_ist_keine_position(tmp_path, summe):
    spalten = [("Pos", "pos", None), ("Bezeichnung", "text", None), ("Menge", "menge", 120),
               ("Einzelpreis", "ep", 150), ("Gesamtpreis", "gp", 185)]
    doc = load(_rechnung(tmp_path / "s.pdf", spalten, summe=summe))
    items = tabelle.extract(doc, text=doc.text)
    assert _kurz(items) == SOLL


def test_kopf_im_anschreiben_verdeckt_die_tabelle_nicht(tmp_path):
    """Eine Zeile oberhalb, die wie ein (zweispaltiger) Kopf aussieht, aber
    nicht aufgeht, darf die echte Tabelle darunter nicht verdecken."""
    spalten = [("Bezeichnung", "text", None), ("Menge", "menge", 120),
               ("Einzelpreis", "ep", 150), ("Gesamtpreis", "gp", 185)]
    p = _rechnung(tmp_path / "v.pdf", spalten,
                  vorspann=[("Leistung laut Angebot", "Betrag"), ("Anzahlung erhalten", "50,00")])
    doc = load(p)
    assert _kurz(tabelle.extract(doc, text=doc.text)) == SOLL


def test_mehrzeiliger_kopf_mit_netto_zeile_bleibt_kopf():
    """"Betrag / Netto / EUR" untereinander: "Netto" ohne Betrag beendet
    nichts (nur "Netto 98,30" ist eine Summenzeile)."""
    from rechnung.pdf import Document, Page, Word

    def w(t, x, y):
        return Word(t, x, x + 8 * len(t), y, y + 9, 0)

    woerter = [w("Bezeichnung", 60, 176), w("Menge", 300, 176), w("Betrag", 420, 176),
               w("Netto", 420, 186), w("EUR", 420, 196),
               w("Diesel", 60, 210), w("2", 300, 210), w("209,91", 420, 210),
               w("Super", 60, 222), w("1", 300, 222), w("661,28", 420, 222),
               w("Netto", 60, 240), w("871,19", 420, 240)]
    doc = Document(path=None, pages=[Page(index=0, width=842, height=595, words=woerter)],
                   sha256="x")
    items = tabelle.extract(doc, netto=871.19, text="")
    assert [(i["beschreibung"], i["gesamt_netto"]) for i in items] == [
        ("Diesel", 209.91), ("Super", 661.28)]


@pytest.mark.parametrize("name,spalten", [
    ("ohne_kopf", [("", "text", None), ("", "menge", 120), ("", "ep", 150), ("", "gp", 185)]),
    ("ohne_kopf_mit_pos", [("", "pos", None), ("", "text", None), ("", "menge", 120),
                           ("", "ep", 150), ("", "gp", 185)]),
    ("ocr_verlesen", [("Bezeichnurg", "text", None), ("Mengc", "menge", 120),
                      ("Prels", "ep", 150), ("Betrog", "gp", 185)]),
])
def test_ohne_erkannten_kopf_nur_mit_netto(tmp_path, name, spalten):
    doc = load(_rechnung(tmp_path / f"{name}.pdf", spalten, kopf=name != "ohne_kopf"
                         and name != "ohne_kopf_mit_pos"))
    assert tabelle.extract(doc, text=doc.text) is None          # ohne Netto: nie
    assert tabelle.extract(doc, netto=120.0, text=doc.text) is None  # Summe passt nicht
    items = tabelle.extract(doc, netto=NETTO, text=doc.text)
    assert _kurz(items) == SOLL
    assert all(it["_quelle"] == "tabelle_ohne_kopf" for it in items)


def test_ohne_kopf_nur_betragsspalte(tmp_path):
    doc = load(_rechnung(tmp_path / "b.pdf", [("", "text", None), ("", "gp", 185)], kopf=False))
    items = tabelle.extract(doc, netto=NETTO, text=doc.text)
    assert [(i["beschreibung"], i["gesamt_netto"]) for i in items] == [
        (b, g) for b, _m, _e, g in SOLL]
    assert all("menge" not in i for i in items)


def test_ust_aufstellung_wird_keine_positionstabelle(tmp_path):
    """Nur eine USt-Aufstellung (zwei Saetze), keine Positionen: deren
    Netto-Spalte summiert sich zwar zum Netto -- ist aber keine Tabelle."""
    p = tmp_path / "ust.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    _w, h = A4
    c.setFont("Helvetica", 9)
    c.drawString(20 * mm, h - 30 * mm, "Muster Service GmbH  Rechnung 77")
    c.drawString(20 * mm, h - 60 * mm, "Fuer die Wartung gemaess Vertrag berechnen wir:")
    y = h - 80 * mm
    for satz, n in [(7, 50.00), (19, 48.30)]:
        c.drawString(20 * mm, y, f"{satz} % MwSt")
        c.drawRightString(90 * mm, y, _de(n))
        c.drawRightString(130 * mm, y, _de(round(n * satz / 100, 2)))
        c.drawRightString(185 * mm, y, _de(round(n * (1 + satz / 100), 2)))
        y -= 6 * mm
    c.save()
    doc = load(p)
    assert tabelle.extract(doc, netto=98.30, brutto=111.17, text=doc.text) is None


def test_pipeline_legt_tabelle_an(tmp_path):
    """Durch die ganze Pipeline (ohne KI, unbekannter Lieferant): Kopf mit
    zusammengesetzten Woertern -> Positionen; ohne Kopf -> Positionen ueber
    den zweiten Versuch mit dem gelesenen Netto."""
    store = Store(tmp_path / "data")
    spalten = [("Artikel-Nr.", "artnr", None), ("Artikelbezeichnung", "text", None),
               ("Menge", "menge", 120), ("Preis", "ep", 150), ("Betrag", "gp", 185)]
    res = pipeline.process(_rechnung(tmp_path / "a.pdf", spalten), store, use_llm=False)
    assert _kurz(res.line_items) == SOLL

    ohne = [("", "text", None), ("", "menge", 120), ("", "ep", 150), ("", "gp", 185)]
    res2 = pipeline.process(_rechnung(tmp_path / "b.pdf", ohne, kopf=False), store,
                            use_llm=False)
    assert _kurz(res2.line_items) == SOLL
