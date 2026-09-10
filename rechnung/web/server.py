"""Lokale Training-UI: PDF ansehen, Woerter anklicken, Template lernen/bestaetigen.

Laeuft nur auf 127.0.0.1. Keine Auth (Einzelplatz-Werkzeug).
"""

from __future__ import annotations

import io
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import billing
from .. import pdf as pdfmod
from ..billing import BillingAddress
from ..identify import FIXED_FIELD_TYPES, Supplier, identify, supplier_from_doc
from ..pipeline import journal_rows, journal_totals, process
from ..store import Store
from ..template import (STANDARD_FIELDS, Anchor, Column, LineItemSpec, Template,
                        apply_line_items, apply_template, build_field_spec,
                        build_line_item_spec)

RENDER_SCALE = 2.0
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


class ConfirmPost(BaseModel):
    file: str
    template_id: str


class CorrectionPost(BaseModel):
    file: str
    field: str
    value: str


class SupplierPost(BaseModel):
    id: str
    name: str = ""
    ustids: list[str] = []
    ibans: list[str] = []
    domains: list[str] = []
    address_lines: list[str] = []
    bank_name: str = ""
    bic: str = ""
    creditor_id: str = ""
    contact: str = ""
    default_payment_terms: str = ""
    default_tax_rate: str = ""
    notes: str = ""
    fixed_fields: dict[str, str] = {}


