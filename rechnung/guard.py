"""Zeitgrenze pro Rechnung (SPEC §7m).

Jede Rechnung bekommt fuer die komplette Verarbeitung -- Lieferant/Vorlage,
OCR, KI, Pruefungen -- hoechstens ``RECHNUNG_TIMEOUT`` Sekunden (Standard
120). Wird es laenger, bricht ``Guard`` ab, stellt die Rechnung zurueck
(Status ``zurueckgestellt``, Grund im Ergebnis und im Audit-Log) und macht
mit der naechsten weiter: eine haengende Rechnung blockiert die Schlange nie.

Zwei Betriebsarten:

- ``"process"`` (Standard fuer Stapellaeufe: ``rechnung scan``, Nachtlauf):
  ein eigener Arbeitsprozess verarbeitet die Rechnungen nacheinander. Bei
  Zeitueberschreitung wird er hart beendet und fuer die naechste Rechnung
  neu gestartet -- auch eine in C-Code (PDF-Parser) festhaengende Rechnung
  ist damit sicher weg. Ein Absturz des Arbeitsprozesses trifft nur die
  eine Rechnung.
- ``"thread"`` (Training-UI): Verarbeitung in einem Hintergrund-Thread im
  selben Prozess. Noetig, weil die UI den KI-Antwort-Cache mit
  ``/api/invoice`` teilen muss (bestaetigt wird exakt, was angezeigt wurde,
  §7j). Ein abgelaufener Thread laeuft im Hintergrund aus; OCR und KI haben
  eigene Zeitgrenzen, er haengt also nicht ewig.

``timeout=0`` schaltet die Zeitgrenze ab (Verarbeitung direkt im Aufrufer).
"""

from __future__ import annotations

import hashlib
import importlib
import multiprocessing as mp
import threading
import time
from pathlib import Path

from .config import operator as _operator
from .config import process_timeout

DEFAULT_TARGET = "rechnung.pipeline:process"


def _resolve(target: str):
    mod, _, name = target.partition(":")
    return getattr(importlib.import_module(mod), name)


def _worker(conn, data_root: str, target: str) -> None:
    """Arbeitsprozess: Rechnungen nacheinander verarbeiten, bis der Elternteil
    die Leitung schliesst oder ``None`` schickt."""
    from .store import Store

    func = _resolve(target)
    while True:
        try:
            msg = conn.recv()
        except (EOFError, OSError):
            return
        if msg is None:
            return
        path, use_llm = msg
        try:
            conn.send(("ok", func(path, Store(data_root), use_llm=use_llm)))
        except Exception as e:  # noqa: BLE001 -- jede Rechnung einzeln abfangen
            conn.send(("err", f"{type(e).__name__}: {e}"))


def _sha(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return ""


def aborted_result(path, reason: str, kind: str, duration: float):
    """Ergebnis fuer eine abgebrochene Rechnung: zurueckgestellt, ohne Werte,
    nie "wuerde durchlassen"."""
    from .pipeline import DEFERRED, Result

    res = Result(file=str(path), sha256=_sha(Path(path)), status=DEFERRED,
                 hold_reasons=[reason], duration_s=round(duration, 2), aborted=kind)
    res.shadow = {"would_pass": False, "reasons": [reason], "new_supplier": True,
                  "values": {}, "sources": {}}
    return res


class Guard:
    def __init__(self, data_root, timeout: float | None = None, mode: str = "process",
                 target: str = DEFAULT_TARGET):
        self.data_root = str(data_root)
        self.timeout = process_timeout() if timeout is None else max(0.0, float(timeout))
        self.mode = mode
        self.target = target
        self._proc = None
        self._conn = None
        self._lock = threading.Lock()

    # ---- Arbeitsprozess ------------------------------------------------
    def _start(self) -> None:
        # "spawn" statt fork: sicher in einem Prozess mit Threads (uvicorn)
        # und identisch unter Windows.
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        self._proc = ctx.Process(target=_worker, args=(child, self.data_root, self.target),
                                 daemon=True, name="rechnung-worker")
        self._proc.start()
        child.close()
        self._conn = parent

    def _kill(self) -> None:
        if self._proc is not None:
            self._proc.kill()
            self._proc.join(5)
        if self._conn is not None:
            self._conn.close()
        self._proc = self._conn = None

    def close(self) -> None:
        if self._conn is not None and self._proc is not None and self._proc.is_alive():
            try:
                self._conn.send(None)
                self._proc.join(5)
            except (OSError, BrokenPipeError):
                pass
        self._kill()

    def __enter__(self) -> "Guard":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- Verarbeitung --------------------------------------------------
    def process(self, path, use_llm: bool = True):
        from . import lock
        from .store import Store

        # Nachtlauf-Lock (lock.py, SPEC §7r): laeuft gerade ``rechnung
        # nacht``, pausiert die Verarbeitung, statt parallel auf denselben
        # Daten zu schreiben. Ohne Lock-Datei (Normalfall) kein Zeitverlust.
        lock.wait_until_unlocked(self.data_root)
        t0 = time.monotonic()
        with self._lock:
            if self.timeout <= 0:
                res = _resolve(self.target)(path, Store(self.data_root), use_llm=use_llm)
                res.duration_s = round(time.monotonic() - t0, 2)
                return res
            kind, payload = (self._run_process(path, use_llm) if self.mode == "process"
                             else self._run_thread(path, use_llm))
        duration = time.monotonic() - t0
        if kind == "ok":
            payload.duration_s = round(duration, 2)
            return payload
        if kind == "timeout":
            reason = (f"Zeitgrenze {self.timeout:g} s ueberschritten -- "
                      f"Verarbeitung abgebrochen")
            res = aborted_result(path, reason, "zeitgrenze", duration)
        else:
            res = aborted_result(path, f"Verarbeitungsfehler: {payload}", "fehler", duration)
        Store(self.data_root).audit("verarbeitung_abgebrochen", {
            "file": Path(path).name, "sha256": res.sha256, "art": res.aborted,
            "grund": res.hold_reasons[0], "dauer_s": res.duration_s,
        }, _operator())
        return res

    def _run_process(self, path, use_llm: bool):
        if self._proc is None or not self._proc.is_alive():
            self._kill()
            self._start()
        try:
            self._conn.send((str(path), use_llm))
            if not self._conn.poll(self.timeout):
                self._kill()   # haengt -> hart beenden, naechste Rechnung neuer Prozess
                return "timeout", None
            return self._conn.recv()
        except (EOFError, OSError, BrokenPipeError) as e:
            self._kill()
            return "err", f"Arbeitsprozess abgestuerzt ({type(e).__name__})"

    def _run_thread(self, path, use_llm: bool):
        from .store import Store

        box: dict = {}
        func = _resolve(self.target)

        def run():
            try:
                box["res"] = ("ok", func(path, Store(self.data_root), use_llm=use_llm))
            except Exception as e:  # noqa: BLE001
                box["res"] = ("err", f"{type(e).__name__}: {e}")

        t = threading.Thread(target=run, daemon=True, name="rechnung-verarbeitung")
        t.start()
        t.join(self.timeout)
        if t.is_alive():
            return "timeout", None
        return box["res"]
