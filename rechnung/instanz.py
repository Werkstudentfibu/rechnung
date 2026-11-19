"""Nur EIN Programmfenster je Datenordner (Serverbetrieb "Einzelplatz per
Remote", Aaron 19.11.2026).

Bisher startete ein zweiter Aufruf von ``rechnung app``/``rechnung web``
still auf einem anderen Port -- zwei Programme schrieben dann auf dieselben
Daten. Auf einem Laptop kam das kaum vor, auf einem Server mit
Remotedesktop leicht (Kollege meldet sich an, getrennte Sitzung laeuft
weiter und es wird neu gestartet). Jetzt haelt das offene Programm die
Datei ``<data>/programm.lock`` (wer, auf welchem Rechner, seit wann, Port)
und erneuert sie laufend; ein zweiter Start meldet, wer das Programm
offen hat, und startet nicht.

Nur das Programmfenster bzw. die Oberflaeche nimmt die Sperre --
Hintergrundaufgaben (Vorbereiten, Nachtlauf, Anlernen) laufen wie bisher
daneben (sie stimmen sich ueber ``lock.py`` ab).

Liegengeblieben (Absturz, Stromausfall, ``/api/neustart``): Die Sperre gilt
als frei, sobald der eingetragene Prozess nicht mehr laeuft ODER sie
laenger als ``VERALTET_S`` nicht erneuert wurde. Die Prozesspruefung nutzt
unter Windows ``OpenProcess`` -- ``os.kill(pid, 0)`` wuerde dort den Prozess
beenden (siehe lock.py)."""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

DATEI = "programm.lock"
ERNEUERN_S = 20.0
VERALTET_S = 120.0

_aktiv: "Sperre | None" = None


class LaeuftBereits(Exception):
    """Ein anderes Programmfenster arbeitet schon auf diesem Datenordner."""

    def __init__(self, info: dict):
        self.info = info
        super().__init__(meldung(info))


def meldung(info: dict) -> str:
    seit = info.get("seit", "")
    return ("Das Programm ist bereits geoeffnet "
            f"(Benutzer {info.get('benutzer') or '?'} auf {info.get('rechner') or '?'}, "
            f"seit {seit or '?'}, Prozess {info.get('pid') or '?'}). "
            "Bitte dort weiterarbeiten. Reagiert das Fenster nicht mehr: dort schliessen "
            "bzw. im Task-Manager beenden -- nach spaetestens "
            f"{int(VERALTET_S // 60)} Minuten ist die Sperre ohnehin frei.")


def _benutzer() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or ""


def _prozess_laeuft(pid: int) -> bool:
    """True, wenn der Prozess laeuft ODER sich das nicht feststellen laesst
    (fremder Benutzer ohne Rechte) -- dann entscheidet das Alter."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            # 5 = Zugriff verweigert: Prozess existiert, gehoert jemand anderem
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(h, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lesen(root) -> dict | None:
    """Inhalt der Sperre, wenn sie noch gilt -- sonst ``None``."""
    p = Path(root) / DATEI
    try:
        info = json.loads(p.read_text(encoding="utf-8"))
        alter = time.time() - p.stat().st_mtime
    except (OSError, ValueError):
        return None
    if alter > VERALTET_S or not _prozess_laeuft(int(info.get("pid") or 0)):
        return None
    return info


class Sperre:
    def __init__(self, root, port: int | None = None):
        self.pfad = Path(root) / DATEI
        self.info = {"pid": os.getpid(), "benutzer": _benutzer(), "rechner": socket.gethostname(),
                     "seit": time.strftime("%d.%m.%Y %H:%M"), "port": port}
        self._stopp = threading.Event()
        self._faden: threading.Thread | None = None

    def nehmen(self) -> "Sperre":
        global _aktiv
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.pfad, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                fremd = lesen(self.pfad.parent)
                if fremd is not None and int(fremd.get("pid") or 0) != os.getpid():
                    raise LaeuftBereits(fremd) from None
                # liegengeblieben -> wegraeumen und noch einmal
                try:
                    self.pfad.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.info, fh, ensure_ascii=False)
            break
        else:
            fremd = lesen(self.pfad.parent) or {}
            raise LaeuftBereits(fremd)
        self._faden = threading.Thread(target=self._erneuern, name="programm-lock", daemon=True)
        self._faden.start()
        _aktiv = self
        return self

    def _erneuern(self) -> None:
        while not self._stopp.wait(ERNEUERN_S):
            try:
                os.utime(self.pfad)
            except OSError:
                pass

    def port_setzen(self, port: int) -> None:
        self.info["port"] = port
        try:
            self.pfad.write_text(json.dumps(self.info, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def freigeben(self) -> None:
        global _aktiv
        self._stopp.set()
        try:
            info = json.loads(self.pfad.read_text(encoding="utf-8"))
            if int(info.get("pid") or 0) == os.getpid():
                self.pfad.unlink()
        except (OSError, ValueError):
            pass
        if _aktiv is self:
            _aktiv = None

    def __enter__(self) -> "Sperre":
        return self.nehmen()

    def __exit__(self, *exc) -> None:
        self.freigeben()


def freigeben() -> None:
    """Die Sperre dieses Prozesses freigeben (z.B. vor ``os._exit`` beim
    Neustart nach einem Update -- dort laufen keine ``finally``-Bloecke)."""
    if _aktiv is not None:
        _aktiv.freigeben()
