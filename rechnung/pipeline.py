"""Orchestrierung: Lieferant erkennen -> Template anwenden -> validieren -> Status."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import hashlib
import re

from . import billing
from . import einvoice
from . import jobrouter
from . import pdf as pdfmod
from . import semantic
from . import shadow
from .config import jobrouter as _jr_cfg
from .config import operator as _operator
from .identify import (DEFAULT_CURRENCY, EXPECTED_RATES, FIXED_FIELD_TYPES, IBAN_DEVIATES,
                       cross_check, extract_identifiers, identify_einvoice,
                       identify_with_reason)
from .store import Store
from .suggest import suggest_category
from .template import (STANDARD_FIELDS, FieldResult, Template, apply_line_items,
                       apply_template)
from .textnorm import coerce, compact, find_ibans
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
SCAN = "scan_no_textlayer"      # kein Textlayer -> OCR nötig (v1 nicht unterstützt)
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
ORIGINS = ("einvoice", "template", "llm", "library", "manual")


def origin(source: str) -> str | None:
    if source == "missing":
        return None
    return "template" if source.startswith("template") else source

INVOICE_SUFFIXES = (".pdf", ".xml")

CONF_MIN = 0.7


@dataclass
class Result:
    file: str
    sha256: str = ""
    supplier_id: str | None = None
    supplier_name: str | None = None
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

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "extraction": self.extraction,
            "einvoice": self.einvoice,
            "sha256": self.sha256,
            "supplier_id": self.supplier_id,
            "supplier_name": self.supplier_name,
            "match_score": self.match_score,
            "template_id": self.template_id,
            "template_variant": self.template_variant,
            "template_secured": self.template_secured,
            "billing_address_id": self.billing_address_id,
            "billing_address_name": self.billing_address_name,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status),
            "hold_reasons": self.hold_reasons,
            "duration_s": self.duration_s,
            "aborted": self.aborted,
            "overall_confidence": self.overall_confidence,
            "line_items": self.line_items,
            "tax_breakdown": self.tax_breakdown,
            "fields": {
                k: {"value": v.value, "raw": v.raw, "confidence": v.confidence,
                    "source": v.source, "origin": origin(v.source)}
                for k, v in self.fields.items()
            },
            "llm_rejected": self.llm_rejected,
            "shadow": ({k: v for k, v in self.shadow.items() if k != "values"}
                       if self.shadow else None),
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }


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


def _tax_breakdown(fields: dict, line_items: list[dict]) -> list[dict]:
    """USt-Aufstellung je Steuersatz. Prioritaet:
    1. explizit markierter ``ust_aufstellung``-Block
    2. aus den Positionen aufsummiert (Spalten ``ust_satz``/``netto``)
    3. Einzelsatz aus den Kopf-Feldern netto/ust_betrag/ust_satz
    """
    fr = fields.get("ust_aufstellung")
    if fr is not None and isinstance(fr.value, list) and fr.value:
        rows = []
        for r in fr.value:
            netto = _to_amount(r.get("netto"))
            ust = _to_amount(r.get("ust"))
            rows.append({"satz": r.get("satz"), "netto": netto, "ust": ust,
                         "brutto": round((netto or 0) + (ust or 0), 2)})
        return rows

    if line_items:
        agg: dict[float, dict] = {}
        for it in line_items:
            rate = _to_amount(it.get("ust_satz") or it.get("mwst") or it.get("ust"))
            base = _to_amount(it.get("netto") or it.get("summe") or it.get("betrag")
                              or it.get("gesamt") or it.get("gesamtpreis"))
            if rate is None or base is None:
                continue
            slot = agg.setdefault(rate, {"satz": rate, "netto": 0.0, "ust": 0.0})
            slot["netto"] = round(slot["netto"] + base, 2)
            slot["ust"] = round(slot["ust"] + base * rate / 100, 2)
        rows = [dict(v, brutto=round(v["netto"] + v["ust"], 2))
                for v in agg.values()]
        if rows:
            return sorted(rows, key=lambda r: r["satz"])

    netto = _to_amount(fields["netto"].value) if "netto" in fields else None
    ust = _to_amount(fields["ust_betrag"].value) if "ust_betrag" in fields else None
    satz = _to_amount(fields["ust_satz"].value) if "ust_satz" in fields else None
    if netto is not None or ust is not None:
        return [{"satz": satz, "netto": netto, "ust": ust,
                 "brutto": round((netto or 0) + (ust or 0), 2)}]
    return []


def _suggest_categories(line_items: list[dict], tpl: Template, store: Store) -> None:
    """Kategorie je Position vorschlagen, gelernt aus ``Store.category_examples``.
    Vergleichstext ist die erste Text-Spalte (typischerweise "Bezeichnung") --
    nicht alle Text-Spalten zusammen: eine zusaetzliche Freitext-/Kommentar-
    Spalte wuerde den Fuzzy-Vergleich sonst verwaessern. ``_kategorie_text``
    traegt den Vergleichstext fuer die spaetere Korrektur mit -- Unterstrich-
    Feld, wird nicht in CSV/UI-Spalten aufgelistet."""
    if not tpl.line_items:
        return
    text_cols = [c.name for c in tpl.line_items.columns if c.value_type == "text"]
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
    "wuerde durchlassen" zaehlen."""
    for c in res.checks:
        if c.name == "iban-stammdaten" and c.ok is False and IBAN_DEVIATES not in res.hold_reasons:
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
        total = round(sum(p.get("netto") or 0 for p in line_items), 2)
        ok = abs(total - inv.summe_positionen) <= 0.05
        return Check(c.name, ok, f"Σ {total:.2f} vs Positionssumme {inv.summe_positionen:.2f}")
    if c.name == "betraege-plausibel" and inv.is_correction and c.ok is False:
        if None not in (inv.netto, inv.ust_betrag, inv.brutto) and \
                abs(inv.netto + inv.ust_betrag - inv.brutto) <= 0.02:
            return Check(c.name, True, f"Korrekturbeleg (Typ {inv.type_code}), negative Betraege zulaessig")
    return c


