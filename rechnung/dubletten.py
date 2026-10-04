"""Dubletten besser erkennen (Postfach-Baustein 4).

Zusaetzlich zu sha256 (``Store.seen_sha``: genau dieselbe Datei) und
(Lieferant, Rechnungsnummer) prueft ``check`` gegen alle bereits
uebernommenen Rechnungen (``processed.jsonl``) desselben Lieferanten:

- **normalisierte Rechnungsnummer**: Gross/klein, Leerzeichen, Binde-/
  Schraegstriche, Punkte und fuehrende Nullen je Zifferngruppe zaehlen
  nicht ("RE-2026-00042" == "re 2026/42"),
- **gleicher Bruttobetrag + gleiches Rechnungsdatum** (andere Nummer) --
  typisch fuer eine erneut ausgestellte/umnummerierte Rechnung,
- **Mahnung/Zahlungserinnerung**: steht im Kopf der ersten Seite
  "Mahnung"/"Zahlungserinnerung"/"Payment reminder" ..., ist das nie eine
  neue Rechnung -- mit Verweis auf die bekannte Ur-Rechnung, deren Nummer im
  Text vorkommt (sonst "Ur-Rechnung unbekannt").

Jeder Verdacht macht die Pruefung ``keine-dublette`` rot -> Ordner
"Menschliche Bearbeitung" mit Verweis auf die Ur-Rechnung (Datei, Nummer,
sha256), nie automatisch weiter nach JobRouter. Dieselbe Datei (gleicher
sha256) ist nie ihre eigene Dublette.
"""

from __future__ import annotations

import re

from .validate import Check

CHECK_NAME = "keine-dublette"

MAHNUNG_RE = re.compile(
    r"(?i)\b(\d\.\s*)?(mahnung|zahlungserinnerung|zahlungsaufforderung|mahnstufe"
    r"|payment\s+reminder|reminder|overdue\s+notice|letzte\s+erinnerung)\b")

# Anteil der ersten Seite, in dem der Dokumenttitel gesucht wird -- "Mahnung"
# im Kleingedruckten ("Mahngebuehren bei Zahlungsverzug") zaehlt nicht.
TITLE_FRACTION = 0.4
TITLE_CHARS = 400   # dasselbe ohne Layout (reiner Text): nur der Anfang


def norm_number(num) -> str:
    """Rechnungsnummer vergleichbar machen: nur Buchstaben/Ziffern, gross,
    fuehrende Nullen je Zifferngruppe weg ("RE-2026-00042" -> "RE202642")."""
    toks = re.findall(r"[A-Z]+|\d+", str(num or "").upper())
    return "".join(t.lstrip("0") or "0" if t.isdigit() else t for t in toks)


def _amount(v) -> float | None:
    try:
        return round(float(v), 2) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _value(fields: dict, key: str):
    fr = fields.get(key)
    if fr is None or fr.source == "missing":
        return None
    return fr.value


def title_text(doc) -> str:
    """Kopfbereich der ersten Seite (Dokumenttitel)."""
    if doc is None or not getattr(doc, "pages", None):
        return ""
    p = doc.pages[0]
    return " ".join(w.text for w in sorted(p.words, key=lambda w: (round(w.top), w.x0))
                    if w.top <= p.height * TITLE_FRACTION)


def is_reminder(doc=None, text: str = "") -> bool:
    return bool(MAHNUNG_RE.search(title_text(doc) if doc is not None else text[:TITLE_CHARS]))


def _ref(r: dict) -> dict:
    return {"datei": r.get("file"), "sha256": r.get("sha256"),
            "rechnungsnummer": r.get("rechnungsnummer"),
            "rechnungsdatum": r.get("rechnungsdatum"), "brutto": r.get("brutto")}


def _ref_text(r: dict) -> str:
    return (f"Ur-Rechnung {r.get('rechnungsnummer') or '?'} vom {r.get('rechnungsdatum') or '?'}"
            f" ({r.get('file') or '?'}, sha {str(r.get('sha256') or '')[:12]})")


def find(fields: dict, supplier_id: str | None, processed: list[dict], sha256: str = "",
         doc=None, text: str = "") -> tuple[str, dict | None] | None:
    """-> (Grund, Ur-Rechnung) beim ersten Verdacht, sonst None."""
    reminder = is_reminder(doc, text)
    if not supplier_id and not reminder:
        return None
    mine = [r for r in processed
            if r.get("supplier_id") == supplier_id and r.get("sha256") != sha256] \
        if supplier_id else []
    num = _value(fields, "rechnungsnummer")
    key = norm_number(num) if num else ""
    if key:
        for r in mine:
            if norm_number(r.get("rechnungsnummer")) == key:
                same = str(r.get("rechnungsnummer")).strip() == str(num).strip()
                why = "gleiche Rechnungsnummer" if same else \
                    f"gleiche Rechnungsnummer (normalisiert: {num} ~ {r.get('rechnungsnummer')})"
                return why, r
    brutto = _amount(_value(fields, "brutto"))
    datum = _value(fields, "rechnungsdatum")
    if brutto is not None and datum:
        for r in mine:
            if _amount(r.get("brutto")) == brutto and r.get("rechnungsdatum") == datum:
                return f"gleicher Lieferant, Bruttobetrag {brutto:.2f} und Datum {datum}", r
    if reminder:
        body = norm_number(doc.text if doc is not None else text)
        for r in mine:
            k = norm_number(r.get("rechnungsnummer"))
            if len(k) >= 4 and k in body:
                return "Mahnung/Zahlungserinnerung zu bekannter Rechnung", r
        return "Mahnung/Zahlungserinnerung (Ur-Rechnung unbekannt)", None
    return None


def check(fields: dict, supplier_id: str | None, store, sha256: str = "", doc=None,
          text: str = "") -> tuple[Check, dict | None]:
    """Pruefung ``keine-dublette`` -> (Check, Ur-Rechnung oder None)."""
    hit = find(fields, supplier_id, store.processed(), sha256, doc, text)
    if hit is None:
        if not supplier_id:
            return Check(CHECK_NAME, None, ""), None
        num = _value(fields, "rechnungsnummer")
        return Check(CHECK_NAME, True, f"{supplier_id}/{num}" if num else "kein Verdacht"), None
    why, ref = hit
    detail = f"Verdacht: {why}" + (f" -- {_ref_text(ref)}" if ref else "")
    return Check(CHECK_NAME, False, detail), (_ref(ref) if ref else None)
