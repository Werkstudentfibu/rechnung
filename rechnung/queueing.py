"""Warteschlange nach Aufwand (SPEC Effizienz-Baustein 5).

Schnelle Faelle (E-Rechnung, Textebene + bekannte, nicht veraltete Vorlage)
werden komplett verarbeitet und verteilt (``pipeline.dispatch``), BEVOR die
langsamen (OCR noetig, kein Template -> KI-Volllesung) ueberhaupt beginnen --
eine einzelne langsame Rechnung soll die vielen schnellen eines Stapellaufs
nicht aufhalten. Innerhalb jeder Schlange gilt "aelteste zuerst"
(Dateialter). Die eigentliche 120-s-Zeitgrenze je Rechnung (guard.py) gilt
davon voellig unberuehrt und unveraendert.

Die Einteilung selbst liest dazu kurz in die Datei hinein (Lieferant/Template
pruefen) -- damit das NIE die 120-s-Zeitgrenze der eigentlichen Verarbeitung
unterlaufen kann (z.B. an einem defekten PDF haengen bleibt), bekommt sie
eine eigene, kurze, unkritische Zeitgrenze: haengt sie, zaehlt die Datei
einfach als "langsam" -- die anschliessende echte Verarbeitung durchlaeuft
danach ganz normal den vollen Guard mit der vollen Zeitgrenze."""

from __future__ import annotations

import threading
from pathlib import Path

CLASSIFY_TIMEOUT = 5.0
SCHNELL = "schnell"
LANGSAM = "langsam"


def _classify(path: Path, store) -> str:
    from . import billing, einvoice, ocr
    from . import pdf as pdfmod
    from .identify import identify_with_reason

    try:
        if einvoice.load(path) is not None:
            return SCHNELL
        if ocr.needs_ocr(path):
            return LANGSAM
        doc = pdfmod.load(path)
        # Lieferant je Rechnungsadresse (09.10.2026): Adresse zuerst bestimmen,
        # damit dieselbe Kandidaten-Einschraenkung wie in pipeline.process()
        # gilt -- sonst koennte hier ein Lieferant einer ANDEREN Adresse eine
        # Rechnung faelschlich als "schnell" (aktives Template) einstufen.
        addresses = store.addresses()
        addr_match, _why = billing.match_text(doc.text, addresses) if addresses else (None, "")
        address_id = addr_match[0].id if addr_match else None
        match, _why = identify_with_reason(
            doc, store.suppliers(), index=store.supplier_index(address_id),
            address_id=address_id, has_addresses=bool(addresses))
        if not match:
            return LANGSAM
        active = [t for t in store.templates_for(match[0].id) if not t.stale]
        return SCHNELL if active else LANGSAM
    except Exception:  # noqa: BLE001 -- defektes PDF: lieber "langsam" als abstuerzen
        return LANGSAM


def _classify_safe(path: Path, store, timeout: float = CLASSIFY_TIMEOUT) -> str:
    """Wie ``_classify``, aber mit eigener kurzer Zeitgrenze -- siehe
    Modul-Docstring. Im Zweifel "langsam"."""
    box: dict = {}

    def run() -> None:
        box["r"] = _classify(path, store)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    return box.get("r", LANGSAM)


def split_by_effort(paths: list[Path], store) -> tuple[list[Path], list[Path]]:
    """-> (schnell, langsam), je aufsteigend nach Dateialter (aelteste zuerst)."""
    ordered = sorted(paths, key=lambda p: p.stat().st_mtime if p.exists() else 0.0)
    fast: list[Path] = []
    slow: list[Path] = []
    for p in ordered:
        (fast if _classify_safe(p, store) == SCHNELL else slow).append(p)
    return fast, slow
