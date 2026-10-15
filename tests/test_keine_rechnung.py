"""Nicht-Rechnungen aussortieren (Entscheidung Aaron 14.10.2026, Nachtrag
14.10.2026 18:14): eigener Ordner "Keine Rechnung" statt gemischt mit echten
Problemrechnungen in "Menschliche Bearbeitung". Nur woertliche Regeln (keine
KI), Fehlalarm-Freiheit vor Abdeckung, und ein Loesch-Knopf NUR fuer diesen
Ordner (das Postfach/IMAP selbst wird nie angefasst)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnung import eventlog, human, keine_rechnung
from rechnung import pdf as pdfmod
from rechnung.pipeline import KEINE_RECHNUNG, dispatch, process
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice, make_plain_doc  # noqa: E402


# ---- Baustein 2: Pipeline-Regel (reine Textregel, keine KI) --------------

@pytest.mark.parametrize("titel,zeilen", [
    ("Lieferschein", ["Lieferschein Nr. 123", "Menge: 5 Stk"]),
    ("Auftragsbestaetigung", ["Ihre Bestellung Nr. 99", "Wir bestaetigen den Auftrag."]),
    ("Angebot", ["Unser Angebot Nr. 55", "Gueltig bis 31.12.2026"]),
])
def test_nicht_beleg_titel_wird_aussortiert(tmp_path, titel, zeilen):
    p = make_plain_doc(tmp_path / "a.pdf", titel=titel, zeilen=zeilen)
    doc = pdfmod.load(p)
    kr = keine_rechnung.pruefe_pipeline(doc, None)
    assert kr is not None and kr["beschriftung"] == titel


def test_rechnung_mit_lieferschein_nr_im_text_bleibt_rechnung(tmp_path):
    p = make_plain_doc(tmp_path / "a.pdf", titel="Rechnung", zeilen=[
        "Rechnungsnummer: RE-2026-1", "Lieferschein-Nr.: 456", "Gesamtbetrag: 100,00 EUR"])
    doc = pdfmod.load(p)
    assert keine_rechnung.pruefe_pipeline(doc, None) is None


def test_rechnung_und_lieferschein_in_einem_pdf_bleibt_rechnung(tmp_path):
    """Seite 1 = Rechnung, Seite 2 = Lieferschein -- der Titel zaehlt nur
    von Seite 1 (dubletten.title_text)."""
    p = make_plain_doc(tmp_path / "a.pdf", titel="Rechnung",
                       zeilen=["Rechnungsnummer: RE-2026-2"], seite2_titel="Lieferschein")
    doc = pdfmod.load(p)
    assert keine_rechnung.pruefe_pipeline(doc, None) is None


def test_rechnung_ohne_titel_bleibt_rechnung(tmp_path):
    p = make_invoice(tmp_path / "a.pdf")
    doc = pdfmod.load(p)
    assert keine_rechnung.pruefe_pipeline(doc, None) is None


def test_qr_zahlteil_verhindert_aussortierung(tmp_path):
    """Ein Schweizer QR-Zahlteil ist ein starkes Beleg-Merkmal -- selbst mit
    einem Nicht-Beleg-Titel wird nicht aussortiert (Fehlalarm-Freiheit)."""
    p = make_plain_doc(tmp_path / "a.pdf", titel="Lieferschein", zeilen=["Lieferschein Nr. 1"])
    doc = pdfmod.load(p)
    assert keine_rechnung.pruefe_pipeline(doc, object()) is None


def test_schalter_aus_laesst_alles_beim_alten(tmp_path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_KEINE_RECHNUNG_ORDNER", "0")
    p = make_plain_doc(tmp_path / "a.pdf", titel="Lieferschein", zeilen=["Lieferschein Nr. 1"])
    doc = pdfmod.load(p)
    assert keine_rechnung.pruefe_pipeline(doc, None) is None


# ---- Pipeline-Integration: process() + dispatch() -------------------------

def test_lieferschein_wird_aus_dem_eingang_aussortiert(tmp_path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    p = make_plain_doc(inbox / "Lieferschein_123.pdf", titel="Lieferschein",
                       zeilen=["Lieferschein Nr. 123"])

    res = process(p, store, use_llm=False)
    assert res.status == KEINE_RECHNUNG
    assert res.keine_rechnung["beschriftung"] == "Lieferschein"

    out = dispatch(res, store)
    assert out["aktion"] == "keine_rechnung"
    assert not p.exists()                       # aus dem Eingang verschoben
    assert human.offen(store) == []              # NICHT in "Menschliche Bearbeitung"

    offen = keine_rechnung.offen(store)
    assert len(offen) == 1
    rec = offen[0]
    assert rec["datei"] == "Lieferschein_123.pdf"
    assert rec["beschriftung"] == "Lieferschein"
    assert rec["herkunft"]["quelle"] == "pipeline"
    ziel = keine_rechnung.ordner(store.root) / rec["datei"]
    assert ziel.exists()

    # Ereignisprotokoll + Audit, ohne an JobRouter zu gehen
    events = [e for e in store.events() if e.get("type") == eventlog.TYPE_KEINE_RECHNUNG]
    assert len(events) == 1 and events[0]["aktion"] == eventlog.KR_AUSSORTIERT
    assert not (store.root / "queue").exists() or list((store.root / "queue").iterdir()) == []


def test_echte_rechnung_landet_nicht_bei_keine_rechnung(tmp_path, monkeypatch):
    """Grundsatz: eine echte Rechnung darf nie aussortiert werden -- ohne
    bekannten Lieferanten geht sie wie bisher nach "Menschliche Bearbeitung"."""
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    p = make_invoice(inbox / "RE-1.pdf", rechnungsnr="RE-2026-77")

    res = process(p, store, use_llm=False)
    assert res.status != KEINE_RECHNUNG
    dispatch(res, store)
    assert keine_rechnung.offen(store) == []
    assert len(human.offen(store)) == 1


def test_schalter_aus_sortiert_nicht_mehr_aus(tmp_path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    monkeypatch.setenv("RECHNUNG_KEINE_RECHNUNG_ORDNER", "0")
    store = Store(tmp_path / "data")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    p = make_plain_doc(inbox / "Lieferschein_123.pdf", titel="Lieferschein",
                       zeilen=["Lieferschein Nr. 123"])

    res = process(p, store, use_llm=False)
    assert res.status != KEINE_RECHNUNG
    dispatch(res, store)
    assert keine_rechnung.offen(store) == []
    assert len(human.offen(store)) == 1


# ---- "Doch eine Rechnung" (Fehlsortierung) --------------------------------

def test_doch_eine_rechnung_geht_zurueck_in_den_eingang(tmp_path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    p = make_plain_doc(inbox / "Lieferschein_123.pdf", titel="Lieferschein",
                       zeilen=["Lieferschein Nr. 123"])
    res = process(p, store, use_llm=False)
    dispatch(res, store)
    sha = keine_rechnung.offen(store)[0]["sha256"]

    out = keine_rechnung.zurueck_in_eingang(store, sha, inbox)
    assert out is not None
    assert (inbox / "Lieferschein_123.pdf").exists()
    assert keine_rechnung.offen(store) == []

    events = [e for e in store.events() if e.get("type") == eventlog.TYPE_KEINE_RECHNUNG]
    assert any(e["aktion"] == eventlog.KR_ZURUECKGEHOLT for e in events)


def test_doch_eine_rechnung_unbekannter_hash(tmp_path):
    store = Store(tmp_path / "data")
    assert keine_rechnung.zurueck_in_eingang(store, "x" * 64, tmp_path / "inbox") is None


# ---- "Keine Rechnung" von Hand aus der Pruefmaske -------------------------

def test_von_hand_aussortieren_verlaesst_menschliche_bearbeitung(tmp_path, monkeypatch):
    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    p = make_invoice(tmp_path / "a.pdf")
    from rechnung.pipeline import Result
    from rechnung.template import FieldResult

    res = Result(file=str(p), sha256="cd" * 32, status="zurueckgestellt")
    res.hold_reasons = ["kein Lieferant"]
    res.fields = {"rechnungsnummer": FieldResult("rechnungsnummer", "X", "X", 0.9, "text")}
    human.ablegen(store, res, [{"art": human.ART_ZURUECKGESTELLT, "grund": "kein Lieferant",
                                "felder": []}])
    assert len(human.offen(store)) == 1

    rec = keine_rechnung.von_hand_aussortieren(store, "a.pdf", operator="tester")
    assert rec is not None
    assert "von Hand aussortiert" in rec["grund"] and "tester" in rec["grund"]
    assert human.offen(store) == []
    assert len(keine_rechnung.offen(store)) == 1
    assert (keine_rechnung.ordner(store.root) / "a.pdf").exists()


def test_von_hand_aussortieren_unbekannte_datei(tmp_path):
    store = Store(tmp_path / "data")
    assert keine_rechnung.von_hand_aussortieren(store, "nichts.pdf") is None


# ---- Loeschen (Nachtrag 14.10.2026) ---------------------------------------

def _lege_ab(store, tmp_path, name="Lieferschein.pdf") -> dict:
    p = make_plain_doc(tmp_path / name, titel="Lieferschein", zeilen=["Lieferschein Nr. 1"])
    content = p.read_bytes()
    return keine_rechnung.ablegen_datei(store, name, content, grund="Test", beschriftung="Lieferschein")


def test_loeschen_entfernt_datei_und_begleitdatei(tmp_path):
    store = Store(tmp_path / "data")
    rec = _lege_ab(store, tmp_path)
    folder = keine_rechnung.ordner(store.root)
    assert (folder / rec["datei"]).exists()

    out = keine_rechnung.loeschen(store, [rec["datei"]], operator="tester")
    assert out["geloescht"] == [rec["datei"]] and out["fehler"] == []
    assert not (folder / rec["datei"]).exists()
    assert not (folder / (rec["datei"] + keine_rechnung.SIDECAR_SUFFIX)).exists()
    assert keine_rechnung.offen(store) == []

    events = [e for e in store.events() if e.get("type") == eventlog.TYPE_KEINE_RECHNUNG]
    assert any(e["aktion"] == eventlog.KR_GELOESCHT for e in events)
    audit = [a for a in store.audit_log() if a.get("action") == "keine_rechnung_geloescht"]
    assert len(audit) == 1 and audit[0]["detail"]["sha256"] == rec["sha256"]


def test_loeschen_mehrfachauswahl(tmp_path):
    store = Store(tmp_path / "data")
    r1 = _lege_ab(store, tmp_path, "a.pdf")
    r2 = _lege_ab(store, tmp_path, "b.pdf")
    out = keine_rechnung.loeschen(store, [r1["datei"], r2["datei"]])
    assert sorted(out["geloescht"]) == ["a.pdf", "b.pdf"]
    assert keine_rechnung.offen(store) == []


def test_loeschen_path_traversal_wird_abgewiesen(tmp_path):
    """Sicherheitsnetz (Nachtrag 14.10.2026): Loeschen nur fuer Dateien, die
    TATSAECHLICH im Ordner "Keine Rechnung" liegen -- kein Pfad-Traversal
    ueber die API."""
    store = Store(tmp_path / "data")
    geheim = tmp_path / "data" / "geheim.txt"
    geheim.parent.mkdir(parents=True, exist_ok=True)
    geheim.write_text("sollte ueberleben")

    out = keine_rechnung.loeschen(store, ["../geheim.txt"])
    assert out["geloescht"] == [] and out["fehler"]
    assert geheim.exists()


def test_loeschen_laesst_archiv_mit_gleichem_hash_unberuehrt(tmp_path):
    """Eine Datei mit demselben sha256 im Archiv/als verarbeitete Rechnung
    wird beim Loeschen aus "Keine Rechnung" NICHT beruehrt -- Loeschen ist
    strikt auf diesen Ordner begrenzt."""
    store = Store(tmp_path / "data")
    rec = _lege_ab(store, tmp_path)
    archiv = store.root / "archive"
    archiv.mkdir(parents=True, exist_ok=True)
    archiv_datei = archiv / f"{rec['sha256']}.pdf"
    archiv_datei.write_bytes(b"archiv-inhalt")

    keine_rechnung.loeschen(store, [rec["datei"]])
    assert archiv_datei.exists() and archiv_datei.read_bytes() == b"archiv-inhalt"


def test_loeschen_unbekannte_datei_meldet_fehler(tmp_path):
    store = Store(tmp_path / "data")
    out = keine_rechnung.loeschen(store, ["nichts.pdf"])
    assert out == {"geloescht": [], "fehler": ["nichts.pdf"]}


# ---- Tagesbericht ----------------------------------------------------------

def test_tagesbericht_zaehlt_aussortierung_rueckholung_und_loeschung(tmp_path, monkeypatch):
    from rechnung import nightly

    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)
    store = Store(tmp_path / "data")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    p = make_plain_doc(inbox / "a.pdf", titel="Lieferschein", zeilen=["Lieferschein Nr. 1"])
    res = process(p, store, use_llm=False)
    dispatch(res, store)

    bericht = nightly.tagesbericht(store)
    assert bericht["nicht_rechnung_aussortiert"] == 1
    assert "a.pdf" in bericht["nicht_rechnung_liste"][0]

    sha = keine_rechnung.offen(store)[0]["sha256"]
    keine_rechnung.zurueck_in_eingang(store, sha, inbox)
    bericht2 = nightly.tagesbericht(store)
    assert bericht2["fehlsortierungen"] == 1

    rec = _lege_ab(store, tmp_path, "b.pdf")
    keine_rechnung.loeschen(store, [rec["datei"]])
    bericht3 = nightly.tagesbericht(store)
    assert bericht3["nicht_rechnung_geloescht"] == 1


# ---- Postfach: Grenzfall gemischt (AGB + unklarer Anhang) ----------------

def test_mail_mit_anlage_und_unklarem_anhang_bleibt_bei_mensch(tmp_path, monkeypatch):
    """ALLE Anhaenge muessen eindeutig Nicht-Beleg sein -- ein gemischter
    Fall (AGB + unzuordenbarer Anhang) bleibt bei "Menschliche Bearbeitung",
    denn der unklare Anhang koennte eine Rechnung sein (Fehlalarm-Freiheit)."""
    import io

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    from rechnung import ingest
    from rechnung.config import ImapConfig

    monkeypatch.delenv("RECHNUNG_MENSCHLICH", raising=False)

    def _pdf(text):
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=A4)
        c.setFont("Helvetica-Bold", 14)
        c.drawString(60, 780, text)
        c.save()
        return buf.getvalue()

    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"] = "x@y.de"
    msg["Subject"] = "Unterlagen"
    msg.set_content("siehe Anhang")
    msg.add_attachment(_pdf("Allgemeine Geschaeftsbedingungen"), maintype="application",
                       subtype="pdf", filename="AGB.pdf")
    msg.add_attachment(_pdf("Nur ein Textblock ohne Beschriftung"), maintype="application",
                       subtype="pdf", filename="dokument123.pdf")

    out = ingest.analyse_mail(msg.as_bytes(), (".pdf",))
    assert out.keine_rechnung is False
    assert ingest.MAIL_OHNE_BELEG_ANHANG in out.human_reason
