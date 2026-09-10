"""Template-Modell: pro Feld ein Anker (angeklicktes Label) + relative Region.

Anker-relativ ist robuster als absolute Koordinaten, weil kleine Layout-
Verschiebungen (andere Adresszeile, längerer Betreff) so aufgefangen werden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .pdf import Document, Word
from .textnorm import coerce, norm

# Feldtypen: name -> value_type, required
STANDARD_FIELDS: dict[str, tuple[str, bool]] = {
    "rechnungsnummer": ("number", True),
    "rechnungsdatum": ("date", True),
    "netto": ("amount", True),
    "ust_betrag": ("amount", True),
    "ust_satz": ("amount", False),
    "brutto": ("amount", True),
    "ustid": ("ustid", False),
    "iban": ("iban", False),
    "bestellnummer": ("number", False),
    "zahlungsziel": ("date", False),
}


@dataclass
class Anchor:
    text: str          # norm() des angeklickten Wortes
    page: int
    occurrence: int = 0


@dataclass
class FieldSpec:
    name: str
    value_type: str = "text"
    required: bool = True
    anchor: Optional[Anchor] = None
    rel: Optional[tuple[float, float, float, float]] = None      # relativ zu anchor.x0/top
    abs_frac: Optional[tuple[float, float, float, float]] = None  # Seitenanteile, wenn kein Anker
    abs_page: int = 0


@dataclass
class Template:
    id: str
    supplier_id: str
    variant: str = "standard"
    fields: list[FieldSpec] = field(default_factory=list)
    confirmations: int = 0
    secured_at: int = 3

    @property
    def secured(self) -> bool:
        return self.confirmations >= self.secured_at

    # ---- Serialisierung -------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "supplier_id": self.supplier_id,
            "variant": self.variant,
            "confirmations": self.confirmations,
            "secured_at": self.secured_at,
            "fields": [
                {
                    "name": f.name,
                    "value_type": f.value_type,
                    "required": f.required,
                    "anchor": (
                        {"text": f.anchor.text, "page": f.anchor.page,
                         "occurrence": f.anchor.occurrence}
                        if f.anchor else None
                    ),
                    "rel": list(f.rel) if f.rel else None,
                    "abs_frac": list(f.abs_frac) if f.abs_frac else None,
                    "abs_page": f.abs_page,
                }
                for f in self.fields
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Template":
        fields = []
        for f in d.get("fields", []):
            a = f.get("anchor")
            fields.append(FieldSpec(
                name=f["name"],
                value_type=f.get("value_type", "text"),
                required=f.get("required", True),
                anchor=Anchor(a["text"], a["page"], a.get("occurrence", 0)) if a else None,
                rel=tuple(f["rel"]) if f.get("rel") else None,
                abs_frac=tuple(f["abs_frac"]) if f.get("abs_frac") else None,
                abs_page=f.get("abs_page", 0),
            ))
        return cls(
            id=d["id"], supplier_id=d["supplier_id"], variant=d.get("variant", "standard"),
            fields=fields, confirmations=d.get("confirmations", 0),
            secured_at=d.get("secured_at", 3),
        )


@dataclass
class FieldResult:
    name: str
    value: object
    raw: str
    confidence: float
    source: str  # template-anchor | template-abs | missing


def _find_anchor(words: list[Word], anchor: Anchor) -> Optional[Word]:
    exact = [w for w in words if w.page == anchor.page and norm(w.text) == anchor.text]
    matches = exact or [
        w for w in words if w.page == anchor.page and anchor.text and anchor.text in norm(w.text)
    ]
    matches.sort(key=lambda w: (round(w.top, 1), w.x0))
    if not matches:
        return None
    return matches[min(anchor.occurrence, len(matches) - 1)]


def _words_in_region(words, x0, y0, x1, y1, page) -> list[Word]:
    hit = [w for w in words if w.page == page and x0 <= w.cx <= x1 and y0 <= w.cy <= y1]
    hit.sort(key=lambda w: (round(w.top, 1), w.x0))
    return hit


def apply_template(doc: Document, tpl: Template) -> dict[str, FieldResult]:
    words = doc.all_words()
    out: dict[str, FieldResult] = {}
    for fs in tpl.fields:
        raw, conf, source = "", 0.0, "missing"
        if fs.anchor and fs.rel:
            aw = _find_anchor(words, fs.anchor)
            if aw:
                dx0, dy0, dx1, dy1 = fs.rel
                region = _words_in_region(
                    words, aw.x0 + dx0, aw.top + dy0, aw.x0 + dx1, aw.top + dy1, aw.page
                )
                raw = " ".join(w.text for w in region)
                source = "template-anchor"
                conf = 0.92 if region else 0.25
            else:
                conf = 0.1
        elif fs.abs_frac:
            p = doc.pages[fs.abs_page] if fs.abs_page < len(doc.pages) else doc.pages[0]
            x0, y0, x1, y1 = fs.abs_frac
            region = _words_in_region(
                words, x0 * p.width, y0 * p.height, x1 * p.width, y1 * p.height, fs.abs_page
            )
            raw = " ".join(w.text for w in region)
            source = "template-abs"
            conf = 0.78 if region else 0.15

        value, ok = coerce(raw, fs.value_type)
        if raw and not ok:
            conf *= 0.5
        out[fs.name] = FieldResult(fs.name, value, raw, round(conf, 2),
                                   source if raw else "missing")
    return out


def build_field_spec(name: str, value_type: str, required: bool,
                     anchor_word: Optional[Word], value_words: list[Word],
                     page_w: float, page_h: float, pad: float = 4.0) -> FieldSpec:
    """Aus den in der UI angeklickten Wörtern eine FieldSpec ableiten."""
    vx0 = min(w.x0 for w in value_words)
    vy0 = min(w.top for w in value_words)
    vx1 = max(w.x1 for w in value_words)
    vy1 = max(w.bottom for w in value_words)
    if anchor_word is not None:
        return FieldSpec(
            name=name, value_type=value_type, required=required,
            anchor=Anchor(norm(anchor_word.text), anchor_word.page),
            rel=(vx0 - anchor_word.x0 - pad, vy0 - anchor_word.top - pad,
                 vx1 - anchor_word.x0 + pad, vy1 - anchor_word.top + pad),
        )
    page = value_words[0].page
    return FieldSpec(
        name=name, value_type=value_type, required=required,
        abs_frac=((vx0 - pad) / page_w, (vy0 - pad) / page_h,
                  (vx1 + pad) / page_w, (vy1 + pad) / page_h),
        abs_page=page,
    )