def _process_einvoice(path: Path, inv, store: Store) -> Result:
    """Stufe 0: Werte direkt aus dem XML, kein Template, keine KI.

    Konfidenz 1.0 je vorhandenem Feld -- der Wert ist nicht geraten, sondern
    vom Absender strukturiert geliefert. Die Plausibilitaetspruefungen laufen
    trotzdem (ein Absender kann auch falsch rechnen), und bestaetigt werden
    muss wie jede andere Rechnung auch (§7b)."""
    res = Result(file=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                 extraction=EXTRACT_EINVOICE, einvoice=inv.to_dict())

    match = identify_einvoice(inv, store.suppliers())
    supplier = None
    if match:
        supplier, res.match_score = match
        res.supplier_id = supplier.id
        res.supplier_name = supplier.name
    else:
        # Lieferant nur aus der Bibliothek -- der Verkaeufername im XML ist
        # ein Hinweis fuer den Menschen, keine Identitaet.
        named = f" (Rechnung nennt: {inv.verkaeufer_name})" if inv.verkaeufer_name else ""
        res.hold_reasons.append(f"Lieferant: kein Lieferant aus der Bibliothek passt{named}")
    pdf_text = _einvoice_pdf_text(path, inv)
    addr_check = _billing_check("\n".join([inv.kaeufer_name, pdf_text]), store, res)
    invoice_ibans = set(inv.verkaeufer_iban) | find_ibans(pdf_text)

    def put(name: str, value, raw: str | None = None) -> None:
        if value is None or value == "" or value == []:
            return
        res.fields[name] = FieldResult(name, value, raw if raw is not None else str(value),
                                       1.0, EXTRACT_EINVOICE)

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
    put("leitweg_id", inv.leitweg_id)
    put("zahlungsbedingungen", inv.zahlungsbedingungen)
    if inv.is_credit_note:
        put("belegart", "Gutschrift", inv.type_code)
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

    res.line_items = [dict(p) for p in inv.positionen]
    res.tax_breakdown = _tax_breakdown(res.fields, [])
    pdf_check = _check_pdf_matches_xml(path, inv)

    def checks_for(fields):
        cs = run_all(fields, supplier, store.seen_invoice, res.line_items)
        cs = [_einvoice_check(c, inv, res.line_items) for c in cs] + [pdf_check]
        return _replace_check(cs, _iban_master_check(supplier, invoice_ibans, fields)) + [addr_check]

    res.checks = checks_for(res.fields)
    res.shadow = shadow.decide(machine, checks_for(machine) if corrections else res.checks,
                               new_supplier=supplier is None)

    present = [n for n in _EINVOICE_REQUIRED if n in res.fields]
    res.overall_confidence = 1.0 if len(present) == len(_EINVOICE_REQUIRED) else 0.5
    checks_ok = all(c.ok is not False for c in res.checks)
    res.status = AUTO if (supplier is not None and checks_ok
                          and res.overall_confidence >= CONF_MIN) else REVIEW
    _apply_hold(res)
    return res


