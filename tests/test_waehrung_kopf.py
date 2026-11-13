"""Waehrung: "€" ist EUR, Positionen ohne Waehrungsspalte erben die
Kopf-Waehrung (Wunsch Aaron 13.11.2026)."""

from rechnung import rechnen, waehrung
from rechnung.template import FieldResult


def _f(name, value):
    return FieldResult(name, value, str(value), 0.9, "template")


def test_euro_zeichen_wird_eur_und_positionen_erben():
    fields = {"waehrung": _f("waehrung", "€")}
    items = [{"beschreibung": "A", "gesamt_netto": 10.0}, {"beschreibung": "B", "waehrung": "€"}]
    rk = rechnen.rechne(fields, items, "")
    assert rk.fields["waehrung"].value == "EUR"
    assert [it["waehrung"] for it in rk.line_items] == ["EUR", "EUR"]
    assert rk.line_items[0]["_herkunft"]["waehrung"] == rechnen.KOPF


def test_chf_kopf_wird_uebernommen():
    rk = rechnen.rechne({"waehrung": _f("waehrung", "CHF")}, [{"beschreibung": "A"}], "")
    assert rk.line_items[0]["waehrung"] == "CHF"


def test_ohne_hinweis_eur_angenommen_bei_fremdwaehrung_leer():
    assert rechnen.rechne({}, [{"beschreibung": "A"}], "Rechnung 100,00")
    assert rechnen.rechne({}, [{"beschreibung": "A"}], "Rechnung 100,00").line_items[0]["waehrung"] == "EUR"
    assert not rechnen.rechne({}, [{"beschreibung": "A"}], "Total 100.00 USD").line_items[0].get("waehrung")


def test_pruefung_akzeptiert_euro_zeichen():
    (c,) = waehrung.checks({"waehrung": _f("waehrung", "€")}, "")
    assert c.ok and c.detail == "EUR"
