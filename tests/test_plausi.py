"""Lieferanten-Gedaechtnis + Plausibilitaet inkl. Datum (rechnung/plausi.py,
Aaron 10.10.2026)."""

from __future__ import annotations

import json
from datetime import date

import pytest

from rechnung import human, plausi
from rechnung import pdf as pdfmod
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import process
from rechnung.store import Store
from rechnung.template import FieldResult
from rechnung.validate import check_date

from conftest import SUPPLIERS, make_invoice

HEUTE = date(2026, 10, 10)


def F(**werte):
    return {k: FieldResult(k, v, str(v), 1.0, "template-anchor") for k, v in werte.items()}


def gold(nr, datum, brutto, satz=19.0, waehrung="EUR", faellig=None, sha=None, sid="alpha"):
    felder = {"rechnungsnummer": nr, "rechnungsdatum": datum, "brutto": brutto,
              "waehrung": waehrung}
    if faellig:
        felder["faelligkeit"] = faellig
    return {"sha256": sha or nr, "supplier_id": sid, "supplier_name": "Alpha",
            "felder": felder, "ust_aufstellung": [{"satz": satz, "netto": 1, "ust": 1}]}


PROFIL = plausi.profil_aus([
    gold("AL-2026-0001", "2026-01-05", 1000.0, faellig="2026-01-19"),
    gold("AL-2026-0002", "2026-02-11", 2000.0, faellig="2026-02-25"),
    gold("AL-2026-0003", "2026-03-03", 1500.0, satz=7.0, faellig="2026-03-17"),
])


def test_profil_aus_bestaetigten():
    p = PROFIL
    assert p["aktiv"] and p["rechnungen"] == 3
    assert p["steuersaetze"] == [7.0, 19.0] and p["waehrung"] == "EUR"
    assert p["brutto"] == {"median": 1500.0, "min": 1000.0, "max": 2000.0, "anzahl": 3}
    m = p["rechnungsnummer"]
    assert m["geruest"] == "A-9-9" and m["praefix"] == "AL"
    assert m["fortlaufend"] and m["hoechste"] == 3
    assert p["zahlungsziel_tage"] == 14


def test_erst_ab_drei_rechnungen_aktiv():
    p = plausi.profil_aus([gold("X1", "2026-01-01", 10.0), gold("X2", "2026-02-01", 10.0)])
    assert not p["aktiv"]
    c, h = plausi.check_nummer(F(rechnungsnummer="voellig anders 123"), p)
    assert c.ok is None and h == []
    assert plausi.check_betrag(F(brutto=99999.0), p)[0].ok is None


@pytest.mark.parametrize("nummer,ok", [
    ("AL-2026-0004", True),
    ("AL-2026-O004", False),     # O statt 0
    ("AI-2026-0004", False),     # anderes Praefix
    ("AL-2026-004", False),      # Stelle fehlt
    ("AL-2026-10004", True),     # Laufnummer waechst um eine Stelle
    ("AL/2026/0004", False),     # anderes Geruest
    ("AL 2026-0004", False),     # Trenner fehlt -> anderes Geruest
])
def test_rechnungsnummer_muster(nummer, ok):
    c, _h = plausi.check_nummer(F(rechnungsnummer=nummer), PROFIL)
    assert c.ok is ok, c.detail
    if not ok:
        assert "AL-2026-0003" in c.detail


def test_fortlaufend_kleiner_gleich_ist_hinweis():
    c, h = plausi.check_nummer(F(rechnungsnummer="AL-2026-0002"), PROFIL)
    assert c.ok is True and h and "nicht größer" in h[0]
    assert plausi.check_nummer(F(rechnungsnummer="AL-2026-0009"), PROFIL)[1] == []


def test_betrag_spanne():
    assert plausi.check_betrag(F(brutto=1800.0), PROFIL) [0].ok is True
    c, h = plausi.check_betrag(F(brutto=7000.0), PROFIL)      # > 3x max
    assert c.ok is True and "ungewöhnlich" in h[0]
    c, h = plausi.check_betrag(F(brutto=250.0), PROFIL)       # < 1/3 min
    assert c.ok is True and h
    c, _ = plausi.check_betrag(F(brutto=150000.0), PROFIL)    # Komma verrutscht
    assert c.ok is False and "Komma" in c.detail
    c, _ = plausi.check_betrag(F(brutto=15.0), PROFIL)
    assert c.ok is False
    # Gutschrift: keine Spanne
    assert plausi.check_betrag(F(brutto=-50.0), PROFIL)[0].ok is None
    assert plausi.check_betrag(F(brutto=50.0, belegart="Gutschrift"), PROFIL)[0].ok is None


def test_steuersatz_und_waehrung_hinweis():
    h = plausi.hinweise_satz_waehrung(F(ust_satz=0.0, waehrung="CHF"), [], PROFIL)
    assert any("Steuersatz 0 %" in x for x in h)
    assert any("Währung CHF" in x for x in h)
    assert plausi.hinweise_satz_waehrung(F(ust_satz=19.0, waehrung="EUR"),
                                         [{"mwst_satz": 7}], PROFIL) == []


