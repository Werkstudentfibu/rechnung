"""Vorverarbeitung im Hintergrund (Befund Aaron 09.10.2026: ein Scan brauchte
auf dem Laptop beim Anklicken ueber 2 Minuten).

Sobald eine Rechnung im Posteingang der App liegt -- beim Start und nach jeder
Postfach-Abholung --, liest ein Hintergrund-Thread sie einmal komplett aus
(Texterkennung + KI). Die Ergebnisse landen in den vorhandenen Zwischen-
speichern (OCR-Arbeitskopie, KI-Antwort-Cache auf der Platte) und im Merkzettel
(``vorbereitung.py``, ueberlebt den Neustart); beim Anklicken ist die Rechnung
dann in der Regel sofort da. Es wird dabei nichts entschieden, gespeichert,
angelegt oder exportiert -- derselbe Vorschau-Aufruf wie beim Anklicken, nur
frueher.

Eine Datei nach der anderen (der Laptop hat keine Kerne zu verschenken), mit
niedriger Prioritaet. Klickt der Mensch eine Rechnung an, die gerade im
Hintergrund laeuft, wartet die Anzeige auf genau diesen Lauf (``sperre``),
statt dieselbe Arbeit parallel ein zweites Mal zu starten; solange der Mensch
selbst rechnen laesst (``mensch``), startet der Hintergrund keine neue Datei.

Zweite Aufgabe: nach dem Lernen schon vorbereitete Rechnungen desselben
Lieferanten neu auswerten (``neu_auswerten``, nur schnelle Vorlagenstufe).
"""

from __future__ import annotations

import itertools
import queue
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable


def _niedrige_prioritaet() -> None:
    """Diesen Thread hinter die Oberflaeche stellen (best effort). Die
    eigentliche Verarbeitung laeuft in einem eigenen Thread der Guard --
    der wird mit ``Guard(niedrig=True)`` gesenkt (prioritaet.py)."""
    from .prioritaet import niedrig_setzen

    niedrig_setzen()


class Vorverarbeiter:
    def __init__(self, verarbeite: Callable[[Path], object],
                 neu_auswerten: Callable[[Path], object] | None = None,
                 ueberspringen: Callable[[Path], bool] | None = None):
        self._verarbeite = verarbeite
        self._neu = neu_auswerten
        self._ueberspringen = ueberspringen
        # (Rang, laufende Nummer, Art, Pfad): Rang 0 = vorgezogen (naechste
        # Rechnungen im Stapel 'Menschliche Bearbeitung'), 1 = normal
        self._q: "queue.PriorityQueue[tuple[int, int, str, Path]]" = queue.PriorityQueue()
        self._nr = itertools.count()
        self._erledigt: set[tuple[str, int, float]] = set()
        self._geplant: set[str] = set()
        self._neu_geplant: set[str] = set()
        self._sperren: dict[str, threading.Lock] = {}
        self._sperren_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._mensch = 0
        self.status = {"wartend": 0, "aktuell": "", "fertig": 0, "fehler": ""}

    @staticmethod
    def _kennung(p: Path) -> tuple[str, int, float]:
        st = p.stat()
        return (p.name, st.st_size, st.st_mtime)

    def sperre(self, path: Path) -> threading.Lock:
        """Je Datei ein Lock -- Hintergrundlauf und Anzeige teilen ihn."""
        with self._sperren_lock:
            return self._sperren.setdefault(Path(path).name, threading.Lock())

    @contextmanager
    def mensch(self):
        """Solange der Mensch selbst etwas rechnen laesst (Anklicken, Senden),
        faengt der Hintergrund keine neue Datei an."""
        with self._sperren_lock:
            self._mensch += 1
        try:
            yield
        finally:
            with self._sperren_lock:
                self._mensch -= 1

    def mensch_aktiv(self) -> bool:
        return self._mensch > 0

    def in_arbeit(self) -> set[str]:
        """Namen der Dateien, die gerade laufen oder eingereiht sind."""
        namen = set(self._geplant)
        if self.status["aktuell"]:
            namen.add(self.status["aktuell"])
        return namen

    def einreihen(self, paths, vorne: bool = False) -> None:
        """``vorne``: vor allen normal eingereihten Dateien bearbeiten (auch
        wenn die Datei schon weiter hinten wartet)."""
        for p in paths:
            p = Path(p)
            if not p.is_file() or (p.name in self._geplant and not vorne):
                continue
            try:
                if self._kennung(p) in self._erledigt:
                    continue
                if self._ueberspringen is not None and self._ueberspringen(p):
                    continue            # Merkzettel da (auch nach Neustart): nichts zu tun
            except OSError:
                continue
            self._geplant.add(p.name)
            self._q.put((0 if vorne else 1, next(self._nr), "vor", p))
        self.status["wartend"] = self._q.qsize()
        self._start()

    def neu_auswerten(self, paths) -> None:
        """Schon vorbereitete Rechnungen nach dem Lernen neu auswerten."""
        if self._neu is None:
            return
        for p in paths:
            p = Path(p)
            if not p.is_file() or p.name in self._neu_geplant:
                continue
            self._neu_geplant.add(p.name)
            self._q.put((1, next(self._nr), "neu", p))
        self.status["wartend"] = self._q.qsize()
        self._start()

    def warte_bis_leer(self, timeout: float = 30.0) -> bool:
        """Fuer Tests: blockiert, bis Schlange leer und nichts in Arbeit."""
        ende = time.monotonic() + timeout
        while time.monotonic() < ende:
            if self._q.empty() and not self.status["aktuell"] and self._q.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return False

    def _start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._schleife, name="vorverarbeitung",
                                            daemon=True)
            self._thread.start()

    def _schleife(self) -> None:
        _niedrige_prioritaet()
        while True:
            _rang, _nr, art, p = self._q.get()
            try:
                while self._mensch > 0:          # Klick des Menschen hat Vorrang
                    time.sleep(0.2)
                if art == "neu":
                    self._neu_geplant.discard(p.name)
                else:
                    self._geplant.discard(p.name)
                self.status["wartend"] = self._q.qsize()
                if not p.is_file():
                    continue
                try:      # vorgezogen und hinten nochmal eingereiht: nur einmal lesen
                    if art == "vor" and self._kennung(p) in self._erledigt:
                        continue
                except OSError:
                    continue
                self.status["aktuell"] = p.name
                try:
                    with self.sperre(p):
                        if art == "neu":
                            self._neu(p)
                            continue
                        r = self._verarbeite(p)
                    if isinstance(r, dict) and r.get("ergebnis") == "fehler":
                        self.status["fehler"] = f"{p.name}: {r.get('fehler', '')}"
                    elif isinstance(r, dict) and r.get("ergebnis") == "belegt":
                        pass            # ein anderer Prozess rechnet sie -- spaeter nachsehen
                    else:
                        self._erledigt.add(self._kennung(p))
                        self.status["fertig"] += 1
                except Exception as e:  # eine kaputte Datei haelt die Schlange nicht auf
                    self.status["fehler"] = f"{p.name}: {type(e).__name__}: {e}"
                finally:
                    self.status["aktuell"] = ""
            finally:
                self._q.task_done()
