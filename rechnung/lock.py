"""Lock-Datei fuer den Nachtlauf (SPEC §7r).

Waehrend ``rechnung nacht`` laeuft, soll die laufende Abholung/Verarbeitung
(IONOS-Abholung in ``ingest.fetch``, Stapellauf-Verarbeitung ueber
``guard.Guard``) pausieren statt parallel auf denselben Daten zu
schreiben -- eine Verzoegerung ist dabei ausdruecklich in Ordnung. Reine
Datei-Sperre, kein Netzwerk, kein zusaetzlicher Prozess.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "nacht.lock"
# Sicherheitsnetz gegen eine liegengebliebene Lock-Datei (z.B. nach einem
# Absturz des Nachtlaufs) -- danach laeuft eine wartende Abholung/
# Verarbeitung trotzdem weiter, statt fuer immer zu haengen.
MAX_WAIT_S = 6 * 3600
POLL_S = 1.0


def lock_path(data_root) -> Path:
    return Path(data_root) / LOCK_NAME


def is_locked(data_root) -> bool:
    return lock_path(data_root).exists()


def wait_until_unlocked(data_root, max_wait: float = MAX_WAIT_S, poll: float = POLL_S) -> None:
    """Blockiert, solange die Lock-Datei besteht -- hoechstens ``max_wait``
    Sekunden. Kein Lock vorhanden (Normalfall): kehrt sofort zurueck, ohne
    auch nur einmal zu schlafen."""
    p = lock_path(data_root)
    waited = 0.0
    while p.exists() and waited < max_wait:
        time.sleep(poll)
        waited += poll


@contextmanager
def acquire(data_root):
    """Exklusive Lock-Datei fuer die Dauer des Nachtlaufs. Besteht bereits
    eine (ein anderer Nachtlauf laeuft noch), schlaegt das mit
    ``RuntimeError`` fehl, statt zwei Laeufe gleichzeitig zuzulassen."""
    p = lock_path(data_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        fh = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f"Nachtlauf laeuft bereits ({p})") from None
    os.write(fh, json.dumps({"pid": os.getpid(),
                            "started": time.strftime("%Y-%m-%dT%H:%M:%S%z")}).encode("utf-8"))
    os.close(fh)
    try:
        yield
    finally:
        p.unlink(missing_ok=True)
