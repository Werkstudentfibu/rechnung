"""Vorverarbeitung im Hintergrund (Befund Aaron 09.10.2026: ein Scan brauchte
auf dem Laptop beim Anklicken ueber 2 Minuten).

Sobald eine Rechnung im Posteingang der App liegt -- beim Start und nach jeder
Postfach-Abholung --, liest ein Hintergrund-Thread sie einmal komplett aus
(Texterkennung + KI). Die Ergebnisse landen in den vorhandenen Zwischen-
speichern (OCR-Arbeitskopie, KI-Antwort-Cache auf der Platte); beim Anklicken
ist die Rechnung dann in der Regel sofort da. Es wird dabei nichts
entschieden, gespeichert, angelegt oder exportiert -- derselbe Vorschau-Aufruf
wie beim Anklicken, nur frueher.

Eine Datei nach der anderen (der Laptop hat keine Kerne zu verschenken). Klickt
der Mensch eine Rechnung an, die gerade im Hintergrund laeuft, wartet die
Anzeige auf genau diesen Lauf (``sperre``), statt dieselbe Arbeit parallel
ein zweites Mal zu starten.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Callable


class Vorverarbeiter:
    def __init__(self, verarbeite: Callable[[Path], object]):
        self._verarbeite = verarbeite
        self._q: "queue.Queue[Path]" = queue.Queue()
        self._erledigt: set[tuple[str, int, float]] = set()
        self._geplant: set[str] = set()
        self._sperren: dict[str, threading.Lock] = {}
        self._sperren_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.status = {"wartend": 0, "aktuell": "", "fertig": 0, "fehler": ""}

    @staticmethod
    def _kennung(p: Path) -> tuple[str, int, float]:
        st = p.stat()
        return (p.name, st.st_size, st.st_mtime)

    def sperre(self, path: Path) -> threading.Lock:
        """Je Datei ein Lock -- Hintergrundlauf und Anzeige teilen ihn."""
        with self._sperren_lock:
            return self._sperren.setdefault(Path(path).name, threading.Lock())

    def einreihen(self, paths) -> None:
        for p in paths:
            p = Path(p)
            if not p.is_file() or p.name in self._geplant:
                continue
            try:
                if self._kennung(p) in self._erledigt:
                    continue
            except OSError:
                continue
            self._geplant.add(p.name)
            self._q.put(p)
        self.status["wartend"] = self._q.qsize()
        self._start()

    def _start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._schleife, name="vorverarbeitung",
                                            daemon=True)
            self._thread.start()

    def _schleife(self) -> None:
        while True:
            p = self._q.get()
            self._geplant.discard(p.name)
            self.status["wartend"] = self._q.qsize()
            if not p.is_file():
                continue
            self.status["aktuell"] = p.name
            try:
                with self.sperre(p):
                    self._verarbeite(p)
                self._erledigt.add(self._kennung(p))
                self.status["fertig"] += 1
            except Exception as e:  # eine kaputte Datei haelt die Schlange nicht auf
                self.status["fehler"] = f"{p.name}: {type(e).__name__}: {e}"
            finally:
                self.status["aktuell"] = ""