class AddressPost(BaseModel):
    id: str
    name: str = ""
    lines: list[str] = []
    match_terms: list[str] = []
    cost_center: str = ""
    company_code: str = ""
    notes: str = ""


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

    def _resolve(file: str) -> Path:
        p = (samples / Path(file).name).resolve()
        if not p.exists() or samples.resolve() not in p.parents:
            raise HTTPException(404, "PDF nicht gefunden")
        return p

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC / "index.html").read_text("utf-8")

    @app.get("/api/scan")
    def scan() -> JSONResponse:
        out = []
        for p in sorted(samples.glob("*.pdf")):
            r = process(p, store)
            out.append(r.to_dict())
        return JSONResponse({
            "results": out,
            "fields": {k: {"value_type": v[0], "required": v[1]}
                       for k, v in STANDARD_FIELDS.items()},
        })

    @app.get("/api/invoice")
    def invoice(file: str) -> JSONResponse:
        path = _resolve(file)
        doc = pdfmod.load(path)
        pages = [
            {
                "index": pg.index,
                "width": pg.width,
                "height": pg.height,
                "scale": RENDER_SCALE,
                "image": f"/api/page.png?file={path.name}&page={pg.index}",
                "words": [
                    {"i": i, "text": w.text, "x0": w.x0, "x1": w.x1,
                     "top": w.top, "bottom": w.bottom}
                    for i, w in enumerate(pg.words)
                ],
            }
            for pg in doc.pages
        ]
        match = identify(doc, store.suppliers())
        suggestion = supplier_from_doc(doc, "", "").to_dict()
        addr_match = billing.detect(doc, store.addresses())
        current = {}
        tid = None
        variant = "standard"
        tpl_fields: list[dict] = []
        tpl_line_items = None
        line_items: list[dict] = []
        supplier_obj = None
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
                # Mix in fixed fields
                for fname, fval in (supplier_obj.fixed_fields or {}).items():
                    if not str(fval).strip():
                        continue
                    if fname in current and current[fname]["source"] != "missing" and current[fname]["raw"]:
                        continue
                    current[fname] = {"value": fval, "raw": str(fval), "confidence": 0.99, "source": "library"}
                # Mix in corrections
                for fname, fval in store.corrections(doc.sha256).items():
                    current[fname] = {"value": fval, "raw": str(fval), "confidence": 1.0, "source": "manual"}
                tpl_fields = [{"name": f.name, "value_type": f.value_type,
                               "required": f.required} for f in chosen.fields]
                if chosen.line_items:
                    tpl_line_items = chosen.line_items.to_dict()
                    line_items = apply_line_items(doc, chosen.line_items)
        return JSONResponse({
            "file": path.name,
            "pages": pages,
            "match": ({"id": match[0].id, "name": match[0].name, "score": match[1]}
                      if match else None),
            "identifiers": suggestion,
            "template_id": tid,
            "variant": variant,
            "template_fields": tpl_fields,
            "template_line_items": tpl_line_items,
            "line_items": line_items,
            "standard_fields": {k: {"value_type": v[0], "required": v[1]}
                                for k, v in STANDARD_FIELDS.items()},
            "fixed_field_types": FIXED_FIELD_TYPES,
            "supplier": supplier_obj.to_dict() if supplier_obj else None,
            "addresses": [a.to_dict() for a in store.addresses()],
            "detected_address": ({"id": addr_match[0].id, "name": addr_match[0].name,
                                  "score": addr_match[1]} if addr_match else None),
            "suggested_terms": billing.suggest_terms(doc),
            "current": current,
        })

    @app.get("/api/page.png")
    def page_png(file: str, page: int = 0) -> Response:
        path = _resolve(file)
        return Response(_render_png(path, page), media_type="image/png")

    @app.post("/api/template")
    def save_template(body: TemplatePost) -> JSONResponse:
        path = _resolve(body.file)
        doc = pdfmod.load(path)
        pages_words = [pg.words for pg in doc.pages]

        sup = store.supplier(body.supplier_id)
        if sup is None:
            sup = supplier_from_doc(doc, body.supplier_id, body.supplier_name)
        else:
            fresh = supplier_from_doc(doc, sup.id, body.supplier_name or sup.name)
            sup.ustids = sorted(set(sup.ustids) | set(fresh.ustids))
            sup.ibans = sorted(set(sup.ibans) | set(fresh.ibans))
            sup.domains = sorted(set(sup.domains) | set(fresh.domains))
            sup.fingerprint = sup.fingerprint or fresh.fingerprint
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
        tpl.fields = specs

        if body.line_items and body.line_items.columns:
            li = body.line_items
            words = pages_words[li.page]
            header_cols = [(words[c.header_word], c.name, c.value_type)
                           for c in li.columns]
            stop = [words[i] for i in li.stop_words]
            tpl.line_items = build_line_item_spec(header_cols, stop,
                                                  doc.pages[li.page].width)
        elif body.line_items is not None:
            tpl.line_items = None

        store.save_template(tpl)
        items = apply_line_items(doc, tpl.line_items) if tpl.line_items else []
        return JSONResponse({"template_id": tid, "fields": len(specs),
                             "line_items": items,
                             "extraction": {k: {"value": v.value, "confidence": v.confidence}
                                            for k, v in apply_template(doc, tpl).items()}})

    @app.post("/api/confirm")
    def confirm(body: ConfirmPost) -> JSONResponse:
        tpl = store.template(body.template_id)
        if tpl is None:
            raise HTTPException(404, "Template nicht gefunden")
        path = _resolve(body.file)
        doc = pdfmod.load(path)
        tpl.confirmations += 1
        store.save_template(tpl)

        # Volle Pipeline -> feste Werte, Positionen, USt-Aufstellung, Pruefungen
        res = process(path, store)
        fields = res.fields
        rn = fields.get("rechnungsnummer")
        store.mark_processed({
            "file": path.name, "sha256": doc.sha256,
            "supplier_id": tpl.supplier_id, "supplier_name": res.supplier_name,
            "template_id": tpl.id, "variant": tpl.variant,
            "billing_address_id": res.billing_address_id,
            "billing_address_name": res.billing_address_name,
            "rechnungsnummer": (rn.value if rn else None),
            "rechnungsdatum": (fields["rechnungsdatum"].value
                               if "rechnungsdatum" in fields else None),
            "netto": (fields["netto"].value if "netto" in fields else None),
            "ust_betrag": (fields["ust_betrag"].value
                           if "ust_betrag" in fields else None),
            "brutto": (fields["brutto"].value if "brutto" in fields else None),
            "tax_breakdown": res.tax_breakdown,
            "line_items": res.line_items,
        })
        out = {k: v.value for k, v in fields.items()}
        out["_positionen"] = res.line_items
        out["_ust_aufstellung"] = res.tax_breakdown
        if tpl.variant and tpl.variant != "standard":
            addr = store.address(tpl.variant)
            out["rechnungsadresse"] = addr.name if addr else tpl.variant
        store.write_output(path.stem, out)
        return JSONResponse({"confirmations": tpl.confirmations, "secured": tpl.secured})

    @app.post("/api/correction")
    def save_correction_ep(body: CorrectionPost) -> JSONResponse:
        from ..config import operator
        path = _resolve(body.file)
        doc = pdfmod.load(path)
        store.save_correction(doc.sha256, body.field, body.value, operator())
        return JSONResponse({"ok": True})

    # ---- Bibliothek: Lieferanten ------------------------------------
    @app.get("/api/suppliers")
    def list_suppliers() -> JSONResponse:
        return JSONResponse([
            {**s.to_dict(),
             "templates": [{"id": t.id, "variant": t.variant,
                            "confirmations": t.confirmations, "secured": t.secured,
                            "fields": [f.name for f in t.fields]}
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
            address_lines=[x for x in body.address_lines if x.strip()],
            bank_name=body.bank_name.strip(), bic=body.bic.strip().upper(),
            creditor_id=body.creditor_id.strip(), contact=body.contact.strip(),
            default_payment_terms=body.default_payment_terms.strip(),
            default_tax_rate=body.default_tax_rate.strip(),
            notes=body.notes.strip(),
            fixed_fields=fixed,
        ))
        return JSONResponse({"ok": True})

    @app.delete("/api/suppliers/{supplier_id}")
    def delete_supplier_ep(supplier_id: str) -> JSONResponse:
        store.delete_supplier(supplier_id)
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
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=journal.csv"})

    if _STATIC.exists():
        app.mount("/static", StaticFiles(directory=_STATIC), name="static")
    return app
