"""Stammdaten aus der Bibliothek, die bei jeder Rechnung mitgehen
(Wunsch Aaron 10.10.2026, Vorbild Abbyy-Maske "European Invoice"): ist der
Geschaeftsbereich (Rechnungsadresse) bzw. der Lieferant erkannt, werden
deren unveraenderliche Daten -- ID, Name, USt-ID, Anschrift, Bank -- aus der
Bibliothek an JobRouter uebergeben, nicht von der Rechnung gelesen.

Anschriften liegen als Zeilen vor (wie in der Oberflaeche, ``splitAdresse``
in index.html); hier in Strasse/PLZ/Ort/Land zerlegt."""

from __future__ import annotations

import re

LAENDER = {"deutschland": "DE", "germany": "DE", "schweiz": "CH", "suisse": "CH",
           "switzerland": "CH", "österreich": "AT", "oesterreich": "AT", "austria": "AT"}
_PLZ_ORT = re.compile(r"^(?:([A-Z]{2})[- ])?(\d{4,5})\s+(.+)$")


def split_adresse(lines) -> dict:
    out = {"strasse": "", "plz": "", "ort": "", "land": ""}
    for raw in (str(x).strip() for x in (lines or [])):
        if not raw:
            continue
        m = _PLZ_ORT.match(raw)
        if m and not out["plz"]:
            out["plz"], out["ort"] = m.group(2), m.group(3)
            if m.group(1):
                out["land"] = m.group(1)
            continue
        if re.fullmatch(r"[A-Z]{2}", raw):
            out["land"] = raw
            continue
        if raw.lower() in LAENDER:
            out["land"] = LAENDER[raw.lower()]
            continue
        if not out["strasse"]:
            out["strasse"] = raw
    return out


def _land_aus(*kennungen: str) -> str:
    for k in kennungen:
        c = (k or "")[:2].upper()
        if re.fullmatch(r"[A-Z]{2}", c):
            return c
    return ""


def ust_aus_notiz(notes: str) -> str:
    m = re.search(r"USt-IdNr:\s*(\S+)", notes or "")
    return m.group(1) if m else ""


def geschaeftsbereich(addr) -> dict | None:
    if addr is None:
        return None
    a = split_adresse(addr.lines)
    ust = getattr(addr, "vatid", "") or ust_aus_notiz(addr.notes)
    return {"id": addr.id, "name": addr.name, "ustid": ust,
            "strasse": a["strasse"], "plz": a["plz"], "ort": a["ort"],
            "land": a["land"] or _land_aus(ust),
            "kostenstelle": addr.cost_center, "mandant": addr.company_code}


def lieferant(sup, iban_rechnung: str | None = None) -> dict | None:
    """``iban_rechnung``: steht auf der Rechnung eine der hinterlegten IBANs,
    geht genau diese mit (Lieferanten mit mehreren Konten)."""
    if sup is None:
        return None
    a = split_adresse(sup.address_lines)
    ibans = [i.replace(" ", "").upper() for i in sup.ibans]
    gelesen = (iban_rechnung or "").replace(" ", "").upper()
    iban = gelesen if gelesen in ibans else (ibans[0] if ibans else "")
    ust = sup.ustids[0] if sup.ustids else ""
    return {"id": sup.external_id or sup.id, "name": sup.name, "ustid": ust,
            "steuernummer": sup.creditor_id,
            "strasse": a["strasse"], "plz": a["plz"], "ort": a["ort"],
            "land": a["land"] or _land_aus(ust, iban),
            "iban": iban, "bic": sup.bic, "bankkonto": sup.bank_account,
            "bankleitzahl": sup.bank_code, "bank": sup.bank_name,
            "bestaetigt": sup.bestaetigt}


def fuer_rechnung(store, res) -> dict:
    """Stammdaten-Schnappschuss zum Zeitpunkt der Freigabe (Result)."""
    addr = store.address(res.billing_address_id) if res.billing_address_id else None
    sup = store.supplier(res.supplier_id) if res.supplier_id else None
    iban = res.fields.get("iban").value if res.fields.get("iban") else None
    return {"geschaeftsbereich": geschaeftsbereich(addr),
            "lieferant": lieferant(sup, iban if isinstance(iban, str) else None)}
