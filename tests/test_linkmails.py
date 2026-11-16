"""``rechnung link-mails`` (Aaron 16.11.2026: "Waere es sinnvoll, wenn das
Programm die Rechnungen selbst herunterlaedt?"): erst zaehlen, wie viele
Mails ohne Anhang einen direkten Download-Link haben, wie viele in ein
Portal mit Anmeldung fuehren. Nur lesend -- kein Link wird geoeffnet.
Alles mit erfundenen Mails."""

from __future__ import annotations

import socket
from email.message import EmailMessage

import pytest

from rechnung import human, linkmails
from rechnung.cli import main as cli_main
from rechnung.identify import Supplier
from rechnung.store import Store


def _mail(von: str, betreff: str, html: str | None = None, text: str | None = None) -> bytes:
    m = EmailMessage()
    m["From"] = von
    m["To"] = "rechnung@hotel-muster.example"
    m["Subject"] = betreff
    m.set_content(text or "Siehe HTML-Teil.")
    if html:
        m.add_alternative(html, subtype="html")
    return bytes(m)


def _ablegen(store, name: str, raw: bytes, *, als_text_pdf: bool = False) -> None:
    grund = [{"art": human.ART_MAIL, "grund": "kein Rechnungsanhang", "felder": []}]
    if als_text_pdf:
        # wie ingest: Mailtext als PDF, die Mail selbst als .eml-Beilage
        human.ablegen_datei(store, name + ".pdf", b"%PDF-1.4 erfundener Mailtext " + raw[:20],
                            grund, beilagen={".eml": raw}, herkunft={"von": ""})
    else:
        human.ablegen_datei(store, name + ".eml", raw, grund)


@pytest.fixture
def kein_netz(monkeypatch):
    """Beweis "nur lesend": jeder Verbindungsversuch scheitert laut."""
    def _verboten(*_a, **_k):
        raise AssertionError("link-mails darf keine Verbindung aufbauen")
    monkeypatch.setattr(socket.socket, "connect", _verboten)
    monkeypatch.setattr(socket, "create_connection", _verboten)


def _store_mit_mails(tmp_path) -> Store:
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="a", name="Lieferant A GmbH", domains=["lieferant-a.example"]))
    _ablegen(store, "a1", _mail("Lieferant A <rechnung@lieferant-a.example>", "Ihre Rechnung 1",
             html='<p>Hallo</p><a href="https://dl.lieferant-a.example/r/AgBx9">Rechnung '
                  'herunterladen</a> <a href="https://news.lieferant-a.example/abmelden">'
                  'Newsletter abmelden</a>'))
    _ablegen(store, "a2", _mail("Lieferant A <rechnung@lieferant-a.example>", "Ihre Rechnung 2",
             html='<a href="https://dl.lieferant-a.example/r/Zz81">Rechnung herunterladen</a>'))
    _ablegen(store, "b1", _mail("Stadtwerke <service@stadtwerke-muster.example>", "Neue Rechnung",
             html='<a href="https://kundenportal.stadtwerke-muster.example/login">Zum '
                  'Kundenportal</a> <a href="https://www.facebook.com/stadtwerke">Facebook</a>'))
    _ablegen(store, "c1", _mail("Tool <noreply@rechnungstool.example>", "Rechnung verfuegbar",
             html='<a href="https://app.rechnungstool.example/invoice/view/xyz">Zur Rechnung</a>'))
    _ablegen(store, "d1", _mail("Shop <info@shop-muster.example>", "Angebot",
             html='<a href="https://www.shop-muster.example/">Zum Shop</a>'))
    _ablegen(store, "e1", _mail("Jemand <j@firma-muster.example>", "Rechnung folgt",
                                text="Die Rechnung kommt per Post."))
    _ablegen(store, "f1", _mail("Ablage <x@files-muster.example>", "Beleg",
                                text="Beleg: https://files-muster.example/Rechnung_1.pdf"),
             als_text_pdf=True)
    # eine normale PDF-Rechnung in der Bearbeitung zaehlt nicht mit
    human.ablegen_datei(store, "rechnung.pdf", b"%PDF-1.4 erfunden",
                        [{"art": human.ART_PRUEFUNG, "grund": "Summe rot", "felder": []}])
    return store


def test_mails_werden_nach_link_art_gezaehlt(tmp_path, kein_netz):
    b = linkmails.bilanz(_store_mit_mails(tmp_path))
    assert b.mails == 7
    assert b.bekannt == 2
    assert dict(b.klassen) == {"direkt": 3, "portal": 1, "unklar": 1, "sonstig": 1,
                               "kein_link": 1}
    assert b.domains["lieferant-a.example"]["mails"] == 2
    assert b.domains["lieferant-a.example"]["bekannt"] == 2
    # Facebook/Abmelden zaehlen nie als massgeblicher Link
    assert "facebook.com" not in b.domains


def test_bericht_zeigt_domains_erst_ab_zwei_mails(tmp_path, kein_netz):
    text = linkmails.bericht(linkmails.bilanz(_store_mit_mails(tmp_path)))
    assert "lieferant-a.example" in text
    # einzelne Domains nur als Anzahl, nicht namentlich
    assert "stadtwerke-muster.example" not in text
    assert "weitere: 4 Domains" in text
    assert "kein Link wurde geoeffnet" in text


@pytest.mark.parametrize("url,text,erwartet", [
    ("https://x.example/dl?id=AgB", "Rechnung herunterladen", "direkt"),
    ("https://x.example/files/Rechnung.pdf?sig=1", "", "direkt"),
    ("https://x.example/invoice/123", "Zur Rechnung", "unklar"),
    ("https://portal.x.example/login", "Rechnung ansehen", "portal"),
    ("https://x.example/", "Mein Konto", "portal"),
    ("https://x.example/", "Webseite", "sonstig"),
    ("https://x.example/datenschutz", "Datenschutz", None),
    ("https://www.linkedin.com/company/x", "", None),
    ("https://x.example/logo.png", "", None),
    # "agb" mitten in einer zufaelligen Zeichenfolge ist kein AGB-Link
    ("https://t.x.example/c/aGbXyZ123", "Rechnung herunterladen", "direkt"),
])
def test_link_klasse(url, text, erwartet):
    assert linkmails.link_klasse(url, text) == erwartet


def test_befehl_link_mails(tmp_path, capsys, kein_netz):
    _store_mit_mails(tmp_path)
    assert cli_main(["--data", str(tmp_path / "data"), "link-mails"]) == 0
    out = capsys.readouterr().out
    assert 'Mails ohne Rechnungsanhang in "Menschliche Bearbeitung": 7' in out
    assert "direkter Download" in out
