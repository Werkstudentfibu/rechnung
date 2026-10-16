"""Lokale Training-UI: PDF ansehen, Woerter anklicken, Template lernen/bestaetigen.

Laeuft nur auf 127.0.0.1. Keine Auth (Einzelplatz-Werkzeug).
"""

from __future__ import annotations

import copy
import hashlib
import io
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import billing
from .. import einvoice
from .. import eventlog
from .. import human
from .. import jobrouter
from .. import keine_rechnung
from .. import learn
from .. import pdf as pdfmod
from .. import positionen
from .. import rechnen
from .. import semantic
from .. import split
from .. import shadow
from .. import tabelle
from .. import testsatz
from .. import vorlernen
from ..billing import BillingAddress
from ..guard import Guard
from ..ocr import IMAGE_SUFFIXES
from ..identify import (FIXED_FIELD_TYPES, POSITIONSMODI, POSITIONSMODUS_EINZELN, Supplier,
                        identify, supplier_from_doc)
from ..pipeline import (CONF_MIN, EXTRACT_EINVOICE, EXTRACT_TEMPLATE, MASTER_FIELDS,
                        POSITIONEN_KEY, STATUS_LABELS, _positionsmodus_abschliessen,
                        _positionsmodus_hinweis, _positionsmodus_vorbereiten,
                        confirm_supplier, discard_supplier, dispatch, invoice_files,
                        journal_rows, journal_totals, load_document, origin,
                        set_positionsmodus)
from ..store import Store
from ..suggest import similar_suppliers
from ..template import (STANDARD_FIELDS, ausgeblendet, sichtbare_felder, Anchor, Column, FieldResult, LineItemSpec,
                        Template, apply_line_items, apply_template, build_field_spec,
                        build_line_item_spec)
from ..textnorm import parse_amount
from ..validate import rechen_pruefungen

RENDER_SCALE = 2.0
VORVERARBEITUNG_TIMEOUT_S = 600
_STATIC = Path(__file__).parent / "static"


class FieldAssign(BaseModel):
    name: str
    value_type: str
    required: bool = True
    anchor_word: int | None = None      # Index in words der Seite
    value_words: list[int]
    page: int = 0


class LineItemColumn(BaseModel):
    header_word: int                    # Index in words der Seite (Kopfzelle)
    name: str
    value_type: str = "text"


class LineItemAssign(BaseModel):
    page: int = 0
    columns: list[LineItemColumn] = []
    stop_words: list[int] = []          # Indizes: Woerter, die das Tabellenende markieren


class TemplatePost(BaseModel):
    file: str
    supplier_id: str
    supplier_name: str
    variant: str = "standard"           # = Rechnungsadress-ID oder "standard"
    fields: list[FieldAssign]
    line_items: LineItemAssign | None = None


class LineItemPreviewPost(BaseModel):
    file: str
    line_items: LineItemAssign


class VorlernenScanPost(BaseModel):
    ordner: str


class VorlernenKorrekturPost(BaseModel):
    sha256: str
    feld: str
    wert: str | None = None             # leer/None = Korrektur zuruecknehmen


class VorlernenAusschlussPost(BaseModel):
    sha256: str
    ausgeschlossen: bool = True


class VorlernenLieferantPost(BaseModel):
    supplier_id: str


class ConfirmPost(BaseModel):
    file: str
    template_id: str | None = None      # None nur bei E-Rechnungen (kein Template)


class SendenPost(ConfirmPost):
    # Mensch hat einen Dublettenverdacht gesehen und will trotzdem senden
    trotzdem: bool = False


class CorrectionPost(BaseModel):
    file: str
    field: str
    value: str
    # Fuer das Ereignisprotokoll (Postfach-Baustein 11c): das Frontend kennt
    # Vorlage/Herkunft des angezeigten Werts bereits (aktueller Anzeigestand),
    # kein zweiter Server-Durchlauf noetig, um das zu rekonstruieren.
    template_id: str | None = None
    quelle_vorher: str | None = None
    # Wert, den das Programm vor der Korrektur anzeigte (anonymisierter
    # Lernbericht: Fehlerart bestimmen, lernbericht.py)
    wert_vorher: str | None = None


class TabellePost(BaseModel):
    """Korrigierte Positionstabelle bzw. USt-Aufstellung aus dem UI
    (Postfach-Baustein 11b): Zelle geaendert, Zeile hinzugefuegt/geloescht,
    Spalte neu zugeordnet -- es kommt immer die ganze Tabelle."""
    file: str
    art: str                            # "positionen" | "ust"
    rows: list[dict]
    template_id: str | None = None
    quelle_vorher: str | None = None
    # SPEC Baustein 5 (Aaron 12.10.2026): Kopfwort -> Zielspalte je Spalte der
    # Rahmen-Markierung (Baustein 2), wie vom Menschen im Dropdown bestaetigt
    # -- wandert ins globale, lieferantenuebergreifende Spaltenwoerterbuch
    # (nur bei ``art == "positionen"``, siehe ``spaltenwoerter.py``).
    spalten_zuordnung: list[dict] | None = None


class TabelleMarkierenPost(BaseModel):
    """Baustein 2 (Aaron 12.10.2026): der Mensch zieht im PDF einen Rahmen
    um die Positionstabelle (bei mehrseitigen Tabellen: je Seite einer) --
    ``boxes`` in PDF-Punkten der jeweiligen Seite. ``mapping`` (optional):
    vom Menschen geaenderte Spalten-Zuordnung (Index wie die Antwort-
    Spalten des vorigen Aufrufs); fehlt sie, gilt der Vorschlag (``guess``)
    je Spalte."""
    file: str
    boxes: list[dict]
    mapping: list[str | None] | None = None


class ZeileVorschlagPost(BaseModel):
    """Baustein 3 (Aaron 12.10.2026): ausgehend von EINER per Rahmen
    bestaetigten Positions-Zelle (``indices`` in ``doc.pages[page].words``,
    Zielspalte ``ziel_spalte``, Wert ``wert``) die uebrigen Spalten
    DERSELBEN Zeile vorschlagen -- Zeilen-/Spaltenzusammenhang. ``zeile``:
    schon bekannte Werte dieser Zeile (fuer die Baustein-4-Pruefung)."""
    file: str
    page: int
    indices: list[int]
    ziel_spalte: str
    wert: object = ""
    zeile: dict = {}


class PruefenPost(BaseModel):
    """Aktueller (ggf. noch ungespeicherter) Stand im UI fuer die Live-
    Anzeige der Rechen-Pruefungen."""
    file: str | None = None
    fields: dict[str, object] = {}
    line_items: list[dict] = []
    ust_aufstellung: list[dict] = []


class CategoryPost(BaseModel):
    text: str
    kategorie: str


class CopyTemplatePost(BaseModel):
    file: str
    source_template_id: str
    target_supplier_id: str
    target_supplier_name: str = ""
    variant: str = "standard"


class SupplierPost(BaseModel):
    id: str
    name: str = ""
    ustids: list[str] = []
    ibans: list[str] = []
    domains: list[str] = []
    addresses: list[str] = []          # Rechnungsadressen, denen er zugeordnet ist (leer = alle)
    external_id: str = ""              # Lieferantennummer im Fremdsystem (Abbyy-'Id')
    address_lines: list[str] = []
    bank_name: str = ""
    bic: str = ""
    bank_code: str = ""
    bank_account: str = ""
    creditor_id: str = ""
    contact: str = ""
    default_payment_terms: str = ""
    default_tax_rate: str = ""
    notes: str = ""
    fixed_fields: dict[str, str] = {}
    currency: str = "EUR"
    # Positionsmodus (Teil B, Entscheidung Aaron 10.10.2026) -- normalerweise
    # ueber den eigenen Weg POST .../positionsmodus gesetzt (markiert
    # ``positionsmodus_gesetzt``); hier nur mitgespeichert, damit ein beim
    # Anlegen eines neuen Lieferanten bereits gewaehlter Modus nicht verloren
    # geht ("ohne bekannten Lieferanten gilt die Auswahl fuer diese Rechnung
    # und wird beim Anlegen mitgespeichert").
    positionsmodus: str = "einzeln"
    positionsbezeichnung: str = ""


class PositionsmodusPost(BaseModel):
    modus: str
    bezeichnung: str = ""


class AddressPost(BaseModel):
    id: str
    name: str = ""
    lines: list[str] = []
    match_terms: list[str] = []
    cost_center: str = ""
    company_code: str = ""
    notes: str = ""


class PostfachPost(BaseModel):
    """Postfach-Stammdaten (Entscheidung Aaron 15.10.2026, ~50 IONOS-
    Postfaecher). ``passwort`` ist optional -- leer = Passwort unveraendert
    lassen, nie wird ein vorhandenes Passwort durch Leerlassen geloescht
    (dafuer gibt es den eigenen Knopf/Endpunkt)."""
    id: str
    mandant_id: str = ""
    mandant_name: str = ""
    user: str = ""
    host: str = "imap.ionos.de"
    port: int = 993
    ssl: bool = True
    source_folder: str = "INBOX"
    done_folder: str = ""
    aktiv: bool = True
    passwort: str = ""


