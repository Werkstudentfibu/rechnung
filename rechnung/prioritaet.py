"""Vorrang fuer den Klick des Menschen (Befund Aaron 13.11.2026: eine Rechnung
brauchte beim Anklicken ueber 3 Minuten "Texterkennung/KI").

Die Hintergrundarbeit (Vorbereitung, Senden im Hintergrund) sollte schon
immer mit niedriger Prioritaet laufen -- tat es aber nicht: die eigentliche
Verarbeitung lief in einem eigenen Thread (guard.py), der die Prioritaet nicht
erbte, und Tesseract startete immer mit normaler Prioritaet. Hier:

- ``hintergrund_an()``: im Verarbeitungs-Thread einer Hintergrund-Guard
  aufrufen -- senkt den Thread und merkt sich "Hintergrund" (thread-lokal).
- ``eilig(name)``: der Mensch wartet gerade auf diese Datei -- laeuft sie im
  Hintergrund, bekommt ihre Texterkennung ab der naechsten Seite wieder
  normale Prioritaet.
- ``niedrig_fuer(name)``: soll ein Unterprozess fuer diese Datei niedrig
  laufen? ``subprocess_flags``: die passenden Startoptionen.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from contextlib import contextmanager

_tl = threading.local()
_eilig: dict[str, int] = {}
_lock = threading.Lock()

# Windows-Prioritaetsklasse fuer Unterprozesse (subprocess-Konstante gibt es
# nur unter Windows)
BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x00004000)


def niedrig_setzen() -> None:
    """Aktuellen Thread hinter die Oberflaeche stellen (best effort)."""
    try:
        if sys.platform == "win32":
            import ctypes

            k = ctypes.windll.kernel32
            k.SetThreadPriority(k.GetCurrentThread(), -1)     # BELOW_NORMAL
        else:
            os.setpriority(os.PRIO_PROCESS, threading.get_native_id(), 10)
    except Exception:  # noqa: BLE001 -- nur ein Gefallen, nie ein Fehler
        pass


def hintergrund_an() -> None:
    _tl.hintergrund = True
    niedrig_setzen()


def ist_hintergrund() -> bool:
    return bool(getattr(_tl, "hintergrund", False))


@contextmanager
def eilig(name: str):
    name = str(name or "")
    with _lock:
        _eilig[name] = _eilig.get(name, 0) + 1
    try:
        yield
    finally:
        with _lock:
            n = _eilig.get(name, 0) - 1
            if n > 0:
                _eilig[name] = n
            else:
                _eilig.pop(name, None)


def ist_eilig(name: str) -> bool:
    with _lock:
        return _eilig.get(str(name or ""), 0) > 0


def niedrig_fuer(name: str, hintergrund: bool | None = None) -> bool:
    """Hintergrundarbeit an einer Datei, auf die gerade niemand wartet."""
    hg = ist_hintergrund() if hintergrund is None else hintergrund
    return bool(hg) and not ist_eilig(name)


def subprocess_flags(niedrig: bool) -> dict:
    """Zusaetzliche Startoptionen fuer ``subprocess.run`` (unter Windows als
    ``creationflags``-Bit, das der Aufrufer mit seinen eigenen ODER-verknuepft)."""
    if not niedrig:
        return {}
    if sys.platform == "win32":
        return {"creationflags": BELOW_NORMAL}
    return {"preexec_fn": lambda: os.nice(10)}
