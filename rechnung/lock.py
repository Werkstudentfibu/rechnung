"""Lock-Datei fuer den Nachtlauf (SPEC §7r).

Waehrend ``rechnung nacht`` laeuft, soll die laufende Abholung/Verarbeitung
(IONOS-Abholung in ``ingest.fetch``, Stapellauf-Verarbeitung ueber
``guard.Guard``) pausieren statt parallel auf denselben Daten zu
schreiben -- eine Verzoegerung ist dabei ausdruecklich in Ordnung. Reine
Datei-Sperre, kein Netzwerk, kein zusaetzlicher Prozess.

Windows-tauglich: ``os.O_CREAT | os.O_EXCL`` ist dort ebenso atomar, die
Datei wird sofort wieder geschlossen (Windows kann eine offene Datei nicht
loeschen). Eine nach einem Absturz liegengebliebene Lock-Datei wird am
Alter (Aenderungszeit) erkannt, nicht an der Prozess-ID -- ``os.kill(pid, 0)``
wuerde unter Windows den Prozess beenden statt ihn nur abzufragen.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "nacht.lock"
# Sicherheitsnetz gegen eine liegengebliebene Lock-Datei (z.B. nach einem
# Absturz des Nachtlaufs) -- danach laeuft eine wartende Abholung/
# Verarbeitung trotzdem weiter, statt fuer immer zu haengen.
MAX_WAIT_S = 6 * 3600
# Aelter als das -> liegengeblieben (Absturz/Stromausfall), ein Nachtlauf
# dauert normalerweise Minuten. Der naechste Nachtlauf raeumt sie weg.
STALE_S = 6 * 3600
POLL_S = 1.0


def lock_path(data_root) -> Path:
    return Path(data_root) / LOCK_NAME


def is_stale(data_root, stale_after: float = STALE_S) -> bool:
    p = lock_path(data_root)
    try:
        return time.time() - p.stat().st_mtime > stale_after
    except OSError:
        return False


def is_locked(data_root) -> bool:
    return lock_path(data_root).exists() and not is_stale(data_root)


def wait_until_unlocked(data_root, max_wait: float = MAX_WAIT_S, poll: float = POLL_S) -> None:
    """Blockiert, solange die Lock-Datei besteht -- hoechstens ``max_wait``
    Sekunden. Kein Lock vorhanden (Normalfall): kehrt sofort zurueck, ohne
    auch nur einmal zu schlafen."""
    waited = 0.0
    while is_locked(data_root) and waited < max_wait:
        time.sleep(poll)
        waited += poll


@contextmanager
def acquire(data_root):
    """Exklusive Lock-Datei fuer die Dauer des Nachtlaufs. Besteht bereits
    eine (ein anderer Nachtlauf laeuft noch), schlaegt das mit
    ``RuntimeError`` fehl, statt zwei Laeufe gleichzeitig zuzulassen."""
    p = lock_path(data_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and is_stale(data_root):
        print(f"WARNUNG: liegengebliebene Lock-Datei {p} (aelter als "
              f"{STALE_S / 3600:g} h) wird entfernt.", file=sys.stderr)
        p.unlink(missing_ok=True)
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