def _einvoice_pdf_text(path: Path, inv) -> str:
    """Sichtbarer Text einer Hybrid-Rechnung (ZUGFeRD/Factur-X), sonst ''."""
    if not inv.source.startswith("pdf:"):
        return ""
    try:
        return pdfmod.load(path).text if pdfmod.has_text_layer(path) else ""
    except Exception:
        return ""


def _apply_corrections(fields: dict[str, FieldResult], corrections: dict,
                       type_map: dict[str, str] | None = None) -> None:
    for fname, fval in corrections.items():
        vtype = (type_map or {}).get(fname) or STANDARD_FIELDS.get(fname, ("text",))[0]
        value, ok = coerce(str(fval), vtype)
        fields[fname] = FieldResult(fname, value if ok else fval, str(fval), 1.0, "manual")


def _llm_full(res: Result, doc) -> None:
    """Stufe 2: komplette KI-Extraktion, wenn es (noch) kein Template gibt.
    Fuellt nur, was nicht schon da ist -- Stammdaten aus dem gepflegten
    Lieferanten-Datensatz (``library``) gewinnen gegen das Modell."""
    full = semantic.extract_full(doc.text)
    res.llm_rejected.update(full.rejected)
    for name, fr in full.fields.items():
        cur = res.fields.get(name)
        if cur is None or cur.source == "missing":
            res.fields[name] = fr
    # Bewusst KEIN res.supplier_name aus der KI: die Lieferanten-Identitaet
    # kommt ausschliesslich aus der Bibliothek.
    if full.line_items and not res.line_items:
        res.line_items = full.line_items
    if full.fields:
        res.extraction = EXTRACT_LLM


def _finish_untemplated(res: Result, doc, supplier, store: Store, use_llm: bool,
                        extra_checks: list[Check] | None = None) -> None:
    """Rechnung ohne Template (Lieferant unbekannt oder noch nicht angelernt):
    optional KI-Extraktion (Stufe 2), Korrekturen einmischen und, sobald Werte
    da sind, pruefen. Damit kann ein Mensch die Werte (KI-Vorschlag oder
    eingetippt) pruefen und bestaetigen -- aus genau diesen bestaetigten
    Werten lernt learn.py dann das Template (Stufe 1), statt dass jeder Anker
    von Hand angeklickt werden muss. Der Status bleibt ``no_template``/
    ``zurueckgestellt`` (die KI-Konfidenz ist ohnehin unter CONF_MIN
    gedeckelt)."""
    extra = list(extra_checks or [])
    if use_llm:
        _llm_full(res, doc)
    machine = dict(res.fields)
    _apply_corrections(res.fields, store.corrections(doc.sha256))
    res.shadow = shadow.decide(machine, _all_checks(machine, supplier, store, res.line_items,
                                                    doc, extra), new_supplier=True)
    if not any(f.source != "library" for f in res.fields.values()):
        res.checks = extra
        return
    res.tax_breakdown = _tax_breakdown(res.fields, res.line_items)
    res.checks = _all_checks(res.fields, supplier, store, res.line_items, doc, extra)


