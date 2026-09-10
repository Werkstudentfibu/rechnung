"""Rechnungsadressen: die eigenen Firmen-/Standort-Anschriften, an die Lieferanten
fakturieren. Dienen als Routing-Schluessel (JobRouter) und als Template-Variante
(derselbe Lieferant kann je Empfaenger ein anderes Layout schicken)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdf import Document
from .textnorm import norm


@dataclass
class BillingAddress:
    id: str
    name: str
    lines: list[str] = field(default_factory=list)        # Anschrift zur Anzeige
    match_terms: list[str] = field(default_factory=list)  # Text, der im Empfaengerblock steht
    cost_center: str = ""                                 # Kostenstelle / Buchungskreis
    company_code: str = ""                                # Mandant / Firmennummer
    notes: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "lines": self.lines,
                "match_terms": self.match_terms, "cost_center": self.cost_center,
                "company_code": self.company_code, "notes": self.notes}

    @classmethod
    def from_dict(cls, d: dict) -> "BillingAddress":
        return cls(id=d["id"], name=d.get("name", ""), lines=d.get("lines", []),
                   match_terms=d.get("match_terms", []),
                   cost_center=d.get("cost_center", ""),
                   company_code=d.get("company_code", ""),
                   notes=d.get("notes", ""))


def detect(doc: Document, addresses: list[BillingAddress], min_hits: int = 1):
    """-> (BillingAddress, score) oder None. score = Anteil getroffener match_terms."""
    text_n = norm(doc.text)
    best = None
    for a in addresses:
        terms = [norm(t) for t in a.match_terms if t.strip()]
        if not terms:
            continue
        hits = sum(1 for t in terms if t and t in text_n)
        score = hits / len(terms)
        if hits >= min_hits and (best is None or score > best[1]):
            best = (a, round(score, 3))
    return best


def suggest_terms(doc: Document, top_fraction: float = 0.45) -> list[str]:
    """Empfaengerblock grob raten: Zeilen im oberen linken Viertel der ersten Seite."""
    if not doc.pages:
        return []
    p = doc.pages[0]
    rows: dict[int, list] = {}
    for w in p.words:
        if w.top <= p.height * top_fraction and w.x0 <= p.width * 0.55:
            rows.setdefault(round(w.top / 3), []).append(w)
    out = []
    for _, ws in sorted(rows.items()):
        ws.sort(key=lambda w: w.x0)
        line = " ".join(w.text for w in ws).strip()
        if len(line) >= 3 and not re.fullmatch(r"[\d.,:/\- ]+", line):
            out.append(line)
    return out[:6]
