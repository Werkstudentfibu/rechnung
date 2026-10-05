"""Selbstlernen 2, Baustein 2: Rechenstufe -- nachrechnen UND ergaenzen.

Jede Zelle bekommt eine Herkunft (gelesen/errechnet/kopf/korrigiert).
Errechnet wird NUR aus gelesenen Werten; ist etwas nicht eindeutig, bleibt
die Zelle leer und der Grund landet in ``RechenResult.gruende`` statt dass
geraten wird. Nachrechnen (rot/gruen) bleibt Sache von ``validate.py``
(Baustein 9) -- hier wird nur gegen den vervollstaendigten Stand geprueft."""

from __future__ import annotations

from rechnung import human, rechnen, validate
from rechnung.pipeline import AUTO, Result
from rechnung.shadow import REQUIRED
from rechnung.template import FieldResult


def _fr(name, value, source="template-anchor"):
    return FieldResult(name, value, str(value), 0.9, source)


def _result(fields, line_items=None, text=""):
    rk = rechnen.rechne(fields, line_items or [], text)
    checks = validate.run_all(rk.fields, None, lambda *a: False, rk.line_items, text)
    return rk, Result(file="x", status=AUTO, fields=rk.fields, line_items=rk.line_items,
                      checks=checks)


# ---- 1. Nur Brutto + Satz bekannt -----------------------------------------

def test_nur_brutto_und_satz_ergaenzt_netto_und_ust():
    fields = {
        "rechnungsnummer": _fr("rechnungsnummer", "RE-1"),
        "rechnungsdatum": _fr("rechnungsdatum", "2026-01-15"),
        "brutto": _fr("brutto", 400.0),
        "ust_satz": _fr("ust_satz", 19.0),
        "waehrung": _fr("waehrung", "EUR"),
    }
    rk, res = _result(fields)
    assert rk.fields["netto"].value == 336.13
    assert rk.fields["netto"].source == "errechnet"
    assert rk.fields["ust_betrag"].value == 63.87
    assert rk.fields["ust_betrag"].source == "errechnet"
    assert rechnen.herkunft(rk.fields["netto"].source) == rechnen.ERRECHNET

    # Status nicht wegen eines fehlenden Netto an den Menschen -- es wurde
    # ja errechnet, nicht geraten.
    gruende = human.gruende(res)
    assert not any("netto" in g.get("grund", "") and g["art"] == "pflichtfeld" for g in gruende)
    assert gruende == []


# ---- 2. Alles gelesen und stimmig -> gruen --------------------------------

def test_alles_gelesen_und_stimmig_bleibt_unveraendert_und_gruen():
    fields = {
        "netto": _fr("netto", 100.0), "ust_betrag": _fr("ust_betrag", 19.0),
        "brutto": _fr("brutto", 119.0), "ust_satz": _fr("ust_satz", 19.0),
    }
    rk, res = _result(fields)
    assert rk.ergaenzt == []
    for name in ("netto", "ust_betrag", "brutto", "ust_satz"):
        assert rk.fields[name].source == "template-anchor"
    assert all(c.ok is not False for c in res.checks)


# ---- 3. Gelesenes Netto passt nicht zu Brutto/Satz -> menschlich ---------

def test_widerspruechliches_netto_wird_nicht_ueberschrieben_und_faellt_auf():
    fields = {"netto": _fr("netto", 100.0), "ust_betrag": _fr("ust_betrag", 19.0),
              "brutto": _fr("brutto", 200.0)}   # muesste 119.00 sein
    rk, res = _result(fields)
    # Netto/USt/Brutto sind schon gelesen -- die Rechenstufe fasst sie nicht
    # an (keine Ergaenzung ueberschreibt einen gelesenen Wert). Der fehlende
    # Satz WIRD aus Netto+USt ergaenzt (19.00/100.00*100 = 19 %, gueltig) --
    # das aendert nichts an der widersphruechlichen Brutto-Angabe.
    for name in ("netto", "ust_betrag", "brutto"):
        assert rk.fields[name].source == "template-anchor"
    assert rk.fields["ust_satz"].value == 19.0
    assert rk.fields["ust_satz"].source == "errechnet"
    chk = next(c for c in res.checks if c.name == "netto+ust=brutto")
    assert chk.ok is False


# ---- 4. Bruttopreis-Positionen --------------------------------------------

def test_bruttopreis_positionen_werden_zu_netto_entrechnet():
    items = [{"beschreibung": "Artikel", "menge": 2, "einzelpreis_netto": 11.90,
             "mwst_satz": 19}]   # 11,90 ist tatsaechlich der BRUTTO-Einzelpreis
    rk, res = _result({}, items, text="Alle Preise inkl. MwSt.")
    it = rk.line_items[0]
    assert it["einzelpreis_netto"] == 10.0
    assert it["gesamt_netto"] == 20.0
    assert it["_herkunft"]["einzelpreis_netto"] == "errechnet"
    assert it["_herkunft"]["gesamt_netto"] == "errechnet"
    assert validate.check_line_item_math(rk.line_items).ok is True


