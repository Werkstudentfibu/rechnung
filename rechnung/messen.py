"""Stufenzeiten-Messung ohne Nebenwirkungen (SPEC Effizienz-Baustein 0).

``rechnung messen <ordner>``: verarbeitet jede Rechnung im Ordner genau wie
``rechnung scan`` (inkl. Zeitgrenze, OCR, KI), aber rein lesend -- kein
Speichern/Archivieren, keine JobRouter-Warteschlange, keine Ablage im Ordner
"Menschliche Bearbeitung", kein Ereignisprotokoll-Eintrag. Liefert nur die
Stufenzeiten je Datei (``Result.stufenzeiten``) -- Grundlage, um die
folgenden Effizienz-Massnahmen mit echten Zahlen (z.B. auf dem
Windows-Firmenlaptop) zu bewerten statt zu schaetzen."""

from __future__ import annotations

from pathlib import Path

from .guard import Guard
from .pipeline import Result, invoice_files
from .timing import distribution


def messen(folder: str | Path, store, guard: Guard | None = None) -> list[Result]:
    """Trockenlauf: wie ``pipeline.scan_folder``, aber ohne ``eventlog.record``/
    ``dispatch`` -- nichts wird gespeichert, abgelegt oder eingereiht."""
    folder = Path(folder)
    own = guard is None
    guard = guard or Guard(store.root)
    try:
        return [guard.process(p) for p in invoice_files(folder)]
    finally:
        if own:
            guard.close()


def verteilung(results: list[Result]) -> dict:
    """Median/P90/Max je Stufe (inkl. "gesamt") ueber alle gemessenen
    Rechnungen -- dieselbe Auswertung wie im Nachtlauf-Tagesbericht
    (``nightly.tagesbericht``), hier aber ohne vorherige Live-Verarbeitung."""
    stufen: dict[str, list[float]] = {}
    for r in results:
        for name, sek in (r.stufenzeiten or {}).items():
            stufen.setdefault(name, []).append(sek)
        stufen.setdefault("gesamt", []).append(r.duration_s)
    return {name: distribution(werte) for name, werte in stufen.items()}
