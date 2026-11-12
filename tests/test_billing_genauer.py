"""Rechnungsadresse: bei Gleichstand gewinnt der genauere Treffer
(Proberunde 12.11.2026). Dazu: Vorlagen-Dateinamen fuer Ids mit '/'."""

from rechnung.billing import BillingAddress, match_text
from rechnung.store import _dateiname


def test_laengerer_begriff_gewinnt_bei_gleichstand():
    a = BillingAddress(id="200", name="Betriebsgesellschaft Hotel Frankfurt mbH",
                       match_terms=["Betriebsgesellschaft Hotel Frankfurt mbH"])
    b = BillingAddress(id="hf", name="Hotel Frankfurt", match_terms=["Hotel Frankfurt"])
    hit, why = match_text("An die Betriebsgesellschaft Hotel Frankfurt mbH, Hauptstr. 1", [a, b])
    assert hit is not None and hit[0].id == "200", why
    hit, why = match_text("An das Hotel Frankfurt, Hauptstr. 1", [a, b])
    assert hit is not None and hit[0].id == "hf"


def test_mehr_getroffene_begriffe_gewinnen():
    a = BillingAddress(id="1", name="A", match_terms=["Hotel Regent Koeln", "Hotel Regent GmbH"])
    b = BillingAddress(id="2", name="B", match_terms=["Hotel Regent Koeln"])
    hit, _ = match_text("Hotel Regent GmbH, Hotel Regent Koeln", [a, b])
    assert hit[0].id == "1"


def test_echter_gleichstand_bleibt_mehrdeutig():
    a = BillingAddress(id="1", name="A", match_terms=["Muster Hotel"])
    b = BillingAddress(id="2", name="B", match_terms=["Muster Hotel"])
    hit, why = match_text("Muster Hotel", [a, b])
    assert hit is None and "mehrdeutig" in why


def test_dateiname_kodiert_unzulaessige_zeichen():
    assert _dateiname("muster__a") == "muster__a"
    n = _dateiname("70000-Kreditoren CH / Stamm\\daten:1__2")
    assert "/" not in n and "\\" not in n and ":" not in n
    assert _dateiname("x.") == "x%2E"


def test_zwei_verschiedene_firmen_bleiben_mehrdeutig():
    """Laenge allein entscheidet nie: zwei unabhaengige Firmen auf derselben
    Rechnung bleiben beim Menschen."""
    abb = BillingAddress(id="5", name="ABB", match_terms=["Althoff Beratungs- und Betreuungsgesellschaf"])
    bfn = BillingAddress(id="127", name="BFN", match_terms=["BFN Development Gesellschaft mbH EINS"])
    text = "BFN Development Gesellschaft mbH EINS c/o Althoff Beratungs- und Betreuungsgesellschaft mbH"
    hit, why = match_text(text, [abb, bfn])
    assert hit is None and "mehrdeutig" in why
