"""Orchestrierung: Lieferant erkennen -> Template anwenden -> validieren -> Status."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import billing
from . import jobrouter
from . import pdf as pdfmod
from .config import jobrouter as _jr_cfg
from .config import operator as _operator
from .identify import FIXED_FIELD_TYPES, cross_check, identify
from .store import Store
from .template import (STANDARD_FIELDS, FieldResult, Template, apply_line_items,
                       apply_template)
from .textnorm import coerce
from .validate import Check, run_all

# Status
AUTO = "auto"                    # kann automatisch weitergeleitet werden
REVIEW = "review"                # Template da, aber Konfidenz/Prüfung schwach
NO_TEMPLATE = "no_template"      # Lieferant bekannt, kein Template -> anlernen
UNKNOWN = "unknown_supplier"    # Lieferant unbekannt -> anlernen
SCAN = "scan_no_textlayer"      # kein Textlayer -> OCR nötig (v1 nicht unterstützt)

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

    def to_dict(self) -> dict:
        return {
            "file": self.file,
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
            "overall_confidence": self.overall_confidence,
            "line_items": self.line_items,
            "tax_breakdown": self.tax_breakdown,
            "fields": {
                k: {"value": v.value, "raw": v.raw, "confidence": v.confidence,
                    "source": v.source}
                for k, v in self.fields.items()
            },
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
                              or it.get("gesamt"))
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


def process(path: str | Path, store: Store) -> Result:
    path = Path(path)
    if not pdfmod.has_text_layer(path):
        return Result(file=str(path), status=SCAN)

    doc = pdfmod.load(path)
    res = Result(file=str(path), sha256=doc.sha256)

    match = identify(doc, store.suppliers())
    if not match:
        res.status = UNKNOWN
        return res

    supplier, score = match
    res.supplier_id = supplier.id
    res.supplier_name = supplier.name
    res.match_score = score

    addr = billing.detect(doc, store.addresses())
    if addr:
        res.billing_address_id = addr[0].id
        res.billing_address_name = addr[0].name

    tpl = _pick_template(doc, store.templates_for(supplier.id), res.billing_address_id)
    if tpl is None:
        res.status = NO_TEMPLATE
        return res

    res.template_id = tpl.id
    res.template_variant = tpl.variant
    tpl.confirmations = max(tpl.confirmations, store.distinct_confirmations(tpl.id))
    res.template_secured = tpl.secured
    res.fields = apply_template(doc, tpl)

    # Feste Werte aus den Stammdaten einmischen: nur wo das Template nichts
    # (Sicheres) liefert. Quelle wird als "library" markiert.
    for fname, fval in (supplier.fixed_fields or {}).items():
        if not str(fval).strip():
            continue
        cur = res.fields.get(fname)
        if cur is not None and cur.source != "missing" and cur.raw:
            continue
        vtype = FIXED_FIELD_TYPES.get(fname, "text")
        value, ok = coerce(str(fval), vtype)
        res.fields[fname] = FieldResult(fname, value, str(fval),
                                        0.99 if ok else 0.6, "library")

    # Manuelle Korrekturen fuer genau diese Rechnung (sha256) gewinnen immer.
    _type_map = {f.name: f.value_type for f in tpl.fields}
    for fname, fval in store.corrections(doc.sha256).items():
        vtype = _type_map.get(fname) or (STANDARD_FIELDS.get(fname, ("text",))[0])
        value, ok = coerce(str(fval), vtype)
        res.fields[fname] = FieldResult(fname, value if ok else fval, str(fval),
                                        1.0, "manual")

    if tpl.line_items:
        res.line_items = apply_line_items(doc, tpl.line_items)

    res.tax_breakdown = _tax_breakdown(res.fields, res.line_items)

    res.checks = run_all(res.fields, supplier, store.seen_invoice, res.line_items)
    # cross_check liest IBAN/USt-IdNr direkt aus dem PDF-Text (nicht nur aus dem
    # extrahierten Feld). Pro Namen genau einen Check behalten: die entschiedene
    # Aussage schlaegt das "nicht pruefbar" von run_all.
    for cc in cross_check(doc, supplier):
        res.checks = [c for c in res.checks
                      if not (c.name == cc["kind"] and c.ok is None)]
        if not any(c.name == cc["kind"] for c in res.checks):
            res.checks.append(Check(cc["kind"], cc["ok"], cc["detail"]))

    required = [f for f in tpl.fields if f.required]
    req_conf = [res.fields[f.name].confidence for f in required if f.name in res.fields]
    res.overall_confidence = round(min(req_conf), 2) if req_conf else 0.0

    checks_ok = all(c.ok is not False for c in res.checks)
    conf_ok = res.overall_confidence >= CONF_MIN and all(
        res.fields[f.name].source != "missing" for f in required if f.name in res.fields
    )
    res.status = AUTO if (tpl.secured and checks_ok and conf_ok) else REVIEW
    return res


def scan_folder(folder: str | Path, store: Store) -> list[Result]:
    folder = Path(folder)
    return [process(p, store) for p in sorted(folder.glob("*.pdf"))]


def _fv(fields: dict, key: str):
    fr = fields.get(key)
    return fr.value if fr is not None else None


def finalize(res: Result, store: Store, *, operator: str | None = None,
             to_jobrouter: bool = True) -> dict:
    """Eine geprüfte Rechnung in die Bücher übernehmen.

    1. Original-PDF unverändert archivieren (``data/archive/<sha>.pdf`` – GoBD).
    2. Audit-Eintrag (wer / wann / was) in ``data/audit.jsonl``.
    3. Journal-Datensatz in ``data/processed.jsonl`` (append-only).
    4. Ergebnis als JSON/CSV nach ``data/out/``.
    5. Für die JobRouter-Übergabe in ``data/queue/`` einreihen, sofern der
       Status in ``RECHNUNG_JOBROUTER_ALLOW_STATUS`` liegt.

    Idempotent über ``sha256`` + Template: mehrfacher Aufruf zählt weder im
    Journal doppelt noch schickt er zweimal an JobRouter.
    """
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


# Steuersaetze, die als eigene Spalten im Journal auftauchen
JOURNAL_RATES = [19.0, 7.0, 0.0]


def _round_rate(r) -> float | None:
    v = _to_amount(r)
    return round(v) * 1.0 if v is not None else None


def journal_rows(store: Store) -> list[dict]:
    """Buchungsjournal: eine Zeile je bestaetigter Rechnung, Netto/USt je Satz
    in eigenen Spalten."""
    # Nach sha256 deduplizieren (append-only Log kann eine Rechnung mehrfach
    # enthalten, wenn sie nachträglich korrigiert/erneut übernommen wurde –
    # der jüngste Eintrag gewinnt, Position bleibt).
    dedup: dict[str, dict] = {}
    for i, r in enumerate(store.processed()):
        if not r.get("rechnungsnummer"):
            continue
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
        for rate in JOURNAL_RATES:
            s = by_rate.get(rate, {})
            row[f"netto_{int(rate)}"] = s.get("netto")
            row[f"ust_{int(rate)}"] = s.get("ust")
        extra = sorted(k for k in by_rate if k not in JOURNAL_RATES)
        row["weitere_saetze"] = ", ".join(
            f"{int(k)}%: {by_rate[k]['netto']:.2f}/{by_rate[k]['ust']:.2f}"
            for k in extra
        )
        dedup[r.get("sha256") or f"__{i}"] = row
    return list(dedup.values())


def journal_totals(rows: list[dict]) -> dict:
    tot: dict = {"rechnungen": len(rows)}
    keys = ["netto_gesamt", "ust_gesamt", "brutto_gesamt"]
    keys += [f"netto_{int(r)}" for r in JOURNAL_RATES]
    keys += [f"ust_{int(r)}" for r in JOURNAL_RATES]
    for k in keys:
        tot[k] = round(sum(x[k] for x in rows if x.get(k) is not None), 2)
    return tot