def _all_checks(fields, supplier, store: Store, line_items, doc,
                extra: list[Check] | None = None) -> list[Check]:
    """Pruefungen fuer PDF-Rechnungen (Template- und Untemplated-Pfad)."""
    checks = run_all(fields, supplier, store.seen_invoice, line_items)
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
    return checks + list(extra or [])


def process(path: str | Path, store: Store, use_llm: bool = True) -> Result:
    path = Path(path)
    # Stufe 0: E-Rechnung (eigenes XML oder in ein PDF eingebettet). Hat
    # Vorrang vor allem anderen -- dort steht nichts, was geraten werden muss.
    inv = einvoice.load(path)
    if inv is not None:
        return _process_einvoice(path, inv, store)
    if path.suffix.lower() != ".pdf":
        return Result(file=str(path), status=UNSUPPORTED)

    if not pdfmod.has_text_layer(path):
        return Result(file=str(path), status=SCAN)

    doc = pdfmod.load(path)
    res = Result(file=str(path), sha256=doc.sha256)

    # Lieferant und Rechnungsadresse ausschliesslich per Abgleich mit der
    # Bibliothek. Kein eindeutiger Treffer -> zurueckgestellt; die Extraktion
    # laeuft trotzdem, damit der Mensch die Werte gleich vor sich hat.
    match, why = identify_with_reason(doc, store.suppliers())
    addr_check = _billing_check(doc.text, store, res)
    if not match:
        res.hold_reasons.insert(0, f"Lieferant: {why}")
        _finish_untemplated(res, doc, None, store, use_llm, [addr_check])
        _apply_hold(res)
        return res

    supplier, score = match
    res.supplier_id = supplier.id
    res.supplier_name = supplier.name
    res.match_score = score

    tpl = _pick_template(doc, store.templates_for(supplier.id), res.billing_address_id)
    if tpl is None:
        res.status = NO_TEMPLATE
        _inject_library_fields(res.fields, supplier)
        _finish_untemplated(res, doc, supplier, store, use_llm, [addr_check])
        _apply_hold(res)
        return res

    res.template_id = tpl.id
    res.template_variant = tpl.variant
    tpl.confirmations = max(tpl.confirmations, store.distinct_confirmations(tpl.id))
    res.template_secured = tpl.secured
    res.fields = apply_template(doc, tpl)
    _inject_library_fields(res.fields, supplier)

    # Semantischer Fallback (§7f, optional/aus per Default): fuer Felder, die
    # das Template nicht oder nur schwach liefert, fragt ein lokales LLM den
    # Volltext -- das Template wird dabei als Hinweis mitgegeben, nicht als
    # Zwang. Konfidenz ist ueber config.llm().max_confidence gedeckelt, kann
    # also nie allein auf 'auto' hochfahren (siehe CONF_MIN unten) -- deshalb
    # kann ``use_llm=False`` (z.B. fuer eine reine Listenuebersicht ueber
    # viele Dateien) den Fallback ueberspringen, ohne res.status zu aendern.
    gaps = [
        name for name in STANDARD_FIELDS
        if res.fields.get(name) is None or res.fields[name].source == "missing"
        or res.fields[name].confidence < CONF_MIN
    ]
    if gaps and use_llm:
        hints = {n: r.raw for n, r in res.fields.items() if r.source != "missing"}
        for name, fr in semantic.extract(doc.text, gaps, hints, res.llm_rejected).items():
            cur = res.fields.get(name)
            if cur is None or cur.source == "missing" or fr.confidence > cur.confidence:
                res.fields[name] = fr

    # Manuelle Korrekturen fuer genau diese Rechnung (sha256) gewinnen immer.
    machine = dict(res.fields)
    corrections = store.corrections(doc.sha256)
    _apply_corrections(res.fields, corrections, {f.name: f.value_type for f in tpl.fields})

    if tpl.line_items:
        res.line_items = apply_line_items(doc, tpl.line_items)
        _suggest_categories(res.line_items, tpl, store)

    res.tax_breakdown = _tax_breakdown(res.fields, res.line_items)

    res.checks = _all_checks(res.fields, supplier, store, res.line_items, doc, [addr_check])
    res.shadow = shadow.decide(
        machine, _all_checks(machine, supplier, store, res.line_items, doc, [addr_check])
        if corrections else res.checks, new_supplier=False)

    required = [f for f in tpl.fields if f.required]
    req_conf = [res.fields[f.name].confidence for f in required if f.name in res.fields]
    res.overall_confidence = round(min(req_conf), 2) if req_conf else 0.0

    checks_ok = all(c.ok is not False for c in res.checks)
    conf_ok = res.overall_confidence >= CONF_MIN and all(
        res.fields[f.name].source != "missing" for f in required if f.name in res.fields
    )
    res.status = AUTO if (tpl.secured and checks_ok and conf_ok) else REVIEW
    _apply_hold(res)
    return res


