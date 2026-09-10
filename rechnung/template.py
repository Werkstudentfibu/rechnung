"""Template-Modell: pro Feld ein Anker (angeklicktes Label) + relative Region.

Anker-relativ ist robuster als absolute Koordinaten, weil kleine Layout-
Verschiebungen (andere Adresszeile, laengerer Betreff) so aufgefangen werden.

Zusaetzlich:
- ein Feld vom Typ ``taxblock`` fasst den USt-Aufstellungs-Block als
  Liste ``{satz, netto, ust}`` (Parser in textnorm.parse_tax_summary).
- ``Template.line_items`` beschreibt die Positionstabelle (Kopf-Anker,
  Stopp-Woerter, Spalten als Seitenanteile).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .pdf import Document, Word
from .textnorm import coerce, norm

# Vorschlagsliste fuer die UI (keine Obergrenze!): name -> value_type, required
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
    "ust_aufstellung": ("taxblock", False),
    "leistungszeitraum": ("text", False),
    "kostenstelle": ("text", False),
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
class Column:
    name: str
    value_type: str = "text"
    x0: float = 0.0    # linke Kante als Seitenanteil 0..1
    x1: float = 1.0    # rechte Kante als Seitenanteil 0..1


@dataclass
class LineItemSpec:
    header_anchor: Anchor              # ein Wort aus der Kopfzeile der Tabelle
    columns: list[Column] = field(default_factory=list)
    stop_texts: list[str] = field(default_factory=list)  # norm() Woerter -> Tabellenende
    row_gap: float = 1.8              # Faktor Kopfzeilen-Hoehe fuer Zeilen-Cluster

    def to_dict(self) -> dict:
        return {
            "header_anchor": {"text": self.header_anchor.text,
                              "page": self.header_anchor.page,
                              "occurrence": self.header_anchor.occurrence},
            "columns": [{"name": c.name, "value_type": c.value_type,
                         "x0": c.x0, "x1": c.x1} for c in self.columns],
            "stop_texts": self.stop_texts,
            "row_gap": self.row_gap,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LineItemSpec":
        a = d["header_anchor"]
        return cls(
            header_anchor=Anchor(a["text"], a.get("page", 0), a.get("occurrence", 0)),
            columns=[Column(c["name"], c.get("value_type", "text"),
                            c.get("x0", 0.0), c.get("x1", 1.0))
                     for c in d.get("columns", [])],
            stop_texts=d.get("stop_texts", []),
            row_gap=d.get("row_gap", 1.8),
        )


@dataclass
class Template:
    id: str
    supplier_id: str
    variant: str = "standard"
    fields: list[FieldSpec] = field(default_factory=list)
    line_items: Optional[LineItemSpec] = None
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
            "line_items": self.line_items.to_dict() if self.line_items else None,
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
        li = d.get("line_items")
        return cls(
            id=d["id"], supplier_id=d["supplier_id"], variant=d.get("variant", "standard"),
            fields=fields,
            line_items=LineItemSpec.from_dict(li) if li else None,
            confirmations=d.get("confirmations", 0),
            secured_at=d.get("secured_at", 3),
        )


@dataclass
class FieldResult:
    name: str
    value: object
    raw: str
    confidence: float
    source: str  # template-anchor | template-abs | library | missing


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
    """Wortmitte innerhalb der Region."""
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


def apply_line_items(doc: Document, spec: LineItemSpec) -> list[dict]:
    """Positionstabelle auslesen: Kopf-Anker finden, Zeilen bis zum Stopp-Wort in
    y-Baender clustern, je Spalte die Woerter im x-Bereich zusammenfassen."""
    if spec is None or not spec.columns:
        return []
    words = doc.all_words()
    hdr = _find_anchor(words, spec.header_anchor)
    if hdr is None:
        return []
    page = hdr.page
    pw = doc.pages[page].width
    ph = doc.pages[page].height
    line_h = max(hdr.bottom - hdr.top, 6.0)
    top = hdr.bottom + line_h * 0.3

    stop_y = ph
    for w in words:
        if (w.page == page and w.cy > hdr.bottom
                and norm(w.text) in spec.stop_texts):
            stop_y = min(stop_y, w.top)

    band = [w for w in words if w.page == page and top <= w.cy < stop_y]
    band.sort(key=lambda w: (w.cy, w.x0))
    rows: list[list[Word]] = []
    cur: list[Word] = []
    anchor_y: Optional[float] = None
    for w in band:
        if anchor_y is None or abs(w.cy - anchor_y) <= line_h * spec.row_gap * 0.5:
            cur.append(w)
            anchor_y = w.cy if anchor_y is None else (anchor_y + w.cy) / 2
        else:
            rows.append(cur)
            cur = [w]
            anchor_y = w.cy
    if cur:
        rows.append(cur)

    out: list[dict] = []
    for r in rows:
        rec: dict = {}
        for col in spec.columns:
            cx0, cx1 = col.x0 * pw, col.x1 * pw
            cell = sorted((w for w in r if cx0 <= w.cx < cx1), key=lambda w: w.x0)
            val, _ = coerce(" ".join(w.text for w in cell), col.value_type)
            rec[col.name] = val
        if any(v not in (None, "", []) for v in rec.values()):
            out.append(rec)
    return out


def build_field_spec(name: str, value_type: str, required: bool,
                     anchor_word: Optional[Word], value_words: list[Word],
                     page_w: float, page_h: float, pad: float = 4.0) -> FieldSpec:
    """Aus den in der UI angeklickten Woertern eine FieldSpec ableiten."""
    vx0 = min(w.x0 for w in value_words)
    vy0 = min(w.top for w in value_words)
    vx1 = max(w.x1 for w in value_words)
    vy1 = max(w.bottom for w in value_words)
    # Wert kann bei anderen Rechnungen laenger/kuerzer sein als beim Anlernen:
    # Region horizontal grosszuegig aufweiten (rechts stark, links leicht fuer
    # rechtsbuendige Betraege), vertikal eng lassen. Beim taxblock den Block
    # vertikal weit lassen (mehrere Steuerzeilen).
    width = max(vx1 - vx0, 1.0)
    grow_r = max(pad, width * 2.0, 60.0)
    grow_l = max(pad, width * 0.5, 18.0)
    grow_down = (vy1 - vy0) + 40.0 if value_type == "taxblock" else pad
    if anchor_word is not None:
        return FieldSpec(
            name=name, value_type=value_type, required=required,
            anchor=Anchor(norm(anchor_word.text), anchor_word.page),
            rel=(vx0 - anchor_word.x0 - grow_l, vy0 - anchor_word.top - pad,
                 vx1 - anchor_word.x0 + grow_r, vy1 - anchor_word.top + grow_down),
        )
    page = value_words[0].page
    return FieldSpec(
        name=name, value_type=value_type, required=required,
        abs_frac=((vx0 - pad) / page_w, (vy0 - pad) / page_h,
                  (vx1 + pad) / page_w, (vy1 + grow_down) / page_h),
        abs_page=page,
    )


def build_line_item_spec(header_cols: list[tuple[Word, str, str]],
                         stop_words: list[Word], page_w: float) -> LineItemSpec:
    """header_cols: (Kopfwort, Spaltenname, value_type), von links nach rechts.
    Spalten-x-Bereiche werden bis zur Mitte zum Nachbarn aufgezogen."""
    items = sorted(header_cols, key=lambda t: t[0].x0)
    n = len(items)
    cols: list[Column] = []
    for i, (w, name, vtype) in enumerate(items):
        left = 0.0 if i == 0 else (items[i - 1][0].x1 + w.x0) / 2
        right = page_w if i == n - 1 else (w.x1 + items[i + 1][0].x0) / 2
        cols.append(Column(name=name, value_type=vtype,
                           x0=round(left / page_w, 4), x1=round(right / page_w, 4)))
    anchor_w = items[0][0]
    return LineItemSpec(
        header_anchor=Anchor(norm(anchor_w.text), anchor_w.page),
        columns=cols,
        stop_texts=sorted({norm(w.text) for w in stop_words if w.text.strip()}),
    )
