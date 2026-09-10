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

from .. import pdf as pdfmod
from ..identify import identify, supplier_from_doc
from ..pipeline import process
from ..store import Store
from ..template import (STANDARD_FIELDS, Template, apply_template, build_field_spec)

RENDER_SCALE = 2.0
_STATIC = Path(__file__).parent / "static"


class FieldAssign(BaseModel):
    name: str
    value_type: str
    required: bool = True
    anchor_word: int | None = None      # Index in words der Seite
    value_words: list[int]
    page: int = 0


class TemplatePost(BaseModel):
    file: str
    supplier_id: str
    supplier_name: str
    variant: str = "standard"
    fields: list[FieldAssign]


class ConfirmPost(BaseModel):
    file: str
    template_id: str


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
        current = {}
        tid = None
        if match:
            sup, _ = match
            tpls = store.templates_for(sup.id)
            if tpls:
                tid = tpls[0].id
                current = {k: {"value": v.value, "raw": v.raw, "confidence": v.confidence}
                           for k, v in apply_template(doc, tpls[0]).items()}
        return JSONResponse({
            "file": path.name,
            "pages": pages,
            "match": ({"id": match[0].id, "name": match[0].name, "score": match[1]}
                      if match else None),
            "identifiers": suggestion,
            "template_id": tid,
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
        store.save_template(tpl)
        return JSONResponse({"template_id": tid, "fields": len(specs),
                             "extraction": {k: {"value": v.value, "confidence": v.confidence}
                                            for k, v in apply_template(doc, tpl).items()}})

    @app.post("/api/confirm")
    def confirm(body: ConfirmPost) -> JSONResponse:
        tpl = store.template(body.template_id)
        if tpl is None:
            raise HTTPException(404, "Template nicht gefunden")
        path = _resolve(body.file)
        doc = pdfmod.load(path)
        fields = apply_template(doc, tpl)
        tpl.confirmations += 1
        store.save_template(tpl)

        rn = fields.get("rechnungsnummer")
        store.mark_processed({
            "file": path.name, "sha256": doc.sha256,
            "supplier_id": tpl.supplier_id, "template_id": tpl.id,
            "rechnungsnummer": (rn.value if rn else None),
        })
        store.write_output(path.stem, {k: v.value for k, v in fields.items()})
        return JSONResponse({"confirmations": tpl.confirmations, "secured": tpl.secured})

    if _STATIC.exists():
        app.mount("/static", StaticFiles(directory=_STATIC), name="static")
    return app