# ---- 5. Mischsaetze ohne Aufteilung -> nichts errechnet, menschlich ------

def test_mischsaetze_ohne_aufteilung_bleibt_netto_leer():
    fields = {"brutto": _fr("brutto", 1000.0)}
    items = [{"beschreibung": "A", "mwst_satz": 19}, {"beschreibung": "B", "mwst_satz": 7}]
    rk, res = _result(fields, items)
    assert "netto" not in rk.fields
    assert "ust_betrag" not in rk.fields
    assert rk.gruende   # konkreter Grund, kein stilles Weglassen
    missing = [n for n in REQUIRED
              if n not in res.fields or res.fields[n].source == "missing"]
    assert "netto" in missing
    gruende = human.gruende(res)
    assert any(g["art"] == "pflichtfeld" and "netto" in g["grund"] for g in gruende)


# ---- 6. Rundungsfall (1 Cent) bleibt gruen --------------------------------

def test_rundungsfall_ust_auf_summe_vs_zeilen_bleibt_gruen():
    # USt in DE ueblich auf die SUMME je Satz gerechnet (10,01 * 19 % =
    # 1,9019 -> 1,90), nicht Zeile fuer Zeile (3 * (3,335 * 0,19) gerundet
    # waere ein anderer Centwert) -- Toleranz 0,01 je Zeile deckt das ab.
    fields = {"netto": _fr("netto", 10.01), "ust_betrag": _fr("ust_betrag", 1.90),
              "brutto": _fr("brutto", 11.91),
              "ust_aufstellung": _fr("ust_aufstellung",
                                     [{"satz": 19, "netto": 10.01, "ust": 1.90}])}
    items = [{"beschreibung": "A", "menge": 3, "einzelpreis_netto": 3.335,
             "gesamt_netto": 10.01, "mwst_satz": 19}]
    rk, res = _result(fields, items)
    assert all(c.ok is not False for c in res.checks)


# ---- 7. Nur ein Satz -> Satz in jede Zeile, Herkunft kopf -----------------

def test_einziger_satz_wird_in_jede_zeile_mit_herkunft_kopf_uebernommen():
    items = [{"beschreibung": "A", "menge": 1, "einzelpreis_netto": 10.0, "gesamt_netto": 10.0},
            {"beschreibung": "B", "menge": 1, "einzelpreis_netto": 5.0, "gesamt_netto": 5.0,
             "mwst_satz": 19}]
    rk, res = _result({}, items)
    a, b = rk.line_items
    assert a["mwst_satz"] == 19
    assert a["_herkunft"]["mwst_satz"] == "kopf"
    assert b.get("_herkunft", {}).get("mwst_satz") is None  # war schon gelesen


# ---- 10. CHF-Rechnung mit 8,1 % -------------------------------------------

def test_chf_8_1_prozent_wird_ergaenzt():
    fields = {"brutto": _fr("brutto", 1081.0), "ust_satz": _fr("ust_satz", 8.1),
              "waehrung": _fr("waehrung", "CHF")}
    rk, res = _result(fields)
    assert rk.fields["netto"].value == 1000.0
    assert rk.fields["ust_betrag"].value == 81.0
    assert all(c.ok is not False for c in res.checks)


def test_ungueltiger_satz_wird_nicht_verwendet():
    """11,3 % ist in keiner unterstuetzten Waehrung ein gueltiger Satz --
    Netto/USt bleiben leer statt mit einem erfundenen Satz gerechnet."""
    fields = {"brutto": _fr("brutto", 400.0), "ust_satz": _fr("ust_satz", 11.3),
              "waehrung": _fr("waehrung", "EUR")}
    rk, _res = _result(fields)
    assert "netto" not in rk.fields
    assert rk.gruende


# ---- Herkunft-Hilfsfunktionen ---------------------------------------------

def test_herkunft_bucket():
    assert rechnen.herkunft("manual") == "korrigiert"
    assert rechnen.herkunft("errechnet") == "errechnet"
    assert rechnen.herkunft("kopf") == "kopf"
    assert rechnen.herkunft("template-anchor") == "gelesen"
    assert rechnen.herkunft("llm") == "gelesen"
    assert rechnen.herkunft("einvoice") == "gelesen"


def test_nur_gelesene_werte_entfernt_errechnet_und_kopf():
    rows = [{"beschreibung": "A", "menge": 1, "einzelpreis_netto": 10.0,
            "gesamt_netto": 10.0, "mwst_satz": 19, "waehrung": "EUR",
            "_herkunft": {"mwst_satz": "kopf", "gesamt_netto": "errechnet"}}]
    out = rechnen.nur_gelesene_werte(rows)
    assert out == [{"beschreibung": "A", "menge": 1, "einzelpreis_netto": 10.0,
                    "waehrung": "EUR"}]
