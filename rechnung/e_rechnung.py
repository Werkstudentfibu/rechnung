"""E-Rechnungen bevorzugen: welche Lieferanten schicken noch PDF/Scan?
(Entscheidung Aaron 10.10.2026)

Eine E-Rechnung (ZUGFeRD/XRechnung) wird ohne Vorlage, OCR und KI gelesen
-- jede Umstellung eines Lieferanten spart dauerhaft Pruef- und
Korrekturaufwand. ``kandidaten()`` listet die Lieferanten mit bestaetigten
Rechnungen (``testsatz.bestaetigte``), die (auch) als PDF oder Scan kamen,
sortiert nach Anzahl solcher Rechnungen und Korrekturaufwand (Korrektur-
Ereignisse aus ``eventlog``). Dazu eine Vorlage fuer ein kurzes Anschreiben
(``web/static/anschreiben-e-rechnung.txt``, Platzhalter in ``{...}``) --
es wird NICHTS versendet, nur Text erzeugt.
"""

from __future__ import annotations

import time
from pathlib import Path

from .eventlog import TYPE_CORRECTION, TYPE_PROCESSED

VORLAGE = Path(__file__).resolve().parent / "web" / "static" / "anschreiben-e-rechnung.txt"


def _art(rec: dict, ocr_shas: set[str]) -> str:
    """"e_rechnung" | "scan" | "pdf"."""
    if rec.get("extraktion") == "einvoice" or str(rec.get("original") or "").endswith(".xml") \
            or str(rec.get("datei") or "").lower().endswith(".xml"):
        return "e_rechnung"
    return "scan" if rec.get("sha256") in ocr_shas else "pdf"


def kandidaten(store) -> list[dict]:
    from .testsatz import bestaetigte

    events = store.events()
    ocr_shas = {e.get("sha256") for e in events
                if e.get("type") == TYPE_PROCESSED and "ocr" in (e.get("stages") or [])}
    korr: dict[str, dict] = {}
    for e in events:
        if e.get("type") != TYPE_CORRECTION or not e.get("supplier_id"):
            continue
        k = korr.setdefault(e["supplier_id"], {"anzahl": 0, "shas": set()})
        k["anzahl"] += 1
        if e.get("sha256"):
            k["shas"].add(e["sha256"])
    rows: dict[str, dict] = {}
    for rec in bestaetigte(store):
        sid = rec.get("supplier_id")
        if not sid:
            continue
        row = rows.setdefault(sid, {"lieferant": sid, "lieferant_name": "", "rechnungen": 0,
                                    "pdf": 0, "scan": 0, "e_rechnung": 0})
        row["lieferant_name"] = row["lieferant_name"] or rec.get("supplier_name") or ""
        row["rechnungen"] += 1
        row[_art(rec, ocr_shas)] += 1
    out = []
    for sid, row in rows.items():
        if not (row["pdf"] + row["scan"]):
            continue
        k = korr.get(sid, {"anzahl": 0, "shas": set()})
        row["korrekturen"] = k["anzahl"]
        row["rechnungen_mit_korrektur"] = len(k["shas"])
        row["korrekturen_je_rechnung"] = round(k["anzahl"] / row["rechnungen"], 2)
        sup = store.supplier(sid)
        if sup is not None:
            row["lieferant_name"] = sup.name or row["lieferant_name"]
        out.append(row)
    out.sort(key=lambda r: (-(r["pdf"] + r["scan"]), -r["korrekturen"], r["lieferant"]))
    return out


def anschreiben(store=None, lieferant: str | None = None, **werte) -> str:
    """Anschreiben aus der Vorlage; bekannte Platzhalter werden gefuellt
    (Lieferant, Anschrift aus den Stammdaten, Datum), alle anderen bleiben
    als ``{PLATZHALTER}`` sichtbar stehen -- nichts wird geraten."""
    text = VORLAGE.read_text("utf-8")
    fill = {"DATUM": time.strftime("%d.%m.%Y")}
    if store is not None and lieferant:
        sup = store.supplier(lieferant)
        if sup is not None:
            fill["LIEFERANT"] = sup.name
            if sup.address_lines:
                fill["LIEFERANT_ANSCHRIFT"] = "\n".join(sup.address_lines)
        else:
            fill["LIEFERANT"] = lieferant
    fill.update({k.upper(): v for k, v in werte.items() if v})
    for k, v in fill.items():
        text = text.replace("{" + k + "}", str(v))
    return text
