"""Lieferanten-Erkennung anhand stabiler Merkmale."""

from __future__ import annotations

from dataclasses import dataclass, field

from .pdf import Document
from .textnorm import find_domains, find_iban, find_ustids, norm


@dataclass
class Supplier:
    id: str
    name: str
    ustids: list[str] = field(default_factory=list)
    ibans: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    fingerprint: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "ustids": self.ustids,
            "ibans": self.ibans, "domains": self.domains,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Supplier":
        return cls(
            id=d["id"], name=d.get("name", ""), ustids=d.get("ustids", []),
            ibans=d.get("ibans", []), domains=d.get("domains", []),
            fingerprint=d.get("fingerprint", []),
        )


def _fingerprint(doc: Document, top_fraction: float = 0.35) -> set[str]:
    """Statische Tokens im Kopfbereich der ersten Seite."""
    if not doc.pages:
        return set()
    p = doc.pages[0]
    cutoff = p.height * top_fraction
    toks = {
        norm(w.text) for w in p.words
        if w.top <= cutoff and len(w.text) >= 4 and any(c.isalpha() for c in w.text)
    }
    return {t for t in toks if len(t) >= 4}


def extract_identifiers(doc: Document) -> dict:
    text = doc.text
    ibans = set()
    iban = find_iban(text)
    if iban:
        ibans.add(iban)
    return {
        "ustids": find_ustids(text),
        "ibans": ibans,
        "domains": find_domains(text),
        "fingerprint": _fingerprint(doc),
    }


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def identify(doc: Document, suppliers: list[Supplier], threshold: float = 0.5):
    """-> (Supplier, score) oder None."""
    ids = extract_identifiers(doc)
    text_n = norm(doc.text)
    best = None
    for s in suppliers:
        score = 0.0
        if ids["ustids"] & set(s.ustids):
            score += 0.6
        if ids["ibans"] & set(s.ibans):
            score += 0.5
        if ids["domains"] & set(s.domains):
            score += 0.3
        if s.name and norm(s.name) in text_n:
            score += 0.2
        score += 0.3 * _jaccard(ids["fingerprint"], set(s.fingerprint))
        if best is None or score > best[1]:
            best = (s, round(score, 3))
    if best and best[1] >= threshold:
        return best
    return None


def supplier_from_doc(doc: Document, supplier_id: str, name: str) -> Supplier:
    ids = extract_identifiers(doc)
    return Supplier(
        id=supplier_id, name=name,
        ustids=sorted(ids["ustids"]), ibans=sorted(ids["ibans"]),
        domains=sorted(ids["domains"]), fingerprint=sorted(ids["fingerprint"]),
    )
