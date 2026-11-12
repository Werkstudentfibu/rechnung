"""Mail ohne Rechnungsanhang (.eml) in 'Menschliche Bearbeitung': eigene
Ansicht statt PDF-Auslesen (vorher 500 nach 121 s, Befund Aaron 12.11.2026),
dazu 'Erledigt' per Knopf."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import Path

from rechnung import human
from rechnung.store import Store


def _mail() -> bytes:
    m = EmailMessage()
    m["Subject"] = "Ihre Rechnung RE-4711 liegt bereit"
    m["From"] = "Portal <noreply@lieferant.example>"
    m["Date"] = "Wed, 05 Nov 2025 10:00:00 +0100"
    m.set_content("Guten Tag,\nIhre Rechnung finden Sie hier: https://portal.example/rechnung/4711\nGruss")
    return bytes(m)


def test_mail_oeffnen_und_erledigen(tmp_path: Path):
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    human.ablegen_datei(store, "mail_20251105_Rechnung.eml", _mail(),
                        [{"art": "mail", "grund": "nur Link im Mailtext", "felder": []}])
    client = TestClient(server_mod.create_app(store, samples))

    r = client.get("/api/invoice", params={"file": "mail_20251105_Rechnung.eml"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["mail"] is True
    assert d["betreff"] == "Ihre Rechnung RE-4711 liegt bereit"
    assert "https://portal.example/rechnung/4711" in d["links"]
    assert "nur Link im Mailtext" in d["gruende"]

    r = client.post("/api/menschlich/erledigt", json={"file": "mail_20251105_Rechnung.eml"})
    assert r.status_code == 200, r.text
    assert [x for x in human.offen(store) if x["datei"] == "mail_20251105_Rechnung.eml"] == []
    r = client.post("/api/menschlich/erledigt", json={"file": "mail_20251105_Rechnung.eml"})
    assert r.status_code == 404


def test_mail_ansicht_im_html():
    html = (Path(__file__).resolve().parent.parent / "rechnung" / "web" / "static" / "index.html").read_text("utf-8")
    assert "if (d.mail) { renderMail(d); return; }" in html
    assert "function renderMail(d)" in html and "async function mailErledigt()" in html
    assert "if (STATE.mail) { mailErledigt(); return; }" in html
