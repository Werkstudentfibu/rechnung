"""Mails ohne Rechnungsanhang: hinter den PDF-Rechnungen und je Absender
gesammelt erledigen (Wunsch Aaron 13.11.2026)."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

from fastapi.testclient import TestClient

from rechnung import human, keine_rechnung
from rechnung.store import Store
from rechnung.web.server import create_app

GRUND = [{"art": "mail", "grund": "nur Link im Mailtext", "felder": []}]


def _mail(von: str, betreff: str, nr: int) -> bytes:
    m = EmailMessage()
    m["From"] = von
    m["Subject"] = betreff
    m.set_content(f"Text {nr}")
    return bytes(m)


def _setup(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    for i in range(3):
        human.ablegen_datei(store, f"mail_news_{i}.eml", _mail("News <News@Lieferant.example>", "Angebote", i),
                            GRUND, herkunft={"von": "News <News@Lieferant.example>", "betreff": "Angebote"})
    # aeltere Ablage ohne Herkunft: Absender aus dem Mailkopf
    human.ablegen_datei(store, "mail_portal_1.eml", _mail("portal@telekom.example", "Ihre Rechnung", 9), GRUND)
    human.ablegen_datei(store, "rechnung.pdf", b"%PDF-1.4 x", [{"art": "pflichtfeld", "grund": "Test", "felder": []}],
                        herkunft={"von": "News <News@Lieferant.example>"})
    return store, samples


def test_mails_stehen_hinter_den_pdf_rechnungen(tmp_path: Path):
    store, samples = _setup(tmp_path)
    offen = TestClient(create_app(store, samples)).get("/api/menschlich").json()["offen"]
    assert offen[0]["datei"] == "rechnung.pdf"
    assert {r["prioritaet"]["kategorie"] for r in offen[1:]} == {"Mail"}


def test_gebuendelt_je_absender(tmp_path: Path):
    store, samples = _setup(tmp_path)
    d = TestClient(create_app(store, samples)).get("/api/menschlich").json()
    assert d["mails_nach_absender"] == [
        {"von": "news@lieferant.example", "anzahl": 3, "betreff": "Angebote"},
        {"von": "portal@telekom.example", "anzahl": 1, "betreff": ""},
    ]


def test_alle_eines_absenders_keine_rechnung(tmp_path: Path):
    store, samples = _setup(tmp_path)
    c = TestClient(create_app(store, samples))
    r = c.post("/api/menschlich/sammel", json={"von": "news@lieferant.example", "aktion": "keine-rechnung"})
    assert r.status_code == 200 and r.json()["anzahl"] == 3
    rest = [x["datei"] for x in human.offen(store)]
    assert sorted(rest) == ["mail_portal_1.eml", "rechnung.pdf"]      # PDF desselben Absenders bleibt
    assert len(keine_rechnung.offen(store)) == 3


def test_alle_eines_absenders_erledigt(tmp_path: Path):
    store, samples = _setup(tmp_path)
    c = TestClient(create_app(store, samples))
    r = c.post("/api/menschlich/sammel", json={"von": "portal@telekom.example", "aktion": "erledigt"})
    assert r.json()["anzahl"] == 1
    assert "mail_portal_1.eml" not in [x["datei"] for x in human.offen(store)]
    assert c.post("/api/menschlich/sammel", json={"von": "portal@telekom.example",
                                                  "aktion": "erledigt"}).status_code == 404
    assert c.post("/api/menschlich/sammel", json={"von": "x@y.example", "aktion": "loeschen"}).status_code == 400


def test_oberflaeche_hat_sammelbereich():
    html = (Path(__file__).resolve().parent.parent / "rechnung" / "web" / "static" / "index.html").read_text("utf-8")
    assert "Mails ohne Rechnungsanhang – nach Absender" in html
    assert "async function mailGruppe(i, aktion)" in html
