"""Zeitmessung je Verarbeitungsstufe (SPEC Effizienz-Baustein 0).

Grundlage fuer alle folgenden Effizienz-Massnahmen ist zu wissen, wo die Zeit
pro Rechnung tatsaechlich hingeht -- insbesondere auf der Zielplattform
(Windows-Firmenlaptop/-server ohne Grafikkarte). ``StageTimer`` sammelt die
Dauer je benannter Stufe waehrend eines einzelnen ``pipeline.process()``-Laufs
(``Result.stufenzeiten``); ``distribution()`` wertet eine Liste von Dauerwerten
aus (Median/P90/Max fuer Tagesbericht und ``rechnung messen``)."""

from __future__ import annotations

import math
import time
from contextlib import contextmanager


class StageTimer:
    """Addiert die Dauer je Stufe -- eine Stufe kann mehrfach durchlaufen
    werden (z.B. "pruefungen" vor und nach der KI-Luecken-Fuellung)."""

    def __init__(self) -> None:
        self.times: dict[str, float] = {}

    @contextmanager
    def stage(self, name: str):
        t0 = time.monotonic()
        try:
            yield
        finally:
            self.times[name] = round(self.times.get(name, 0.0) + (time.monotonic() - t0), 4)

    def add(self, name: str, seconds: float) -> None:
        """Dauer, die ausserhalb eines ``with``-Blocks schon gemessen wurde
        (z.B. ``OcrResult.seconds``), nachtragen."""
        self.times[name] = round(self.times.get(name, 0.0) + max(0.0, seconds), 4)


def percentile(values: list[float], p: float) -> float:
    """p-Perzentil (0-100), naechstgelegener Rang -- genau genug fuer eine
    Messgroesse mit wenigen Dutzend bis einigen Hundert Werten je Tag."""
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, math.ceil(p / 100 * len(s)) - 1))
    return s[idx]


def distribution(values: list[float]) -> dict:
    """Min/Median/P90/Max -- leeres dict ohne Werte."""
    if not values:
        return {}
    s = sorted(values)
    return {
        "anzahl": len(s), "min": round(s[0], 2), "max": round(s[-1], 2),
        "median": round(percentile(s, 50), 2), "p90": round(percentile(s, 90), 2),
    }
