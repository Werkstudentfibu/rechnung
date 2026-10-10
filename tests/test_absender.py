"""Effizienz-Baustein 1 (Aaron 10.10.2026): Absender der Mail als
Lieferanten-Hinweis -- Herkunft beim Abholen festhalten, Weiterleitungen,
Freemail/Portale/eigene Domains, Hinweis im Abgleich (nie allein
Identitaet), Widerspruch Domain <-> Kennung, Lernen, Anzeige."""

from __future__ import annotations

import hashlib
import json
from email.message import EmailMessage
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from rechnung import ingest, mailherkunft, pipeline
from rechnung.config import ImapConfig
from rechnung.identify import Supplier
from rechnung.store import Store

from conftest import SUPPLIERS, make_invoice

W, H = A4


def _pdf_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _mail(von: str, pdf: bytes, *, text: str = "Anbei die Rechnung.",
          betreff: str = "Rechnung 4711", angehaengt_von: str = "") -> bytes:
    m = EmailMessage()
    m["From"] = von
    m["To"] = "rechnung@firma.de"
    m["Subject"] = betreff
    m["Message-ID"] = "<abc@test>"
    m["Date"] = "Sat, 10 Oct 2026 09:00:00 +0200"
    m.set_content(text)
    if angehaengt_von:
        inner = EmailMessage()
        inner["From"] = angehaengt_von
        inner["Subject"] = "Ihre Rechnung"
        inner.set_content("Rechnung anbei")
        inner.add_attachment(pdf, maintype="application", subtype="pdf",
                             filename="Rechnung_4711.pdf")
        m.add_attachment(inner)
    else:
        m.add_attachment(pdf, maintype="application", subtype="pdf",
                         filename="Rechnung_4711.pdf")
    return m.as_bytes()


