"""Regelmaessige Postfach-Abholung, solange die App laeuft (Wunsch Aaron
09.10.2026: "das Programm sollte regelmaessig nachschauen, ob Rechnungen
eingetroffen sind").

Ein Hintergrund-Thread ruft alle ``RECHNUNG_IMAP_POLL_MINUTES`` Minuten
(Standard 5, 0 = aus) ``ingest.fetch`` auf und legt neue Anhaenge in den
Posteingang der App. Verarbeitet wird dort wie bisher beim Laden der Liste
(/api/scan); die Oberflaeche fragt ``/api/abholung`` ab und laedt die Liste
neu, sobald etwas Neues da ist. Nur Abholen -- keine Uebergabe an JobRouter.
Der Nachtlauf-Lock gilt weiter (``ingest.fetch`` wartet darauf).
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable


class Abholer:
    def __init__(self, dest: Path, minuten: float, fetch: Callable | None = None):
        self.dest = Path(dest)
        self.minuten = minuten
        self._fetch = fetch
        self._lock = threading.Lock()
        self._jetzt = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.status = {"aktiv": minuten > 0, "minuten": minuten, "letzte_pruefung": "",
                       "laeuft": False, "neu_gesamt": 0, "zuletzt_neu": 0,
                       "mails_zuletzt": 0, "fehler": ""}

    def _call_fetch(self):
        if self._fetch is not None:
            return self._fetch(self.dest)
        from .ingest import fetch
        return fetch(dest=self.dest)

    def einmal(self) -> dict:
        """Eine Abholung jetzt (auch von Hand ueber /api/abholung). Laeuft
        schon eine, wird nicht parallel ein zweiter IMAP-Abruf gestartet."""
        if not self._lock.acquire(blocking=False):
            return dict(self.status)
        try:
            self.status["laeuft"] = True
            try:
                res = self._call_fetch()
                self.status["fehler"] = res.error or ""
                self.status["zuletzt_neu"] = len(res.saved)
                self.status["mails_zuletzt"] = res.mails
                self.status["neu_gesamt"] += len(res.saved)
            except Exception as e:  # Netz weg, Login falsch ... -> naechster Takt
                self.status["fehler"] = f"{type(e).__name__}: {e}"
            self.status["letzte_pruefung"] = time.strftime("%H:%M")
            return dict(self.status)
        finally:
            self.status["laeuft"] = False
            self._lock.release()

    def anstossen(self) -> None:
        self._jetzt.set()

    def _schleife(self) -> None:
        while not self._stop.is_set():
            self.einmal()
            self._jetzt.wait(self.minuten * 60)
            self._jetzt.clear()

    def start(self) -> None:
        if self.minuten <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._schleife, name="abholung", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._jetzt.set()


def aus_konfiguration(dest: Path) -> Abholer | None:
    """Abholer fuer die App -- nur, wenn das Postfach eingerichtet ist."""
    from .config import ImapConfig, imap_poll_minutes

    if not ImapConfig().configured:
        return None
    return Abholer(dest, imap_poll_minutes())
