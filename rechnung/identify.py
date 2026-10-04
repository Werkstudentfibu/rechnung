"""Lieferanten-Erkennung anhand stabiler Merkmale + gepflegte Stammdaten."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdf import Document
from .textnorm import find_domains, find_iban, find_ibans, find_ustids, norm

# Stammdaten-Felder, die als "fester Wert" pro Lieferant hinterlegt werden koennen
# (Name -> value_type). Werden von der Pipeline in die Extraktion gemischt, statt
# sie aus jeder Rechnung zu lesen.
FIXED_FIELD_TYPES: dict[str, str] = {
    "iban": "iban",
    "ustid": "ustid",
    "steuernummer": "text",
    "bestellnummer": "number",
    "ust_satz": "amount",
}

# Nur ein Startwert je Waehrung fuers Buchungsjournal (praeferiert vorbelegte
# Spalten, damit sie nicht erst nach der ersten passenden Rechnung auftauchen).
# Legt NICHTS fest -- tatsaechlich beobachtete Saetze kommen in pipeline.py
# unabhaengig davon dazu, egal ob mehr oder weniger als hier vorgeschlagen.
EXPECTED_RATES: dict[str, list[float]] = {
    "EUR": [19.0, 7.0, 0.0],
    "CHF": [8.1, 2.6, 0.0],
}
DEFAULT_CURRENCY = "EUR"

# Pruefungs-Grund, wenn auf der Rechnung eine andere IBAN steht als im
# Lieferanten-Stammdatensatz -- klassisches Betrugsmuster (geaenderte
# Bankverbindung). Wortgleich in Pruefung, Rueckstellgrund und Fehlerbericht.
IBAN_DEVIATES = "IBAN weicht von Stammdaten ab (Betrugsschutz)"


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
    bank_code: str = ""            # Bankleitzahl (BLZ) -- vor SEPA/IBAN ueblich,
                                    # manche Stammdatenlisten fuehren sie noch separat
    bank_account: str = ""         # alte Kontonummer (vor IBAN), dito
    creditor_id: str = ""          # Glaeubiger-ID / Steuernummer
    contact: str = ""              # Ansprechpartner / Telefon / Mail
    default_payment_terms: str = ""
    default_tax_rate: str = ""
    notes: str = ""
    currency: str = DEFAULT_CURRENCY
    # Feld -> fester Wert, gilt fuer alle Rechnungen dieses Lieferanten
    fixed_fields: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "ustids": self.ustids,
            "ibans": self.ibans, "domains": self.domains,
            "fingerprint": self.fingerprint,
            "address_lines": self.address_lines, "bank_name": self.bank_name,
            "bic": self.bic, "bank_code": self.bank_code,
            "bank_account": self.bank_account, "creditor_id": self.creditor_id,
            "contact": self.contact,
            "default_payment_terms": self.default_payment_terms,
            "default_tax_rate": self.default_tax_rate, "notes": self.notes,
            "fixed_fields": self.fixed_fields, "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Supplier":
        return cls(
            id=d["id"], name=d.get("name", ""), ustids=d.get("ustids", []),
            ibans=d.get("ibans", []), domains=d.get("domains", []),
            fingerprint=d.get("fingerprint", []),
            address_lines=d.get("address_lines", []),
            bank_name=d.get("bank_name", ""), bic=d.get("bic", ""),
            bank_code=d.get("bank_code", ""), bank_account=d.get("bank_account", ""),
            creditor_id=d.get("creditor_id", ""), contact=d.get("contact", ""),
            default_payment_terms=d.get("default_payment_terms", ""),
            default_tax_rate=d.get("default_tax_rate", ""),
            notes=d.get("notes", ""),
            fixed_fields=d.get("fixed_fields", {}) or {},
            currency=d.get("currency") or DEFAULT_CURRENCY,
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
    """-> (Supplier, score) oder None. Kurzform von ``identify_with_reason``."""
    return identify_with_reason(doc, suppliers, threshold, min_margin)[0]


def identify_with_reason(doc: Document, suppliers: list[Supplier], threshold: float = 0.5,
                         min_margin: float = 0.15):
    """-> ((Supplier, score) oder None, Grund).

    Einzige Quelle fuer die Lieferanten-Identitaet ist die Bibliothek
    (Abbyy-Import, Lieferanten-Tab) -- erkannt wird nur per Abgleich, nie
    angelegt oder per KI abgeleitet. Kein eindeutiger Treffer -> ``None`` und
    ein Grund fuer den Menschen (Rechnung wird zurueckgestellt).

    Bei zu knappem Abstand zum zweitbesten Treffer (< ``min_margin``) wird
    lieber "kein sicherer Treffer" zurückgegeben statt eine mehrdeutige
    Zuordnung zu raten -- verhindert eine stille Verwechslung zwischen zwei
    Lieferanten mit ähnlichem Kopf-Layout (z.B. dieselbe Buchhaltungssoftware).
    Ein exakter Treffer auf USt-IdNr./IBAN gilt als eindeutig genug und
    überspringt die Margin-Prüfung -- ausser USt-IdNr/IBAN zeigen auf
    verschiedene Lieferanten (dann mehrdeutig).
    """
    ids = extract_identifiers(doc)
    ids["ibans"] = ids["ibans"] | find_ibans(doc.text)
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
        return None, "Bibliothek ist leer -- kein Lieferant hinterlegt"
    hard = [x for x in scored if x[2]]
    if len(hard) > 1:
        names = ", ".join(sorted(x[0].id for x in hard))
        return None, f"mehrdeutig: USt-IdNr/IBAN passen zu mehreren Lieferanten ({names})"
    scored.sort(key=lambda x: x[1], reverse=True)
    best_s, best_score, best_hard = scored[0]
    if best_score < threshold:
        return None, "kein Lieferant aus der Bibliothek passt"
    if len(scored) > 1 and not best_hard:
        second_s, second_score, _ = scored[1]
        if (best_score - second_score) < min_margin:
            return None, (f"mehrdeutig: {best_s.id} / {second_s.id} "
                          f"(Score {best_score} vs. {second_score})")
    return (best_s, best_score), ""


def cross_check(doc: Document, supplier: Supplier) -> list[dict]:
    """Auf der Rechnung gefundene IBAN/USt-IdNr gegen die Stammdaten pruefen.
    -> Liste {kind, ok, detail}."""
    ids = extract_identifiers(doc)
    out = []
    if supplier.ibans:
        found = ids["ibans"] | find_ibans(doc.text)
        foreign = sorted(found - set(supplier.ibans))
        out.append({"kind": "iban-stammdaten", "ok": not foreign,
                    "detail": (f"{IBAN_DEVIATES}: {', '.join(foreign)}" if foreign else "ok")})
    if supplier.ustids:
        found = ids["ustids"]
        ok = (not found) or bool(found & set(supplier.ustids))
        out.append({"kind": "ustid-stammdaten", "ok": ok,
                    "detail": ",".join(sorted(found - set(supplier.ustids))) or "ok"})
    return out


def _detect_currency(text: str) -> str:
    """Grobe Waehrungs-Erkennung am Rechnungstext -- nur ein Vorschlag fuer
    einen neu angelegten Lieferanten, im Lieferanten-Tab jederzeit korrigierbar.
    CHF gewinnt nur, wenn es vorkommt und kein EUR/€-Hinweis auf der Rechnung
    steht (manche Schweizer Lieferanten fakturieren auch in Euro)."""
    t = text.upper()
    has_chf = bool(re.search(r"\bCHF\b", t))
    has_eur = bool(re.search(r"\bEUR\b", t) or "€" in text)
    if has_chf and not has_eur:
        return "CHF"
    return DEFAULT_CURRENCY


def supplier_from_doc(doc: Document, supplier_id: str, name: str) -> Supplier:
    ids = extract_identifiers(doc)
    return Supplier(
        id=supplier_id, name=name,
        ustids=sorted(ids["ustids"]), ibans=sorted(ids["ibans"]),
        domains=sorted(ids["domains"]), fingerprint=sorted(ids["fingerprint"]),
        currency=_detect_currency(doc.text),
    )


def identify_einvoice(inv, suppliers: list[Supplier]):
    """Lieferant einer E-Rechnung ueber die strukturierten Verkaeufer-Daten
    finden -> (Supplier, score) oder None. USt-IdNr/IBAN sind eindeutig; der
    Name allein zaehlt nur bei exakter (normalisierter) Gleichheit und nur,
    wenn genau ein Lieferant so heisst -- sonst lieber "unbekannt" als raten."""
    ustid = (inv.verkaeufer_ustid or "").replace(" ", "").upper()
    ibans = {i.replace(" ", "").upper() for i in inv.verkaeufer_iban}
    hits = [s for s in suppliers
            if (ustid and ustid in set(s.ustids)) or (ibans & set(s.ibans))]
    if len(hits) == 1:
        return hits[0], 1.0
    if len(hits) > 1:
        both = [s for s in hits if ustid in set(s.ustids) and ibans & set(s.ibans)]
        return (both[0], 1.0) if len(both) == 1 else None
    name = norm(inv.verkaeufer_name or "")
    if name:
        by_name = [s for s in suppliers if s.name and norm(s.name) == name]
        if len(by_name) == 1:
            return by_name[0], 0.6
    return None
