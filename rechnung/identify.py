"""Lieferanten-Erkennung anhand stabiler Merkmale + gepflegte Stammdaten."""

from __future__ import annotations

from dataclasses import dataclass, field

from .pdf import Document
from .textnorm import find_domains, find_iban, find_ustids, norm

# Stammdaten-Felder, die als "fester Wert" pro Lieferant hinterlegt werden koennen
# (Name -> value_type). Werden von der Pipeline in die Extraktion gemischt, statt
# sie aus jeder Rechnung zu lesen.
FIXED_FIELD_TYPES: dict[str, str] = {
    "iban": "iban",
    "ustid": "ustid",
    "bestellnummer": "number",
    "zahlungsziel": "text",
    "ust_satz": "amount",
}


@dataclass
class Supplier:
    id: str
    name: str
    ustids: list[str] = field(default_factory=list)
    ibans: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    fingerprint: list[str] = field(default_factory=list)
    # --- gepflegte Stammdaten -------------------------------------------
    address_lines: list[str] = field(default_factory=list)
    bank_name: str = ""
    bic: str = ""
    creditor_id: str = ""          # Glaeubiger-ID / Steuernummer
    contact: str = ""              # Ansprechpartner / Telefon / Mail
    default_payment_terms: str = ""
    default_tax_rate: str = ""
    notes: str = ""
    # Feld -> fester Wert, gilt fuer alle Rechnungen dieses Lieferanten
    fixed_fields: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "ustids": self.ustids,
            "ibans": self.ibans, "domains": self.domains,
            "fingerprint": self.fingerprint,
            "address_lines": self.address_lines, "bank_name": self.bank_name,
            "bic": self.bic, "creditor_id": self.creditor_id,
            "contact": self.contact,
            "default_payment_terms": self.default_payment_terms,
            "default_tax_rate": self.default_tax_rate, "notes": self.notes,
            "fixed_fields": self.fixed_fields,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Supplier":
        return cls(
            id=d["id"], name=d.get("name", ""), ustids=d.get("ustids", []),
            ibans=d.get("ibans", []), domains=d.get("domains", []),
            fingerprint=d.get("fingerprint", []),
            address_lines=d.get("address_lines", []),
            bank_name=d.get("bank_name", ""), bic=d.get("bic", ""),
            creditor_id=d.get("creditor_id", ""), contact=d.get("contact", ""),
            default_payment_terms=d.get("default_payment_terms", ""),
            default_tax_rate=d.get("default_tax_rate", ""),
            notes=d.get("notes", ""),
            fixed_fields=d.get("fixed_fields", {}) or {},
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


def identify(doc: Document, suppliers: list[Supplier], threshold: float = 0.5,
            min_margin: float = 0.15):
    """-> (Supplier, score) oder None.

    Bei zu knappem Abstand zum zweitbesten Treffer (< ``min_margin``) wird
    lieber "kein sicherer Treffer" zurückgegeben statt eine mehrdeutige
    Zuordnung zu raten -- verhindert eine stille Verwechslung zwischen zwei
    Lieferanten mit ähnlichem Kopf-Layout (z.B. dieselbe Buchhaltungssoftware).
    Ein exakter Treffer auf USt-IdNr./IBAN gilt als eindeutig genug und
    überspringt die Margin-Prüfung.
    """
    ids = extract_identifiers(doc)
    text_n = norm(doc.text)
    scored: list[tuple[Supplier, float, bool]] = []
    for s in suppliers:
        score = 0.0
        hard_hit = bool(ids["ustids"] & set(s.ustids) or ids["ibans"] & set(s.ibans))
        if ids["ustids"] & set(s.ustids):
            score += 0.6
        if ids["ibans"] & set(s.ibans):
            score += 0.5
        if ids["domains"] & set(s.domains):
            score += 0.3
        if s.name and norm(s.name) in text_n:
            score += 0.2
        score += 0.3 * _jaccard(ids["fingerprint"], set(s.fingerprint))
        scored.append((s, round(score, 3), hard_hit))
    if not scored:
        return None
    scored.sort(key=lambda x: x[1], reverse=True)
    best_s, best_score, best_hard = scored[0]
    if best_score < threshold:
        return None
    if len(scored) > 1 and not best_hard:
        second_score = scored[1][1]
        if (best_score - second_score) < min_margin:
            return None
    return best_s, best_score


def cross_check(doc: Document, supplier: Supplier) -> list[dict]:
    """Auf der Rechnung gefundene IBAN/USt-IdNr gegen die Stammdaten pruefen.
    -> Liste {kind, ok, detail}."""
    ids = extract_identifiers(doc)
    out = []
    if supplier.ibans:
        found = ids["ibans"]
        ok = (not found) or bool(found & set(supplier.ibans))
        out.append({"kind": "iban-stammdaten", "ok": ok,
                    "detail": ",".join(sorted(found - set(supplier.ibans))) or "ok"})
    if supplier.ustids:
        found = ids["ustids"]
        ok = (not found) or bool(found & set(supplier.ustids))
        out.append({"kind": "ustid-stammdaten", "ok": ok,
                    "detail": ",".join(sorted(found - set(supplier.ustids))) or "ok"})
    return out


def supplier_from_doc(doc: Document, supplier_id: str, name: str) -> Supplier:
    ids = extract_identifiers(doc)
    return Supplier(
        id=supplier_id, name=name,
        ustids=sorted(ids["ustids"]), ibans=sorted(ids["ibans"]),
        domains=sorted(ids["domains"]), fingerprint=sorted(ids["fingerprint"]),
    )
