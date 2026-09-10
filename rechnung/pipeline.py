"""Orchestrierung: Lieferant erkennen -> Template anwenden -> validieren -> Status."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import pdf as pdfmod
from .identify import identify
from .store import Store
from .template import FieldResult, Template, apply_template
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
    template_secured: bool = False
    fields: dict[str, FieldResult] = field(default_factory=dict)
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
            "template_secured": self.template_secured,
            "status": self.status,
            "overall_confidence": self.overall_confidence,
            "fields": {
                k: {"value": v.value, "raw": v.raw, "confidence": v.confidence,
                    "source": v.source}
                for k, v in self.fields.items()
            },
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }


def _pick_template(doc, templates: list[Template]) -> Template | None:
    """Bei mehreren Varianten: die mit den meisten auffindbaren Ankern."""
    if not templates:
        return None
    if len(templates) == 1:
        return templates[0]
    scored = []
    for t in templates:
        res = apply_template(doc, t)
        found = sum(1 for r in res.values() if r.source != "missing")
        scored.append((found, t.secured, t))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return scored[0][2]


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

    tpl = _pick_template(doc, store.templates_for(supplier.id))
    if tpl is None:
        res.status = NO_TEMPLATE
        return res

    res.template_id = tpl.id
    res.template_secured = tpl.secured
    res.fields = apply_template(doc, tpl)
    res.checks = run_all(res.fields, supplier.id, store.seen_invoice)

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