def scan_folder(folder: str | Path, store: Store, guard=None) -> list[Result]:
    """Alle Rechnungen eines Ordners verarbeiten -- jede mit Zeitgrenze
    (guard.py), damit eine haengende Rechnung die Schlange nicht blockiert."""
    from .guard import Guard

    folder = Path(folder)
    own = guard is None
    guard = guard or Guard(store.root)
    try:
        return [guard.process(p) for p in invoice_files(folder)]
    finally:
        if own:
            guard.close()


def invoice_files(folder: str | Path) -> list[Path]:
    """Alle Rechnungsdateien eines Ordners: PDFs und E-Rechnungs-XML."""
    folder = Path(folder)
    return sorted(p for p in folder.iterdir()
                  if p.is_file() and p.suffix.lower() in INVOICE_SUFFIXES)


def _fv(fields: dict, key: str):
    fr = fields.get(key)
    return fr.value if fr is not None else None


def finalize(res: Result, store: Store, *, operator: str | None = None,
             to_jobrouter: bool = True) -> dict:
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
    """
    if not store.is_confirmed(res.sha256):
        return {"finalized": False, "error": "not_confirmed", "status": res.status}

    op = operator or _operator()
    payload = res.to_dict()
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
            "rechnungsnummer": _fv(res.fields, "rechnungsnummer"),
            "rechnungsdatum": _fv(res.fields, "rechnungsdatum"),
            "netto": _fv(res.fields, "netto"),
            "ust_betrag": _fv(res.fields, "ust_betrag"),
            "brutto": _fv(res.fields, "brutto"),
            "tax_breakdown": res.tax_breakdown,
            "line_items": res.line_items,
        })
        store.audit("finalize", {
            "sha256": res.sha256, "status": res.status,
            "template_id": res.template_id,
            "rechnungsnummer": _fv(res.fields, "rechnungsnummer"),
        }, op)

    out = {k: v.value for k, v in res.fields.items()}
    out["_positionen"] = res.line_items
    out["_ust_aufstellung"] = res.tax_breakdown
    if res.billing_address_name:
        out["rechnungsadresse"] = res.billing_address_name
    store.write_output(Path(res.file).stem, out)

    queued = None
    if to_jobrouter:
        cfg = _jr_cfg()
        if (not cfg.allow_status) or res.status in cfg.allow_status:
            q = jobrouter.enqueue(store.root, payload)
            queued = str(q) if q else None
    return {"finalized": True, "already": already, "queued": queued,
            "status": res.status}


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
