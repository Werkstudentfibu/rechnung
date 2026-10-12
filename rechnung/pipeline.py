"""Orchestrierung: Lieferant erkennen -> Template anwenden -> validieren -> Status."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import hashlib
import json
import re
import time

from . import anlagen
from . import belegart
from . import billing
from . import dubletten
from . import einvoice
from . import jobrouter
from . import ocr
from . import pdf as pdfmod
from . import pflicht
from . import plausi
from . import positionen
from . import rechnen
from . import semantic
from . import shadow
from . import split
from . import swissqr
from . import tabelle
from . import waehrung
from . import zahlung
from .timing import StageTimer
from .config import jobrouter as _jr_cfg
from .config import operator as _operator
from .identify import (DEFAULT_CURRENCY, EXPECTED_RATES, FIXED_FIELD_TYPES, IBAN_DEVIATES,
                       NEW_SUPPLIER_HOLD, POSITIONSMODI, POSITIONSMODUS_EINE_POSITION,
                       POSITIONSMODUS_EINZELN, POSITIONSMODUS_ZUSAMMENFASSUNG, Supplier,
                       _address_scope_reason, _detect_currency, cross_check,
                       extract_identifiers, identify_einvoice, identify_with_reason, kandidaten)
from .store import Store
from .suggest import suggest_category
from .template import (STANDARD_FIELDS, FieldResult, Template, apply_line_items,
                       apply_template)
from .textnorm import coerce, compact, find_ibans, norm
from .validate import Check, run_all

# Status
AUTO = "auto"                    # kann automatisch weitergeleitet werden
REVIEW = "review"                # Template da, aber Konfidenz/Prüfung schwach
NO_TEMPLATE = "no_template"      # Lieferant bekannt, kein Template -> anlernen
# Kein eindeutiger Lieferant / keine eindeutige Rechnungsadresse aus der
# Bibliothek, IBAN weicht von den Stammdaten ab, ... -> ein Mensch muss die
# Rechnung bearbeiten. Gruende stehen in ``Result.hold_reasons``. Ersetzt das
# fruehere "unknown_supplier" (UNKNOWN bleibt als Alias fuer alten Code).
DEFERRED = "zurueckgestellt"
UNKNOWN = DEFERRED
SCAN = "scan_no_textlayer"      # historisch, wird nicht mehr vergeben -- Scans
                                 # laufen seit Stufe 3 automatisch durch ocr.py
UNSUPPORTED = "unsupported_file"  # weder PDF noch lesbare E-Rechnung (z.B. fremdes XML)

# Anzeigetexte fuer die Oberflaeche
STATUS_LABELS = {
    AUTO: "auto",
    REVIEW: "review",
    NO_TEMPLATE: "keine Vorlage",
    DEFERRED: "zurückgestellt – menschlich bearbeiten",
    SCAN: "Scan ohne Text",
    UNSUPPORTED: "nicht lesbar",
}

# Woher die Feldwerte stammen (Result.extraction)
EXTRACT_TEMPLATE = "template"
EXTRACT_EINVOICE = "einvoice"
EXTRACT_LLM = "llm"              # Stufe 2: KI-Extraktion ohne Template

# Herkunft je Feld, vereinheitlicht fuer Anzeige/Auswertung (FieldResult.source
# ist feiner: template-anchor/template-abs).
ORIGINS = ("einvoice", "template", "llm", "library", "manual", "text")


def origin(source: str) -> str | None:
    if source == "missing":
        return None
    return "template" if source.startswith("template") else source

INVOICE_SUFFIXES = (".pdf", ".xml") + ocr.IMAGE_SUFFIXES

CONF_MIN = 0.7


@dataclass
class Result:
    file: str
    sha256: str = ""
    supplier_id: str | None = None
    supplier_name: str | None = None
    # Lieferantennummer im Fremdsystem (Supplier.external_id, Abbyy-'Id') --
    # fuer den JobRouter-Export (jobrouter.payload_from_result), unabhaengig
    # von unserer eigenen (ggf. um die BusinessUnitId erweiterten) internen
    # ``supplier_id``.
    supplier_external_id: str | None = None
    match_score: float = 0.0
    template_id: str | None = None
    template_variant: str | None = None
    template_secured: bool = False
    billing_address_id: str | None = None
    billing_address_name: str | None = None
    fields: dict[str, FieldResult] = field(default_factory=dict)
    line_items: list[dict] = field(default_factory=list)
    tax_breakdown: list[dict] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    status: str = UNKNOWN
    overall_confidence: float = 0.0
    extraction: str = EXTRACT_TEMPLATE
    einvoice: dict | None = None     # Rohdaten der E-Rechnung (Stufe 0), sonst None
    # KI-Vorschlaege, die NICHT im PDF-Text belegt waren (Feld -> Grund) --
    # verworfen, aber sichtbar, damit der Mensch weiss, was das Modell meinte.
    llm_rejected: dict[str, str] = field(default_factory=dict)
    # Von der KI auf der Rechnung belegte Lieferantenangaben (semantic
    # _SUPPLIER_KEYS) -- nur Hinweis, nie Identitaet; dient dazu, den Namen
    # eines Lieferanten-Entwurfs zu fuellen (09.10.2026).
    ki_lieferant: dict = field(default_factory=dict)
    # Ebenso der Rechnungsempfaenger (name/lines) -- Vorschlag fuer eine noch
    # unbekannte Rechnungsadresse, angelegt wird nur per Klick im UI.
    ki_empfaenger: dict = field(default_factory=dict)
    # Schattenbetrieb: haette die Maschine allein durchgelassen? Entschieden
    # auf dem Stand VOR manuellen Korrekturen (shadow.decide) -- reine
    # Messung, aendert weder Status noch Bestaetigungspflicht.
    shadow: dict | None = None
    # Warum die Rechnung zurueckgestellt ist (Status DEFERRED), fuer den Menschen
    hold_reasons: list[str] = field(default_factory=list)
    # Verarbeitungsdauer in Sekunden (guard.py) und ggf. Abbruchart
    # ("zeitgrenze" / "fehler"), sonst leer
    duration_s: float = 0.0
    aborted: str = ""
    # Dauer je Stufe in Sekunden (timing.py, SPEC Effizienz-Baustein 0):
    # laden, ocr, erechnung, lieferant, vorlage, ki, pruefungen
    stufenzeiten: dict[str, float] = field(default_factory=dict)
    # Texterkennung (ocr.py), falls der Text per OCR statt aus einer
    # Textebene stammt -- dann tragen alle gelesenen Felder ``via: ocr``.
    ocr: dict | None = None
    # Deutliche Warnungen, die mit der Rechnung bis nach JobRouter gehen
    # (z.B. IBAN-Abweichung bei RECHNUNG_IBAN_ABWEICHUNG=warnen)
    warnings: list[str] = field(default_factory=list)
    # Was dispatch() mit der Rechnung gemacht hat (Mensch/autonom/...)
    verteilung: dict | None = None
    # Teil eines aufgeteilten Sammel-PDFs (split.py): Verweis auf das
    # Original (sha256, Name, Seiten), sonst None
    sammel: dict | None = None
    # Seitenzahl (Zeitmessung je Seitenzahl, Postfach-Baustein 3)
    seiten: int = 0
    # Dublettenverdacht (dubletten.py): Verweis auf die Ur-Rechnung
    dublette: dict | None = None
    # Hinweise fuer den Menschen, die nichts blockieren (z.B. "Skonto
    # gestaffelt -- leer gelassen", Positionen folgen nach OCR) -- gehen mit
    # nach JobRouter (``hints``), anders als ``warnings`` ohne "ACHTUNG"
    hinweise: list[str] = field(default_factory=list)
    # Schweizer QR-Zahlteil (swissqr.py), falls auf der Rechnung gefunden,
    # sonst None -- rein informativ fuer die Oberflaeche
    qr: dict | None = None
    # Andere beschriftete Mail-Anhaenge derselben Mail, die an diesen Beleg
    # gehaengt sind (anlagen.py, Postfach-Baustein 8): [{dateiname, art}]
    anlagen: list[dict] = field(default_factory=list)
    # Pflichtangaben-Pruefung (pflicht.py, Aaron 10.10.2026): reine
    # Hinweis-Gruppe {regelwerk, punkte, fehlt}, nie ein Rueckstellgrund
    pflichtangaben: dict | None = None
    # Rechnungstext fuer die Nachlauf-Pruefungen in ``process`` -- wird dort
    # wieder geleert (nicht mit dem Ergebnis herumtragen)
    _pruef_text: str = field(default="", repr=False)

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "extraction": self.extraction,
            "einvoice": self.einvoice,
            "sha256": self.sha256,
            "supplier_id": self.supplier_id,
            "supplier_name": self.supplier_name,
            "supplier_external_id": self.supplier_external_id,
            "match_score": self.match_score,
            "template_id": self.template_id,
            "template_variant": self.template_variant,
            "template_secured": self.template_secured,
            "billing_address_id": self.billing_address_id,
            "billing_address_name": self.billing_address_name,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "hold_reasons": self.hold_reasons,
            "warnungen": self.warnings,
            "hinweise": self.hinweise,
            "verteilung": self.verteilung,
            "sammel": self.sammel,
            "dublette": self.dublette,
            "qr": self.qr,
            "anlagen": self.anlagen,
            "pflichtangaben": self.pflichtangaben,
            "duration_s": self.duration_s,
            "aborted": self.aborted,
            "stufenzeiten": self.stufenzeiten,
            "seiten": self.seiten,
            "overall_confidence": self.overall_confidence,
            "line_items": self.line_items,
            "tax_breakdown": self.tax_breakdown,
            "fields": {
                k: {"value": v.value, "raw": v.raw, "confidence": v.confidence,
                    "source": v.source, "origin": origin(v.source),
                    # Vierstufige Herkunft (gelesen/errechnet/kopf/korrigiert,
                    # SPEC Baustein 3) -- feiner ist "origin"/"source".
                    "herkunft": rechnen.herkunft(v.source),
                    "via": "ocr" if self.ocr and v.source not in _NOT_READ else None}
                for k, v in self.fields.items()
            },
            "ocr": self.ocr,
            "llm_rejected": self.llm_rejected,
            "shadow": ({k: v for k, v in self.shadow.items() if k != "values"}
                       if self.shadow else None),
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }


# Quellen, die nicht aus dem Rechnungstext gelesen sind (kein "via ocr")
_NOT_READ = ("library", "manual", "missing", "einvoice")


def extraction_text(doc) -> str:
    """Text fuer die KI: bei langen Rechnungen nur Kopf- und Fussseiten
    (Postfach-Baustein 3) -- Nummer/Datum/Lieferant stehen vorn, Summen/Bank
    hinten; die Positionsseiten dazwischen kosten nur Modellzeit."""
    idx = ocr.partial_pages(len(doc.pages))
    if idx is None:
        return doc.text
    return "\n".join(" ".join(w.text for w in doc.pages[i].words) for i in idx)


# Positionsmodus "zusammenfassung" ohne Vorlage (Teil B, Entscheidung Aaron
# 10.10.2026): die Uebersichtstabelle steht am Rechnungsende -- die KI soll
# nicht alle Einzelpositionsseiten lesen, nur um am Schluss die paar
# Summenzeilen zu finden (spart Laptop-Zeit bei 10+ Seiten).
_ZUSAMMENFASSUNG_SCHLUSSSEITEN = 2


def _schlussseiten_text(doc, n: int = _ZUSAMMENFASSUNG_SCHLUSSSEITEN) -> str:
    if len(doc.pages) <= n:
        return doc.text
    idx = range(len(doc.pages) - n, len(doc.pages))
    return "\n".join(" ".join(w.text for w in doc.pages[i].words) for i in idx)


def load_document(path: str | Path, store: Store):
    """-> (Document, OcrResult | None). Scans/Bilder laufen ueber die OCR-
    Arbeitskopie; ``Document.sha256`` ist immer der Hash des Originals
    (Korrekturen, Bestaetigung, Archiv). Ist OCR noetig, aber nicht moeglich,
    ist das Document ``None`` und ``OcrResult.error`` nennt den Grund."""
    from .config import process_timeout

    path = Path(path)
    if not ocr.needs_ocr(path):
        return pdfmod.load(path), None
    o = ocr.ensure_text(path, store.root / "ocr", process_timeout() or None)
    if o.error:
        return None, o
    doc = pdfmod.load(o.pdf)
    doc.sha256 = o.sha256
    return doc, o


def _ocr_adjust(res: "Result", o) -> None:
    """Nach OCR: unsichere Woerter/Seiten an den Menschen. Ein Feld, dessen
    Wert ein unsicher erkanntes Wort enthaelt, faellt unter CONF_MIN; ist die
    mittlere Konfidenz der ganzen Rechnung niedrig, ist die Pruefung
    ``ocr-konfidenz`` rot. Beides: nie ``auto``, nie "wuerde durchlassen"."""
    if o is None:
        return
    res.ocr = o.to_dict()
    low = set(o.low_words)
    unsure = []
    for name, fr in res.fields.items():
        if fr.source in _NOT_READ or not fr.raw:
            continue
        if any(norm(t) in low for t in str(fr.raw).split()):
            fr.confidence = min(fr.confidence, 0.5)
            unsure.append(name)
    chk = Check("ocr-konfidenz", not o.low,
                f"Ø {o.confidence:g} (Schwelle {ocr.min_confidence():g})"
                + (f", unsicher: {', '.join(unsure)}" if unsure else ""))
    res.checks = _replace_check(res.checks, chk)
    if o.low or unsure:
        if res.status == AUTO:
            res.status = REVIEW
        if res.shadow is not None:
            res.shadow["would_pass"] = False
            res.shadow["reasons"] = list(res.shadow.get("reasons") or []) + [
                "OCR unsicher" + (f": {', '.join(unsure)}" if unsure else "")]


def _pick_template(doc, templates: list[Template],
                   address_id: str | None = None) -> Template | None:
    """Erst die zur erkannten Rechnungsadresse passende Variante, sonst die mit
    den meisten auffindbaren Ankern."""
    if not templates:
        return None
    if address_id:
        exact = [t for t in templates if t.variant == address_id]
        if exact:
            return exact[0]
    if len(templates) == 1:
        return templates[0]
    scored = []
    for t in templates:
        res = apply_template(doc, t)
        found = sum(1 for r in res.values() if r.source != "missing")
        scored.append((found, t.secured, t))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return scored[0][2]


def _to_amount(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    return coerce(str(v or ""), "amount")[0]


def _suggest_categories(line_items: list[dict], tpl: Template, store: Store) -> None:
    """Kategorie je Position vorschlagen, gelernt aus ``Store.category_examples``.
    Vergleichstext ist die erste Text-Spalte (typischerweise "Bezeichnung") --
    nicht alle Text-Spalten zusammen: eine zusaetzliche Freitext-/Kommentar-
    Spalte wuerde den Fuzzy-Vergleich sonst verwaessern. ``_kategorie_text``
    traegt den Vergleichstext fuer die spaetere Korrektur mit -- Unterstrich-
    Feld, wird nicht in CSV/UI-Spalten aufgelistet."""
    if not tpl.line_items:
        return
    # ``line_items`` ist schon umbenannt (positionen.normalize_columns) --
    # die Vorlagen-Spaltennamen (ggf. noch eine Altvorlage mit Freitext-
    # Namen) muessen daher erst auf den Standardnamen abgebildet werden.
    text_cols = [positionen.canonical_name(c.name) for c in tpl.line_items.columns
                if c.value_type == "text"]
    if not text_cols:
        return
    first_text_col = text_cols[0]
    examples = store.category_examples()
    for item in line_items:
        text = str(item.get(first_text_col) or "").strip()
        item["_kategorie_text"] = text
        sug = suggest_category(text, examples) if text else None
        item["kategorie"], item["kategorie_konfidenz"] = sug if sug else (None, None)


def _inject_library_fields(fields: dict[str, FieldResult], supplier) -> None:
    """Feste Werte + Stammdaten (IBAN/USt-IdNr/Steuernummer/Adresse) eines
    Lieferanten einmischen -- unabhaengig davon, ob fuer ihn schon ein Layout-
    Template trainiert wurde. Ein per Bulk-Import bekannter Lieferant (siehe
    importers.py) soll diese Werte schon bei der allerersten Rechnung zeigen,
    nicht erst ab der zweiten (nach dem ersten manuellen Anlernen) -- siehe
    Konversation vom 2026-09-18. Mutiert ``fields`` in place, ueberschreibt nur
    fehlende/leere Eintraege."""
    for fname, fval in (supplier.fixed_fields or {}).items():
        if not str(fval).strip():
            continue
        cur = fields.get(fname)
        if fname not in MASTER_FIELDS and cur is not None and cur.source != "missing" and cur.raw:
            continue
        vtype = FIXED_FIELD_TYPES.get(fname, "text")
        value, ok = coerce(str(fval), vtype)
        fields[fname] = FieldResult(fname, value, str(fval), 0.99 if ok else 0.6, "library")

    # IBAN, USt-IdNr, Steuernummer und Adresse sind Lieferanten-Stammdaten
    # (Lieferanten-Tab / Abbyy-Import), keine Extraktionsfelder -- die bleiben
    # von Rechnung zu Rechnung gleich und kommen direkt aus dem einmal
    # gepflegten Stammdatensatz. Sie haben VORRANG vor dem, was auf der
    # Rechnung steht (Template, E-Rechnungs-XML, KI): eine abweichende IBAN
    # auf der Rechnung wird nicht uebernommen, sondern als rote Pruefung
    # gemeldet (_iban_master_check, Betrugsschutz).
    master_fields = {
        "iban": supplier.ibans[0] if supplier.ibans else "",
        "ustid": supplier.ustids[0] if supplier.ustids else "",
        "steuernummer": supplier.creditor_id,
        "adresse": ", ".join(supplier.address_lines) if supplier.address_lines else "",
    }
    for fname, fval in master_fields.items():
        if not fval:
            continue
        fields[fname] = FieldResult(fname, fval, fval, 0.99, "library")


# Stammdaten-Felder, bei denen die Bibliothek immer gewinnt
MASTER_FIELDS = ("iban", "ustid", "steuernummer", "adresse")


def _replace_check(checks: list[Check], new: Check) -> list[Check]:
    return [c for c in checks if c.name != new.name] + [new]


def _iban_master_check(supplier, invoice_ibans: set[str], fields: dict) -> Check:
    """Betrugsschutz: jede IBAN, die auf der Rechnung steht (PDF-Text bzw.
    E-Rechnungs-XML) oder manuell eingetragen wurde, muss im Stammdatensatz
    des Lieferanten stehen. Eine einzige fremde IBAN macht die Pruefung rot
    -- auch wenn daneben die hinterlegte steht."""
    if supplier is None:
        return Check("iban-stammdaten", None, "Lieferant nicht aus der Bibliothek erkannt")
    if not supplier.ibans:
        return Check("iban-stammdaten", None, "keine IBAN in den Stammdaten hinterlegt")
    seen = {compact(i).upper() for i in invoice_ibans if i}
    fr = fields.get("iban")
    if fr is not None and fr.source == "manual" and fr.raw:
        seen.add(compact(str(fr.value or fr.raw)).upper())
    foreign = sorted(seen - set(supplier.ibans))
    if foreign:
        return Check("iban-stammdaten", False, f"{IBAN_DEVIATES}: {', '.join(foreign)}")
    return Check("iban-stammdaten", True,
                 "ok" if seen else "keine IBAN auf der Rechnung -- Stammdaten-IBAN verwendet")


def _billing_check(text: str, store: Store, res: "Result") -> Check:
    """Rechnungsadresse eindeutig aus der Bibliothek? Setzt bei Treffer
    ``res.billing_address_*``, sonst einen Rueckstellgrund. Solange (noch)
    keine Rechnungsadressen gepflegt/importiert sind, ist die Pruefung
    "nicht pruefbar" statt jede Rechnung zurueckzustellen."""
    addresses = store.addresses()
    if not addresses:
        return Check("rechnungsadresse", None, "keine Rechnungsadressen in der Bibliothek")
    hit, why = billing.match_text(text, addresses)
    if hit is None:
        res.hold_reasons.append(why)
        return Check("rechnungsadresse", False, why)
    res.billing_address_id = hit[0].id
    res.billing_address_name = hit[0].name
    return Check("rechnungsadresse", True, hit[0].name)


def _apply_hold(res: "Result") -> None:
    """Rueckstellgruende -> Status DEFERRED. Eine zurueckgestellte Rechnung
    (oder eine mit abweichender IBAN) kann im Schattenbetrieb nie als
    "wuerde durchlassen" zaehlen.

    IBAN-Abweichung von den Stammdaten: Standard (``RECHNUNG_IBAN_ABWEICHUNG
    =mensch``) zurueckstellen. Bei ``warnen`` laeuft die Rechnung weiter,
    bekommt aber eine deutliche Warnung (``res.warnings``), die bis in den
    JobRouter-Datensatz mitgeht -- dort gibt ohnehin ein Mensch frei."""
    from .config import iban_mode

    for c in res.checks:
        if c.name == "iban-stammdaten" and c.ok is False and IBAN_DEVIATES not in res.hold_reasons:
            if iban_mode() == "warnen":
                warn = f"ACHTUNG {c.detail} -- vor Zahlung pruefen!"
                if warn not in res.warnings:
                    res.warnings.append(warn)
            else:
                res.hold_reasons.append(IBAN_DEVIATES)
    if not res.hold_reasons:
        return
    res.status = DEFERRED
    note = "zurueckgestellt: " + "; ".join(res.hold_reasons)
    if res.shadow is None:
        res.shadow = {"would_pass": False, "reasons": [note],
                      "new_supplier": res.supplier_id is None, "values": {}, "sources": {}}
    else:
        res.shadow["would_pass"] = False
        res.shadow["reasons"] = list(res.shadow.get("reasons") or []) + [note]


# ---- Unbekannte Lieferanten: automatischer Entwurf (Entscheidung Aaron,
# 09.10.2026) -------------------------------------------------------------
# Findet die Pipeline beim Verarbeiten einer Rechnung keinen Lieferanten,
# legt sie -- nur in der echten Verarbeitung, siehe ``create_draft_supplier``
# unten -- einen Lieferanten mit ``bestaetigt=False`` an, gefuellt aus genau
# dieser Rechnung. Ersetzt die fruehere Regel "Lieferanten werden nie
# automatisch angelegt" (SPEC §7h); unveraendert gilt: kein Raten (nur
# belegte Werte), nichts laeuft automatisch durch, was ein Mensch nicht
# bestaetigt hat -- siehe NEW_SUPPLIER_HOLD unten.

def _draft_placeholder_name(ustids: list[str], ibans: list[str]) -> str:
    ident = (ustids or ibans or [""])[0]
    return f"Unbekannt ({ident})" if ident else "Unbekannt"


def _draft_id_base(name: str, ustid: str, iban: str) -> str:
    """Stabiler, lesbarer Id-Rohling -- aus dem Namen, sonst aus der
    USt-IdNr/IBAN (``neu-<kennung>``). Eindeutigkeit stellt
    ``_unique_supplier_id`` sicher."""
    if name:
        slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
        if slug:
            return slug
    ident = compact(ustid or iban or "")
    return f"neu-{ident.lower()}" if ident else "neu"


def _unique_supplier_id(store: Store, base: str) -> str:
    existing = {s.id for s in store.suppliers()}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def _new_draft_supplier(store: Store, *, name: str, ustids: list[str], ibans: list[str],
                        domains: list[str], address_lines: list[str], currency: str,
                        address_id: str | None, sha: str) -> Supplier:
    """Nur belegte Werte (Beleg-Pflicht wie im Rest des Programms) -- fehlt
    der Name, ein lesbarer Platzhalter. ``addresses=[address_id]`` (leer,
    wenn keine Adresse erkannt wurde) -- ein Entwurf ohne Adresse gilt dann
    ueber ``identify.kandidaten`` nur fuer Rechnungen ohne erkannte Adresse,
    nie global."""
    display_name = name.strip() or _draft_placeholder_name(ustids, ibans)
    base = _draft_id_base(name.strip(), ustids[0] if ustids else "", ibans[0] if ibans else "")
    sid = _unique_supplier_id(store, base)
    return Supplier(
        id=sid, name=display_name, ustids=ustids, ibans=ibans, domains=domains,
        address_lines=address_lines, currency=currency or DEFAULT_CURRENCY,
        addresses=[address_id] if address_id else [],
        bestaetigt=False, entwurf_quelle=sha,
        entwurf_am=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )


def _find_draft_by_identifiers(store: Store, address_id: str | None,
                               ustids: list[str], ibans: list[str]) -> Supplier | None:
    """Vorhandenen (unbestaetigten) Entwurf unter derselben Rechnungsadresse
    mit einer der Kennungen finden -- keine Duplikate: die naechste Rechnung
    desselben unbekannten Lieferanten (gleiche USt-IdNr/IBAN unter derselben
    Adresse) soll den vorhandenen Entwurf treffen statt einen zweiten
    anzulegen. Nur ein Sicherheitsnetz fuer den (seltenen) Fall, dass der
    Entwurf aus irgendeinem Grund nicht schon ueber den normalen
    Index-Abgleich (``identify_with_reason``) gefunden wurde."""
    from .textnorm import norm_ustid

    has_addr = bool(store.addresses())
    cands = kandidaten(store.suppliers(), address_id, has_addr)
    u = {norm_ustid(x) for x in ustids if x}
    i = {compact(x).upper() for x in ibans if x}
    hits = [s for s in cands
            if u & {norm_ustid(x) for x in s.ustids}
            or i & {compact(x).upper() for x in s.ibans}]
    drafts = [s for s in hits if not s.bestaetigt]
    if len(drafts) == 1 and len(hits) == 1:
        return drafts[0]
    if hits or not address_id:
        return None
    # Entwurf, der entstand, bevor die Rechnungsadresse bekannt war
    # (addresses=[]): uebernimmt jetzt die erkannte Adresse, statt dass ein
    # zweiter Entwurf desselben Lieferanten entsteht.
    waisen = [s for s in store.suppliers()
              if not s.bestaetigt and not s.addresses
              and (u & {norm_ustid(x) for x in s.ustids}
                   or i & {compact(x).upper() for x in s.ibans})]
    if len(waisen) != 1:
        return None
    sup = waisen[0]
    sup.addresses = [address_id]
    store.save_supplier(sup)
    return sup


def _draft_blocked(store: Store, address_id: str | None,
                   ustids: list[str], ibans: list[str]) -> bool:
    """True, wenn USt-IdNr/IBAN der Rechnung schon bei einem Lieferanten
    unter dieser Adresse stehen, der Treffer aber nicht eindeutig ein
    einzelner Entwurf ist (z. B. bestaetigter Lieferant + Entwurf mit
    derselben IBAN). Dann weder einen Entwurf nehmen noch einen neuen
    anlegen -- Widerspruch -> Mensch (Betrugsschutz, Entscheidung Aaron
    09.10.2026: ein Entwurf darf nie einen bestaetigten verdraengen)."""
    from .textnorm import norm_ustid

    has_addr = bool(store.addresses())
    cands = kandidaten(store.suppliers(), address_id, has_addr)
    u = {norm_ustid(x) for x in ustids if x}
    i = {compact(x).upper() for x in ibans if x}
    hits = [s for s in cands
            if u & {norm_ustid(x) for x in s.ustids}
            or i & {compact(x).upper() for x in s.ibans}]
    return bool(hits) and not (len(hits) == 1 and not hits[0].bestaetigt)


def _auto_draft_pdf(res: "Result", doc, store: Store, use_llm: bool,
                    timer: StageTimer) -> tuple[Supplier, float] | None:
    """Entwurf eines unbekannten Lieferanten aus einer PDF-Rechnung anlegen.
    USt-IdNr/IBAN/Domain kommen wie beim Bibliotheksabgleich selbst per
    Regex (``extract_identifiers``/``find_ibans``, kein Raten); der Name nur,
    nachtraeglich aus der ohnehin laufenden KI-Stufe, wenn sie ihn im Text
    belegt findet (``_enrich_draft``; semantic.py ``_SUPPLIER_KEYS``,
    Beleg-Pflicht dort schon eingebaut). Anschrift/Bank/BIC/Steuernummer/
    Kontakt kommen ebenso nachtraeglich aus der KI-Stufe (``_enrich_draft``),
    nur belegte Teile -- ohne KI bleiben sie leer statt geraten."""
    ids = extract_identifiers(doc)
    ibans = sorted(ids["ibans"] | find_ibans(doc.text))
    ustids = sorted(ids["ustids"])
    if _draft_blocked(store, res.billing_address_id, ustids, ibans):
        return None
    existing = _find_draft_by_identifiers(store, res.billing_address_id, ustids, ibans)
    if existing is not None:
        return existing, 0.0
    # Name kommt nicht hier, sondern aus der ohnehin folgenden KI-Stufe
    # (_llm_full -> _enrich_draft) -- kein zweiter KI-Aufruf je Rechnung.
    sup = _new_draft_supplier(
        store, name="", ustids=ustids, ibans=ibans, domains=sorted(ids["domains"]),
        address_lines=[], currency=_detect_currency(doc.text),
        address_id=res.billing_address_id, sha=res.sha256)
    store.save_supplier(sup)
    return sup, 0.0


def _is_placeholder_name(name: str) -> bool:
    return not name.strip() or name.startswith("Unbekannt")


_DRAFT_DETAIL_FIELDS = ("address_lines", "bank_name", "bic", "creditor_id", "contact")


def _enrich_draft(res: "Result", supplier: Supplier, store: Store) -> None:
    """Leere Felder eines (unbestaetigten) Entwurfs mit den von der KI auf
    der Rechnung belegten Lieferantenangaben fuellen: Name (nur statt des
    Platzhalters), Anschrift, Bank, BIC, Steuernummer, Kontakt. Nur in der
    echten Verarbeitung (create_draft_supplier), nie in der Vorschau; ein
    bestaetigter Lieferant und von Hand gepflegte Werte bleiben unberuehrt."""
    if supplier.bestaetigt or not res.ki_lieferant:
        return
    changed = False
    name = str(res.ki_lieferant.get("name") or "").strip()
    if name and _is_placeholder_name(supplier.name):
        supplier.name = name
        res.supplier_name = name
        changed = True
    for attr in _DRAFT_DETAIL_FIELDS:
        val = res.ki_lieferant.get(attr)
        if val and not getattr(supplier, attr):
            setattr(supplier, attr, list(val) if attr == "address_lines" else str(val))
            changed = True
    if changed:
        store.save_supplier(supplier)


def _auto_draft_einvoice(res: "Result", inv, store: Store) -> tuple[Supplier, float] | None:
    """Entwurf eines unbekannten Lieferanten aus einer E-Rechnung -- die
    Verkaeuferdaten des XML sind bereits strukturiert/belegt, keine KI
    noetig."""
    ustid = (inv.verkaeufer_ustid or "").strip().upper()
    ustids = [ustid] if ustid else []
    ibans = sorted({compact(i).upper() for i in (inv.verkaeufer_iban or []) if i})
    if _draft_blocked(store, res.billing_address_id, ustids, ibans):
        return None
    existing = _find_draft_by_identifiers(store, res.billing_address_id, ustids, ibans)
    if existing is not None:
        return existing, 1.0
    sup = _new_draft_supplier(
        store, name=(inv.verkaeufer_name or "").strip(), ustids=ustids, ibans=ibans,
        domains=[], address_lines=[a for a in (inv.verkaeufer_adresse or []) if a],
        currency=inv.waehrung or DEFAULT_CURRENCY,
        address_id=res.billing_address_id, sha=res.sha256)
    store.save_supplier(sup)
    return sup, 1.0


# Felder, die bei einer E-Rechnung gesetzt sein muessen, damit sie ohne
# Rueckfrage als "sieht sicher aus" (AUTO) gilt.
_EINVOICE_REQUIRED = ("rechnungsnummer", "rechnungsdatum", "netto", "ust_betrag", "brutto")


def _amount_variants(v: float) -> list[str]:
    """1234.5 -> ["1.234,50", "1234,50", "1,234.50", "1234.50"] -- so wie ein
    Betrag in einem deutschen oder englischen PDF stehen kann."""
    en = f"{abs(v):,.2f}"
    de = en.replace(",", "X").replace(".", ",").replace("X", ".")
    return list({de, de.replace(".", ""), en, en.replace(",", "")})


def _check_pdf_matches_xml(path: Path, inv) -> Check:
    """Hybrid-Rechnung (ZUGFeRD/Factur-X): stimmen sichtbares PDF und
    eingebettetes XML ueberein? Rechtlich massgeblich ist das XML -- weicht das
    PDF ab (anderer Betrag, falsches/veraltetes XML angehaengt), soll ein
    Mensch draufschauen statt dass das XML still gewinnt."""
    if not inv.source.startswith("pdf:"):
        return Check("pdf-xml-abgleich", None, "reine XML-Datei")
    try:
        if not pdfmod.has_text_layer(path):
            return Check("pdf-xml-abgleich", None, "PDF ohne Textlayer")
        text = pdfmod.load(path).text
    except Exception:
        return Check("pdf-xml-abgleich", None, "PDF nicht lesbar")
    flat = text.replace(" ", "")
    missing = []
    if inv.rechnungsnummer and inv.rechnungsnummer.replace(" ", "") not in flat:
        missing.append(f"Rechnungsnummer {inv.rechnungsnummer}")
    if inv.brutto is not None and not any(
            re.search(r"(?<![\d.,])" + re.escape(a) + r"(?!\d)", flat)
            for a in _amount_variants(inv.brutto)):
        missing.append(f"Brutto {inv.brutto:.2f}")
    if missing:
        return Check("pdf-xml-abgleich", False, "im PDF nicht gefunden: " + ", ".join(missing))
    return Check("pdf-xml-abgleich", True, "PDF und XML stimmen ueberein")


def _einvoice_check(c: Check, inv, line_items: list[dict]) -> Check:
    """Allgemeine Pruefungen dort ersetzen, wo die E-Rechnung es genauer weiss.

    - positionen-summe: Positionen gegen BT-106 (Summe der Positionen), nicht
      gegen den Nettobetrag -- der enthaelt Rabatte/Zuschlaege auf
      Belegebene, die keine Position sind (sonst Fehlalarm bei jeder
      Rechnung mit Skonto-/Mengenrabatt).
    - betraege-plausibel: bei Korrektur/Gutschrift sind negative Betraege
      richtig; geprueft wird dann nur, dass die Summen aufgehen.
    """
    if c.name == "positionen-summe" and inv.summe_positionen is not None and line_items:
        total = round(sum(p.get("gesamt_netto") or 0 for p in line_items), 2)
        ok = abs(total - inv.summe_positionen) <= 0.05
        return Check(c.name, ok, f"Σ {total:.2f} vs Positionssumme {inv.summe_positionen:.2f}")
    if c.name == "betraege-plausibel" and inv.is_correction and c.ok is False:
        if None not in (inv.netto, inv.ust_betrag, inv.brutto) and \
                abs(inv.netto + inv.ust_betrag - inv.brutto) <= 0.02:
            return Check(c.name, True, f"Korrekturbeleg (Typ {inv.type_code}), negative Betraege zulaessig")
    return c


def _process_einvoice(path: Path, inv, store: Store, timer: StageTimer | None = None,
                      create_draft_supplier: bool = False) -> Result:
    """Stufe 0: Werte direkt aus dem XML, kein Template, keine KI.

    Konfidenz 1.0 je vorhandenem Feld -- der Wert ist nicht geraten, sondern
    vom Absender strukturiert geliefert. Die Plausibilitaetspruefungen laufen
    trotzdem (ein Absender kann auch falsch rechnen), und bestaetigt werden
    muss wie jede andere Rechnung auch (§7b)."""
    timer = timer or StageTimer()
    res = Result(file=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                 extraction=EXTRACT_EINVOICE, einvoice=inv.to_dict(),
                 _pruef_text=_einvoice_pdf_text(path, inv))
    res.anlagen = anlagen.info(store, res.sha256)

    with timer.stage("lieferant"):
        # Rechnungsadresse zuerst bestimmen (wie beim PDF-Pfad unten) --
        # Lieferant je Rechnungsadresse, Entscheidung Aaron 09.10.2026: eine
        # E-Rechnung wird nur unter den zu dieser Adresse passenden
        # Lieferanten gesucht, nie uebergreifend.
        pdf_text = _einvoice_pdf_text(path, inv)
        addr_check = _billing_check("\n".join([inv.kaeufer_name, pdf_text]), store, res)
        has_addr = bool(store.addresses())
        candidates = kandidaten(store.suppliers(), res.billing_address_id, has_addr)
        match = identify_einvoice(inv, candidates)
        if not match and create_draft_supplier:
            match = _auto_draft_einvoice(res, inv, store)
        supplier = None
        if match:
            supplier, res.match_score = match
            res.supplier_id = supplier.id
            res.supplier_name = supplier.name
            res.supplier_external_id = supplier.external_id or None
            if not supplier.bestaetigt:
                res.hold_reasons.append(NEW_SUPPLIER_HOLD)
        else:
            # Lieferant nur aus der Bibliothek -- der Verkaeufername im XML ist
            # ein Hinweis fuer den Menschen, keine Identitaet.
            named = f" (Rechnung nennt: {inv.verkaeufer_name})" if inv.verkaeufer_name else ""
            grund = _address_scope_reason(has_addr, res.billing_address_id,
                                          res.billing_address_name or "",
                                          "kein Lieferant aus der Bibliothek passt")
            res.hold_reasons.append(f"Lieferant: {grund}{named}")
        invoice_ibans = set(inv.verkaeufer_iban) | find_ibans(pdf_text)

    def put(name: str, value, raw: str | None = None) -> None:
        if value is None or value == "" or value == []:
            return
        res.fields[name] = FieldResult(name, value, raw if raw is not None else str(value),
                                       1.0, EXTRACT_EINVOICE)

    with timer.stage("erechnung"):
        put("rechnungsnummer", inv.rechnungsnummer)
        put("rechnungsdatum", inv.rechnungsdatum)
        put("netto", inv.netto)
        put("ust_betrag", inv.ust_betrag)
        put("brutto", inv.brutto)
        rates = {t["satz"] for t in inv.steuern if t.get("satz") is not None}
        if len(rates) == 1:
            put("ust_satz", rates.pop())
        put("bestellnummer", inv.bestellnummer)
        put("faelligkeit", inv.faelligkeit)
        put("zahlbetrag", inv.zahlbetrag)
        put("waehrung", inv.waehrung)
        waehrung.apply(res.fields, pdf_text, res.hinweise)
        put("leitweg_id", inv.leitweg_id)
        put("zahlungsbedingungen", inv.zahlungsbedingungen)
        # Belegart aus dem Typcode (381 Gutschrift, 384 Korrektur, ...)
        belegart.apply(res.fields, text=pdf_text, type_code=inv.type_code,
                       hinweise=res.hinweise)
        # Stammdaten aus dem XML selbst; der Abgleich mit dem gepflegten
        # Lieferanten-Datensatz passiert in den Pruefungen (iban-/ustid-stammdaten).
        put("iban", inv.verkaeufer_iban[0] if inv.verkaeufer_iban else "")
        put("ustid", inv.verkaeufer_ustid)
        put("steuernummer", inv.verkaeufer_steuernummer)
        put("adresse", ", ".join(inv.verkaeufer_adresse))
        if inv.steuern:
            put("ust_aufstellung", [{"satz": t["satz"], "netto": t["netto"], "ust": t["ust"]}
                                    for t in inv.steuern], "")
        if supplier is not None:
            _inject_library_fields(res.fields, supplier)

    machine = dict(res.fields)
    corrections = store.corrections(res.sha256)
    _apply_corrections(res.fields, corrections)
    # Skonto steht im XML nur als Text (XRechnung: #SKONTO#TAGE=..#PROZENT=..#)
    zahlung.apply(res.fields, inv.zahlungsbedingungen or "", res.hinweise)

    with timer.stage("pruefungen"):
        res.line_items = positionen.normalize_columns([dict(p) for p in inv.positionen])
        _positionsmodus_vorbereiten(res, supplier)
        rk = rechnen.rechne(res.fields, res.line_items, pdf_text)
        res.fields, res.line_items, res.tax_breakdown = rk.fields, rk.line_items, rk.tax_breakdown
        res.hinweise.extend(g for g in rk.gruende if g not in res.hinweise)
        _positionsmodus_abschliessen(res, supplier)
        pdf_check = _check_pdf_matches_xml(path, inv)

        def checks_for(fields):
            cs = run_all(fields, supplier, store.seen_invoice, res.line_items, pdf_text)
            cs = [_einvoice_check(c, inv, res.line_items) for c in cs] + [pdf_check]
            cs = _replace_check(cs, dubletten.check(
                fields, supplier.id if supplier is not None else None, store, res.sha256,
                text=pdf_text)[0])
            for bc in belegart.checks(fields, title=pdf_text[:dubletten.TITLE_CHARS]):
                cs = _replace_check(cs, bc)
            for wc in waehrung.checks(fields, pdf_text):
                cs = _replace_check(cs, wc)
            for pc in plausi.checks(fields, supplier.id if supplier is not None else None,
                                    store, res.sha256):
                cs = _replace_check(cs, pc)
            return _replace_check(cs, _iban_master_check(supplier, invoice_ibans, fields)) + [addr_check]

        res.checks = checks_for(res.fields)
        _set_dublette(res, store, text=pdf_text)
        res.shadow = shadow.decide(
            machine, checks_for(machine) if corrections else res.checks,
            new_supplier=supplier is None or not supplier.bestaetigt)

    present = [n for n in _EINVOICE_REQUIRED if n in res.fields]
    res.overall_confidence = 1.0 if len(present) == len(_EINVOICE_REQUIRED) else 0.5
    checks_ok = all(c.ok is not False for c in res.checks)
    res.status = AUTO if (supplier is not None and checks_ok
                          and res.overall_confidence >= CONF_MIN) else REVIEW
    _apply_hold(res)
    res.stufenzeiten = timer.times
    return res


def _einvoice_pdf_text(path: Path, inv) -> str:
    """Sichtbarer Text einer Hybrid-Rechnung (ZUGFeRD/Factur-X), sonst ''."""
    if not inv.source.startswith("pdf:"):
        return ""
    try:
        return pdfmod.load(path).text if pdfmod.has_text_layer(path) else ""
    except Exception:
        return ""


# Korrigierte Positionstabelle aus dem Training-UI (Postfach-Baustein 11b):
# eigener Schluessel in der Korrektur-Datei, kein Feld der Rechnung.
POSITIONEN_KEY = "_positionen"


def _apply_corrections(fields: dict[str, FieldResult], corrections: dict,
                       type_map: dict[str, str] | None = None) -> None:
    for fname, fval in corrections.items():
        if fname.startswith("_"):
            continue
        if isinstance(fval, list):
            # USt-Aufstellung, im UI zeilenweise korrigiert -- schon strukturiert
            fields[fname] = FieldResult(fname, fval, json.dumps(fval, ensure_ascii=False),
                                        1.0, "manual")
            continue
        vtype = (type_map or {}).get(fname) or STANDARD_FIELDS.get(fname, ("text",))[0]
        value, ok = coerce(str(fval), vtype)
        fields[fname] = FieldResult(fname, value if ok else fval, str(fval), 1.0, "manual")


def _apply_line_item_correction(res: "Result", corrections: dict) -> None:
    """Vom Menschen korrigierte Positionen (Zelle/Zeile/Spalte im UI) gewinnen
    wie jede Korrektur -- aus genau diesen bestaetigten Positionen lernt
    learn.learn_line_items die Tabellen-Spec."""
    items = corrections.get(POSITIONEN_KEY)
    if isinstance(items, list):
        res.line_items = positionen.normalize_columns(
            [dict(it) for it in items if isinstance(it, dict)])


def _llm_full(res: Result, doc, positionsmodus: str = POSITIONSMODUS_EINZELN,
              variante: str = semantic.VARIANTE_A, skip_items: bool = False) -> None:
    """Stufe 2: komplette KI-Extraktion, wenn es (noch) kein Template gibt.
    Fuellt nur, was nicht schon da ist -- Stammdaten aus dem gepflegten
    Lieferanten-Datensatz (``library``) gewinnen gegen das Modell.

    Positionsmodus (Teil B, Entscheidung Aaron 10.10.2026): 'eine_position'
    -- die KI liest ueberhaupt keine Einzelpositionen (die Positionen werden
    stattdessen aus der USt-Aufstellung gebildet, ``positionen.aus_summen``
    in ``_positionsmodus_post``). 'zusammenfassung' -- die Uebersichts-
    tabelle steht am Rechnungsende, die KI bekommt nur die Schlussseiten
    statt der ganzen (ggf. langen) Rechnung. ``skip_items``: Baustein 2 hat
    die Positionen schon ohne KI gefunden -- gar nicht erst danach fragen
    (kleinerer Prompt, schneller).

    ``variante`` (Testsatz, Aaron 10.10.2026): A = eine Anfrage ueber die
    ganze Rechnung (unten); B = Positionen als eigener kleiner Block
    (SPEC Baustein 3, Schwerpunkt Aaron 12.10.2026), Kopf/Parteien wie A;
    C = noch nicht gebaut (``semantic.pruefe_variante``)."""
    v = semantic.pruefe_variante(variante)
    text = (_schlussseiten_text(doc) if positionsmodus == POSITIONSMODUS_ZUSAMMENFASSUNG
            else extraction_text(doc))
    want_lines = positionsmodus != POSITIONSMODUS_EINE_POSITION and not skip_items
    if v == semantic.VARIANTE_B and want_lines:
        full = semantic.extract_full(text, want_line_items=False)
        tbl_pages = (list(range(max(0, len(doc.pages) - _ZUSAMMENFASSUNG_SCHLUSSSEITEN),
                                len(doc.pages)))
                    if positionsmodus == POSITIONSMODUS_ZUSAMMENFASSUNG else None)
        excerpt = tabelle.tabelle_text(doc, pages=tbl_pages)
        if excerpt:
            items, rejected = semantic.positions_block(excerpt, doc.text)
            netto_fr = full.fields.get("netto")
            netto_wert = _to_amount(netto_fr.value) if netto_fr else None
            items, grund = semantic.positionen_plausibel(items, netto_wert)
            if grund:
                rejected["positionen"] = grund
            full.line_items = items
            full.rejected.update(rejected)
    else:
        full = semantic.extract_full(text, want_line_items=want_lines)
    res.llm_rejected.update(full.rejected)
    res.ki_lieferant = {**(full.lieferant_details or {}), **(full.supplier or {})}
    res.ki_empfaenger = dict(full.empfaenger or {})
    for name, fr in full.fields.items():
        cur = res.fields.get(name)
        if cur is None or cur.source == "missing":
            res.fields[name] = fr
    # Bewusst KEIN res.supplier_name aus der KI: die Lieferanten-Identitaet
    # kommt ausschliesslich aus der Bibliothek.
    if full.line_items and not res.line_items:
        res.line_items = positionen.normalize_columns(full.line_items)
    if full.fields:
        res.extraction = EXTRACT_LLM


# Grobe Schaetzung, was eine zusaetzliche gezielte KI-Anfrage kostet (SPEC
# Effizienz-Baustein 1: ein lokales Kleinmodell, keine lange Antwort) --
# der Gegencheck (Postfach-Baustein 10) laeuft nur an, wenn danach noch
# Luft bis zur 120-s-Zeitgrenze bleibt.
GEGENCHECK_BUDGET_MARGIN = 15.0


def _gegencheck_active(tpl: Template, store: Store) -> bool:
    from . import modus
    from .config import gegencheck_autonom, gegencheck_min

    if tpl.confirmations >= gegencheck_min():
        return False
    return gegencheck_autonom() if modus.is_autonomous(store) else True


def _gegencheck_felder(tpl: Template, fields: dict) -> list[str]:
    """Pflichtfelder + von der Vorlage gelernte Felder, die aktuell einen
    Wert haben (keine manuelle Korrektur -- die ist schon vom Menschen
    bestaetigt, dagegen gibt es nichts gegenzupruefen)."""
    tpl_names = {f.name for f in tpl.fields}
    return sorted(
        n for n, (_vtype, required) in STANDARD_FIELDS.items()
        if (required or n in tpl_names)
        and fields.get(n) is not None and fields[n].source not in ("missing", "manual")
    )


def _gegencheck_mismatch(name: str, vorlage, ki) -> bool:
    vtype = STANDARD_FIELDS.get(name, ("text", False))[0]
    if vtype == "amount":
        a, b = _to_amount(vorlage), _to_amount(ki)
        return a is None or b is None or abs(a - b) > 0.02
    if vtype == "date":
        return str(vorlage) != str(ki)
    return compact(str(vorlage)) != compact(str(ki))


def _item_total(it: dict) -> float | None:
    return _to_amount(it.get("gesamt_netto"))


def _gegencheck(res: "Result", doc, tpl: Template, store: Store, timer: StageTimer,
                use_llm: bool) -> None:
    """Postfach-Baustein 10: solange eine Vorlage noch unter
    ``config.gegencheck_min()`` Bestaetigungen hat, liest zusaetzlich die
    KI gezielt dieselbe Rechnung (Beleg-Pflicht wie bei jeder KI-Antwort,
    ``semantic._check_value``) -- komplett unabhaengig von der Vorlage,
    deshalb OHNE Hinweise/bereits gelesene Werte im Prompt. Weichen Vorlage
    und KI bei einem Pflichtfeld, einem Betrag oder der Positionssumme ab,
    geht die Rechnung mit beiden Werten nebeneinander (im Rueckstellgrund)
    an den Menschen -- seine Korrektur fliesst wie jede andere in die
    Vorlage (bestehender Stale-/Layoutwechsel-Mechanismus, SPEC §7p). Nur
    wenn das Zeitbudget noch reicht, sonst wird der Gegencheck
    uebersprungen und als Hinweis vermerkt (geht mit ins
    Ereignisprotokoll, eventlog.record). Aktiv in der Testphase; im
    autonomen Modus per Schalter (``config.gegencheck_autonom``, Standard
    aus)."""
    from .config import process_timeout

    if not use_llm or not _gegencheck_active(tpl, store):
        return
    budget = process_timeout()
    if budget and sum(timer.times.values()) + GEGENCHECK_BUDGET_MARGIN > budget:
        res.hinweise.append("Gegencheck uebersprungen (Zeitbudget fuer diese Rechnung "
                            "reicht nicht mehr)")
        return
    namen = _gegencheck_felder(tpl, res.fields)
    hat_positionen = bool(tpl.line_items and res.line_items)
    if not namen and not hat_positionen:
        return
    with timer.stage("ki"):
        ki_fields = semantic.extract(extraction_text(doc), namen, {}, res.llm_rejected)
    abweichungen = []
    for name in namen:
        fr_ki = ki_fields.get(name)
        fr_vorlage = res.fields.get(name)
        if fr_ki is None or fr_vorlage is None:
            continue
        if _gegencheck_mismatch(name, fr_vorlage.value, fr_ki.value):
            abweichungen.append(f"{name} (Vorlage: {fr_vorlage.value!r}, KI: {fr_ki.value!r})")
    if hat_positionen:
        vorlage_summe = round(sum(_item_total(it) or 0.0 for it in res.line_items), 2)
        fr_ki_netto = ki_fields.get("netto")
        ki_netto = _to_amount(fr_ki_netto.value) if fr_ki_netto is not None else None
        if ki_netto is not None and abs(vorlage_summe - ki_netto) > 0.02:
            abweichungen.append(f"Positionssumme (Vorlage: {vorlage_summe!r}, KI netto: "
                                f"{ki_netto!r})")
    if abweichungen:
        res.hold_reasons.append("Gegencheck Vorlage/KI weicht ab: " + "; ".join(abweichungen))


def _finish_untemplated(res: Result, doc, supplier, store: Store, use_llm: bool,
                        extra_checks: list[Check] | None = None,
                        timer: StageTimer | None = None,
                        qr: "swissqr.SwissQR | None" = None,
                        variante: str = semantic.VARIANTE_A) -> None:
    """Rechnung ohne Template (Lieferant unbekannt oder noch nicht angelernt):
    optional KI-Extraktion (Stufe 2), Korrekturen einmischen und, sobald Werte
    da sind, pruefen. Damit kann ein Mensch die Werte (KI-Vorschlag oder
    eingetippt) pruefen und bestaetigen -- aus genau diesen bestaetigten
    Werten lernt learn.py dann das Template (Stufe 1), statt dass jeder Anker
    von Hand angeklickt werden muss. Der Status bleibt ``no_template``/
    ``zurueckgestellt`` (die KI-Konfidenz ist ohnehin unter CONF_MIN
    gedeckelt)."""
    timer = timer or StageTimer()
    extra = list(extra_checks or [])
    modus = _positionsmodus(supplier)
    # SPEC Effizienz-Baustein 2 (Schwerpunkt Aaron 12.10.2026): die
    # Positionstabelle zuerst OHNE KI aus dem Wort-Layout versuchen -- noch
    # ohne Netto/Brutto (die kommen erst aus ``rechnen.rechne`` unten), also
    # greift hier nur die Zeilen-Mathematik (menge * einzelpreis ≈ gesamt),
    # nicht die Summenpruefung. Gelingt das, fragt die KI unten erst gar
    # nicht mehr nach Positionen (``skip_items``).
    tbl_pages = (list(range(max(0, len(doc.pages) - _ZUSAMMENFASSUNG_SCHLUSSSEITEN),
                            len(doc.pages)))
                if modus == POSITIONSMODUS_ZUSAMMENFASSUNG else None)
    tbl_items = (tabelle.extract(doc, text=doc.text, pages=tbl_pages)
                if modus != POSITIONSMODUS_EINE_POSITION else None)
    if tbl_items:
        res.line_items = positionen.normalize_columns(tbl_items)
    if use_llm:
        with timer.stage("ki"):
            _llm_full(res, doc, modus, variante, skip_items=bool(tbl_items))
    if tbl_items:
        # Deterministisch gefundene Tabelle gewinnt gegen einen KI-Vorschlag
        # (der bei ``skip_items`` ohnehin nicht mehr erfragt wurde).
        res.line_items = positionen.normalize_columns(tbl_items)
    machine = dict(res.fields)
    corrections = store.corrections(doc.sha256)
    _apply_corrections(res.fields, corrections)
    machine_items = res.line_items
    _apply_line_item_correction(res, corrections)
    manuell_korrigiert = POSITIONEN_KEY in corrections
    _positionsmodus_vorbereiten(res, supplier, manuell_korrigiert)
    # Faelligkeit/Skonto per Textregel -- nach den Korrekturen, damit eine
    # relative Frist ein vom Menschen eingetragenes Rechnungsdatum nutzt
    zahlung.apply(res.fields, doc.text, res.hinweise)
    belegart.apply(res.fields, doc, hinweise=res.hinweise)
    belegart.reverse_charge(res.fields, doc.text)
    waehrung.apply(res.fields, doc.text, res.hinweise)
    swissqr.apply(res.fields, qr, res.hinweise)
    swissqr.parteien(res.ki_lieferant, res.ki_empfaenger, qr)
    with timer.stage("pruefungen"):
        res.shadow = shadow.decide(machine, _all_checks(machine, supplier, store, machine_items,
                                                        doc, extra, qr), new_supplier=True)
        # nur Stammdaten bzw. Zahlungsbedingungen per Textregel: noch nichts
        # Pruefbares extrahiert
        if not any(f.source not in ("library", zahlung.SOURCE) for f in res.fields.values()):
            # Belegart/Waehrung/QR-Zahlteil sind auch ohne sonstige Werte
            # pruefbar -- bleiben also auch hier ein Grund fuer den Menschen.
            res.checks = (extra
                         + [c for c in belegart.checks(res.fields, doc) if c.name == "belegart"]
                         + waehrung.checks(res.fields, doc.text)
                         + swissqr.checks(res.fields, qr, supplier))
            return
        rk = rechnen.rechne(res.fields, res.line_items, doc.text)
        res.fields, res.line_items, res.tax_breakdown = rk.fields, rk.line_items, rk.tax_breakdown
        res.hinweise.extend(g for g in rk.gruende if g not in res.hinweise)
        # Baustein 2, zweiter Versuch: jetzt sind Netto/Brutto bekannt (ohne
        # KI, aus ``rechnen.rechne``/``summen.py``) -- eine Tabelle, die beim
        # ersten Versuch nur an der fehlenden Summenpruefung scheiterte (z.B.
        # keine Mengen-Spalte), noch einmal pruefen, bevor die Rechnung ohne
        # Positionen bleibt.
        if (not manuell_korrigiert and not res.line_items
                and modus != POSITIONSMODUS_EINE_POSITION):
            netto_fr, brutto_fr = res.fields.get("netto"), res.fields.get("brutto")
            retry = tabelle.extract(doc, netto=_to_amount(netto_fr.value) if netto_fr else None,
                                    brutto=_to_amount(brutto_fr.value) if brutto_fr else None,
                                    text=doc.text, pages=tbl_pages)
            if retry:
                rk2 = rechnen.rechne(res.fields, positionen.normalize_columns(retry), doc.text)
                res.fields, res.line_items, res.tax_breakdown = (
                    rk2.fields, rk2.line_items, rk2.tax_breakdown)
                res.hinweise.extend(g for g in rk2.gruende if g not in res.hinweise)
        _positionsmodus_abschliessen(res, supplier, manuell_korrigiert)
        res.checks = _all_checks(res.fields, supplier, store, res.line_items, doc, extra, qr)
        _set_dublette(res, store, doc)


def _all_checks(fields, supplier, store: Store, line_items, doc,
                extra: list[Check] | None = None,
                qr: "swissqr.SwissQR | None" = None) -> list[Check]:
    """Pruefungen fuer PDF-Rechnungen (Template- und Untemplated-Pfad)."""
    checks = run_all(fields, supplier, store.seen_invoice, line_items, doc.text)
    if supplier is not None:
        # cross_check liest die USt-IdNr direkt aus dem PDF-Text (nicht nur
        # aus dem Feld, das ja aus den Stammdaten kommt). Pro Namen genau
        # einen Check behalten: die entschiedene Aussage schlaegt das "nicht
        # pruefbar" von run_all. Die IBAN prueft _iban_master_check.
        for cc in cross_check(doc, supplier):
            if cc["kind"] == "iban-stammdaten":
                continue
            checks = [c for c in checks if not (c.name == cc["kind"] and c.ok is None)]
            if not any(c.name == cc["kind"] for c in checks):
                checks.append(Check(cc["kind"], cc["ok"], cc["detail"]))
    invoice_ibans = extract_identifiers(doc)["ibans"] | find_ibans(doc.text)
    checks = _replace_check(checks, _iban_master_check(supplier, invoice_ibans, fields))
    checks = _replace_check(checks, dubletten.check(
        fields, supplier.id if supplier is not None else None, store, doc.sha256, doc)[0])
    for bc in belegart.checks(fields, doc):
        checks = _replace_check(checks, bc)
    for wc in waehrung.checks(fields, doc.text):
        checks = _replace_check(checks, wc)
    for qc in swissqr.checks(fields, qr, supplier):
        checks = _replace_check(checks, qc)
    for pc in plausi.checks(fields, supplier.id if supplier is not None else None, store,
                            doc.sha256):
        checks = _replace_check(checks, pc)
    checks = [_credit_check(c, fields) for c in checks]
    return checks + list(extra or [])


def _credit_check(c: Check, fields: dict) -> Check:
    """Gutschrift/Storno: negative Betraege sind richtig, solange die Summen
    aufgehen (wie bei der E-Rechnung, _einvoice_check)."""
    if c.name != "betraege-plausibel" or c.ok is not False or not belegart.is_credit(fields):
        return c
    vals = [belegart._amount(fields, k) for k in ("netto", "ust_betrag", "brutto")]
    if None not in vals and all(v <= 0 for v in vals) and abs(vals[0] + vals[1] - vals[2]) <= 0.02:
        return Check(c.name, True, f"{fields['belegart'].value}, negative Betraege zulaessig")
    return c


def _set_dublette(res: "Result", store: Store, doc=None, text: str = "") -> None:
    """Verweis auf die Ur-Rechnung, falls die Dublettenpruefung rot ist."""
    if any(c.name == dubletten.CHECK_NAME and c.ok is False for c in res.checks):
        res.dublette = dubletten.check(res.fields, res.supplier_id, store, res.sha256,
                                       doc, text)[1]


# ---- Positionsmodus je Lieferant (Teil B, Entscheidung Aaron 10.10.2026) --
# Wie in Abbyy entscheidet die bearbeitende Person beim Pruefen einer
# Rechnung, wie die Positionstabelle dieses Lieferanten zu lesen ist
# (Supplier.positionsmodus) -- Normalfall "einzeln" (unveraendertes
# Verhalten). "zusammenfassung": die ohnehin gelesene/gelernte
# Positionstabelle IST die Uebersichtstabelle am Rechnungsende -- dafuer
# muss hier nichts Besonderes passieren, nur die KI (ohne Vorlage) bekommt
# gezielt nur die Schlussseiten (siehe ``_llm_full``). "eine_position":
# die Positionen werden IMMER aus der USt-Aufstellung gebildet -- nie aus
# gelesenen Einzelpositionen (weder Vorlage noch KI noch E-Rechnung).

def _positionsmodus(supplier: Supplier | None) -> str:
    return supplier.positionsmodus if supplier is not None else POSITIONSMODUS_EINZELN


def _positionsmodus_vorbereiten(res: "Result", supplier: Supplier | None,
                                manuell_korrigiert: bool = False) -> None:
    """Vor ``rechnen.rechne``: im Modus 'eine_position' nie gelesene
    Einzelpositionen verwenden -- die USt-Aufstellung wird dann aus den
    Kopf-Feldern hergeleitet, nicht aus (in diesem Modus gar nicht erst
    gelesenen bzw. u.U. veralteten) Positionszeilen. Eine manuelle
    Korrektur der Positionstabelle fuer GENAU diese Rechnung gewinnt
    trotzdem immer (wie jede andere Korrektur im Programm)."""
    if not manuell_korrigiert and _positionsmodus(supplier) == POSITIONSMODUS_EINE_POSITION:
        res.line_items = []


def _positionsmodus_abschliessen(res: "Result", supplier: Supplier | None,
                                 manuell_korrigiert: bool = False) -> None:
    """Nach ``rechnen.rechne``: im Modus 'eine_position' die Positionen aus
    der (jetzt vollstaendigen) USt-Aufstellung bilden -- eine Zeile je
    Steuersatz, nie zusammengefasst (``positionen.aus_summen``,
    Entscheidung Aaron 10.10.2026, Punkt 1)."""
    if manuell_korrigiert or _positionsmodus(supplier) != POSITIONSMODUS_EINE_POSITION:
        return
    waehrung_fr = res.fields.get("waehrung")
    currency = waehrung_fr.value if waehrung_fr and waehrung_fr.value else None
    res.line_items = positionen.aus_summen(res.tax_breakdown, supplier.positionsbezeichnung,
                                           currency)


# Dezenter Vorschlag (nie automatisch umstellen, Normalfall bleibt
# "einzeln"): ab wann gilt ein Beleg als "lang genug", um auf die anderen
# Modi hinzuweisen.
_LANGER_BELEG_SEITEN = 5
_LANGER_BELEG_POSITIONEN = 20
_LANGER_BELEG_HINWEIS = ('Langer Beleg -- evtl. Positionsmodus "eine Position je Steuersatz" '
                         'oder "Zusammenfassung"? (Lieferanten-Reiter bzw. beim Pruefen oben '
                         'bei "Positionsdaten")')


def _positionsmodus_hinweis(res: "Result", supplier: Supplier | None) -> None:
    """Nur ein Hinweis fuer den Menschen (Teil B) -- wenn der Lieferant den
    Positionsmodus noch nie bewusst gesetzt hat (``positionsmodus_gesetzt``)
    UND der Beleg sehr lang ist bzw. sehr viele Positionen hat. Niemals
    automatisch umstellen."""
    if supplier is not None and supplier.positionsmodus_gesetzt:
        return
    if (res.seiten >= _LANGER_BELEG_SEITEN
            or len(res.line_items) >= _LANGER_BELEG_POSITIONEN):
        if _LANGER_BELEG_HINWEIS not in res.hinweise:
            res.hinweise.append(_LANGER_BELEG_HINWEIS)


def _qualitaet(res: Result, store: Store) -> None:
    """Nachlauf fuer jedes Ergebnis (Aaron 10.10.2026), blockiert nichts:
    Leistungsdatum per Textregel, gelbe Hinweise aus dem Lieferanten-
    Gedaechtnis/Datum (plausi.py) und die Pflichtangaben-Gruppe
    (pflicht.py)."""
    text, res._pruef_text = res._pruef_text, ""
    if res.aborted:
        return
    if res.fields:
        pflicht.apply_leistungsdatum(res.fields, text)
    neu = plausi.hinweise(res.fields, res.line_items, res.supplier_id, store, res.sha256)
    res.pflichtangaben = pflicht.pruefen(res, text, store)
    if (h := pflicht.hinweis_text(res.pflichtangaben)):
        neu.append(h)
    for h in neu:
        if h not in res.hinweise:
            res.hinweise.append(h)


def process(path: str | Path, store: Store, use_llm: bool = True,
            create_draft_supplier: bool = False,
            variante: str = semantic.VARIANTE_A) -> Result:
    """Eine Rechnung verarbeiten (``_process``) und danach die Qualitaets-
    Hinweise anhaengen (Lieferanten-Gedaechtnis, Datum) -- an einer Stelle
    statt an jedem der vielen Ausgaenge von ``_process``."""
    res = _process(path, store, use_llm=use_llm, create_draft_supplier=create_draft_supplier,
                   variante=variante)
    try:
        _qualitaet(res, store)
    except Exception as e:  # noqa: BLE001 -- reine Hinweise duerfen nie die Verarbeitung kippen
        res.hinweise.append(f"Qualitaetshinweise nicht verfuegbar ({type(e).__name__}: {e})")
    return res


def _process(path: str | Path, store: Store, use_llm: bool = True,
             create_draft_supplier: bool = False,
             variante: str = semantic.VARIANTE_A) -> Result:
    """``create_draft_supplier`` (Standard ``False``): legt bei einem nicht
    erkannten Lieferanten einen Entwurf an (``Supplier.bestaetigt=False``,
    Entscheidung Aaron 09.10.2026) -- nur in der echten Verarbeitung
    (``scan_folder``/CLI ``rechnung scan``/Web-UI ``/api/scan``), NIE im
    Trockenlauf/Vorschau (``/api/invoice``-Anzeige, ``messen``,
    ``vorlernen``) -- deshalb ein expliziter Parameter statt eines
    Seiteneffekts, den jeder Aufrufer woertlich entscheiden muss.

    ``variante``: Extraktionsvariante fuer den KI-Teil (Testsatz-Vergleich
    A/B/C, Aaron 10.10.2026) -- unbekannt/nicht gebaut = Fehler, bevor
    irgendetwas gelesen wird."""
    variante = semantic.pruefe_variante(variante)
    path = Path(path)
    timer = StageTimer()
    # Passwortgeschuetztes PDF: nichts lesbar, nichts zu raten -> Mensch.
    if path.suffix.lower() == ".pdf" and pdfmod.password_protected(path):
        res = Result(file=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                     status=DEFERRED, hold_reasons=[pdfmod.PDF_PASSWORD])
        _apply_hold(res)
        return res
    # Stufe 0: E-Rechnung (eigenes XML oder in ein PDF eingebettet). Hat
    # Vorrang vor allem anderen -- dort steht nichts, was geraten werden muss.
    with timer.stage("erechnung"):
        inv = einvoice.load(path)
    if inv is not None:
        res = _process_einvoice(path, inv, store, timer,
                                create_draft_supplier=create_draft_supplier)
        res.stufenzeiten = timer.times
        return res
    if path.suffix.lower() not in (".pdf",) + ocr.IMAGE_SUFFIXES:
        return Result(file=str(path), status=UNSUPPORTED)

    # Scans/Bilder: Texterkennung (lokal, Tesseract), danach normal weiter.
    with timer.stage("laden"):
        doc, o = load_document(path, store)
    if o is not None and o.seconds:
        # bereits von ocr.ensure_text gemessen (Cache-Treffer: aus den Meta-
        # daten der ersten Erkennung, nicht die aktuelle -- sehr schnelle --
        # Cache-Lesezeit) -- eigene Zeile im Bericht statt im "laden"-Anteil
        # versteckt, da OCR das mit Abstand teuerste Teilstueck ist.
        timer.add("ocr", o.seconds)
    if doc is None:
        res = Result(file=str(path), sha256=o.sha256, status=DEFERRED,
                     hold_reasons=[o.error], ocr=o.to_dict())
        _apply_hold(res)
        res.stufenzeiten = timer.times
        return res
    res = Result(file=str(path), sha256=doc.sha256, seiten=len(doc.pages), _pruef_text=doc.text)

    # Swiss QR-Rechnung (Postfach-Baustein 7): nur rendern/dekodieren, wenn
    # der Text ueberhaupt danach aussieht (Label "Zahlteil"/"QR-IBAN" etc.)
    # -- teures Seiten-Rendern nicht bei jeder gewoehnlichen Rechnung.
    qr_obj = swissqr.read(path) if swissqr.likely(doc.text) else None
    res.qr = qr_obj.to_dict() if qr_obj else None

    # Andere beschriftete Anhaenge derselben Mail (anlagen.py, Postfach-
    # Baustein 8), falls beim Postfach-Abholen welche an diesen Beleg
    # (sha256 der Originaldatei) gehaengt wurden.
    res.anlagen = anlagen.info(store, doc.sha256)

    # Sammel-PDF (split.py): Teile tragen den Verweis aufs Original. Ein PDF
    # mit (moeglicherweise) mehreren Rechnungen wird nie als eine verarbeitet.
    res.sammel = split.part_info(store, doc.sha256)
    if res.sammel is None:
        mehrere = split.analyse(doc)
        if mehrere.uncertain:
            res.hold_reasons.append(mehrere.uncertain)
        elif mehrere.multiple:
            res.hold_reasons.append(f"{split.MULTI} ({len(mehrere.segments)}) -- "
                                    f"wird beim Scan aufgeteilt")

    # Lieferant und Rechnungsadresse ausschliesslich per Abgleich mit der
    # Bibliothek. Kein eindeutiger Treffer -> zurueckgestellt; die Extraktion
    # laeuft trotzdem, damit der Mensch die Werte gleich vor sich hat.
    #
    # Lieferant je Rechnungsadresse (wie Abbyy FlexiCapture, Entscheidung
    # Aaron 09.10.2026): die Rechnungsadresse wird ZUERST bestimmt, danach
    # nur unter den ihr zugeordneten Lieferanten gesucht (``identify_with_
    # reason`` filtert selbst ueber ``kandidaten()`` -- hier wird nur die
    # dazu passende Kandidatenmenge des Index mitgegeben).
    with timer.stage("lieferant"):
        addr_check = _billing_check(doc.text, store, res)
        has_addr = bool(store.addresses())
        match, why = identify_with_reason(
            doc, store.suppliers(), index=store.supplier_index(res.billing_address_id),
            address_id=res.billing_address_id, has_addresses=has_addr,
            address_name=res.billing_address_name or "")
        if not match and create_draft_supplier:
            match = _auto_draft_pdf(res, doc, store, use_llm, timer)
    if not match:
        res.hold_reasons.insert(0, f"Lieferant: {why}")
        _finish_untemplated(res, doc, None, store, use_llm, [addr_check], timer, qr_obj,
                            variante)
        _ocr_adjust(res, o)
        _apply_hold(res)
        _positionsmodus_hinweis(res, None)
        res.stufenzeiten = timer.times
        return res

    supplier, score = match
    res.supplier_id = supplier.id
    res.supplier_name = supplier.name
    res.supplier_external_id = supplier.external_id or None
    res.match_score = score
    if not supplier.bestaetigt:
        # Entwurf (frisch angelegt oder ein vorhandener, noch nicht
        # bestaetigter) -- die Extraktion laeuft normal weiter, die Rechnung
        # bleibt aber immer beim Menschen (_apply_hold -> DEFERRED), auch im
        # autonomen Modus (Entscheidung Aaron 09.10.2026).
        res.hold_reasons.append(NEW_SUPPLIER_HOLD)

    # Als veraltet markierte Vorlagen (learn.py, SPEC §7p: Layoutwechsel
    # erkennen) werden nicht mehr angewendet -- die Rechnung laeuft ueber die
    # KI-Stufe/Handeingabe, bis aus der naechsten Bestaetigung eine neue
    # Vorlage/Variante gelernt ist. Die veraltete Vorlage bleibt gespeichert
    # (Audit/Nachvollziehbarkeit), wird nur nicht mehr ausgewaehlt.
    with timer.stage("vorlage"):
        active_templates = [t for t in store.templates_for(supplier.id) if not t.stale]
        tpl = _pick_template(doc, active_templates, res.billing_address_id)
    if tpl is None:
        res.status = NO_TEMPLATE
        _inject_library_fields(res.fields, supplier)
        _finish_untemplated(res, doc, supplier, store, use_llm, [addr_check], timer, qr_obj,
                            variante)
        if create_draft_supplier:
            _enrich_draft(res, supplier, store)
        _ocr_adjust(res, o)
        _apply_hold(res)
        _positionsmodus_hinweis(res, supplier)
        res.stufenzeiten = timer.times
        return res

    res.template_id = tpl.id
    res.template_variant = tpl.variant
    tpl.confirmations = max(tpl.confirmations, store.distinct_confirmations(tpl.id))
    res.template_secured = tpl.secured
    with timer.stage("vorlage"):
        res.fields = apply_template(doc, tpl)
    _inject_library_fields(res.fields, supplier)

    # Semantischer Fallback (§7f, optional/aus per Default): fuer Felder, die
    # das Template nicht oder nur schwach liefert, fragt ein lokales LLM den
    # Volltext -- das Template wird dabei als Hinweis mitgegeben, nicht als
    # Zwang. Konfidenz ist ueber config.llm().max_confidence gedeckelt, kann
    # also nie allein auf 'auto' hochfahren (siehe CONF_MIN unten) -- deshalb
    # kann ``use_llm=False`` (z.B. fuer eine reine Listenuebersicht ueber
    # viele Dateien) den Fallback ueberspringen, ohne res.status zu aendern.
    #
    # SPEC Effizienz-Baustein 1: nachgefragt werden nur Pflichtfelder und die
    # Felder, die die Vorlage selbst kennt -- ein optionales Feld, das fuer
    # diesen Lieferanten nie angelernt wurde (z.B. Kostenstelle), kostet sonst
    # bei JEDER Rechnung einen Modellaufruf und laedt das Modell zum Raten ein.
    tpl_names = {f.name for f in tpl.fields}
    gaps = [
        name for name, (_vtype, required) in STANDARD_FIELDS.items()
        if (required or name in tpl_names)
        and (res.fields.get(name) is None or res.fields[name].source == "missing"
             or res.fields[name].confidence < CONF_MIN)
    ]
    if gaps and use_llm:
        with timer.stage("ki"):
            hints = {n: r.raw for n, r in res.fields.items() if r.source != "missing"}
            for name, fr in semantic.extract(extraction_text(doc), gaps, hints,
                                             res.llm_rejected).items():
                cur = res.fields.get(name)
                if cur is None or cur.source == "missing" or fr.confidence > cur.confidence:
                    res.fields[name] = fr

    # Manuelle Korrekturen fuer genau diese Rechnung (sha256) gewinnen immer.
    machine = dict(res.fields)
    corrections = store.corrections(doc.sha256)
    _apply_corrections(res.fields, corrections, {f.name: f.value_type for f in tpl.fields})
    # Faelligkeit/Skonto: was Vorlage/Mensch nicht liefern, per Regel aus dem Text
    zahlung.apply(res.fields, doc.text, res.hinweise)
    belegart.apply(res.fields, doc, hinweise=res.hinweise)
    belegart.reverse_charge(res.fields, doc.text)
    waehrung.apply(res.fields, doc.text, res.hinweise)
    swissqr.apply(res.fields, qr_obj, res.hinweise)

    modus = _positionsmodus(supplier)
    if modus == POSITIONSMODUS_EINE_POSITION:
        # Positionen werden IMMER aus der USt-Aufstellung gebildet -- eine
        # dafuer angelernte Vorlagen-Positionstabelle wird (sollte es
        # ausnahmsweise eine geben) in diesem Modus nicht angewendet.
        pass
    elif tpl.line_items and o is not None and o.partial:
        # Langer Scan, Positionsseiten noch ohne Texterkennung: keine halbe
        # Tabelle lesen (die Positionssumme waere falsch rot) -- die Positionen
        # kommen, sobald complete_pending den Rest erkannt hat.
        res.hinweise.append("Positionen erst nach vollstaendiger Texterkennung "
                            f"(Seiten {', '.join(str(i + 1) for i in o.missing_pages)})")
    elif tpl.line_items:
        with timer.stage("vorlage"):
            res.line_items = positionen.normalize_columns(apply_line_items(doc, tpl.line_items))
        _suggest_categories(res.line_items, tpl, store)
    machine_items = res.line_items
    _apply_line_item_correction(res, corrections)
    manuell_korrigiert = POSITIONEN_KEY in corrections
    _positionsmodus_vorbereiten(res, supplier, manuell_korrigiert)

    _gegencheck(res, doc, tpl, store, timer, use_llm)

    with timer.stage("pruefungen"):
        rk = rechnen.rechne(res.fields, res.line_items, doc.text)
        res.fields, res.line_items, res.tax_breakdown = rk.fields, rk.line_items, rk.tax_breakdown
        res.hinweise.extend(g for g in rk.gruende if g not in res.hinweise)
        _positionsmodus_abschliessen(res, supplier, manuell_korrigiert)

        res.checks = _all_checks(res.fields, supplier, store, res.line_items, doc, [addr_check],
                                 qr_obj)
        _set_dublette(res, store, doc)
        res.shadow = shadow.decide(
            machine, _all_checks(machine, supplier, store, machine_items, doc, [addr_check],
                                 qr_obj)
            if corrections else res.checks, new_supplier=False)

    required = [f for f in tpl.fields if f.required]
    req_conf = [res.fields[f.name].confidence for f in required if f.name in res.fields]
    res.overall_confidence = round(min(req_conf), 2) if req_conf else 0.0

    checks_ok = all(c.ok is not False for c in res.checks)
    conf_ok = res.overall_confidence >= CONF_MIN and all(
        res.fields[f.name].source != "missing" for f in required if f.name in res.fields
    )
    res.status = AUTO if (tpl.secured and checks_ok and conf_ok) else REVIEW
    _ocr_adjust(res, o)
    _apply_hold(res)
    _positionsmodus_hinweis(res, supplier)
    res.stufenzeiten = timer.times
    return res


def scan_folder(folder: str | Path, store: Store, guard=None) -> list[Result]:
    """Alle Rechnungen eines Ordners verarbeiten -- jede mit Zeitgrenze
    (guard.py), damit eine haengende Rechnung die Schlange nicht blockiert.
    Jedes Ergebnis (auch ein Abbruch durch Zeitgrenze/OCR-Fehler) haengt ein
    Ereignis an ``data/events.jsonl`` -- Grundlage fuer den Fehlerbericht
    (eventlog.py), unabhaengig davon, ob die Rechnung spaeter bestaetigt wird.
    Danach wird jede Rechnung verteilt (``dispatch``): unsichere in den Ordner
    "Menschliche Bearbeitung" (human.py).

    SPEC Effizienz-Baustein 5: schnelle Faelle (E-Rechnung, Textebene +
    bekannte Vorlage) werden komplett abgearbeitet, BEVOR die langsamen
    (OCR/KI) ueberhaupt beginnen (``queueing.split_by_effort``) -- eine
    einzelne langsame Rechnung haelt die vielen schnellen eines Stapellaufs
    damit nicht auf. Nach dem KI-Stapel wird das Modell wieder freigegeben
    (``semantic.release_model``, Laptop-RAM)."""
    from . import eventlog, semantic
    from .guard import Guard
    from .queueing import split_by_effort

    folder = Path(folder)
    own = guard is None
    guard = guard or Guard(store.root)
    results: list[Result] = []
    had_slow = False
    try:
        # Sammel-PDFs vorher sicher aufteilen (split.py)
        split.prepare_folder(folder, store)
        fast, slow = split_by_effort(invoice_files(folder), store)
        for p in fast:
            res = guard.process(p, create_draft_supplier=True)
            eventlog.record(store, res)
            dispatch(res, store)
            results.append(res)
        had_slow = bool(slow)
        for p in slow:
            res = guard.process(p, create_draft_supplier=True)
            eventlog.record(store, res)
            dispatch(res, store)
            results.append(res)
    finally:
        if own:
            guard.close()
        if had_slow:
            semantic.release_model()
    # Hintergrundschritt ausserhalb der Zeitgrenze je Rechnung: lange Scans
    # vollstaendig erkennen (Positionen/Archiv), Postfach-Baustein 3.
    complete_background_ocr(store)
    return results


def complete_background_ocr(store: Store, limit: int = 0) -> list[dict]:
    done = ocr.complete_pending(store.root / "ocr", store.root / "archive", limit)
    for d in done:
        store.audit("ocr_rest", d)
    return done


def dispatch(res: Result, store: Store) -> dict:
    """Was passiert mit einer frisch verarbeiteten Rechnung? Bereits
    uebernommene Rechnungen bleiben unberuehrt; jede unsichere (human.py:
    zurueckgestellt, Pflichtfeld fehlt, rote Pruefung, OCR unsicher, Wert
    unsicher) geht in den Ordner "Menschliche Bearbeitung" -- nichts wird
    geraten. Sichere Rechnungen:

    - Testphase (modus.py, Standard): warten auf den Bestaetigungs-Klick
      im Training-UI (§7c).
    - autonom: ohne Klick durch ``finalize`` in die JobRouter-Warteschlange
      (dort gibt noch ein Mensch frei) -- ausser die optionale Stichprobe
      schickt sie trotzdem zum Menschen.

    Das Ergebnis steht auch in ``res.verteilung``."""
    from . import human, modus

    if res.sha256 and store.seen_sha(res.sha256):
        out = {"aktion": "bereits_uebernommen"}
    elif gruende := human.gruende(res):
        human.ablegen(store, res, gruende)
        out = {"aktion": "mensch", "gruende": gruende}
    elif not modus.is_autonomous(store):
        out = {"aktion": "bestaetigung"}
    elif modus.stichprobe(res.sha256):
        stich = [{"art": "stichprobe", "felder": [],
                  "grund": f"Stichprobe ({modus.stichprobe_prozent()} %) -- bitte gegenpruefen"}]
        human.ablegen(store, res, stich)
        out = {"aktion": "mensch", "gruende": stich}
    else:
        fin = finalize(res, store, operator=modus.AUTONOM_OPERATOR, autonomous=True)
        shadow.record(store, res, modus.AUTONOM_OPERATOR)
        out = {"aktion": "autonom", "finalize": fin}
    res.verteilung = out
    return out


def invoice_files(folder: str | Path) -> list[Path]:
    """Alle Rechnungsdateien eines Ordners: PDFs und E-Rechnungs-XML."""
    folder = Path(folder)
    # Feste Reihenfolge auf jedem System (Windows vergleicht Pfade sonst
    # ohne Gross-/Kleinschreibung, Linux mit)
    return sorted((p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in INVOICE_SUFFIXES),
                  key=lambda p: (p.name.lower(), p.name))


def _fv(fields: dict, key: str):
    fr = fields.get(key)
    return fr.value if fr is not None else None


def finalize(res: Result, store: Store, *, operator: str | None = None,
             to_jobrouter: bool = True, autonomous: bool = False) -> dict:
    """Eine geprüfte Rechnung in die Bücher übernehmen.

    1. Original unverändert archivieren (``data/archive/<sha>.pdf|.xml`` – GoBD;
       bei E-Rechnungen ist das XML das rechtlich maßgebliche Original).
    2. Audit-Eintrag (wer / wann / was) in ``data/audit.jsonl``.
    3. Journal-Datensatz in ``data/processed.jsonl`` (append-only).
    4. Ergebnis als JSON/CSV nach ``data/out/``.
    5. Für die JobRouter-Übergabe in ``data/queue/`` einreihen, sofern der
       Status in ``RECHNUNG_JOBROUTER_ALLOW_STATUS`` liegt.

    Idempotent über ``sha256`` + Template: mehrfacher Aufruf zählt weder im
    Journal doppelt noch schickt er zweimal an JobRouter.

    Voraussetzung: die Rechnung wurde im Training-UI explizit bestätigt
    (``Store.mark_confirmed``) -- unabhängig vom Status. ``auto``/``review``
    sind nur noch Konfidenz-Signale, keine Freigabe fürs automatische
    Durchlaufen; ohne Bestätigung wird nichts archiviert, geloggt oder an
    JobRouter eingereiht.

    Einzige Ausnahme: ``autonomous=True`` im Betriebsmodus ``autonom``
    (modus.py, SPEC §7v) UND die Rechnung ist sicher (``human.gruende``
    leer) -- dann ohne Klick, vermerkt als ``freigabe: autonom`` im Audit
    und im JobRouter-Datensatz. Unsichere Werte gehen nie ohne Mensch raus.
    """
    from . import human, modus

    freigabe = "mensch"
    if not store.is_confirmed(res.sha256):
        if not autonomous:
            return {"finalized": False, "error": "not_confirmed", "status": res.status}
        if not modus.is_autonomous(store):
            return {"finalized": False, "error": "not_autonomous", "status": res.status}
        if human.gruende(res):
            return {"finalized": False, "error": "unsicher", "status": res.status}
        freigabe = "autonom"

    op = operator or _operator()
    payload = res.to_dict()
    payload["freigabe"] = freigabe
    # Unveraenderliche Stammdaten aus der Bibliothek mitgeben (stammdaten.py)
    from . import stammdaten
    payload["stammdaten"] = stammdaten.fuer_rechnung(store, res)
    already = store.seen_sha_template(res.sha256, res.template_id)

    store.archive_pdf(res.file, res.sha256)

    if not already:
        store.mark_processed({
            "file": Path(res.file).name, "sha256": res.sha256,
            "supplier_id": res.supplier_id, "supplier_name": res.supplier_name,
            "template_id": res.template_id, "variant": res.template_variant,
            "billing_address_id": res.billing_address_id,
            "billing_address_name": res.billing_address_name,
            "status": res.status,
            "freigabe": freigabe,
            "sammel": res.sammel,
            "rechnungsnummer": _fv(res.fields, "rechnungsnummer"),
            "rechnungsdatum": _fv(res.fields, "rechnungsdatum"),
            "belegart": _fv(res.fields, "belegart"),
            **{k: v for k, v in belegart.signed(
                {"belegart": _fv(res.fields, "belegart"), "netto": _fv(res.fields, "netto"),
                 "ust_betrag": _fv(res.fields, "ust_betrag"),
                 "brutto": _fv(res.fields, "brutto")}).items() if k != "belegart"},
            "tax_breakdown": res.tax_breakdown,
            "line_items": res.line_items,
        })
        store.audit("finalize", {
            "sha256": res.sha256, "status": res.status, "freigabe": freigabe,
            "warnungen": res.warnings,
            "template_id": res.template_id,
            "rechnungsnummer": _fv(res.fields, "rechnungsnummer"),
        }, op)

    out = belegart.signed({k: v.value for k, v in res.fields.items()})
    out["_positionen"] = res.line_items
    out["_ust_aufstellung"] = res.tax_breakdown
    # Herkunft je Kopf-Feld (gelesen/errechnet/kopf/korrigiert, SPEC
    # Baustein 3) -- Positionen/USt-Aufstellung tragen ihre Herkunft bereits
    # selbst (``_herkunft`` je Zeile, rechnen.py).
    out["_herkunft"] = {k: rechnen.herkunft(v.source) for k, v in res.fields.items()}
    if res.billing_address_name:
        out["rechnungsadresse"] = res.billing_address_name
    if res.warnings:
        out["warnung"] = "; ".join(res.warnings)
    out["freigabe"] = freigabe
    store.write_output(Path(res.file).stem, out)

    queued = None
    if to_jobrouter:
        cfg = _jr_cfg()
        # Vom Menschen gepruefte und freigegebene Rechnung ("Senden", Aaron
        # 11.10.2026) geht unabhaengig vom Konfidenz-Status raus.
        if freigabe == "mensch" or (not cfg.allow_status) or res.status in cfg.allow_status:
            q = jobrouter.enqueue(store.root, payload)
            queued = str(q) if q else None
    return {"finalized": True, "already": already, "queued": queued,
            "status": res.status, "freigabe": freigabe}


def _round_rate(r) -> float | None:
    v = _to_amount(r)
    return round(v, 1) if v is not None else None


def _rate_suffix(rate: float) -> str:
    s = f"{rate:.1f}".rstrip("0").rstrip(".")
    return (s or "0").replace(".", "_")


def _journal_rate_columns(store: Store, rows_raw: list[dict]) -> list[float]:
    """Welche Saetze im Journal eigene Spalten bekommen -- wird bei jedem
    Aufruf frisch aus dem kompletten Bestand ermittelt, nie aus einer
    hartkodierten Liste oder nur den ersten paar Rechnungen: neue Saetze
    (weitere MwSt-Stufen, andere Laender) tauchen automatisch als neue Spalte
    auf, sobald sie einmal vorkommen -- unabhaengig davon, ob ein Lieferant
    bisher nur einen Satz hatte oder umgekehrt mal mehrere und spaeter
    wieder nur einen. Zusaetzlich vorbelegt mit den fuer die beteiligten
    Waehrungen typischen Saetzen (EUR: 19/7/0, CHF: 8,1/2,6/0), damit die
    Spalten fuer einen bekannten Schweizer Lieferanten schon vor der ersten
    Rechnung mit z.B. 8,1 % stabil stehen."""
    rates: set[float] = set()
    currencies: set[str] = set()
    for r in rows_raw:
        sup = store.supplier(r.get("supplier_id") or "")
        currencies.add(sup.currency if sup and sup.currency else DEFAULT_CURRENCY)
        for tb in r.get("tax_breakdown") or []:
            rate = _round_rate(tb.get("satz"))
            if rate is not None:
                rates.add(rate)
    for cur in currencies or {DEFAULT_CURRENCY}:
        rates.update(EXPECTED_RATES.get(cur, EXPECTED_RATES[DEFAULT_CURRENCY]))
    return sorted(rates)


def journal_rows(store: Store) -> list[dict]:
    """Buchungsjournal: eine Zeile je bestaetigter Rechnung, Netto/USt je Satz
    in eigenen Spalten."""
    # Nach sha256 deduplizieren (append-only Log kann eine Rechnung mehrfach
    # enthalten, wenn sie nachträglich korrigiert/erneut übernommen wurde –
    # der jüngste Eintrag gewinnt, Position bleibt).
    raw_rows = [r for r in store.processed() if r.get("rechnungsnummer")]
    rate_cols = _journal_rate_columns(store, raw_rows)
    dedup: dict[str, dict] = {}
    for i, r in enumerate(raw_rows):
        row = {
            "datei": r.get("file"),
            "lieferant": r.get("supplier_name") or r.get("supplier_id"),
            "rechnungsadresse": r.get("billing_address_name") or "",
            "rechnungsnummer": r.get("rechnungsnummer"),
            "rechnungsdatum": r.get("rechnungsdatum"),
            "netto_gesamt": _to_amount(r.get("netto")),
            "ust_gesamt": _to_amount(r.get("ust_betrag")),
            "brutto_gesamt": _to_amount(r.get("brutto")),
        }
        by_rate: dict[float, dict] = {}
        for tb in r.get("tax_breakdown") or []:
            rate = _round_rate(tb.get("satz"))
            if rate is None:
                rate = 0.0
            slot = by_rate.setdefault(rate, {"netto": 0.0, "ust": 0.0})
            slot["netto"] = round(slot["netto"] + (_to_amount(tb.get("netto")) or 0), 2)
            slot["ust"] = round(slot["ust"] + (_to_amount(tb.get("ust")) or 0), 2)
        for rate in rate_cols:
            s = by_rate.get(rate, {})
            row[f"netto_{_rate_suffix(rate)}"] = s.get("netto")
            row[f"ust_{_rate_suffix(rate)}"] = s.get("ust")
        # Sicherheitsnetz: sollte trotz obiger Vorbelegung ein Satz auftauchen,
        # der in keiner Spalte landet, geht er hier nicht verloren.
        extra = sorted(k for k in by_rate if k not in rate_cols)
        row["weitere_saetze"] = ", ".join(
            f"{_rate_suffix(k)}%: {by_rate[k]['netto']:.2f}/{by_rate[k]['ust']:.2f}"
            for k in extra
        )
        dedup[r.get("sha256") or f"__{i}"] = row
    return list(dedup.values())


def journal_totals(rows: list[dict]) -> dict:
    """Summenzeile -- Spalten werden aus den tatsaechlich vorhandenen Zeilen
    abgeleitet, nicht aus einer festen Satz-Liste."""
    tot: dict = {"rechnungen": len(rows)}
    keys = {"netto_gesamt", "ust_gesamt", "brutto_gesamt"}
    for row in rows:
        keys.update(k for k in row if k.startswith("netto_") or k.startswith("ust_"))
    for k in sorted(keys):
        tot[k] = round(sum(x[k] for x in rows if x.get(k) is not None), 2)
    return tot


# ---- Lieferanten-Entwurf bestaetigen/verwerfen (Training-UI, Abschnitt
# "2 Lieferant" und Reiter "Lieferanten") -------------------------------
def confirm_supplier(store: Store, supplier_id: str, operator: str = "") -> Supplier:
    """Entwurf bestaetigen -- ab jetzt ein normaler Lieferant: die naechste
    Rechnung dieses Lieferanten laeuft ohne ``NEW_SUPPLIER_HOLD``. Ein
    bereits bestaetigter Lieferant ist ein No-op (kein doppelter
    Audit-/Ereigniseintrag)."""
    from . import eventlog
    from .config import operator as _op

    sup = store.supplier(supplier_id)
    if sup is None:
        raise ValueError(f"Lieferant {supplier_id!r} nicht gefunden")
    if not sup.bestaetigt:
        sup.bestaetigt = True
        store.save_supplier(sup)
        op = operator or _op()
        store.audit("lieferant_bestaetigt", {"id": supplier_id, "name": sup.name}, op)
        eventlog.record_supplier(store, supplier_id, sup.name, "bestaetigt", op)
    return sup


def discard_supplier(store: Store, supplier_id: str, operator: str = "") -> None:
    """Entwurf verwerfen -- nur fuer unbestaetigte erlaubt (fuer einen
    bestaetigten Lieferanten gibt es den normalen "Loeschen"-Knopf im
    Reiter "Lieferanten"; der soll nicht aus Versehen ueber diesen Weg
    ausgeloest werden)."""
    from . import eventlog
    from .config import operator as _op

    sup = store.supplier(supplier_id)
    if sup is None:
        raise ValueError(f"Lieferant {supplier_id!r} nicht gefunden")
    if sup.bestaetigt:
        raise ValueError("Nur unbestaetigte Lieferanten-Entwuerfe koennen verworfen werden")
    store.delete_supplier(supplier_id)
    op = operator or _op()
    store.audit("lieferant_verworfen", {"id": supplier_id, "name": sup.name}, op)
    eventlog.record_supplier(store, supplier_id, sup.name, "verworfen", op)


# ---- Positionsmodus setzen (Training-UI, Gruppe "Positionsdaten" bzw.
# Reiter "Lieferanten") -- Teil B, Entscheidung Aaron 10.10.2026 ----------
def set_positionsmodus(store: Store, supplier_id: str, modus: str,
                       bezeichnung: str = "", operator: str = "") -> Supplier:
    """Positionsmodus eines Lieferanten setzen -- wie in Abbyy die
    Entscheidung der bearbeitenden Person beim Pruefen einer Rechnung
    (keine Stammdaten-Rechte noetig). Gilt danach fuer alle Rechnungen
    dieses Lieferanten, bleibt aber jederzeit aenderbar. ``bezeichnung``
    wird nur bei 'eine_position' gespeichert (sonst verworfen, damit ein
    spaeterer Wechsel zurueck zu 'eine_position' nicht eine laengst
    irrelevante alte Bezeichnung wieder auftauchen laesst)."""
    from .config import operator as _op

    if modus not in POSITIONSMODI:
        raise ValueError(f"Unbekannter Positionsmodus {modus!r} (erlaubt: {', '.join(POSITIONSMODI)})")
    sup = store.supplier(supplier_id)
    if sup is None:
        raise ValueError(f"Lieferant {supplier_id!r} nicht gefunden")
    sup.positionsmodus = modus
    sup.positionsbezeichnung = bezeichnung.strip() if modus == POSITIONSMODUS_EINE_POSITION else ""
    sup.positionsmodus_gesetzt = True
    store.save_supplier(sup)
    op = operator or _op()
    store.audit("lieferant_positionsmodus", {
        "id": supplier_id, "name": sup.name, "modus": modus,
        "bezeichnung": sup.positionsbezeichnung,
    }, op)
    return sup
