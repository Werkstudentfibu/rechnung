"""Netto = Brutto bei Steuer > 0 (Befund Aaron 09.10.2026)."""

from rechnung import rechnen
from rechnung.template import FieldResult


def _f(**kw):
    return {k: FieldResult(k, v, str(v), 0.6, src) for k, (v, src) in kw.items()}


def test_netto_gleich_brutto_mit_satz_wird_neu_errechnet():
    f = _f(netto=(1081.35, "llm"), brutto=(1081.35, "qr"), ust_satz=(8.1, "llm"),
           waehrung=("CHF", "qr"))
    rk = rechnen.rechne(f, [])
    assert rk.fields["brutto"].value == 1081.35
    assert rk.fields["netto"].value == round(1081.35 / 1.081, 2)
    assert rk.fields["netto"].source == rechnen.ERRECHNET
    assert any("Netto gleich Brutto" in g for g in rk.gruende)


def test_netto_gleich_brutto_mit_steuerbetrag():
    f = _f(netto=(105.48, "llm"), brutto=(105.48, "llm"), ust_betrag=(6.90, "llm"))
    rk = rechnen.rechne(f, [])
    assert rk.fields["netto"].value == 98.58


def test_steuerfrei_bleibt():
    f = _f(netto=(100.0, "llm"), brutto=(100.0, "llm"), ust_satz=(0.0, "llm"))
    rk = rechnen.rechne(f, [])
    assert rk.fields["netto"].value == 100.0


def test_korrektur_des_menschen_bleibt():
    f = _f(netto=(100.0, "manual"), brutto=(100.0, "llm"), ust_satz=(19.0, "llm"))
    rk = rechnen.rechne(f, [])
    assert rk.fields["netto"].value == 100.0
