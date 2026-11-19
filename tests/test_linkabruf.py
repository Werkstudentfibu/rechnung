"""Link-Abruf -- vorbereitet, nicht scharf (Aaron 16.11.2026: erst mit der
IT klaeren). Geprueft wird: ohne Schalter kein einziger Verbindungsversuch,
nur bestaetigte Lieferanten mit ausdruecklicher Domain-Freigabe, und jede
Sicherung des Abrufs (HTTPS, Port, interne Adressen, Weiterleitungen,
Inhaltstyp, PDF-Kennung, Groesse). Das Netz ist in allen Tests nur
simuliert, alle Mails sind erfunden."""

from __future__ import annotations

import socket
from email.message import EmailMessage

import pytest

from rechnung import human, linkabruf
from rechnung.cli import main as cli_main
from rechnung.identify import Supplier
from rechnung.store import Store

PDF = b"%PDF-1.4\n% erfundene Rechnung\n"


def _mail(von: str, html: str) -> bytes:
    m = EmailMessage()
    m["From"] = von
    m["To"] = "rechnung@hotel-muster.example"
    m["Subject"] = "Ihre Rechnung"
    m.set_content("Siehe HTML-Teil.")
    m.add_alternative(html, subtype="html")
    return bytes(m)


def _ablegen(store, name: str, raw: bytes) -> None:
    human.ablegen_datei(store, name + ".eml", raw,
                        [{"art": human.ART_MAIL, "grund": "kein Rechnungsanhang", "felder": []}])


def _link(url: str, text: str = "Rechnung herunterladen") -> str:
    return f'<a href="{url}">{text}</a>'


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("RECHNUNG_LINK_ABRUF", raising=False)
    st = Store(tmp_path / "data")
    st.save_supplier(Supplier(id="a", name="Lieferant A GmbH", domains=["lieferant-a.example"]))
    st.save_supplier(Supplier(id="b", name="Entwurf B GmbH", domains=["entwurf-b.example"],
                              bestaetigt=False))
    _ablegen(st, "a1", _mail("A <rechnung@lieferant-a.example>",
                             _link("https://dl.lieferant-a.example/r/Tok1")))
    _ablegen(st, "a2", _mail("A <rechnung@lieferant-a.example>",
                             _link("http://dl.lieferant-a.example/r/Tok2")))
    _ablegen(st, "a3", _mail("A <rechnung@lieferant-a.example>",
                             _link("https://fremd.example/r.pdf")))
    _ablegen(st, "b1", _mail("B <x@entwurf-b.example>", _link("https://entwurf-b.example/r.pdf")))
    _ablegen(st, "c1", _mail("C <x@unbekannt.example>", _link("https://unbekannt.example/r.pdf")))
    return st


@pytest.fixture
def kein_netz(monkeypatch):
    def _verboten(*_a, **_k):
        raise AssertionError("ohne Schalter darf keine Verbindung aufgebaut werden")
    monkeypatch.setattr(socket.socket, "connect", _verboten)
    monkeypatch.setattr(socket, "create_connection", _verboten)
    monkeypatch.setattr(socket, "getaddrinfo", _verboten)


class _Antwort:
    def __init__(self, status=200, headers=None, body=b""):
        self.status, self._h, self._body = status, headers or {}, body

    def getheader(self, k, default=None):
        return self._h.get(k, default)

    def read(self, n=-1):
        return self._body if n is None or n < 0 else self._body[:n]


class _Netz:
    """Simuliertes Netz: je Host eine Liste von Antworten; merkt sich, mit
    welcher (gepruften) Adresse verbunden wurde."""

    def __init__(self, monkeypatch, antworten: dict[str, list[_Antwort]],
                 adressen: dict[str, str] | None = None):
        self.anfragen: list[tuple[str, str, str]] = []
        self._antworten = {h: list(a) for h, a in antworten.items()}
        adressen = adressen or {}

        def _getaddrinfo(host, port, *a, **k):
            ip = adressen.get(host, "93.184.216.34")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

        netz = self

        class _Verbindung:
            def __init__(self, host, ip, timeout):
                self.host, self.ip = host, ip

            def request(self, method, pfad, headers=None):
                netz.anfragen.append((self.host, self.ip, pfad))
                assert method == "GET" and "Cookie" not in (headers or {})

            def getresponse(self):
                return netz._antworten[self.host].pop(0)

            def close(self):
                pass

        monkeypatch.setattr(socket, "getaddrinfo", _getaddrinfo)
        monkeypatch.setattr(linkabruf, "_verbindung", _Verbindung)