def _sha(path: Path) -> str:
    """Datei-Hash wie pdf.load().sha256 -- funktioniert auch fuer XML."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Zelle "1.234,50"/"300,00" -> Betrag; ganze Zahlen ("2", "19") bleiben Text
# wie bei einer gelernten "number"-Spalte (apply_line_items).
_BETRAG_RE = re.compile(r"[-+]?[\d.,'’ ]*\d[.,]\d{1,2}")


def _zelle(v):
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        if _BETRAG_RE.fullmatch(v):
            amt = parse_amount(v)
            return amt if amt is not None else v
    return v


def _ust_zeile(r: dict) -> dict | None:
    out = {}
    for k in ("satz", "netto", "ust"):
        v = r.get(k)
        if isinstance(v, str):
            v = parse_amount(v) if v.strip() else None
        out[k] = None if v is None else float(v)
    return out if any(v is not None for v in out.values()) else None


def _live_fields(values: dict) -> dict[str, FieldResult]:
    """UI-Werte -> FieldResult wie in der Pipeline, nur fuer die Rechen-
    Pruefungen (Betraege als Zahl)."""
    out = {}
    for name, v in values.items():
        if v is None or v == "":
            continue
        if STANDARD_FIELDS.get(name, ("text",))[0] == "amount" and isinstance(v, str):
            amt = parse_amount(v)
            v = amt if amt is not None else v
        out[name] = FieldResult(name, v, str(v), 1.0, "manual")
    return out


def _ki_positionen() -> bool:
    """Liest die KI bei unbekannten Lieferanten auch die Positionen?"""
    from ..config import llm
    c = llm()
    return bool(c.enabled and c.line_items)


def _render_png(path: Path, page_index: int) -> bytes:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        page = doc[page_index]
        bitmap = page.render(scale=RENDER_SCALE)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        doc.close()


def create_app(store: Store, samples: Path) -> FastAPI:
    app = FastAPI(title="Rechnung Training-UI")
    samples = Path(samples)
    # Zeitgrenze je Rechnung (guard.py). Thread-Modus: die UI muss den KI-
    # Antwort-Cache mit /api/invoice teilen -- bestaetigt wird, was angezeigt wurde.
    guard = Guard(store.root, mode="thread")
    # KI-Antworten auf der Platte merken: jede Rechnung nur einmal vom Modell lesen
    semantic.set_cache_dir(store.root / "ki-cache")

    def _process(path: Path, use_llm: bool = True, create_draft_supplier: bool = False):
        # create_draft_supplier=False bleibt der Standard fuer jede Vorschau/
        # jeden Einzelaufruf (/api/invoice, /api/confirm, ...) -- nur das
        # echte Verarbeiten des Posteingangs (/api/scan, wie CLI "rechnung
        # scan") legt bei einem unbekannten Lieferanten einen Entwurf an
        # (Entscheidung Aaron 09.10.2026).
        return guard.process(path, use_llm=use_llm, create_draft_supplier=create_draft_supplier)

    def _load(path: Path):
        """PDF bzw. OCR-Arbeitskopie eines Scans/Bildes laden."""
        doc, _o = load_document(path, store)
        if doc is None:
            raise HTTPException(409, _o.error)
        return doc

    def _process_for_confirm(path: Path):
        res = _process(path)
        if res.aborted:
            raise HTTPException(504, res.hold_reasons[0])
        return res

    def _resolve(file: str) -> Path:
        """Erst der Eingangsordner, dann die Arbeitskopien im Ordner
        "Menschliche Bearbeitung" (human.py), dann im Ordner "Keine
        Rechnung" (keine_rechnung.py) -- fuer die Vorschau dort."""
        name = human.file_name(file)
        p = (samples / name).resolve()
        if p.exists() and samples.resolve() in p.parents:
            return p
        hp = human.datei_pfad(store, name)
        if hp is not None:
            return hp
        kp = keine_rechnung.datei_pfad(store, name)
        if kp is not None:
            return kp
        raise HTTPException(404, "Datei nicht gefunden")

    def _address_scope(doc, override: str | None = None):
        """-> (addr_match, address_id, has_addresses). Lieferant je
        Rechnungsadresse (09.10.2026): erkennt die Rechnungsadresse (oder
        uebernimmt eine vom Menschen uebersteuerte, siehe ``#variant`` im
        Reiter "1 Rechnungsadresse" -- ``/api/invoice?...&address=<id>``) --
        Grundlage fuer die Lieferantensuche nur unter dieser Adresse."""
        addresses = store.addresses()
        addr_match = billing.detect(doc, addresses)
        if override:
            ov = store.address(override)
            if ov is not None:
                addr_match = (ov, 1.0)
        address_id = addr_match[0].id if addr_match else None
        return addr_match, address_id, bool(addresses)

    # Regelmaessige Postfach-Abholung in den Posteingang der App (abholung.py)
    from ..abholung import aus_konfiguration

    # Vorverarbeitung im Hintergrund (vorverarbeitung.py): Rechnungen schon
    # auslesen, bevor sie angeklickt werden. Eigene, grosszuegigere Zeitgrenze --
    # es wird nur vorgewaermt, die verbindliche Grenze gilt beim Anklicken.
    from ..config import vorverarbeitung_an
    from ..vorverarbeitung import Vorverarbeiter

    vorverarbeiter = None
    if vorverarbeitung_an():
        bg_guard = Guard(store.root, timeout=VORVERARBEITUNG_TIMEOUT_S, mode="thread")
        vorverarbeiter = Vorverarbeiter(lambda p: bg_guard.process(p, use_llm=True))
        if samples.is_dir():
            vorverarbeiter.einreihen(invoice_files(samples))

    abholer = aus_konfiguration(samples, store=store,
                                bei_neuen=vorverarbeiter.einreihen if vorverarbeiter else None)
    if abholer is not None:
        abholer.start()

    @app.get("/api/abholung")
    def abholung_status() -> JSONResponse:
        if abholer is None:
            return JSONResponse({"aktiv": False, "eingerichtet": False})
        return JSONResponse(dict(abholer.status, eingerichtet=True))

    @app.post("/api/abholung")
    def abholung_jetzt() -> JSONResponse:
        """Postfach sofort pruefen (Knopf in der Kopfzeile)."""
        if abholer is None:
            return JSONResponse({"aktiv": False, "eingerichtet": False,
                                 "fehler": "Postfach nicht eingerichtet (rechnung.env)"})
        return JSONResponse(dict(abholer.einmal(), eingerichtet=True))

    _gestartet = __import__("time").strftime("%d.%m. %H:%M:%S")

    def _systemstatus() -> dict:
        """Wirksame Einstellungen DIESES laufenden Prozesses (Fehlersuche
        09.10.2026: rechnung.env sagte OCR=1, Programm meldete OCR aus)."""
        import os
        import sys

        from .. import ocr as ocrmod
        from .. import selbstupdate
        from ..config import LlmConfig

        llm = LlmConfig()
        return {
            "prozess": f"PID {os.getpid()}, gestartet {_gestartet}",
            "stand": selbstupdate.version().get("commit", ""),
            "python": sys.executable,
            "env_ocr": repr(os.environ.get("RECHNUNG_OCR_ENABLED")),
            "ocr_an": ocrmod.enabled(),
            "tesseract": ocrmod.tesseract_cmd() or "",
            "ocr_verfuegbar": bool(ocrmod.tesseract_cmd()) and ocrmod.available(),
            "tessdata": os.environ.get("TESSDATA_PREFIX", ""),
            "ki_an": llm.enabled, "ki_modell": llm.model,
            "daten": str(store.root),
        }

    @app.post("/api/import-abbyy")
    def import_abbyy(body: dict) -> JSONResponse:
        """Abbyy-Export einlesen (Reiter Lieferanten, Aaron 11.10.2026):
        ``art`` = "lieferanten" (Vendors) | "geschaeftsbereiche"
        (BusinessUnits), ``inhalt`` = Dateiinhalt Base64."""
        import base64
        import tempfile

        from ..importers import import_business_units_csv, import_vendors_csv

        art = body.get("art")
        if art not in ("lieferanten", "geschaeftsbereiche"):
            raise HTTPException(400, "art muss lieferanten oder geschaeftsbereiche sein")
        try:
            daten = base64.b64decode(body.get("inhalt") or "", validate=True)
        except ValueError:
            raise HTTPException(400, "Datei nicht lesbar")
        if not daten:
            raise HTTPException(400, "Datei ist leer")
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "import.csv"
            f.write_bytes(daten)
            try:
                if art == "geschaeftsbereiche":
                    return JSONResponse({"anzahl": import_business_units_csv(f, store), "fehlend": []})
                fehlend: list[str] = []
                n = import_vendors_csv(f, store, fehlend)
                return JSONResponse({"anzahl": n, "fehlend": sorted(set(fehlend))})
            except ValueError as e:
                raise HTTPException(400, str(e))

    # ---- Excel-Stammdaten-Import (Entscheidung Aaron 15.10.2026) --------
    # Grundsatz (Aaron, woertlich): "Wenn das Programm irgendwo nicht sicher
    # ist, soll es nicht raten, sondern mir Bescheid geben" -- siehe
    # stammdaten_import.py. Zustandslos: die geparsten Zeilen reisen
    # zwischen den drei Schritten (Hochladen -> Vorschau -> Uebernehmen) im
    # Client mit, nur die bestaetigte Spaltenzuordnung je Layout und das
    # Ergebnis selbst werden serverseitig gemerkt.
    @app.post("/api/stammdaten/parse")
    def stammdaten_parse(body: dict) -> JSONResponse:
        """Excel-Datei (xlsx) einlesen -> je Blatt Kopfzeile/Datenzeilen plus
        einen Zuordnungs-/Art-Vorschlag. Entscheidet nichts -- der naechste
        Schritt (Vorschau) ist die eigentliche Pruefung."""
        import base64

        from .. import stammdaten_import as si
        from ..xlsx import XlsxError, header_and_rows, read_workbook

        try:
            daten = base64.b64decode(body.get("inhalt") or "", validate=True)
        except ValueError:
            raise HTTPException(400, "Datei nicht lesbar (kein gueltiges Base64)")
        if not daten:
            raise HTTPException(400, "Datei ist leer")
        try:
            rohblaetter = read_workbook(daten)
        except XlsxError as e:
            raise HTTPException(400, str(e))
        sheets = {}
        for name, rows in rohblaetter.items():
            headers, datenzeilen = header_and_rows(rows)
            if not headers:
                continue
            gelernt = si.gelernte_zuordnung(store, headers)
            mapping = (gelernt or {}).get("mapping") or si.spalten_vorschlag(headers)
            art_vorschlag = si.art_vorschlag(name, mapping)
            art = (gelernt or {}).get("art") or art_vorschlag
            sheets[name] = {
                "headers": headers, "rows": datenzeilen, "mapping": mapping, "art": art,
                "art_vorschlag": art_vorschlag, "gelernt": gelernt is not None,
            }
        if not sheets:
            raise HTTPException(400, "Keine Tabelle mit einer Kopfzeile gefunden")
        return JSONResponse({"sheets": sheets, "zielfelder": sorted(si.ZIELFELDER),
                            "arten": list(si.BLATT_ARTEN)})

    @app.post("/api/stammdaten/vorschau")
    def stammdaten_vorschau(body: dict) -> JSONResponse:
        """Zeilenpruefung mit aktueller Zuordnung + Antworten -- Ampel
        gruen/gelb/rot je Zeile, jede Unsicherheit als begruendete Frage."""
        from .. import stammdaten_import as si

        sheets = body.get("sheets") or {}
        if not sheets:
            raise HTTPException(400, "keine Blaetter uebergeben")
        return JSONResponse(si.vorschau(store, sheets, body.get("antworten") or {}))

    @app.post("/api/stammdaten/uebernehmen")
    def stammdaten_uebernehmen(body: dict) -> JSONResponse:
        """Nur gruene Zeilen + vollstaendig beantwortete gelbe/rote Zeilen
        werden uebernommen -- die komplette Pruefung laeuft dabei
        serverseitig NEU (vertraut nie einer vom Client mitgeschickten
        Ampel). Lernt dabei die bestaetigte Spaltenzuordnung je Blatt-Layout."""
        from .. import stammdaten_import as si
        from ..config import operator as _operator

        sheets = body.get("sheets") or {}
        if not sheets:
            raise HTTPException(400, "keine Blaetter uebergeben")
        op = _operator()
        antworten = body.get("antworten") or {}
        # Die tatsaechlich VERWENDETE Zuordnung (inkl. automatisch
        # vorgeschlagener, falls der Client ``mapping``/``art`` leer liess)
        # merken -- nicht nur, was der Client zufaellig mitschickte.
        vor = si.vorschau(store, sheets, antworten)
        ergebnis = si.anwenden(store, sheets, antworten, op)
        for name, blatt in vor["blaetter"].items():
            headers = (sheets.get(name) or {}).get("headers")
            if headers and blatt.get("mapping") and blatt.get("art"):
                si.zuordnung_lernen(store, headers, blatt["mapping"], blatt["art"], op)
        return JSONResponse(ergebnis)

    # ---- Mehrere Postfaecher (Entscheidung Aaron 15.10.2026) -----------
    @app.get("/api/postfaecher")
    def list_postfaecher() -> JSONResponse:
        from .. import secrets as secrets_mod

        return JSONResponse([
            {**p.to_dict(), "passwort_gesetzt": secrets_mod.has_password(store, p.id)}
            for p in store.postfaecher()
        ])

    @app.post("/api/postfaecher")
    def save_postfach_ep(body: PostfachPost) -> JSONResponse:
        from .. import secrets as secrets_mod
        from ..config import operator as _operator
        from ..postfach import Postfach

        store.save_postfach(Postfach(
            id=body.id, mandant_id=body.mandant_id.strip(),
            mandant_name=body.mandant_name.strip(), user=body.user.strip(),
            host=body.host.strip() or "imap.ionos.de", port=body.port, ssl=body.ssl,
            source_folder=body.source_folder.strip() or "INBOX",
            done_folder=body.done_folder.strip(), aktiv=body.aktiv,
        ))
        if body.passwort:
            secrets_mod.set_password(store, body.id, body.passwort, _operator())
        return JSONResponse({"ok": True})

    @app.delete("/api/postfaecher/{postfach_id}")
    def delete_postfach_ep(postfach_id: str) -> JSONResponse:
        from .. import secrets as secrets_mod
        from ..config import operator as _operator

        store.delete_postfach(postfach_id)
        secrets_mod.delete_password(store, postfach_id, _operator())
        return JSONResponse({"ok": True})

    @app.post("/api/postfaecher/testen")
    def postfaecher_testen() -> JSONResponse:
        """"Alle testen" -- Verbindung/Login/Ordner je Postfach pruefen,
        OHNE etwas abzuholen (wie ``rechnung imap-test``, nur fuer alle
        konfigurierten Postfaecher statt nur dem ENV-Postfach)."""
        from .. import ingest
        from ..config import postfaecher as postfaecher_cfg

        out = []
        for label, cfg, mandant_id in postfaecher_cfg(store):
            c = ingest.check_connection(cfg)
            out.append({"postfach": label, "mandant_id": mandant_id, **c})
        return JSONResponse({"ergebnisse": out})

    @app.get("/api/postfach-bericht")
    def postfach_bericht() -> JSONResponse:
        """Je Mail im Postfach: wird sie abgeholt, und wenn nicht, warum
        (ingest.bericht, nur lesend)."""
        from ..ingest import bericht

        r = bericht(dest=samples)
        r["abholung"] = dict(abholer.status) if abholer is not None else None
        r["eingang"] = str(samples)
        r["system"] = _systemstatus()
        r["im_eingang"] = [p.name for p in invoice_files(samples)]
        return JSONResponse(r)

    @app.post("/api/entfernen")
    def entfernen(file: str) -> JSONResponse:
        """Rechnung aus dem Eingang nehmen (Testmail, Spam ...; Wunsch Aaron
        09.10.2026). Nicht loeschen, sondern nach ``data/entfernt`` schieben
        -- wiederherstellbar. Nur Dateien direkt im Eingang."""
        import time as _time

        name = human.file_name(file)
        p = (samples / name).resolve()
        if not (p.is_file() and p.parent == samples.resolve()):
            raise HTTPException(404, "Datei nicht im Eingang")
        ziel = store.root / "entfernt"
        ziel.mkdir(parents=True, exist_ok=True)
        p.replace(ziel / f"{_time.strftime('%Y%m%d-%H%M%S')}_{p.name}")
        return JSONResponse({"ok": True})

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        # no-store: nach einem Update soll das Fenster nie die alte Oberflaeche zeigen
        return HTMLResponse((_STATIC / "index.html").read_text("utf-8"),
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/scan")
    def scan() -> JSONResponse:
        # use_llm=False: die Liste braucht nur Status/Konfidenz je Datei, und
        # der LLM-Fallback ist konfidenz-gedeckelt (siehe pipeline.CONF_MIN) --
        # er kann res.status hier nie veraendern, wuerde aber bei vielen
        # offenen Rechnungen mit Luecken jede Aktualisierung der Liste um
        # 30-50s pro betroffener Datei verzoegern. Der volle Fallback laeuft
        # weiterhin beim Oeffnen einer einzelnen Rechnung (/api/invoice) und
        # beim tatsaechlichen Bestaetigen (/api/confirm).
        out = []
        split.prepare_folder(samples, store)
        for p in invoice_files(samples):
            # Echte Verarbeitung des Posteingangs (wie CLI "rechnung scan") --
            # legt bei einem unbekannten Lieferanten einen Entwurf an.
            r = _process(p, use_llm=False, create_draft_supplier=True)
            # Unsichere Rechnungen in den Ordner "Menschliche Bearbeitung"
            dispatch(r, store)
            out.append(r.to_dict())
        return JSONResponse({
            "results": out,
            "status_labels": STATUS_LABELS,
            "fields": {k: {"value_type": v[0], "required": v[1]}
                       for k, v in sichtbare_felder().items()},
        })

    @app.get("/api/invoice")
    def invoice(file: str, address: str | None = None) -> JSONResponse:
        """``address`` (optional): Mensch uebersteuert die erkannte
        Rechnungsadresse im Reiter "1 Rechnungsadresse" (``#variant`` ->
        ``zeigeAdresse()``) -- die Lieferantensuche laeuft dann unter dieser
        Adresse neu (Lieferant je Rechnungsadresse, 09.10.2026)."""
        path = _resolve(file)
        if einvoice.load(path) is not None:
            return JSONResponse(_einvoice_view(path))
        doc, ocr_res = load_document(path, store)
        if doc is None:
            # Scan/Bild, aber OCR nicht moeglich -> zurueckgestellt, nichts
            # zum Anklicken; der Mensch bearbeitet die Rechnung ausserhalb.
            return JSONResponse(_einvoice_view(path, readable=False))
        pages = [
            {
                "index": pg.index,
                "width": pg.width,
                "height": pg.height,
                "scale": RENDER_SCALE,
                "image": f"/api/page.png?file={quote(path.name)}&page={pg.index}",
                "words": [
                    {"i": i, "text": w.text, "x0": w.x0, "x1": w.x1,
                     "top": w.top, "bottom": w.bottom}
                    for i, w in enumerate(pg.words)
                ],
            }
            for pg in doc.pages
        ]
        suggestion = supplier_from_doc(doc, "", "").to_dict()
        addr_match, address_id, has_addr = _address_scope(doc, override=address)
        match = identify(doc, store.suppliers(), index=store.supplier_index(address_id),
                         address_id=address_id, has_addresses=has_addr)
        current = {}
        tid = None
        # Vorbelegt mit der erkannten/uebersteuerten Adresse, damit das
        # Auswahlfeld (#variant) nach einem Reload die Wahl behaelt, auch
        # wenn (noch) kein Template existiert -- unten ggf. auf die Variante
        # der tatsaechlich gewaehlten Vorlage korrigiert.
        variant = address_id or "standard"
        tpl_fields: list[dict] = []
        tpl_line_items = None
        line_items: list[dict] = []
        supplier_obj = None
        llm_rejected: dict[str, str] = {}
        ki_lieferant: dict[str, str] = {}
        ki_empfaenger: dict = {}
        extraction = EXTRACT_TEMPLATE
        if match:
            sup, _ = match
            supplier_obj = store.supplier(sup.id) or sup
            tpls = store.templates_for(sup.id)
            if tpls:
                chosen = next(
                    (t for t in tpls if addr_match and t.variant == addr_match[0].id),
                    tpls[0],
                )
                tid = chosen.id
                variant = chosen.variant
                current = {k: {"value": v.value, "raw": v.raw, "confidence": v.confidence,
                               "source": v.source}
                           for k, v in apply_template(doc, chosen).items()}

            # Feste Werte + Stammdaten (IBAN/USt-IdNr/Steuernummer/Adresse) gelten
            # unabhaengig davon, ob fuer diesen Lieferanten schon ein Layout-
            # Template trainiert wurde -- ein per Bulk-Import (importers.py)
            # bekannter Lieferant soll das schon bei der allerersten Rechnung
            # zeigen, nicht erst ab der zweiten (Konversation 2026-09-18).
            for fname, fval in (supplier_obj.fixed_fields or {}).items():
                if not str(fval).strip():
                    continue
                if (fname not in MASTER_FIELDS and fname in current
                        and current[fname]["source"] != "missing" and current[fname]["raw"]):
                    continue
                current[fname] = {"value": fval, "raw": str(fval), "confidence": 0.99, "source": "library"}
            # IBAN/USt-IdNr/Steuernummer/Adresse kommen direkt aus dem Lieferanten-
            # Stammdatensatz (Lieferanten-Tab), nicht aus "Feste Werte" -- eigene
            # strukturierte Felder statt Freitext-Duplikat, siehe pipeline.process().
            master_fields = {
                "iban": supplier_obj.ibans[0] if supplier_obj.ibans else "",
                "ustid": supplier_obj.ustids[0] if supplier_obj.ustids else "",
                "steuernummer": supplier_obj.creditor_id,
                "adresse": ", ".join(supplier_obj.address_lines) if supplier_obj.address_lines else "",
            }
            # Stammdaten haben Vorrang vor dem, was auf der Rechnung steht.
            for fname, fval in master_fields.items():
                if not fval:
                    continue
                current[fname] = {"value": fval, "raw": fval,
                                  "confidence": 0.99, "source": "library"}

            if tpls:
                # Semantischer Fallback (§7f) -- muss hier dieselbe Luecken-
                # Logik wie pipeline.process() durchlaufen, sonst sieht der
                # Mensch beim Pruefen/Bestaetigen nie den Vorschlag, den er
                # spaeter beim tatsaechlichen Bestaetigen (process() in
                # /api/confirm) unbemerkt mitbekommen wuerde -- widerspraeche
                # der Bestaetigungspflicht (§7c: erst sehen, dann bestaetigen).
                gaps = [
                    n for n in sichtbare_felder()
                    if current.get(n) is None or current[n]["source"] == "missing"
                    or current[n]["confidence"] < CONF_MIN
                ]
                if gaps:
                    hints = {n: v["raw"] for n, v in current.items() if v["source"] != "missing"}
                    for fname, fr in semantic.extract(doc.text, gaps, hints,
                                                      llm_rejected).items():
                        cur = current.get(fname)
                        if cur is None or cur["source"] == "missing" or fr.confidence > cur["confidence"]:
                            current[fname] = {"value": fr.value, "raw": fr.raw,
                                              "confidence": fr.confidence, "source": fr.source}

                tpl_fields = [{"name": f.name, "value_type": f.value_type,
                               "required": f.required} for f in chosen.fields]
                if chosen.line_items:
                    tpl_line_items = chosen.line_items.to_dict()
                    line_items = apply_line_items(doc, chosen.line_items)

        # Manuelle Korrekturen gewinnen immer -- auch ohne Template: dann sind
        # sie die Werte, die der Mensch bestaetigt und aus denen Stufe 1 die
        # Vorlage lernt (learn.py).
        corr = store.corrections(doc.sha256)
        for fname, fval in corr.items():
            if fname.startswith("_"):
                continue
            current[fname] = {"value": fval, "raw": str(fval), "confidence": 1.0, "source": "manual"}
        if isinstance(corr.get(POSITIONEN_KEY), list):
            line_items = corr[POSITIONEN_KEY]

        if tid is None:
            # Ohne Template genau das zeigen, was beim Bestaetigen uebernommen
            # wird: Pipeline-Ergebnis inkl. Stufe 2 (KI-Extraktion, falls an),
            # Stammdaten und Korrekturen -- erst sehen, dann bestaetigen (§7c).
            # ``res.fields``/``res.line_items`` sind dabei schon die
            # Rechenstufe durchlaufen (pipeline.process/rechnen.rechne).
            if vorverarbeiter is not None:
                # laeuft genau diese Datei gerade im Hintergrund: auf das
                # Ergebnis warten statt dieselbe Arbeit parallel zu starten
                with vorverarbeiter.sperre(path):
                    res = _process(path)
            else:
                res = _process(path)
            current = {k: {"value": v.value, "raw": v.raw, "confidence": v.confidence,
                           "source": v.source} for k, v in res.fields.items()}
            llm_rejected = res.llm_rejected
            extraction = res.extraction
            line_items = res.line_items
            tax_breakdown_view = res.tax_breakdown
            ki_lieferant = res.ki_lieferant
            ki_empfaenger = res.ki_empfaenger
        else:
            # Status/Rueckstellgruende/Pruefungen fuer die Anzeige; die KI
            # aendert daran nichts (Konfidenz gedeckelt), also ohne Modell.
            res = _process(path, use_llm=False)
            # Dieselbe Rechenstufe wie in der Pipeline (SPEC Baustein 2) auch
            # hier anwenden -- sonst sieht der Mensch beim Pruefen/Bestaetigen
            # nie, was errechnet/vom Kopf uebernommen wuerde. Spaltennamen
            # zuerst normalisieren (Baustein 1, Altvorlagen).
            line_items = positionen.normalize_columns(line_items)
            current_fr = {k: FieldResult(k, v["value"], v["raw"], v["confidence"], v["source"])
                         for k, v in current.items()}
            res.line_items = line_items
            _positionsmodus_vorbereiten(res, supplier_obj)
            rk = rechnen.rechne(current_fr, res.line_items, doc.text)
            current = {k: {"value": fr.value, "raw": fr.raw, "confidence": fr.confidence,
                           "source": fr.source} for k, fr in rk.fields.items()}
            res.fields, res.line_items, res.tax_breakdown = rk.fields, rk.line_items, rk.tax_breakdown
            _positionsmodus_abschliessen(res, supplier_obj)
            line_items = res.line_items
            tax_breakdown_view = res.tax_breakdown
        _positionsmodus_hinweis(res, supplier_obj)
        for v in current.values():
            v["origin"] = origin(v["source"])
            # Vierstufige Herkunft (gelesen/errechnet/kopf/korrigiert, SPEC
            # Baustein 3) -- feiner als "origin" (Vorlage/KI/Stammdaten/...
            # zaehlen dort alle als "gelesen").
            v["herkunft"] = rechnen.herkunft(v["source"])
            v["via"] = ("ocr" if ocr_res is not None
                        and v["source"] not in ("library", "manual", "missing") else None)

        # Kein Template fuer diesen Lieferanten (oder Lieferant unbekannt):
        # aehnliche bekannte Lieferanten mit Template als Anlern-Starthilfe
        # vorschlagen ("sieht aus wie X, Vorlage uebernehmen?"). Nie eine
        # automatische Zuordnung, nur ein Vorschlag im UI.
        similar = []
        if tid is None:
            fp = set(suggestion.get("fingerprint") or [])
            exclude_id = supplier_obj.id if supplier_obj else None
            candidates = [s for s in store.suppliers() if s.id != exclude_id]
            for s, sc in similar_suppliers(fp, candidates):
                s_tpls = store.templates_for(s.id)
                if not s_tpls:
                    continue
                similar.append({
                    "id": s.id, "name": s.name, "score": sc,
                    "templates": [{"id": t.id, "variant": t.variant, "secured": t.secured}
                                  for t in s_tpls],
                })
        tax_rows = (current.get("ust_aufstellung") or {}).get("value")
        tax_rows = tax_rows if isinstance(tax_rows, list) else []
        return JSONResponse({
            "file": path.name,
            "pages": pages,
            # Vorschlaege im PDF umrahmen + Rechen-Pruefungen je Zeile (11b)
            "markierungen": learn.markierungen(doc, current, line_items, tax_rows),
            "positionen_korrigiert": isinstance(corr.get(POSITIONEN_KEY), list),
            "pruefung_live": rechen_pruefungen(
                _live_fields({k: v.get("value") for k, v in current.items()
                              if not isinstance(v.get("value"), list)}
                             | ({"ust_aufstellung": tax_rows} if tax_rows else {})),
                line_items, doc.text),
            "match": ({"id": match[0].id, "name": match[0].name, "score": match[1]}
                      if match else None),
            "identifiers": suggestion,
            # KI-belegter Lieferantenname/-kennungen (nur Vorschlag fuer den
            # Entwurf/"Neuer Lieferant"-Kasten, nie Identitaet).
            "ki_lieferant": ki_lieferant,
            "ki_empfaenger": ki_empfaenger,
            "template_id": tid,
            "variant": variant,
            "template_fields": tpl_fields,
            "template_line_items": tpl_line_items,
            "line_items": line_items,
            # Vollstaendig aggregierte/ergaenzte USt-Aufstellung (SPEC
            # Baustein 2/3) -- je Zeile mit Herkunft (``_herkunft``), unabhaengig
            # davon, ob "ust_aufstellung" als eigenes Feld gelesen wurde.
            "tax_breakdown": tax_breakdown_view,
            "standard_fields": {k: {"value_type": v[0], "required": v[1]}
                                for k, v in sichtbare_felder().items()},
            "hidden_fields": sorted(ausgeblendet()),
            "ki_positionen": _ki_positionen(),
            "fixed_field_types": FIXED_FIELD_TYPES,
            "supplier": supplier_obj.to_dict() if supplier_obj else None,
            "addresses": [a.to_dict() for a in store.addresses()],
            "detected_address": ({"id": addr_match[0].id, "name": addr_match[0].name,
                                  "score": addr_match[1]} if addr_match else None),
            "suggested_terms": billing.suggest_terms(doc),
            "current": current,
            "extraction": extraction,
            "llm_rejected": llm_rejected,
            "similar_suppliers": similar,
            "status": res.status,
            "status_label": STATUS_LABELS.get(res.status, res.status),
            "hold_reasons": res.hold_reasons,
            # Hinweise, die nichts blockieren (z.B. der "Langer Beleg"-
            # Vorschlag fuer den Positionsmodus, Teil B) -- anders als
            # ``hold_reasons`` ohne "ACHTUNG".
            "hinweise": res.hinweise,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in res.checks],
            "rote_felder": human.rote_felder(res.checks),
            "pflichtangaben": res.pflichtangaben,
            "ocr": res.ocr,
        })

    def _einvoice_view(path: Path, readable: bool = True) -> dict:
        """E-Rechnung: Werte kommen aus dem XML, es gibt nichts anzulernen.
        Bei ZUGFeRD/Factur-X wird das PDF trotzdem als Seitenbild gezeigt,
        damit der Mensch beim Bestaetigen sieht, was er bestaetigt; bei
        reinem XML (XRechnung) gibt es keine Seiten."""
        res = _process(path)
        pages = []
        doc = None
        if path.suffix.lower() == ".pdf":
            # Auch ein Scan ohne Texterkennung (readable=False) wird als
            # Seitenbild gezeigt -- der Mensch muss sehen, was er bearbeitet.
            doc = pdfmod.load(path)
            pages = [{"index": pg.index, "width": pg.width, "height": pg.height,
                      "scale": RENDER_SCALE,
                      "image": f"/api/page.png?file={quote(path.name)}&page={pg.index}",
                      "words": []}
                     for pg in doc.pages]
        supplier_obj = store.supplier(res.supplier_id) if res.supplier_id else None
        inv = res.einvoice or {}
        suggestion = {
            "id": "", "name": inv.get("verkaeufer_name", ""),
            "ustids": [inv["verkaeufer_ustid"]] if inv.get("verkaeufer_ustid") else [],
            "ibans": list(inv.get("verkaeufer_iban") or []),
            "domains": [], "fingerprint": [],
            "address_lines": list(inv.get("verkaeufer_adresse") or []),
            "bic": inv.get("verkaeufer_bic", ""),
            "creditor_id": inv.get("verkaeufer_steuernummer", ""),
            "currency": inv.get("waehrung") or "EUR",
        }
        current = {k: {"value": v.value, "raw": v.raw, "confidence": v.confidence,
                       "source": v.source, "origin": origin(v.source)}
                   for k, v in res.fields.items()}
        tb = res.fields.get("ust_aufstellung")
        tax_rows = tb.value if tb is not None and isinstance(tb.value, list) else []
        return {
            "file": path.name,
            "extraction": EXTRACT_EINVOICE if readable else res.extraction,
            # ZUGFeRD: auch die XML-Werte im Seitenbild umrahmen (11b)
            "markierungen": (learn.markierungen(doc, current, res.line_items, tax_rows)
                             if doc is not None and readable else None),
            "pruefung_live": rechen_pruefungen(res.fields, res.line_items),
            "einvoice": inv,
            "status": res.status,
            "status_label": STATUS_LABELS.get(res.status, res.status),
            "hold_reasons": res.hold_reasons,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in res.checks],
            "rote_felder": human.rote_felder(res.checks),
            "pflichtangaben": res.pflichtangaben,
            "pages": pages,
            "match": ({"id": res.supplier_id, "name": res.supplier_name,
                       "score": res.match_score} if res.supplier_id else None),
            "identifiers": suggestion,
            "template_id": None,
            "variant": "standard",
            "template_fields": [],
            "template_line_items": None,
            "line_items": res.line_items,
            "standard_fields": {k: {"value_type": v[0], "required": v[1]}
                                for k, v in sichtbare_felder().items()},
            "hidden_fields": sorted(ausgeblendet()),
            "ki_positionen": _ki_positionen(),
            "fixed_field_types": FIXED_FIELD_TYPES,
            "supplier": supplier_obj.to_dict() if supplier_obj else None,
            "addresses": [a.to_dict() for a in store.addresses()],
            "detected_address": None,
            "suggested_terms": None,
            "current": current,
            "similar_suppliers": [],
        }

    @app.get("/api/page.png")
    def page_png(file: str, page: int = 0) -> Response:
        path = _resolve(file)
        if path.suffix.lower() in IMAGE_SUFFIXES or (
                path.suffix.lower() == ".pdf" and not pdfmod.has_text_layer(path)):
            # Scan/Bild: die OCR-Arbeitskopie zeigen -- deren Woerter sind es,
            # die angeklickt werden.
            _doc, o = load_document(path, store)
            if o is not None and o.pdf is not None:
                path = o.pdf
            elif path.suffix.lower() != ".pdf":
                raise HTTPException(409, (o.error if o else "") or "OCR nicht verfuegbar")
            # sonst: Scan-PDF ohne Texterkennung -> das Original zeigen
        return Response(_render_png(path, page), media_type="image/png")

    @app.post("/api/line-items/preview")
    def preview_line_items(body: LineItemPreviewPost) -> JSONResponse:
        """Positionstabelle mit dem aktuellen (noch ungespeicherten) Spalten-
        Mapping auslesen -- damit die Uebersicht "was wurde bestellt" schon
        waehrend des Anlernens sichtbar ist, nicht erst nach dem Speichern."""
        path = _resolve(body.file)
        doc = _load(path)
        li = body.line_items
        if not li.columns:
            return JSONResponse({"items": []})
        words = doc.pages[li.page].words
        header_cols = [(words[c.header_word], positionen.canonical_name(c.name), c.value_type)
                       for c in li.columns]
        stop = [words[i] for i in li.stop_words]
        spec = build_line_item_spec(header_cols, stop, doc.pages[li.page].width)
        return JSONResponse({"items": apply_line_items(doc, spec)})

    @app.post("/api/template")
    def save_template(body: TemplatePost) -> JSONResponse:
        path = _resolve(body.file)
        doc = _load(path)
        pages_words = [pg.words for pg in doc.pages]

        sup = store.supplier(body.supplier_id)
        if sup is None:
            sup = supplier_from_doc(doc, body.supplier_id, body.supplier_name)
            store.save_supplier(sup)
        elif not sup.fingerprint:
            # Bestehende Stammdaten (IBAN/USt-IdNr/Domain) werden beim
            # Anlernen NICHT aus der Rechnung ergaenzt -- sie sind aus der
            # Bibliothek vorgegeben. Sonst wuerde eine gefaelschte IBAN auf der
            # Trainingsrechnung still in die Stammdaten wandern.
            sup.fingerprint = supplier_from_doc(doc, sup.id, sup.name).fingerprint
            store.save_supplier(sup)

        tid = f"{body.supplier_id}__{body.variant}"
        tpl = store.template(tid) or Template(id=tid, supplier_id=body.supplier_id,
                                              variant=body.variant)
        specs = []
        for fa in body.fields:
            if not fa.value_words:
                continue
            words = pages_words[fa.page]
            anchor = words[fa.anchor_word] if fa.anchor_word is not None else None
            vwords = [words[i] for i in fa.value_words]
            pg = doc.pages[fa.page]
            specs.append(build_field_spec(fa.name, fa.value_type, fa.required,
                                          anchor, vwords, pg.width, pg.height))

        # Merge by Feldname statt Ersetzen: der Aufrufer schickt oft nur die
        # Felder mit, die er gerade (neu) angeklickt hat -- v.a. bei einer
        # gezielten Nachtrag-Korrektur eines einzelnen Feldes auf einer
        # bereits trainierten Rechnung (STATE.assign startet beim Oeffnen
        # immer leer). Ein Ersetzen wuerde dabei alle uebrigen, laengst
        # gesicherten Anker dieses Templates stillschweigend loeschen.
        by_name = {f.name: f for f in tpl.fields}
        for spec in specs:
            by_name[spec.name] = spec
        tpl.fields = list(by_name.values())

        # Ein frisch trainierter Anker ersetzt eine bestehende manuelle
        # Korrektur fuer GENAU diese Rechnung -- sonst zeigt das Feld hier
        # fuer immer Quelle "manual", obwohl die Positions-Extraktion den
        # Wert jetzt selbst zuverlaessig liefert. Kuenftige Rechnungen
        # desselben Lieferanten waren davon ohnehin nie betroffen (Korrektur
        # ist an sha256 gebunden).
        from ..config import operator as _operator_for_correction
        for spec in specs:
            if spec.name in store.corrections(doc.sha256):
                store.delete_correction(doc.sha256, spec.name, _operator_for_correction())

        if body.line_items and body.line_items.columns:
            li = body.line_items
            words = pages_words[li.page]
            # Spaltenname beim Anlernen auf den Standardnamen abbilden (SPEC
            # Baustein 1) -- das UI erlaubt zwar noch Freitext, das Template
            # merkt sich aber nur noch Standardnamen (Altvorlagen mit
            # Freitext-Namen werden beim Anwenden ueber
            # ``positionen.normalize_columns`` ebenso umgeschluesselt).
            header_cols = [(words[c.header_word], positionen.canonical_name(c.name),
                           c.value_type) for c in li.columns]
            stop = [words[i] for i in li.stop_words]
            tpl.line_items = build_line_item_spec(header_cols, stop,
                                                  doc.pages[li.page].width)
        elif body.line_items is not None:
            tpl.line_items = None

        store.save_template(tpl)
        if specs:
            # Fuer den Schattenbetrieb: an genau dieser Rechnung hat ein Mensch
            # Anker gesetzt -- der spaetere maschinelle Stand ist dann kein
            # eigener Verdienst der Maschine (shadow.corrected_fields).
            from ..config import operator as _op
            store.audit("template_trained", {"sha256": doc.sha256, "template_id": tid,
                                              "fields": [sp.name for sp in specs]}, _op())
        items = apply_line_items(doc, tpl.line_items) if tpl.line_items else []
        return JSONResponse({"template_id": tid, "fields": len(specs),
                             "line_items": items,
                             "extraction": {k: {"value": v.value, "confidence": v.confidence}
                                            for k, v in apply_template(doc, tpl).items()}})

    def _log_confirmed(path: Path, res, template_id: str | None, variant: str | None,
                       extraction: str | None = None) -> None:
        """Journal-Eintrag + JSON/CSV-Ausgabe einer gerade bestaetigten Rechnung."""
        fields = res.fields

        def fv(name):
            return fields[name].value if name in fields else None

        rec = {
            "file": path.name, "sha256": res.sha256,
            "supplier_id": res.supplier_id, "supplier_name": res.supplier_name,
            "template_id": template_id, "variant": variant,
            "billing_address_id": res.billing_address_id,
            "billing_address_name": res.billing_address_name,
            "rechnungsnummer": fv("rechnungsnummer"),
            "rechnungsdatum": fv("rechnungsdatum"),
            "netto": fv("netto"),
            "ust_betrag": fv("ust_betrag"),
            "brutto": fv("brutto"),
            "tax_breakdown": res.tax_breakdown,
            "line_items": res.line_items,
        }
        if extraction:
            rec["extraction"] = extraction
            rec["status"] = res.status
        store.mark_processed(rec)
        out = {k: v.value for k, v in fields.items()}
        out["_positionen"] = res.line_items
        out["_ust_aufstellung"] = res.tax_breakdown
        if variant and variant != "standard":
            addr = store.address(variant)
            out["rechnungsadresse"] = addr.name if addr else variant
        store.write_output(path.stem, out)

    @app.post("/api/confirm")
    def confirm(body: ConfirmPost) -> JSONResponse:
        from ..config import operator as _operator

        if body.template_id is None:
            return _confirm_untemplated(body.file, _operator())
        tpl = store.template(body.template_id)
        if tpl is None:
            raise HTTPException(404, "Template nicht gefunden")
        path = _resolve(body.file)
        doc = _load(path)
        # Erst verarbeiten (mit Zeitgrenze): eine abgebrochene Rechnung wird
        # weder bestaetigt noch gezaehlt.
        res = _process_for_confirm(path)
        was_secured = tpl.secured
        tpl.confirmations += 1
        store.save_template(tpl)
        # Dieser Klick ist der einzige Weg, wie eine Rechnung "bestaetigt"
        # gilt -- unabhaengig vom Status (auto/review). finalize() verweigert
        # sonst Archivierung/JobRouter-Uebergabe, egal wie sicher die
        # Extraktion aussieht.
        store.mark_confirmed(doc.sha256, _operator())

        # Volle Pipeline -> feste Werte, Positionen, USt-Aufstellung, Pruefungen.
        # Hat genau dieser Klick das Template gesichert, aendert sich der
        # Status (auto) -- dann einmal neu rechnen.
        if tpl.secured != was_secured:
            res = _process_for_confirm(path)
        _log_confirmed(path, res, tpl.id, tpl.variant)
        # Goldstandard fuer den Testsatz (testsatz.py, Aaron 10.10.2026)
        testsatz.merken(store, res, _operator())
        shadow.record(store, res, _operator())
        eventlog.record(store, res, _operator())
        # Stufe 1: aus den eben bestaetigten Werten das Template ergaenzen/
        # praezisieren (gesicherte Templates werden nur ergaenzt).
        learned = learn.learn_from_confirmed(store, res, doc, _operator())
        # Lag die Rechnung im Ordner "Menschliche Bearbeitung", verlaesst sie
        # ihn jetzt (erst NACH dem Lernen -- die Arbeitskopie wird entfernt).
        human.erledigt(store, res, _operator())
        return JSONResponse({"confirmations": tpl.confirmations, "secured": tpl.secured,
                             "learned": learned.to_dict()})

    @app.get("/api/startklar")
    def startklar_ep() -> JSONResponse:
        from .. import startklar
        return JSONResponse({"punkte": startklar.pruefen(store, samples)})

    @app.post("/api/sicherung")
    def sicherung_jetzt() -> JSONResponse:
        from .. import sicherung
        try:
            return JSONResponse(sicherung.sichern(store.root))
        except OSError as e:
            raise HTTPException(502, f"Sicherung fehlgeschlagen: {e}")

    @app.get("/api/jobrouter-vorschau")
    def jobrouter_vorschau(file: str) -> JSONResponse:
        """Genau das, was "Senden" an JobRouter schicken wuerde -- je Feld
        unser Name, JobRouter-Feldname (Zuordnung) und Wert (Aaron 11.10.2026:
        Zuordnung einmal mit der IT an einer Testrechnung pruefen)."""
        from .. import stammdaten
        from ..config import jobrouter as _jr

        path = _resolve(file)
        res = _process_for_confirm(path)
        payload = res.to_dict()
        payload["freigabe"] = "mensch"
        payload["stammdaten"] = stammdaten.fuer_rechnung(store, res)
        cfg = _jr()
        body = jobrouter.payload_from_result(payload, cfg)
        mapping = jobrouter.load_mapping(cfg)
        felder = [{"feld": k, "jobrouter": mapping.get(k, k), "zugeordnet": k in mapping,
                   "wert": (v.get("value") if isinstance(v, dict) else v)}
                  for k, v in (payload.get("fields") or {}).items()]
        return JSONResponse({"felder": felder, "nutzlast": body, "live": cfg.live,
                             "zuordnung_vorhanden": bool(mapping),
                             "prozess": {"name": cfg.process, "version": cfg.process_version,
                                         "schritt": cfg.step}})

    @app.get("/api/lernbericht")
    def lernbericht_vorschau() -> JSONResponse:
        from .. import lernbericht
        stand = lernbericht._stand(store.root)
        b = lernbericht.erstellen(store, stand.get("bis", ""))
        # Zuordnung Pseudonym -> Lieferant bleibt auf dem Laptop (nur Anzeige),
        # damit Aaron Rueckfragen zu "L-3f2a1c" beantworten kann.
        zuordnung = sorted(({"pseudonym": lernbericht.pseudonym(store.root, s.id), "name": s.name}
                            for s in store.suppliers()), key=lambda x: x["name"].lower())
        return JSONResponse({"zusammenfassung": b["zusammenfassung"], "letzter": stand,
                             "an": lernbericht._cfg()["an"], "zuordnung": zuordnung})

    @app.post("/api/lernbericht")
    def lernbericht_jetzt() -> JSONResponse:
        from .. import lernbericht
        try:
            return JSONResponse(lernbericht.senden(store, erzwingen=True))
        except Exception as e:  # noqa: BLE001 -- SMTP-Fehler sichtbar machen
            raise HTTPException(502, f"Versand fehlgeschlagen: {type(e).__name__}: {e}")

    @app.post("/api/ausschnitt-lesen")
    def ausschnitt_lesen(body: dict) -> JSONResponse:
        """Rahmen ohne erkannte Woerter (schlechter Scan, Text in Grafik):
        genau diesen Ausschnitt in hoher Aufloesung per Tesseract lesen
        (Aaron 11.10.2026). Koordinaten in PDF-Punkten der Seite."""
        import tempfile

        import pypdfium2 as pdfium

        from .. import ocr as ocrmod

        if not (ocrmod.enabled() and ocrmod.available()):
            raise HTTPException(409, "Texterkennung nicht verfügbar – Wert bitte von Hand eintragen")
        path = _resolve(str(body.get("file", "")))
        try:
            page_i = int(body.get("page", 0))
            x0, y0, x1, y1 = (float(body[k]) for k in ("x0", "y0", "x1", "y1"))
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Ausschnitt ungültig")
        if path.suffix.lower() != ".pdf":
            raise HTTPException(400, "nur PDF")
        scale = 400 / 72
        doc = pdfium.PdfDocument(str(path))
        try:
            img = doc[page_i].render(scale=scale).to_pil().convert("L")
        finally:
            doc.close()
        rand = 4
        box = (max(0, int(min(x0, x1) * scale) - rand), max(0, int(min(y0, y1) * scale) - rand),
               min(img.width, int(max(x0, x1) * scale) + rand), min(img.height, int(max(y0, y1) * scale) + rand))
        if box[2] - box[0] < 5 or box[3] - box[1] < 5:
            raise HTTPException(400, "Ausschnitt zu klein")
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "a.png"
            img.crop(box).save(png, dpi=(400, 400))
            r = ocrmod._run([ocrmod.tesseract_cmd(), str(png), "stdout", "-l", ocrmod.LANG,
                             "--psm", "6"], 60, text=True, encoding="utf-8", errors="replace")
        text = " ".join(" ".join((r.stdout or "").split("\n")).split())
        return JSONResponse({"text": text})

    @app.post("/api/senden")
    def senden(body: SendenPost) -> JSONResponse:
        """Ein Klick fuer die taegliche Arbeit (Aaron 11.10.2026): pruefen,
        bestaetigen (= lernen, Goldstandard), fuer JobRouter freigeben
        (finalize -> Warteschlange) und aus dem Eingang nehmen."""
        import time as _time

        from ..pipeline import finalize
        from ..template import sichtbare_felder

        path = _resolve(body.file)
        res = _process_for_confirm(path)
        fehlt = []
        sup = store.supplier(res.supplier_id) if res.supplier_id else None
        if sup is None:
            fehlt.append("Lieferant (über „Suchen...“ wählen oder anlegen)")
        elif not sup.bestaetigt:
            fehlt.append("Lieferant bestätigen")
        if store.addresses() and not res.billing_address_id:
            fehlt.append("Geschäftsbereich")
        if not res.einvoice:
            for name, (_t, req) in sichtbare_felder().items():
                fr = res.fields.get(name)
                if req and (fr is None or fr.source == "missing" or fr.value in (None, "")):
                    fehlt.append(name)
        if fehlt:
            raise HTTPException(400, "Noch nicht vollständig: " + ", ".join(fehlt))
        from .. import dubletten
        dub = [c for c in res.checks if c.name == dubletten.CHECK_NAME and c.ok is False]
        if dub and not body.trotzdem:
            return JSONResponse(status_code=409, content={
                "detail": "Mögliche Dublette: " + (dub[0].detail or "gleiche Rechnung schon verarbeitet"),
                "code": "dublette"})
        gelernt = confirm(ConfirmPost(file=body.file, template_id=body.template_id))            # lernen + Goldstandard + Ereignisse
        res = _process_for_confirm(path)   # Stand nach dem Lernen
        fin = finalize(res, store)
        if not fin.get("finalized"):
            raise HTTPException(409, f"Freigabe nicht möglich: {fin.get('error')}")
        if path.parent.resolve() == samples.resolve():
            ziel = store.root / "erledigt"
            ziel.mkdir(parents=True, exist_ok=True)
            path.replace(ziel / f"{_time.strftime('%Y%m%d-%H%M%S')}_{path.name}")
        if abholer is not None:
            abholer.anstossen()            # JobRouter-Nachschicken gleich versuchen
        from ..config import jobrouter as _jr
        return JSONResponse({"gesendet": True, "queued": fin.get("queued"),
                             "jobrouter_live": _jr().live,
                             "gelernt": __import__("json").loads(gelernt.body)})

    def _confirm_untemplated(file: str, operator: str) -> JSONResponse:
        """Bestaetigen ohne Template: E-Rechnung (Werte aus dem XML) oder ein
        PDF, dessen Werte der Mensch eingetippt/korrigiert bzw. aus der KI-
        Extraktion uebernommen und geprueft hat. Aus Letzterem lernt Stufe 1
        automatisch ein Template fuer den Lieferanten."""
        path = _resolve(file)
        if einvoice.load(path) is not None:
            return _confirm_einvoice(path, operator)
        res = _process_for_confirm(path)
        if not any(f.source not in ("library", "missing", "text") for f in res.fields.values()):
            raise HTTPException(400, "Keine Werte zum Bestaetigen -- erst Werte eintragen "
                                     "oder Template anlernen")
        store.mark_confirmed(res.sha256, operator)
        if not store.seen_sha_template(res.sha256, res.template_id):
            _log_confirmed(path, res, res.template_id, res.template_variant,
                           extraction=res.extraction)
        testsatz.merken(store, res, operator)
        shadow.record(store, res, operator)
        eventlog.record(store, res, operator)
        learned = learn.learn_from_confirmed(store, res, _load(path), operator)
        human.erledigt(store, res, operator)
        return JSONResponse({"confirmed": True, "status": res.status,
                             "confirmations": 0, "secured": False,
                             "learned": learned.to_dict()})

    def _confirm_einvoice(path: Path, operator: str) -> JSONResponse:
        res = _process_for_confirm(path)
        store.mark_confirmed(res.sha256, operator)
        if not store.seen_sha_template(res.sha256, None):
            _log_confirmed(path, res, None, None, extraction=EXTRACT_EINVOICE)
        testsatz.merken(store, res, operator)
        shadow.record(store, res, operator)
        eventlog.record(store, res, operator)
        human.erledigt(store, res, operator)
        return JSONResponse({"confirmed": True, "extraction": EXTRACT_EINVOICE,
                             "status": res.status})

    @app.post("/api/correction")
    def save_correction_ep(body: CorrectionPost) -> JSONResponse:
        from ..config import operator
        path = _resolve(body.file)
        sha = _sha(path)
        op = operator()
        # "alt" ist der Wert der vorherigen Korrektur dieser Rechnung/dieses
        # Feldes (falls vorhanden) -- fuer den Fehlerbericht (eventlog.py).
        alt = store.corrections(sha).get(body.field)
        store.save_correction(sha, body.field, body.value, op)
        sup_id = sup_name = None
        try:
            doc = _load(path)
            _addr_match, address_id, has_addr = _address_scope(doc)
            match = identify(doc, store.suppliers(), index=store.supplier_index(address_id),
                             address_id=address_id, has_addresses=has_addr)
            if match:
                sup_id, sup_name = match[0].id, match[0].name
        except HTTPException:
            pass
        eventlog.record_correction(store, sha, body.field, alt, body.value, op,
                                   supplier_id=sup_id, supplier_name=sup_name,
                                   file=path.name, template_id=body.template_id,
                                   quelle_vorher=body.quelle_vorher,
                                   programmwert=body.wert_vorher)
        return JSONResponse({"ok": True})

    @app.post("/api/tabelle")
    def save_tabelle_ep(body: TabellePost) -> JSONResponse:
        """Positionstabelle bzw. USt-Aufstellung als Ganzes korrigieren (11b).
        Gespeichert wie jede Korrektur dieser Rechnung (sha256) -- beim
        Bestaetigen lernt learn.py daraus Tabellen-Spec bzw. USt-Block."""
        from ..config import operator
        if body.art not in ("positionen", "ust"):
            raise HTTPException(400, "art muss 'positionen' oder 'ust' sein")
        path = _resolve(body.file)
        sha = _sha(path)
        op = operator()
        if body.art == "positionen":
            key = POSITIONEN_KEY
            rows = []
            for r in body.rows:
                row = {str(k).strip(): _zelle(v) for k, v in r.items()
                       if str(k).strip() and not str(k).startswith("_")}
                if any(v is not None for v in row.values()):
                    rows.append(row)
            # Spaltenname (ggf. frei vom Menschen vergeben) sofort auf den
            # Standardnamen abbilden (SPEC Baustein 1) -- validate.py und
            # das Lernen (learn_line_items) kennen nur noch Standardnamen.
            rows = positionen.normalize_columns(rows)
            # SPEC Baustein 5: die vom Menschen bestaetigte Spalten-Zuordnung
            # der Rahmen-Markierung (Baustein 2) wandert ins globale
            # Spaltenwoerterbuch -- gilt als bestaetigt, sobald der Mensch
            # "Positionen uebernehmen" klickt (keine Extra-Bestaetigung).
            for eintrag in body.spalten_zuordnung or []:
                text, ziel_spalte = eintrag.get("header_text"), eintrag.get("ziel")
                if text and ziel_spalte:
                    store.spaltenwort_lernen(text, ziel_spalte, op)
        else:
            key = "ust_aufstellung"
            rows = [z for z in (_ust_zeile(r) for r in body.rows) if z is not None]
        alt = store.corrections(sha).get(key)
        store.save_correction(sha, key, rows, op)
        sup_id = sup_name = None
        try:
            doc = _load(path)
            _addr_match, address_id, has_addr = _address_scope(doc)
            match = identify(doc, store.suppliers(), index=store.supplier_index(address_id),
                             address_id=address_id, has_addresses=has_addr)
            if match:
                sup_id, sup_name = match[0].id, match[0].name
        except HTTPException:
            pass
        eventlog.record_correction(store, sha, key, alt, rows, op,
                                   supplier_id=sup_id, supplier_name=sup_name,
                                   file=path.name, template_id=body.template_id,
                                   quelle_vorher=body.quelle_vorher)
        return JSONResponse({"ok": True, "rows": rows})

    @app.post("/api/tabelle/markieren")
    def tabelle_markieren_ep(body: TabelleMarkierenPost) -> JSONResponse:
        """Baustein 2: Rahmen(e) um die Positionstabelle -> erkannte Spalten
        (mit Zuordnungs-Vorschlag) + die daraus gelesenen Positionen. Reine
        Vorschau (speichert nichts) -- "Positionen uebernehmen" (/api/tabelle)
        macht aus dem Ergebnis danach eine normale Korrektur."""
        doc = _load(_resolve(body.file))
        try:
            boxes = [{"page": int(b["page"]), "x0": float(b["x0"]), "top": float(b["top"]),
                     "x1": float(b["x1"]), "bottom": float(b["bottom"])} for b in body.boxes]
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Rahmen ungültig")
        grid = tabelle.markiere_tabelle(doc, boxes, woerterbuch=store.spaltenwoerter())
        items = tabelle.anwenden_markierung(grid, body.mapping)
        return JSONResponse({"columns": grid["columns"], "rows": grid["rows"], "items": items})

    @app.post("/api/tabelle/zeile_vorschlag")
    def zeile_vorschlag_ep(body: ZeileVorschlagPost) -> JSONResponse:
        """Baustein 3 (Aaron 12.10.2026): "stehen sehr wahrscheinlich links
        und rechts davon in der gleichen Zeile die restlichen Informationen
        zu diesem Artikel" -- ausgehend von einer per Rahmen bestaetigten
        Zelle die uebrigen Spalten der Zeile vorschlagen. Reine Vorschau
        (speichert nichts); ``zulaessig=False`` heisst: die Rechen-Pruefung
        (Baustein 4) widerlegt den Vorschlag, das UI darf ihn nicht
        anbieten."""
        doc = _load(_resolve(body.file))
        wert = _zelle(body.wert)
        vorschlag = tabelle.zeilen_vorschlag(doc, body.page, body.indices, body.ziel_spalte,
                                             body.zeile)
        zulaessig = tabelle.zeilen_vorschlag_zulaessig(vorschlag, body.zeile, body.ziel_spalte, wert)
        return JSONResponse({"vorschlag": vorschlag if zulaessig else {}, "zulaessig": zulaessig})

    @app.post("/api/pruefen")
    def pruefen_ep(body: PruefenPost) -> JSONResponse:
        """Rechen-Pruefungen (Baustein 9) live auf dem aktuellen UI-Stand,
        rot/gruen je Zeile -- reine Anzeige, speichert nichts."""
        text = ""
        if body.file:
            try:
                text = _load(_resolve(body.file)).text
            except HTTPException:
                text = ""
        fields = _live_fields({k: v for k, v in body.fields.items() if not isinstance(v, list)})
        tax_rows = [z for z in (_ust_zeile(r) for r in body.ust_aufstellung) if z is not None]
        if tax_rows:
            fields["ust_aufstellung"] = FieldResult("ust_aufstellung", tax_rows, "", 1.0, "manual")
        items = positionen.normalize_columns([{k: _zelle(v) for k, v in r.items()}
                                              for r in body.line_items])
        return JSONResponse(rechen_pruefungen(fields, items, text))

    @app.delete("/api/correction")
    def delete_correction_ep(file: str, field: str) -> JSONResponse:
        """Manuelle Korrektur zuruecknehmen -- faellt auf den automatisch
        extrahierten/festen Wert zurueck, statt den Tippfehler zu behalten."""
        from ..config import operator
        path = _resolve(file)
        store.delete_correction(_sha(path), field, operator())
        return JSONResponse({"ok": True})

    @app.post("/api/category")
    def save_category_ep(body: CategoryPost) -> JSONResponse:
        """Positionstext -> Kategorie lernen. Gilt lieferantenuebergreifend
        und wirkt sofort auf die naechste Vorschlags-Berechnung (process())."""
        from ..config import operator
        store.save_category_example(body.text, body.kategorie, operator())
        return JSONResponse({"ok": True})

    @app.get("/api/categories")
    def list_categories() -> JSONResponse:
        """Gelernte Kategorie-Zuordnungen einsehbar machen -- sonst poisont
        ein einzelner Tippfehler die Vorschlaege unsichtbar fuer immer."""
        rows = store.category_examples()
        return JSONResponse([{"key": k, **v} for k, v in sorted(rows.items())])

    @app.delete("/api/categories/{key}")
    def delete_category_ep(key: str) -> JSONResponse:
        from ..config import operator
        store.delete_category_example(key, operator())
        return JSONResponse({"ok": True})

    @app.post("/api/template/copy")
    def copy_template_ep(body: CopyTemplatePost) -> JSONResponse:
        """Bestehendes Template als Startpunkt fuer einen neuen/aehnlichen
        Lieferanten uebernehmen, statt Anker von Null anzulernen. Das neue
        Template startet mit confirmations=0 -- es muss wie jedes andere erst
        an echten Rechnungen bestaetigt werden, bevor es auf 'auto' springt."""
        src = store.template(body.source_template_id)
        if src is None:
            raise HTTPException(404, "Quell-Template nicht gefunden")
        path = _resolve(body.file)
        doc = _load(path)

        sup = store.supplier(body.target_supplier_id)
        if sup is None:
            sup = supplier_from_doc(doc, body.target_supplier_id, body.target_supplier_name)
            store.save_supplier(sup)

        tid = f"{body.target_supplier_id}__{body.variant}"
        tpl = Template(id=tid, supplier_id=body.target_supplier_id, variant=body.variant,
                       fields=copy.deepcopy(src.fields),
                       line_items=copy.deepcopy(src.line_items))
        store.save_template(tpl)
        return JSONResponse({
            "template_id": tid, "fields": len(tpl.fields),
            "extraction": {k: {"value": v.value, "confidence": v.confidence}
                          for k, v in apply_template(doc, tpl).items()},
        })

    # ---- Bibliothek: Lieferanten ------------------------------------
    @app.get("/api/suppliers")
    def list_suppliers() -> JSONResponse:
        return JSONResponse([
            {**s.to_dict(),
             "templates": [{"id": t.id, "variant": t.variant,
                            "confirmations": t.confirmations, "secured": t.secured,
                            "stale": t.stale, "fields": [f.name for f in t.fields]}
                           for t in store.templates_for(s.id)]}
            for s in store.suppliers()
        ])

    @app.post("/api/suppliers")
    def save_supplier_ep(body: SupplierPost) -> JSONResponse:
        existing = store.supplier(body.id)
        fp = existing.fingerprint if existing else []
        fixed = {k: v.strip() for k, v in (body.fixed_fields or {}).items()
                 if v and v.strip()}
        store.save_supplier(Supplier(
            id=body.id, name=body.name,
            ustids=sorted({x.strip().upper() for x in body.ustids if x.strip()}),
            ibans=sorted({x.strip().upper().replace(" ", "") for x in body.ibans if x.strip()}),
            domains=sorted({x.strip().lower() for x in body.domains if x.strip()}),
            fingerprint=fp,
            addresses=sorted({x.strip() for x in body.addresses if x.strip()}),
            external_id=body.external_id.strip(),
            address_lines=[x for x in body.address_lines if x.strip()],
            bank_name=body.bank_name.strip(), bic=body.bic.strip().upper(),
            bank_code=body.bank_code.strip(), bank_account=body.bank_account.strip(),
            creditor_id=body.creditor_id.strip(), contact=body.contact.strip(),
            default_payment_terms=body.default_payment_terms.strip(),
            default_tax_rate=body.default_tax_rate.strip(),
            notes=body.notes.strip(),
            fixed_fields=fixed,
            currency=(body.currency or "EUR").strip().upper(),
            # Entwurfsstatus bleibt unberuehrt, solange nur Felder gespeichert
            # werden -- bestaetigt wird ausschliesslich ueber den eigenen
            # Weg (POST .../bestaetigen), sonst wuerde ein einfaches
            # Speichern der Felder (z.B. beim Pruefen eines Entwurfs) den
            # Entwurf unbemerkt ohne Audit-Eintrag bestaetigen.
            bestaetigt=existing.bestaetigt if existing else True,
            entwurf_quelle=existing.entwurf_quelle if existing else "",
            entwurf_am=existing.entwurf_am if existing else "",
            # Positionsmodus (Teil B): ein vorhandener Lieferant aendert ihn
            # ausschliesslich ueber POST .../positionsmodus -- hier bleibt er
            # unberuehrt, damit ein einfaches Speichern der Stammdaten ihn
            # nicht nebenbei zuruecksetzt. Nur beim ERSTEN Anlegen (kein
            # ``existing``) uebernimmt dieser Endpunkt den beim Pruefen der
            # Rechnung gewaehlten Modus mit ("ohne bekannten Lieferanten gilt
            # die Auswahl fuer diese Rechnung und wird beim Anlegen mitgespeichert").
            positionsmodus=(existing.positionsmodus if existing
                           else (body.positionsmodus if body.positionsmodus in POSITIONSMODI
                                 else POSITIONSMODUS_EINZELN)),
            positionsbezeichnung=(existing.positionsbezeichnung if existing
                                  else body.positionsbezeichnung.strip()),
            positionsmodus_gesetzt=(existing.positionsmodus_gesetzt if existing
                                    else body.positionsmodus != POSITIONSMODUS_EINZELN),
        ))
        return JSONResponse({"ok": True})

    @app.post("/api/suppliers/{supplier_id}/positionsmodus")
    def set_positionsmodus_ep(supplier_id: str, body: PositionsmodusPost) -> JSONResponse:
        """Positionsmodus setzen (Gruppe "Positionsdaten" beim Pruefen bzw.
        Reiter "Lieferanten", Teil B) -- gilt danach fuer alle Rechnungen
        dieses Lieferanten, Audit-Eintrag mit Operator."""
        from ..config import operator as _operator

        if body.modus not in POSITIONSMODI:
            raise HTTPException(400, f"Unbekannter Positionsmodus {body.modus!r}")
        try:
            sup = set_positionsmodus(store, supplier_id, body.modus, body.bezeichnung,
                                     _operator())
        except ValueError as e:
            raise HTTPException(404, str(e))
        return JSONResponse({"ok": True, "supplier": sup.to_dict()})

    @app.delete("/api/suppliers/{supplier_id}")
    def delete_supplier_ep(supplier_id: str) -> JSONResponse:
        store.delete_supplier(supplier_id)
        return JSONResponse({"ok": True})

    @app.post("/api/suppliers/{supplier_id}/bestaetigen")
    def confirm_supplier_ep(supplier_id: str) -> JSONResponse:
        """Lieferanten-Entwurf bestaetigen (Abschnitt "2 Lieferant" bzw.
        Reiter "Lieferanten") -- ab jetzt ein normaler Lieferant."""
        from ..config import operator as _operator

        try:
            sup = confirm_supplier(store, supplier_id, _operator())
        except ValueError as e:
            raise HTTPException(404, str(e))
        return JSONResponse({"ok": True, "supplier": sup.to_dict()})

    @app.post("/api/suppliers/{supplier_id}/verwerfen")
    def discard_supplier_ep(supplier_id: str) -> JSONResponse:
        """Lieferanten-Entwurf verwerfen -- nur fuer unbestaetigte erlaubt."""
        from ..config import operator as _operator

        try:
            discard_supplier(store, supplier_id, _operator())
        except ValueError as e:
            raise HTTPException(409, str(e))
        return JSONResponse({"ok": True})

    @app.delete("/api/templates/{template_id}")
    def delete_template_ep(template_id: str) -> JSONResponse:
        store.delete_template(template_id)
        return JSONResponse({"ok": True})

    # ---- Bibliothek: Rechnungsadressen ----------------------------
    @app.get("/api/addresses")
    def list_addresses() -> JSONResponse:
        return JSONResponse([a.to_dict() for a in store.addresses()])

    @app.post("/api/addresses")
    def save_address_ep(body: AddressPost) -> JSONResponse:
        store.save_address(BillingAddress(
            id=body.id, name=body.name,
            lines=[x for x in body.lines if x.strip()],
            match_terms=[x for x in body.match_terms if x.strip()],
            cost_center=body.cost_center.strip(),
            company_code=body.company_code.strip(),
            notes=body.notes.strip(),
        ))
        return JSONResponse({"ok": True})

    @app.delete("/api/addresses/{address_id}")
    def delete_address_ep(address_id: str) -> JSONResponse:
        store.delete_address(address_id)
        return JSONResponse({"ok": True})

    # ---- Buchungsjournal -----------------------------------------
    @app.get("/api/modus")
    def modus_ep() -> JSONResponse:
        """Betriebsmodus + Fortschritt Testphase -> Autonomie (modus.py).
        Nur lesend -- umgeschaltet wird ausschliesslich per CLI-Befehl
        ``rechnung modus ... --operator NAME``."""
        from .. import modus

        fp = modus.fortschritt(store)
        return JSONResponse(dict(fp, text=modus.text(fp)))

    # ---- Selbst-Update (Laptop-Testphase, selbstupdate.py) ----------
    @app.get("/api/version")
    def version_ep() -> JSONResponse:
        from .. import selbstupdate

        return JSONResponse(selbstupdate.version())

    @app.post("/api/update")
    def update_ep() -> JSONResponse:
        """Neuen Stand holen + Tests. Startet NICHT selbst neu -- das
        bestaetigt der Nutzer danach (/api/neustart), damit keine offene
        Bearbeitung verloren geht."""
        from .. import selbstupdate

        return JSONResponse(selbstupdate.aktualisieren(store.root).dict())

    @app.post("/api/neustart")
    def neustart_ep() -> JSONResponse:
        """Beendet das Programm mit RESTART_EXIT_CODE; der Laptop-Starter
        startet es sofort mit dem neuen Code wieder."""
        import os
        import threading

        from .. import selbstupdate

        threading.Timer(0.5, lambda: os._exit(selbstupdate.RESTART_EXIT_CODE)).start()
        return JSONResponse({"ok": True})

    @app.get("/api/menschlich")
    def menschlich_ep() -> JSONResponse:
        """Ordner "Menschliche Bearbeitung": was wartet auf wen und warum
        (Gruende, betroffene Felder, Vorschlagswerte mit Herkunft)."""
        return JSONResponse({"ordner": str(human.human_dir(store.root)),
                             "offen": human.offen(store)})

    # ---- Ordner "Keine Rechnung" (Entscheidung Aaron 14.10.2026) -------
    @app.get("/api/keine-rechnung")
    def keine_rechnung_ep() -> JSONResponse:
        """Ordner "Keine Rechnung": aussortierte Dokumente (Datei, Grund,
        Absender/Betreff, Zeitpunkt)."""
        return JSONResponse({"ordner": str(keine_rechnung.ordner(store.root)),
                             "offen": keine_rechnung.offen(store)})

    @app.post("/api/keine-rechnung/zurueck")
    def keine_rechnung_zurueck(body: dict) -> JSONResponse:
        """Knopf "Doch eine Rechnung": die Regel lag falsch -- Datei zurueck
        in den Eingang, wird normal verarbeitet (zaehlt als
        Fehlsortierung)."""
        from ..config import operator as _operator

        rec = keine_rechnung.zurueck_in_eingang(store, body.get("sha256", ""), samples,
                                                operator=_operator())
        if rec is None:
            raise HTTPException(404, "Nicht im Ordner 'Keine Rechnung' gefunden")
        return JSONResponse(rec)

    @app.post("/api/keine-rechnung/von-hand")
    def keine_rechnung_von_hand(body: dict) -> JSONResponse:
        """Knopf "Keine Rechnung" in der Pruefmaske einer offenen Rechnung
        (Ordner "Menschliche Bearbeitung"): der Mensch sortiert von Hand
        aus."""
        from ..config import operator as _operator

        rec = keine_rechnung.von_hand_aussortieren(store, body.get("file", ""),
                                                   operator=_operator())
        if rec is None:
            raise HTTPException(404, "Nicht in 'Menschliche Bearbeitung' gefunden")
        return JSONResponse(rec)

    @app.get("/api/keine-rechnung/datei")
    def keine_rechnung_datei(file: str) -> Response:
        """Vorschau/Download einer aussortierten Datei (PDF inline, .eml/.txt
        als Download) -- bewusst ein eigener, einfacher Endpunkt statt der
        vollen Rechnungs-Pruefmaske: diese Dateien sind keine Rechnungen."""
        p = keine_rechnung.datei_pfad(store, file)
        if p is None:
            raise HTTPException(404, "Datei nicht gefunden")
        media = "application/pdf" if p.suffix.lower() == ".pdf" else "application/octet-stream"
        return Response(p.read_bytes(), media_type=media)

    @app.post("/api/keine-rechnung/loeschen")
    def keine_rechnung_loeschen(body: dict) -> JSONResponse:
        """Endgueltig loeschen (Nachtrag 14.10.2026) -- einzeln oder
        Mehrfachauswahl. Bestaetigung passiert im UI vor diesem Aufruf."""
        from ..config import operator as _operator

        namen = body.get("namen") or ([body["name"]] if body.get("name") else [])
        if not namen:
            raise HTTPException(400, "Kein Dateiname angegeben")
        return JSONResponse(keine_rechnung.loeschen(store, namen, operator=_operator()))

    @app.get("/api/shadow")
    def shadow_stats() -> JSONResponse:
        """Schattenbetrieb je Lieferant: Rechnungen / wuerde durchlassen /
        davon ohne Korrektur / Fehlentscheidungen. Reine Auswertung -- die
        Bestaetigungspflicht bleibt unveraendert."""
        return JSONResponse(shadow.stats(store))

    @app.get("/api/fehlerbericht")
    def fehlerbericht_ep(seit: str | None = None) -> JSONResponse:
        """Haeufigste wiederkehrende Fehler (rote Pruefungen, fehlende/
        unsichere Felder, Korrekturen) je Pruefung/Feld und Lieferant,
        optional ab einem Datum (``seit=YYYY-MM-DD``). Siehe eventlog.py."""
        return JSONResponse(eventlog.report(store, seit))

    # ---- Qualitaet: Testsatz aus bestaetigten Rechnungen (testsatz.py) ----
    # Laeuft im Hintergrund (eigener Arbeitsprozess mit Zeitgrenze je
    # Rechnung), das UI fragt den Fortschritt ab (Aaron 10.10.2026).
    import threading as _threading

    testsatz_lauf: dict = {"laeuft": False, "fertig": 0, "gesamt": 0, "datei": "",
                           "fehler": "", "gestartet": ""}

    @app.get("/api/testsatz")
    def testsatz_status() -> JSONResponse:
        letzter = testsatz.letzter_bericht(store.root)
        return JSONResponse({"lauf": dict(testsatz_lauf), "letzter": letzter,
                             "faelle": len(testsatz.faelle(store)[0])})

    @app.get("/api/qualitaet")
    def qualitaet_ep() -> JSONResponse:
        """Reiter "Qualitaet": Lieferanten-Gedaechtnis (plausi.py),
        Fehlerstatistik (eventlog.py). E-Rechnungs-Kandidaten/Anschreiben
        bewusst entfernt (Aaron 10.10.2026: nicht Aufgabe des Programms)."""
        from .. import plausi

        return JSONResponse({"profile": plausi.profile(store),
                             "min_rechnungen": plausi.MIN_RECHNUNGEN,
                             "fehlerstatistik": eventlog.fehlerstatistik(store)})

    @app.post("/api/testsatz")
    def testsatz_start() -> JSONResponse:
        if testsatz_lauf["laeuft"]:
            raise HTTPException(409, "Testsatz laeuft bereits")
        import time as _time

        testsatz_lauf.update(laeuft=True, fertig=0, gesamt=0, datei="", fehler="",
                             gestartet=_time.strftime("%Y-%m-%dT%H:%M:%S"))

        def fortschritt(fertig: int, gesamt: int, datei: str) -> None:
            testsatz_lauf.update(fertig=fertig, gesamt=gesamt, datei=datei)

        def run() -> None:
            try:
                testsatz.pruefen(store, fortschritt=fortschritt)
            except Exception as e:  # noqa: BLE001 -- Fehler im UI anzeigen
                testsatz_lauf["fehler"] = f"{type(e).__name__}: {e}"
            finally:
                testsatz_lauf["laeuft"] = False

        _threading.Thread(target=run, daemon=True, name="testsatz").start()
        return JSONResponse({"gestartet": True})

    # ---- Vorab-Lernen aus Altbestand (vorlernen.py) -------------------
    # Bewusst getrennt vom Rechnungen-Reiter: nichts hiervon geht nach
    # JobRouter oder in die Schattenstatistik; aktiv wird eine Vorlage erst
    # nach dem Klick "Gruppe bestaetigen".
    def _vl_view(g: dict) -> dict:
        return dict(g, markiert=vorlernen.deviations(g))

    def _vl_or_404(g: dict | None) -> JSONResponse:
        if g is None:
            raise HTTPException(404, "Vorlernen-Gruppe nicht gefunden")
        return JSONResponse(_vl_view(g))

    @app.get("/api/vorlernen")
    def vorlernen_list() -> JSONResponse:
        return JSONResponse({"gruppen": [_vl_view(g) for g in vorlernen.list_groups(store)]})

    @app.post("/api/vorlernen/scan")
    def vorlernen_scan(body: VorlernenScanPost) -> JSONResponse:
        ordner = Path(body.ordner).expanduser()
        if not ordner.is_dir():
            raise HTTPException(404, f"Ordner nicht gefunden: {ordner}")
        return JSONResponse(vorlernen.scan(ordner, store).to_dict())

    @app.post("/api/vorlernen/{gruppe_id}/korrektur")
    def vorlernen_korrektur(gruppe_id: str, body: VorlernenKorrekturPost) -> JSONResponse:
        try:
            g = vorlernen.set_correction(store, gruppe_id, body.sha256, body.feld, body.wert)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return _vl_or_404(g)

    @app.post("/api/vorlernen/{gruppe_id}/ausschliessen")
    def vorlernen_ausschliessen(gruppe_id: str, body: VorlernenAusschlussPost) -> JSONResponse:
        return _vl_or_404(vorlernen.set_excluded(store, gruppe_id, body.sha256,
                                                 body.ausgeschlossen))

    @app.post("/api/vorlernen/{gruppe_id}/lieferant")
    def vorlernen_lieferant(gruppe_id: str, body: VorlernenLieferantPost) -> JSONResponse:
        sup = store.supplier(body.supplier_id)
        if sup is None:
            raise HTTPException(404, "Lieferant nicht in der Bibliothek -- erst anlegen")
        return _vl_or_404(vorlernen.assign_supplier(store, gruppe_id, sup.id, sup.name))

    @app.post("/api/vorlernen/{gruppe_id}/bestaetigen")
    def vorlernen_bestaetigen(gruppe_id: str) -> JSONResponse:
        from ..config import operator as _op

        try:
            return JSONResponse(vorlernen.confirm(store, gruppe_id, _op()))
        except ValueError as e:
            raise HTTPException(409, str(e))

    @app.delete("/api/vorlernen/{gruppe_id}")
    def vorlernen_verwerfen(gruppe_id: str) -> JSONResponse:
        if not vorlernen.discard(store, gruppe_id):
            raise HTTPException(404, "Vorlernen-Gruppe nicht gefunden")
        return JSONResponse({"ok": True})

    @app.get("/api/journal")
    def journal() -> JSONResponse:
        rows = journal_rows(store)
        return JSONResponse({
            "rows": rows,
            "totals": journal_totals(rows),
            "fixed_field_types": FIXED_FIELD_TYPES,
        })

    @app.get("/api/journal.csv")
    def journal_csv() -> Response:
        import csv
        import io as _io

        rows = journal_rows(store)
        buf = _io.StringIO()
        cols = (["datei", "lieferant", "rechnungsadresse", "rechnungsnummer",
                 "rechnungsdatum", "netto_gesamt", "ust_gesamt", "brutto_gesamt"]
                + [f"netto_{int(r)}" for r in (19, 7, 0)]
                + [f"ust_{int(r)}" for r in (19, 7, 0)] + ["weitere_saetze"])
        w = csv.writer(buf, delimiter=";")
        w.writerow(cols)
        for row in rows:
            w.writerow(["" if row.get(c) is None else row.get(c) for c in cols])
        tot = journal_totals(rows)
        w.writerow(["SUMME", "", "", f"{tot['rechnungen']} Rechnungen", ""]
                   + [tot.get(c, "") for c in cols[5:8]]
                   + [tot.get(c, "") for c in cols[8:14]] + [""])
        # BOM voran: Excel unter Windows erkennt sonst UTF-8 nicht (Umlaute)
        return Response("\ufeff" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=journal.csv"})

    if _STATIC.exists():
        app.mount("/static", StaticFiles(directory=_STATIC), name="static")
    return app
