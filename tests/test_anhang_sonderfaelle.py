"""Baustein 1: ZIP-Anhaenge (Grenzen gegen Zip-Bomben, kein Pfad-Traversal)
und passwortgeschuetzte PDFs -> Mensch mit Grund."""

from __future__ import annotations

import io
import zipfile

from reportlab.lib import pdfencrypt
from reportlab.pdfgen import canvas

from rechnung import human, ingest
from rechnung import pdf as pdfmod
from rechnung.config import ImapConfig
from rechnung.pipeline import DEFERRED, process
from rechnung.store import Store

from test_postfach import FakeImap, _mail

CFG = ImapConfig(host="h", user="u", password="p", extensions=(".pdf", ".xml"))


def _pdf(text="Rechnung", password: str | None = None) -> bytes:
    buf = io.BytesIO()
    enc = pdfencrypt.StandardEncryption(password, ownerPassword="o") if password else None
    c = canvas.Canvas(buf, encrypt=enc)
    c.drawString(100, 700, text)
    c.save()
    return buf.getvalue()


def _zip(entries: dict[str, bytes], method=zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as zf:
        for n, d in entries.items():
            zf.writestr(n, d)
    return buf.getvalue()


def _att(name, data):
    return ingest.Attachment(name, data, "a@b.de", "s", "m")


def test_zip_wird_entpackt_pfadanteile_verworfen():
    z = _zip({"../../etc/RE-1.pdf": _pdf(), "unterordner/RE-2.pdf": _pdf(),
              "liesmich.txt": b"x"})
    good, problems = ingest.unpack_zip(_att("rechnungen.zip", z), (".pdf",))
    assert sorted(a.filename for a in good) == ["RE-1.pdf", "RE-2.pdf"]
    assert problems == []


def test_zip_bombe_wird_nicht_entpackt():
    z = _zip({"bombe.pdf": b"0" * (60 * 1024 * 1024)})
    good, problems = ingest.unpack_zip(_att("x.zip", z), (".pdf",))
    assert good == [] and ingest.ANHANG_ZIP_GRENZE in problems[0][2]


def test_zip_zu_viele_dateien():
    z = _zip({f"{i}.pdf": b"%PDF" for i in range(ingest.ZIP_MAX_FILES + 1)})
    good, problems = ingest.unpack_zip(_att("x.zip", z), (".pdf",))
    assert good == [] and "Dateien" in problems[0][2]


def test_zip_im_zip_und_defektes_zip():
    inner = _zip({"a.pdf": _pdf()})
    _g, problems = ingest.unpack_zip(_att("x.zip", _zip({"inner.zip": inner})), (".pdf",))
    assert "ZIP im ZIP" in problems[0][2]
    _g, problems = ingest.unpack_zip(_att("kaputt.zip", b"PK\x03\x04kaputt"), (".pdf",))
    assert problems[0][2].startswith(ingest.ANHANG_ZIP_DEFEKT)


def test_zip_mit_passwort_erkannt():
    z = bytearray(_zip({"a.pdf": _pdf()}, zipfile.ZIP_STORED))
    # Verschluesselungs-Bit (general purpose flag bit 0) in lokalem + zentralem Kopf setzen
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = bytes(z).index(sig)
        z[i + off] |= 0x01
    _g, problems = ingest.unpack_zip(_att("geheim.zip", bytes(z)), (".pdf",))
    assert problems[0][2].startswith(ingest.ANHANG_ZIP_PASSWORT)


def test_passwort_pdf_erkannt_rechte_pdf_nicht():
    assert pdfmod.password_protected(_pdf(password="geheim"))
    assert not pdfmod.password_protected(_pdf())
    # nur Rechte-Einschraenkung, leeres Benutzerpasswort -> lesbar
    buf = io.BytesIO()
    c = canvas.Canvas(buf, encrypt=pdfencrypt.StandardEncryption("", ownerPassword="o",
                                                                  canPrint=0))
    c.drawString(10, 10, "x")
    c.save()
    assert not pdfmod.password_protected(buf.getvalue())


def test_mail_mit_zip_und_passwort_pdf(tmp_path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    raw = _mail(attachments=[
        ("rechnungen.zip", _zip({"RE-10.pdf": _pdf(), "RE-11.pdf": _pdf()}), "application/zip"),
        ("RE-12.pdf", _pdf(password="geheim"), "application/pdf"),
    ])
    fake = FakeImap({b"1": raw})
    monkeypatch.setattr(ingest.imaplib, "IMAP4_SSL", lambda *a, **k: fake)
    res = ingest.fetch(CFG, tmp_path / "inbox", store=store)
    assert sorted(p.name for p in (tmp_path / "inbox").iterdir()) == ["RE-10.pdf", "RE-11.pdf"]
    offen = human.offen(store)
    assert len(offen) == 1 and offen[0]["datei"] == "RE-12.pdf"
    assert offen[0]["gruende"][0]["grund"] == ingest.ANHANG_PDF_PASSWORT
    assert offen[0]["beilagen"] == ["RE-12.pdf.eml"]
    assert "\\Seen" in fake.flags[b"1"] and len(res.human) == 1


def test_pipeline_stellt_passwort_pdf_zurueck(tmp_path):
    p = tmp_path / "geheim.pdf"
    p.write_bytes(_pdf(password="geheim"))
    store = Store(tmp_path / "data")
    res = process(p, store)
    assert res.status == DEFERRED and res.hold_reasons == [pdfmod.PDF_PASSWORD]
    g = human.gruende(res)
    assert [x["art"] for x in g] == [human.ART_ANHANG]