def test_ohne_schalter_nur_probelauf_ohne_netz(store, tmp_path, kein_netz):
    linkabruf.freigeben(store, "a", "lieferant-a.example")
    r = linkabruf.lauf(store, tmp_path / "eingang")
    assert r["modus"] == "aus"
    assert r["kandidaten"] == 1 and r["geholt"] == 0
    assert r["liste"] == [{"lieferant": "Lieferant A GmbH", "domain": "lieferant-a.example",
                           "datei": "a1.eml"}]
    assert r["uebersprungen"] == {linkabruf.GRUND_KEIN_LINK: 2,
                                  linkabruf.GRUND_KEIN_LIEFERANT: 2}
    assert not (tmp_path / "eingang").exists()


@pytest.mark.parametrize("wert", ["", "1", "ja", "true", "AN ", "probe"])
def test_nur_genau_an_schaltet_ein(monkeypatch, wert):
    monkeypatch.setenv("RECHNUNG_LINK_ABRUF", wert)
    assert linkabruf.modus() == ("an" if wert.strip().lower() == "an" else "aus")


def test_ohne_freigabe_wird_nichts_ausgewaehlt(store, kein_netz):
    a = linkabruf.auswahl(store)
    assert a.kandidaten == []
    assert a.uebersprungen[linkabruf.GRUND_KEINE_FREIGABE] == 3