def _simple_invoice(path: Path, name: str = "Alpha Werkzeug GmbH", no: str = "AL-1",
                    ustid: str = "", iban: str = "") -> Path:
    """Rechnung OHNE Mail-Domain im Text (und auf Wunsch ohne Kennungen)."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 14)
    if name:
        c.drawString(40, H - 60, name)
    c.setFont("Helvetica", 10)
    c.drawString(40, H - 160, "Rechnungsnummer")
    c.drawString(200, H - 160, no)
    c.drawString(40, H - 176, "Rechnungsdatum")
    c.drawString(200, H - 176, "05.01.2026")
    c.drawString(40, H - 250, "Nettobetrag 100,00 EUR")
    c.drawString(40, H - 266, "Umsatzsteuer 19,00 % 19,00 EUR")
    c.drawString(40, H - 282, "Rechnungsbetrag 119,00 EUR")
    if ustid:
        c.drawString(40, 70, f"USt-IdNr: {ustid}")
    if iban:
        c.drawString(40, 58, f"IBAN: {iban}")
    c.showPage()
    c.save()
    return path


def _alpha(**kw) -> Supplier:
    s = SUPPLIERS["alpha"]
    d = dict(id="alpha", name=s["supplier"], ustids=[s["ustid"]], ibans=[s["iban"]],
             domains=[s["domain"]])
    d.update(kw)
    return Supplier(**d)


def _beta(**kw) -> Supplier:
    s = SUPPLIERS["beta"]
    d = dict(id="beta", name=s["supplier"], ustids=[s["ustid"]], ibans=[s["iban"]],
             domains=[s["domain"]])
    d.update(kw)
    return Supplier(**d)


def _herkunft(store: Store, path: Path, von: str, domain: str | None = None, **kw) -> None:
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    rec = {"von": von, "domain": domain if domain is not None else mailherkunft.domain_von(von),
           "weitergeleitet_von": "", "betreff": "Rechnung", "message_id": "<x@y>",
           "datum": "", **kw}
    mailherkunft.merken(store.root, sha, rec)


# ---- Abholen: Herkunft festhalten ------------------------------------------

def test_abholen_haelt_herkunft_fest_und_duplikat_ueberschreibt_nicht(tmp_path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_DATA", str(tmp_path / "data"))
    store = Store(tmp_path / "data")
    pdf = _pdf_bytes(_simple_invoice(tmp_path / "r.pdf"))
    cfg = ImapConfig()
    dest = tmp_path / "inbox"
    r = ingest.handle_mail(_mail("Alpha Rechnung <rechnung@alpha-werkzeug.de>", pdf), cfg, dest,
                           store)
    assert len(r["saved"]) == 1
    sha = hashlib.sha256(pdf).hexdigest()
    rec = mailherkunft.lesen(store.root, sha)
    assert rec["domain"] == "alpha-werkzeug.de"
    assert rec["von"] == "Alpha Rechnung <rechnung@alpha-werkzeug.de>"
    assert rec["betreff"] == "Rechnung 4711" and rec["message_id"] == "<abc@test>"
    assert rec["datum"].startswith("Sat, 10 Oct 2026")
    # dieselbe Rechnung nochmal von woanders: kein zweites PDF, Herkunft bleibt
    r2 = ingest.handle_mail(_mail("x@andere-firma.de", pdf), cfg, dest, store)
    assert r2["saved"] == []
    assert mailherkunft.lesen(store.root, sha)["domain"] == "alpha-werkzeug.de"


def test_duplikat_ohne_herkunft_bekommt_sie_nachgetragen(tmp_path, monkeypatch):
    """Rechnung lag schon im Eingang (vor dem Baustein abgeholt): die
    Herkunft wird beim naechsten Abholen nachgetragen, die Datei bleibt."""
    monkeypatch.setenv("RECHNUNG_DATA", str(tmp_path / "data"))
    store = Store(tmp_path / "data")
    pdf = _pdf_bytes(_simple_invoice(tmp_path / "r.pdf"))
    dest = tmp_path / "inbox"
    dest.mkdir()
    (dest / "alt.pdf").write_bytes(pdf)
    r = ingest.handle_mail(_mail("rechnung@alpha-werkzeug.de", pdf), ImapConfig(), dest, store)
    assert r["saved"] == []
    assert sorted(p.name for p in dest.iterdir()) == ["alt.pdf"]
    rec = mailherkunft.lesen(store.root, hashlib.sha256(pdf).hexdigest())
    assert rec["domain"] == "alpha-werkzeug.de"


def test_weiterleitung_aus_eigenem_outlook_nimmt_zitierten_absender(tmp_path):
    pdf = _pdf_bytes(_simple_invoice(tmp_path / "r.pdf"))
    text = ("Bitte buchen.\n\nGruss Aaron\n\n-----Ursprüngliche Nachricht-----\n"
            "Von: Alpha Buchhaltung <rechnung@alpha-werkzeug.de>\n"
            "Gesendet: Freitag, 9. Oktober 2026 10:00\nAn: Aaron <aaron@althoffhotels.com>\n"
            "Betreff: Rechnung 4711\n")
    out = ingest.analyse_mail(_mail("Aaron <aaron@althoffhotels.com>", pdf, text=text))
    assert out.ursprung["domain"] == "alpha-werkzeug.de"
    assert out.ursprung["von"] == "rechnung@alpha-werkzeug.de"
    assert out.ursprung["weitergeleitet_von"] == "Aaron <aaron@althoffhotels.com>"


def test_weiterleitung_englischer_kopf_mit_mailto(tmp_path):
    pdf = _pdf_bytes(_simple_invoice(tmp_path / "r.pdf"))
    text = ("FYI\n\n*From:* Beta Billing [mailto:billing@beta-buero.de]\n*Sent:* Friday\n"
            "*To:* Aaron\n")
    out = ingest.analyse_mail(_mail("aaron@AlthoffHotels.com", pdf, text=text))
    assert out.ursprung["domain"] == "beta-buero.de"


def test_weiterleitung_als_anlage_nimmt_absender_der_angehaengten_mail(tmp_path):
    pdf = _pdf_bytes(_simple_invoice(tmp_path / "r.pdf"))
    out = ingest.analyse_mail(_mail("aaron@althoffhotels.com", pdf,
                                    angehaengt_von="Alpha <rechnung@alpha-werkzeug.de>"),
                              (".pdf",))
    assert [a.filename for a in out.attachments] == ["Rechnung_4711.pdf"]
    assert out.ursprung["domain"] == "alpha-werkzeug.de"


def test_weiterleitung_nicht_eindeutig_kein_hinweis(tmp_path):
    pdf = _pdf_bytes(_simple_invoice(tmp_path / "r.pdf"))
    text = ("Von: a@alpha-werkzeug.de\nBetreff: x\n\n> Von: b@beta-buero.de\n")
    out = ingest.analyse_mail(_mail("aaron@althoffhotels.com", pdf, text=text))
    assert out.ursprung["domain"] == ""
    assert "mehrere" in out.ursprung["grund"]
    # ohne zitierten Kopf: eigene Domain bleibt stehen, aber kein Hinweis
    out2 = ingest.analyse_mail(_mail("aaron@althoffhotels.com", pdf, text="anbei"))
    assert out2.ursprung["domain"] == ""


def test_freemail_portal_eigene_domains_sind_kein_hinweis(monkeypatch):
    assert mailherkunft.verwendbar("alpha-werkzeug.de")
    assert mailherkunft.verwendbar("rechnung.alpha-werkzeug.de")
    for d in ("gmail.com", "gmx.de", "web.de", "outlook.com", "hotmail.de", "t-online.de",
              "icloud.com", "yahoo.de", "althoffhotels.com", "mail.althoffhotels.com",
              "sevdesk.de", "billomat.net", "bounce.sendgrid.net", ""):
        assert not mailherkunft.verwendbar(d), d
    monkeypatch.setenv("RECHNUNG_EIGENE_DOMAINS", "firma.de, @tochter.de")
    assert not mailherkunft.verwendbar("tochter.de")
    assert mailherkunft.verwendbar("althoffhotels.com")
    monkeypatch.setenv("RECHNUNG_PORTAL_DOMAINS", "mein-portal.de")
    assert not mailherkunft.verwendbar("mein-portal.de")


# ---- Pipeline: Hinweis, nie allein Identitaet ------------------------------

def test_absender_domain_plus_name_erkennt_lieferant(tmp_path):
    store = Store(tmp_path / "data")
    store.save_supplier(_alpha(ustids=[], ibans=[]))
    store.save_supplier(_beta())
    pdf = _simple_invoice(tmp_path / "r.pdf")
    # ohne Absender: nur der Name (0,2) -> kein Treffer
    assert pipeline.process(pdf, store, use_llm=False).supplier_id is None
    _herkunft(store, pdf, "rechnung@alpha-werkzeug.de")
    res = pipeline.process(pdf, store, use_llm=False)
    assert res.supplier_id == "alpha"
    assert res.absender["von"] == "rechnung@alpha-werkzeug.de" and res.absender["hinweis"]
    assert res.to_dict()["absender"]["domain"] == "alpha-werkzeug.de"


def test_domain_allein_nur_vorschlag_und_kein_entwurf(tmp_path):
    store = Store(tmp_path / "data")
    store.save_supplier(_alpha(ustids=[], ibans=[]))
    pdf = _simple_invoice(tmp_path / "r.pdf", name="")
    _herkunft(store, pdf, "rechnung@alpha-werkzeug.de")
    res = pipeline.process(pdf, store, use_llm=False, create_draft_supplier=True)
    assert res.supplier_id is None
    assert res.status == pipeline.DEFERRED
    assert res.lieferant_vorschlag == {"id": "alpha", "name": "Alpha Werkzeug GmbH",
                                       "grund": "Absender-Domain alpha-werkzeug.de"}
    assert any("nur Vorschlag" in h for h in res.hinweise)
    # kein zweiter (Entwurfs-)Lieferant fuer dieselbe Firma
    assert [s.id for s in store.suppliers()] == ["alpha"]


def test_kein_vorschlag_wenn_kennung_zu_anderem_lieferanten_gehoert(tmp_path):
    """Betrugsschutz: steht auf der Rechnung die IBAN von Beta UND Gamma
    (mehrdeutig), wird die Absender-Domain von Alpha nicht vorgeschlagen."""
    store = Store(tmp_path / "data")
    store.save_supplier(_alpha(ustids=[], ibans=[]))
    store.save_supplier(_beta(domains=[]))
    store.save_supplier(Supplier(id="gamma", name="Gamma", ibans=[SUPPLIERS["beta"]["iban"]]))
    pdf = _simple_invoice(tmp_path / "r.pdf", name="", iban=SUPPLIERS["beta"]["iban"])
    _herkunft(store, pdf, "rechnung@alpha-werkzeug.de")
    res = pipeline.process(pdf, store, use_llm=False)
    assert res.supplier_id is None
    assert res.lieferant_vorschlag is None


def test_widerspruch_domain_iban_ist_warnung(tmp_path):
    store = Store(tmp_path / "data")
    store.save_supplier(_alpha())
    store.save_supplier(_beta())
    # Rechnung traegt Kennungen von Beta, Mail kam von Alphas Domain
    pdf = _simple_invoice(tmp_path / "r.pdf", name="Beta Buerobedarf AG",
                          ustid=SUPPLIERS["beta"]["ustid"], iban=SUPPLIERS["beta"]["iban"])
    _herkunft(store, pdf, "rechnung@alpha-werkzeug.de")
    res = pipeline.process(pdf, store, use_llm=False)
    assert res.supplier_id == "beta"
    assert any(w.startswith("ACHTUNG " + pipeline.ABSENDER_WIDERSPRUCH) for w in res.warnings)
    assert res.shadow["would_pass"] is False
    assert pipeline.ABSENDER_WIDERSPRUCH in res.shadow["reasons"]
    # passt die Domain zum erkannten Lieferanten: keine Warnung
    pdf2 = _simple_invoice(tmp_path / "r2.pdf", name="Beta Buerobedarf AG", no="B-2",
                           ustid=SUPPLIERS["beta"]["ustid"], iban=SUPPLIERS["beta"]["iban"])
    _herkunft(store, pdf2, "billing@beta-buero.de")
    assert pipeline.process(pdf2, store, use_llm=False).warnings == []


def test_freemail_absender_wirkt_nicht(tmp_path):
    store = Store(tmp_path / "data")
    store.save_supplier(_alpha(ustids=[], ibans=[], domains=["gmail.com"]))
    pdf = _simple_invoice(tmp_path / "r.pdf")
    _herkunft(store, pdf, "alpha.werkzeug@gmail.com")
    res = pipeline.process(pdf, store, use_llm=False)
    assert res.supplier_id is None and res.lieferant_vorschlag is None
    assert res.absender["hinweis"] is False


def test_schalter_aus(tmp_path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_ABSENDER_HINWEIS", "0")
    store = Store(tmp_path / "data")
    store.save_supplier(_alpha(ustids=[], ibans=[]))
    pdf = _simple_invoice(tmp_path / "r.pdf")
    _herkunft(store, pdf, "rechnung@alpha-werkzeug.de")
    res = pipeline.process(pdf, store, use_llm=False)
    assert res.supplier_id is None
    assert res.absender["von"] == "rechnung@alpha-werkzeug.de"   # Anzeige bleibt


def test_sammel_teil_erbt_herkunft(tmp_path):
    store = Store(tmp_path / "data")
    mailherkunft.merken(store.root, "orig", {"von": "a@alpha-werkzeug.de",
                                             "domain": "alpha-werkzeug.de"})
    rec = mailherkunft.fuer_beleg(store, "teil", {"original_sha256": "orig"})
    assert rec["domain"] == "alpha-werkzeug.de"


# ---- Lernen ----------------------------------------------------------------

def test_lieferant_bestaetigen_lernt_absender_domain(tmp_path):
    store = Store(tmp_path / "data")
    pdf = _simple_invoice(tmp_path / "r.pdf", name="Neu GmbH", ustid="DE111111111")
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    _herkunft(store, pdf, "Neu <rechnung@neu-gmbh.de>")
    res = pipeline.process(pdf, store, use_llm=False, create_draft_supplier=True)
    assert res.supplier_id and store.supplier(res.supplier_id).bestaetigt is False
    assert store.supplier(res.supplier_id).entwurf_quelle == sha
    sup = pipeline.confirm_supplier(store, res.supplier_id, "test")
    assert "neu-gmbh.de" in sup.domains
    assert any(a["action"] == "lieferant_domain_gelernt" for a in store.audit_log())


def test_lernen_nur_verwendbare_und_eindeutige_domains(tmp_path):
    store = Store(tmp_path / "data")
    store.save_supplier(_alpha(domains=[]))
    store.save_supplier(_beta())
    mailherkunft.merken(store.root, "s1", {"von": "x@gmail.com", "domain": "gmail.com"})
    mailherkunft.merken(store.root, "s2", {"von": "x@beta-buero.de", "domain": "beta-buero.de"})
    mailherkunft.merken(store.root, "s3", {"von": "x@althoffhotels.com",
                                           "domain": "althoffhotels.com"})
    mailherkunft.merken(store.root, "s4", {"von": "x@alpha-werkzeug.de",
                                           "domain": "alpha-werkzeug.de"})
    assert mailherkunft.lerne_domain(store, "alpha", "s1") == ""
    assert mailherkunft.lerne_domain(store, "alpha", "s2") == ""   # gehoert Beta
    assert mailherkunft.lerne_domain(store, "alpha", "s3") == ""
    assert mailherkunft.lerne_domain(store, "alpha", "s4") == "alpha-werkzeug.de"
    assert store.supplier("alpha").domains == ["alpha-werkzeug.de"]
    assert mailherkunft.lerne_domain(store, "alpha", "s4") == ""   # schon da


# ---- Anzeige in der Pruefmaske ---------------------------------------------

def test_pruefmaske_zeigt_absender_und_bestaetigen_lernt(tmp_path):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    f = make_invoice(samples / "a.pdf", invoice_no="AL-1", datum="05.01.2026",
                     netto="100,00", ust="19,00", brutto="119,00",
                     **dict(SUPPLIERS["alpha"], domain="alpha-mail.de"))
    store.save_supplier(_alpha(domains=[]))
    _herkunft(store, f, "Alpha <rechnung@alpha-werkzeug.de>")
    client = TestClient(server_mod.create_app(store, samples))
    inv = client.get("/api/invoice", params={"file": f.name}).json()
    assert inv["absender"]["von"] == "Alpha <rechnung@alpha-werkzeug.de>"
    assert inv["match"]["id"] == "alpha"
    for k, v in {"rechnungsnummer": "AL-1", "rechnungsdatum": "05.01.2026", "netto": "100,00",
                 "ust_betrag": "19,00", "brutto": "119,00"}.items():
        client.post("/api/correction", json={"file": f.name, "field": k, "value": v})
    r = client.post("/api/confirm", json={"file": f.name})
    assert r.status_code == 200, r.text
    assert "alpha-werkzeug.de" in store.supplier("alpha").domains
    html = (Path(server_mod.__file__).parent / "static" / "index.html").read_text("utf-8")
    assert "Absender: " in html