def test_datum_zukunft_dreher_alt():
    c = check_date(F(rechnungsdatum="2062-10-01"), today=HEUTE)
    assert c.ok is False and "Jahreszahl-Dreher? 2062 statt 2026" in c.detail
    assert check_date(F(rechnungsdatum="11.10.2026"), today=HEUTE).ok is True  # 1 Tag Toleranz
    c = check_date(F(rechnungsdatum="12.10.2026"), today=HEUTE)
    assert c.ok is False and "Zukunft" in c.detail and "Dreher" not in c.detail
    assert check_date(F(rechnungsdatum="01.01.2010"), today=HEUTE).ok is False
    # aelter als ein Jahr: nur Hinweis
    assert check_date(F(rechnungsdatum="01.09.2025"), today=HEUTE).ok is True
    assert plausi.datum_hinweise(F(rechnungsdatum="01.09.2025"), HEUTE)
    assert plausi.datum_hinweise(F(rechnungsdatum="01.11.2025"), HEUTE) == []


def test_faelligkeit_vor_rechnungsdatum():
    c = plausi.check_faelligkeit(F(rechnungsdatum="2026-03-10", faelligkeit="2026-03-01"))
    assert c.ok is False and "vor Rechnungsdatum" in c.detail
    assert plausi.check_faelligkeit(F(rechnungsdatum="2026-03-10",
                                      faelligkeit="2026-03-24")).ok is True
    assert plausi.check_faelligkeit(F(rechnungsdatum="2026-03-10")).ok is None
    assert human._CHECK_FIELDS["faelligkeit-plausibel"] == ("faelligkeit",)


def _store_mit_gold(tmp_path) -> Store:
    store = Store(tmp_path / "data")
    d = store.root / "testsatz"
    d.mkdir(parents=True)
    for i, (nr, datum, b) in enumerate([("AL-2026-0001", "2026-01-05", 1190.0),
                                        ("AL-2026-0002", "2026-02-11", 2975.0),
                                        ("AL-2026-0003", "2026-03-03", 952.0)]):
        (d / f"{i}.json").write_text(json.dumps(gold(nr, datum, b, sha=f"s{i}")), "utf-8")
    return store


def test_pipeline_schickt_falsch_gelesene_nummer_zum_menschen(tmp_path):
    store = _store_mit_gold(tmp_path)
    src = make_invoice(tmp_path / "x.pdf", invoice_no="AL-2026-0004", datum="09.04.2026",
                       netto="1.000,00", ust="190,00", brutto="1.190,00", **SUPPLIERS["alpha"])
    doc = pdfmod.load(src)
    store.save_supplier(supplier_from_doc(doc, "alpha", "Alpha Werkzeug GmbH"))
    for k, v in {"rechnungsnummer": "AL-2026-O004", "rechnungsdatum": "09.04.2026",
                 "netto": "1000,00", "ust_betrag": "190,00", "brutto": "1190,00"}.items():
        store.save_correction(doc.sha256, k, v)
    res = process(src, store, use_llm=False)
    c = next(c for c in res.checks if c.name == "rechnungsnummer-muster")
    assert c.ok is False
    gr = human.gruende(res)
    assert any("rechnungsnummer-muster" in g["grund"] and g["felder"] == ["rechnungsnummer"]
               for g in gr)
    assert human.rote_felder(res.checks)["rechnungsnummer"]
    # richtig gelesen -> gruen, und der Profil-Cache merkt neue Bestaetigungen
    store.save_correction(doc.sha256, "rechnungsnummer", "AL-2026-0004")
    res = process(src, store, use_llm=False)
    assert next(c for c in res.checks if c.name == "rechnungsnummer-muster").ok is True


def test_pipeline_hinweise_und_eigene_rechnung_zaehlt_nicht(tmp_path):
    store = _store_mit_gold(tmp_path)
    src = make_invoice(tmp_path / "x.pdf", invoice_no="AL-2026-0002", datum="11.02.2026",
                       netto="2.500,00", ust="475,00", brutto="2.975,00", **SUPPLIERS["alpha"])
    doc = pdfmod.load(src)
    store.save_supplier(supplier_from_doc(doc, "alpha", "Alpha Werkzeug GmbH"))
    for k, v in {"rechnungsnummer": "AL-2026-0002", "rechnungsdatum": "11.02.2026",
                 "brutto": "2975,00"}.items():
        store.save_correction(doc.sha256, k, v)
    res = process(src, store, use_llm=False)
    assert any("nicht größer" in h for h in res.hinweise)
    # ist genau diese Rechnung schon bestaetigt (gleiche sha256), zaehlt sie
    # nicht zu ihrem eigenen Profil -> nur noch 2 andere, Profil inaktiv
    p = store.root / "testsatz" / "1.json"
    rec = json.loads(p.read_text("utf-8"))
    rec["sha256"] = doc.sha256
    p.write_text(json.dumps(rec), "utf-8")
    res = process(src, store, use_llm=False)
    assert not any("nicht größer" in h for h in res.hinweise)
    assert next(c for c in res.checks if c.name == "rechnungsnummer-muster").ok is None
