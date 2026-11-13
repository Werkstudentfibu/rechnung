"""Senden im Hintergrund (Wunsch Aaron 13.11.2026: "Koennen wir auch die
Bearbeitung nach dem ich die Rechnung ueberprueft habe in den Hintergrund
packen?").

Die schnelle Pruefung beim Klick auf "Senden" (Pflichtfelder,
Geschaeftsbereich, Dublettenverdacht) bleibt vorne -- dort muss der Mensch
entscheiden. Lernen, Freigeben und Aufraeumen laufen danach hier, eine
Rechnung nach der anderen, waehrend der Mensch schon die naechste prueft.

Offene Auftraege stehen in ``<data>/senden-warteschlange.json`` und werden
nach einem Neustart fortgesetzt -- wird das Programm mitten im Senden
geschlossen, bleibt keine Rechnung halb erledigt liegen. Fehlgeschlagene
Auftraege bleiben sichtbar, bis der Mensch sie gesehen hat; die Rechnung
selbst bleibt dabei im Eingang bzw. in 'Menschliche Bearbeitung'.
"""

from __future__ import annotations

import itertools
import json
import queue
import threading
import time
from pathlib import Path
from typing import Callable

WARTET, LAEUFT, FERTIG, FEHLER = "wartet", "laeuft", "fertig", "fehler"
DATEI = "senden-warteschlange.json"
BEHALTE_FERTIG = 50


def _jetzt() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _fehlertext(e: Exception) -> str:
    detail = getattr(e, "detail", None)
    return str(detail) if detail else f"{type(e).__name__}: {e}"


class SendeWarteschlange:
    def __init__(self, ausfuehren: Callable[[dict], dict | None], datei: Path | None = None):
        self._ausfuehren = ausfuehren
        self._datei = Path(datei) if datei else None
        self._q: "queue.Queue[str]" = queue.Queue()
        self._lock = threading.Lock()
        self._auftraege: dict[str, dict] = {}
        self._nr = itertools.count(1)
        self._thread: threading.Thread | None = None

    # ---- Auftraege -----------------------------------------------------
    def einreihen(self, auftrag: dict) -> dict:
        """``auftrag``: mindestens ``datei``. Laeuft fuer dieselbe Datei schon
        ein Auftrag, wird dieser zurueckgegeben (kein doppeltes Senden)."""
        with self._lock:
            for a in self._auftraege.values():
                if a["datei"] == auftrag["datei"] and a["status"] in (WARTET, LAEUFT):
                    return dict(a)
            a = dict(auftrag, id=f"{int(time.time() * 1000)}-{next(self._nr)}", status=WARTET,
                     detail="", angenommen=_jetzt(), gesehen=False)
            self._auftraege[a["id"]] = a
            self._speichern()
        self._q.put(a["id"])
        self._start()
        return dict(a)

    def fortsetzen(self) -> int:
        """Nach einem Neustart: offene Auftraege aus der Datei wieder einreihen."""
        if self._datei is None or not self._datei.exists():
            return 0
        try:
            offen = json.loads(self._datei.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return 0
        n = 0
        for a in offen if isinstance(offen, list) else []:
            if not isinstance(a, dict) or not a.get("datei"):
                continue
            if a.get("status") == FEHLER and not a.get("gesehen"):
                with self._lock:          # Hinweis "nicht gesendet" bleibt sichtbar
                    aid = a.get("id") or f"{int(time.time() * 1000)}-{next(self._nr)}"
                    self._auftraege[aid] = dict(a, id=aid)
            elif a.get("status") in (WARTET, LAEUFT):
                rest = {k: v for k, v in a.items() if k not in ("id", "status", "detail", "gesehen")}
                self.einreihen(dict(rest, fortgesetzt=True))
                n += 1
        return n

    def gesehen(self, aid: str) -> bool:
        with self._lock:
            a = self._auftraege.get(aid)
            if a is None or a["status"] != FEHLER:
                return False
            a["gesehen"] = True
            self._speichern()
            return True

    def laeuft_fuer(self, datei: str) -> bool:
        with self._lock:
            return any(a["datei"] == datei and a["status"] in (WARTET, LAEUFT)
                       for a in self._auftraege.values())

    def stand(self) -> dict:
        with self._lock:
            alle = list(self._auftraege.values())
        offen = [a for a in alle if a["status"] in (WARTET, LAEUFT)]
        return {
            "offen": len(offen),
            "aktuell": next((a["datei"] for a in offen if a["status"] == LAEUFT), ""),
            "dateien": [a["datei"] for a in offen],
            "fertig": sum(1 for a in alle if a["status"] == FERTIG),
            "fehler": [{"id": a["id"], "datei": a["datei"], "detail": a["detail"],
                        "ende": a.get("ende", "")}
                       for a in alle if a["status"] == FEHLER and not a.get("gesehen")],
        }

    def warte_bis_leer(self, timeout: float = 30.0) -> bool:
        ende = time.monotonic() + timeout
        while time.monotonic() < ende:
            if self._q.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return False

    # ---- Arbeit --------------------------------------------------------
    def _start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._schleife, name="senden-hintergrund",
                                            daemon=True)
            self._thread.start()

    def _schleife(self) -> None:
        while True:
            aid = self._q.get()
            try:
                with self._lock:
                    a = self._auftraege.get(aid)
                    if a is None:
                        continue
                    a["status"] = LAEUFT
                    a["beginn"] = _jetzt()
                    self._speichern()
                try:
                    erg = self._ausfuehren(dict(a))
                    status, detail = FERTIG, ""
                except Exception as e:  # noqa: BLE001 -- ein Fehler haelt die Schlange nicht auf
                    erg, status, detail = None, FEHLER, _fehlertext(e)
                with self._lock:
                    a.update(status=status, detail=detail, ergebnis=erg or {}, ende=_jetzt())
                    self._kuerzen()
                    self._speichern()
            finally:
                self._q.task_done()

    def _kuerzen(self) -> None:
        fertig = [k for k, a in self._auftraege.items() if a["status"] == FERTIG]
        for k in fertig[:-BEHALTE_FERTIG]:
            del self._auftraege[k]

    def _speichern(self) -> None:
        """Offene Auftraege und noch nicht gesehene Fehler auf die Platte
        (fuer ``fortsetzen``)."""
        if self._datei is None:
            return
        offen = [{k: v for k, v in a.items() if k != "ergebnis"}
                 for a in self._auftraege.values()
                 if a["status"] in (WARTET, LAEUFT) or (a["status"] == FEHLER and not a.get("gesehen"))]
        try:
            self._datei.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._datei.with_name(self._datei.name + ".tmp")
            tmp.write_text(json.dumps(offen, ensure_ascii=False, indent=1), "utf-8")
            tmp.replace(self._datei)
        except OSError:
            pass
