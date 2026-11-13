"""Summenzeile als Anker (Nachtlauf 12./13.11.2026, Amazon Business): der
Nettobetrag steht in der Steuersatz-Zeile und in der Zeile "USt. Gesamt";
nur "Gesamt" ist ein eindeutiger Anker -> lernen statt "mehrdeutig"."""

from rechnung import learn
from rechnung.pdf import Document, Page, Word


def _w(text, x0, top, page=0):
    return Word(text=text, x0=x0, x1=x0 + 5.5 * len(text), top=top, bottom=top + 7.5, page=page)


def _amazon_doc(gesamt_wort="Gesamt"):
    woerter = [
        _w("Zahlbetrag", 344, 183), _w("125,06", 412, 183), _w("\u20ac", 450, 183),
        _w("USt.", 60, 581), _w("%", 82, 581), _w("Zwischensumme", 400, 581), _w("USt.", 480, 581),
        _w("(ohne", 400, 590), _w("USt.)", 430, 590),
        _w("19.0%", 300, 615), _w("105,10", 412, 615), _w("\u20ac", 450, 615),
        _w("19,96", 480, 615), _w("\u20ac", 510, 615),
        _w("USt.", 290, 638), _w(gesamt_wort, 318, 638), _w("105,10", 412, 638), _w("\u20ac", 450, 638),
        _w("19,96", 480, 638), _w("\u20ac", 510, 638),
        _w("Zwischensumme", 400, 470), _w("USt.", 100, 470), _w("USt.", 200, 470), _w("USt.", 150, 300),
    ]
    return Document(path=None, pages=[Page(index=0, width=595, height=842, words=woerter)], sha256="x")


def test_netto_und_ust_lernen_an_der_gesamtzeile():
    doc = _amazon_doc()
    for fname, wert in (("netto", 105.10), ("ust_betrag", 19.96)):
        span, anker, grund = learn.locate(doc, fname, wert, "amount")
        assert grund == "", (fname, grund)
        assert anker.text == "Gesamt" and abs(span[0].top - 638) < 1


def test_ohne_summenwort_bleibt_es_mehrdeutig():
    doc = _amazon_doc(gesamt_wort="Hinweis")
    span, anker, grund = learn.locate(doc, "netto", 105.10, "amount")
    assert span is None and "mehrdeutig" in grund


def test_summenzeile_gilt_nur_fuer_summenfelder():
    doc = _amazon_doc()
    span, anker, grund = learn.locate(doc, "kostenstelle", "105,10", "text")
    assert span is None
