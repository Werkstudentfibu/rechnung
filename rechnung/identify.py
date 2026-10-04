"""Lieferanten-Erkennung anhand stabiler Merkmale + gepflegte Stammdaten."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdf import Document
from .textnorm import find_domains, find_iban, find_ibans, find_ustids, norm, norm_ustid

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
# CHF: aktuelle Saetze (seit 2024) 8,1/2,6/3,8 sowie die alten 7,7/2,5/3,7
# (Belege von vor 2024) -- beide Generationen bleiben gueltig, es wird kein
# Satz als "falsch" verworfen.
EXPECTED_RATES: dict[str, list[float]] = {
    "EUR": [19.0, 7.0, 0.0],
    "CHF": [8.1, 2.6, 3.8, 7.7, 2.5, 3.7, 0.0],
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


def build_identifier_index(suppliers: list[Supplier]) -> dict[tuple[str, str], set[str]]:
    """USt-IdNr/IBAN -> Menge der Lieferanten-IDs, die diese Kennung fuehren.
    Einmal aus der Bibliothek aufgebaut und gecacht (``Store.supplier_index``,
    SPEC Effizienz-Baustein 4) -- erspart bei einem eindeutigen Treffer das
    Fingerprint-/Domain-/Namens-Scoring ueber die ganze Bibliothek. Die
    Steuernummer ist bewusst NICHT Teil dieses Index: ihr Format ist je
    Bundesland uneinheitlich, eine Freitext-Erkennung im Rechnungstext waere
    riskant (falsche Treffer) -- sie bleibt reiner Stammdaten-Abgleich
    (``cross_check``)."""
    idx: dict[tuple[str, str], set[str]] = {}
    for s in suppliers:
        for u in s.ustids:
            # normalisiert (Leerzeichen/Punkte/Bindestriche weg, Schweizer
            # UID "CHE-123.456.789 MWST" -> "CHE123456789") -- ein Stammdatum
            # darf anders geschrieben sein als der Fund im Rechnungstext.
            idx.setdefault(("ustid", norm_ustid(u)), set()).add(s.id)
        for i in s.ibans:
            idx.setdefault(("iban", i), set()).add(s.id)
    return idx


def identify(doc: Document, suppliers: list[Supplier], threshold: float = 0.5,
            min_margin: float = 0.15, index: dict | None = None):
    """-> (Supplier, score) oder None. Kurzform von ``identify_with_reason``."""
    return identify_with_reason(doc, suppliers, threshold, min_margin, index)[0]


def identify_with_reason(doc: Document, suppliers: list[Supplier], threshold: float = 0.5,
                         min_margin: float = 0.15, index: dict | None = None):
    """-> ((Supplier, score) oder None, Grund).

    Einzige Quelle fuer die Lieferanten-Identitaet ist die Bibliothek
    (Abbyy-Import, Lieferanten-Tab) -- erkannt wird nur per Abgleich, nie
    angelegt oder per KI abgeleitet. Kein eindeutiger Treffer -> ``None`` und
    ein Grund fuer den Menschen (Rechnung wird zurueckgestellt).

    SPEC Effizienz-Baustein 4: harte Kennungen (USt-IdNr/IBAN) zuerst gegen
    einen Index abgleichen (``index``, Standard: einmalig aus ``suppliers``
    aufgebaut -- ``Store.supplier_index()`` cacht ihn ueber mehrere Aufrufe).
    Ein EINDEUTIGER Treffer entscheidet sofort, ohne das Fingerprint-/Domain-/
    Namens-Scoring ueber die ganze Bibliothek laufen zu lassen -- ein exakter
    Treffer soll ohnehin nie von einem bloss aehnlichen Kopf-Layout eines
    anderen Lieferanten ueberstimmt werden (Betrugsschutz: zeigen USt-IdNr
    und IBAN auf verschiedene Lieferanten, ist das ein Widerspruch -> Mensch,
    nie eine stille Entscheidung fuer einen von beiden). Ohne harten Treffer
    entscheidet wie bisher das Fingerprint-/Domain-/Namens-Scoring -- bei zu
    knappem Abstand zum zweitbesten Treffer (< ``min_margin``) lieber "kein
    sicherer Treffer" als eine mehrdeutige Zuordnung zu raten (verhindert
    eine stille Verwechslung zwischen zwei Lieferanten mit aehnlichem
    Kopf-Layout, z.B. derselben Buchhaltungssoftware).
    """
    if not suppliers:
        return None, "Bibliothek ist leer -- kein Lieferant hinterlegt"
    ids = extract_identifiers(doc)
    ids["ibans"] = ids["ibans"] | find_ibans(doc.text)
    idx = index if index is not None else build_identifier_index(suppliers)
    hard_ids: set[str] = set()
    for u in ids["ustids"]:
        hard_ids |= idx.get(("ustid", norm_ustid(u)), set())
    for i in ids["ibans"]:
        hard_ids |= idx.get(("iban", i), set())
    if len(hard_ids) > 1:
        names = ", ".join(sorted(hard_ids))
        return None, f"mehrdeutig: USt-IdNr/IBAN passen zu mehreren Lieferanten ({names})"
    text_n = norm(doc.text)
    if len(hard_ids) == 1:
        by_id = {s.id: s for s in suppliers}
        s = by_id[next(iter(hard_ids))]
        score = 0.0
        if {norm_ustid(u) for u in ids["ustids"]} & {norm_ustid(u) for u in s.ustids}:
            score += 0.6
        if ids["ibans"] & set(s.ibans):
            score += 0.5
        if ids["domains"] & set(s.domains):
            score += 0.3
        if s.name and norm(s.name) in text_n:
            score += 0.2
        score += 0.3 * _jaccard(ids["fingerprint"], set(s.fingerprint))
        return (s, round(score, 3)), ""

    # Kein harter Treffer: wie bisher per Fingerprint/Domain/Name scoren.
    scored: list[tuple[Supplier, float]] = []
    for s in suppliers:
        score = 0.0
        if ids["domains"] & set(s.domains):
            score += 0.3
        if s.name and norm(s.name) in text_n:
            score += 0.2
        score += 0.3 * _jaccard(ids["fingerprint"], set(s.fingerprint))
        scored.append((s, round(score, 3)))
    scored.sort(key=lambda x: x[1], reverse=True)
    best_s, best_score = scored[0]
    if best_score < threshold:
        return None, "kein Lieferant aus der Bibliothek passt"
    if len(scored) > 1:
        second_s, second_score = scored[1]
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
        found = {norm_ustid(u) for u in ids["ustids"]}
        sup_ustids = {norm_ustid(u) for u in supplier.ustids}
        ok = (not found) or bool(found & sup_ustids)
        out.append({"kind": "ustid-stammdaten", "ok": ok,
                    "detail": ",".join(sorted(found - sup_ustids)) or "ok"})
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