def test_mit_schalter_holt_pdf_in_den_eingang(store, tmp_path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_LINK_ABRUF", "an")
    linkabruf.freigeben(store, "a", "lieferant-a.example")
    netz = _Netz(monkeypatch, {"dl.lieferant-a.example": [
        _Antwort(200, {"Content-Type": "application/pdf"}, PDF)]})
    r = linkabruf.lauf(store, tmp_path / "eingang")
    assert r["geholt"] == 1 and r["fehler"] == {}
    assert netz.anfragen == [("dl.lieferant-a.example", "93.184.216.34", "/r/Tok1")]
    pdfs = list((tmp_path / "eingang").glob("*.pdf"))
    assert len(pdfs) == 1 and pdfs[0].read_bytes() == PDF
    # Audit ohne vollstaendige Adresse (Zugriffsschluessel "Tok1")
    eintrag = next(a for a in store.audit_log() if a["action"] == "link_abruf")
    assert eintrag["detail"]["domain"] == "lieferant-a.example"
    assert "Tok1" not in str(eintrag)
    # Mail bleibt beim Menschen, mit Vermerk -- und wird nicht noch einmal geholt
    _side, rec = next((s, d) for s, d in [human.finde(store, x["sha256"])
                                          for x in human.offen(store)] if d["datei"] == "a1.eml")
    assert rec["link_geholt"] == pdfs[0].name
    assert any("per Link geholt" in h for h in rec["hinweise"])
    r2 = linkabruf.lauf(store, tmp_path / "eingang")
    assert r2["kandidaten"] == 0 and r2["uebersprungen"][linkabruf.GRUND_SCHON_GEHOLT] == 1


ERLAUBT = {"lieferant-a.example"}
OK = _Antwort(200, {"Content-Type": "application/pdf"}, PDF)


@pytest.mark.parametrize("url,antworten,adressen,fehler", [
    ("http://dl.lieferant-a.example/r", {}, {}, "nicht HTTPS"),
    ("https://dl.lieferant-a.example:8443/r", {}, {}, "anderer Port als 443"),
    ("https://user:pw@dl.lieferant-a.example/r", {}, {}, "Benutzer/Passwort in der Adresse"),
    ("https://boese.example/r.pdf", {}, {}, "Domain nicht freigegeben"),
    ("https://dl.lieferant-a.example/r", {}, {"dl.lieferant-a.example": "10.0.0.5"},
     "Adresse im internen Netz"),
    ("https://dl.lieferant-a.example/r", {}, {"dl.lieferant-a.example": "127.0.0.1"},
     "Adresse im internen Netz"),
    ("https://dl.lieferant-a.example/r", {}, {"dl.lieferant-a.example": "169.254.169.254"},
     "Adresse im internen Netz"),
    ("https://dl.lieferant-a.example/r",
     {"dl.lieferant-a.example": [_Antwort(302, {"Location": "https://boese.example/x.pdf"})]},
     {}, "Domain nicht freigegeben"),
    ("https://dl.lieferant-a.example/r",
     {"dl.lieferant-a.example": [_Antwort(302, {"Location": "http://dl.lieferant-a.example/x"})]},
     {}, "nicht HTTPS"),
    ("https://dl.lieferant-a.example/r",
     {"dl.lieferant-a.example": [_Antwort(302, {"Location": "/r"})] * 4},
     {}, "zu viele Weiterleitungen"),
    ("https://dl.lieferant-a.example/r",
     {"dl.lieferant-a.example": [_Antwort(200, {"Content-Type": "text/html"}, b"<html>")]},
     {}, "keine PDF (Inhaltstyp)"),
    ("https://dl.lieferant-a.example/r",
     {"dl.lieferant-a.example": [_Antwort(200, {"Content-Type": "application/octet-stream"},
                                          b"MZ\x90\x00 Programm")]},
     {}, "keine PDF (Inhalt)"),
    ("https://dl.lieferant-a.example/r",
     {"dl.lieferant-a.example": [_Antwort(200, {"Content-Type": "application/pdf",
                                                "Content-Length": str(10 ** 9)}, PDF)]},
     {}, "zu gross"),
    ("https://dl.lieferant-a.example/r",
     {"dl.lieferant-a.example": [_Antwort(404, {}, b"")]}, {}, "HTTP 404"),
])
def test_abruf_sicherungen(monkeypatch, url, antworten, adressen, fehler):
    _Netz(monkeypatch, antworten, adressen)
    with pytest.raises(linkabruf.AbrufFehler, match=__import__("re").escape(fehler)):
        linkabruf.abrufen(url, ERLAUBT)


def test_weiterleitung_innerhalb_der_freigabe_wird_verfolgt(monkeypatch):
    netz = _Netz(monkeypatch, {
        "dl.lieferant-a.example": [_Antwort(302, {"Location": "https://cdn.lieferant-a.example/f"})],
        "cdn.lieferant-a.example": [OK]})
    assert linkabruf.abrufen("https://dl.lieferant-a.example/r", ERLAUBT) == PDF
    assert [a[0] for a in netz.anfragen] == ["dl.lieferant-a.example", "cdn.lieferant-a.example"]


def test_verbindung_geht_genau_an_die_gepruefte_adresse(monkeypatch):
    """Kein zweites Aufloesen: verbunden wird mit der gepruften IP, das
    Zertifikat wird gegen den Namen geprueft."""
    gesehen = {}

    def _create_connection(adresse, timeout=None, *a, **k):
        gesehen["adresse"] = adresse
        return object()

    class _Kontext:
        def wrap_socket(self, sock, server_hostname=None):
            gesehen["name"] = server_hostname
            return sock

    monkeypatch.setattr(socket, "create_connection", _create_connection)
    v = linkabruf._GepinnteVerbindung("dl.lieferant-a.example", "93.184.216.34", 5)
    v._context = _Kontext()
    v.connect()
    assert gesehen == {"adresse": ("93.184.216.34", 443), "name": "dl.lieferant-a.example"}


def test_freigabe_befehl(store, capsys):
    data = str(store.root)
    assert cli_main(["--data", data, "link-freigabe", "--lieferant", "a",
                     "--domain", "https://dl.lieferant-a.example/x"]) == 0
    assert linkabruf.freigaben(store) == {"a": ["lieferant-a.example"]}
    assert "Lieferant A GmbH: lieferant-a.example" in capsys.readouterr().out
    assert cli_main(["--data", data, "link-freigabe", "--lieferant", "gibtsnicht",
                     "--domain", "x.example"]) == 2
    assert cli_main(["--data", data, "link-freigabe", "--lieferant", "a",
                     "--domain", "lieferant-a.example", "--entfernen"]) == 0
    assert linkabruf.freigaben(store) == {}
    assert any(a["action"] == "link_freigabe_entzogen" for a in store.audit_log())


def test_probelauf_befehl(store, tmp_path, capsys, kein_netz):
    linkabruf.freigeben(store, "a", "lieferant-a.example")
    assert cli_main(["--data", str(store.root), "--samples", str(tmp_path / "eingang"),
                     "link-abruf"]) == 0
    out = capsys.readouterr().out
    assert "AUS (nur Probelauf" in out
    assert "wuerde geholt: 1" in out
    assert "Lieferant A GmbH  (lieferant-a.example)  a1.eml" in out
