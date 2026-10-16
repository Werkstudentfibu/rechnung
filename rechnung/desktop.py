"""Startet die Training-UI als eigenstaendiges natives Fenster (pywebview)
statt im Browser-Tab.

Gleiche FastAPI-App wie `rechnung web` (siehe web/server.py) -- es aendert
sich nur, wo sie angezeigt wird. Die Bestaetigungspflicht (README, SPEC-v1.md
§7c) bleibt unveraendert: jede Rechnung braucht weiterhin einen expliziten
Klick im Fenster, bevor sie archiviert/gebucht wird. Uvicorn laeuft im
Hintergrund-Thread nur fuer 127.0.0.1, ohne Netzwerk-Freigabe.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from .store import Store

_STARTUP_TIMEOUT = 5.0
_STARTUP_POLL = 0.05


def freier_port(wunsch: int) -> int:
    """``wunsch``, wenn frei -- sonst ein vom System vergebener freier Port."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", wunsch))
            return wunsch
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run(store: Store, samples: Path, port: int = 8000,
        title: str = "Rechnung") -> None:
    import uvicorn
    import webview

    from .web.server import create_app

    app = create_app(store, Path(samples))
    # Zwischenspeicher fuer den Excel-Import (Befund Aaron 16.10.2026: eingelesene
    # Listen waren nach dem Schliessen weg). Faellt er aus, startet das Programm
    # trotzdem -- die Excel-Seite arbeitet dann wie bisher ohne Zwischenstand.
    try:
        from .stammdaten_entwurf import registrieren as entwurf_registrieren

        entwurf_registrieren(app, store)
    except Exception as e:  # noqa: BLE001
        print(f"Hinweis: Zwischenspeicher fuer den Excel-Import nicht verfuegbar ({e})")
    # Belegt ein alter, haengengebliebener Prozess den Port, wuerde sich das
    # Fenster sonst still mit DESSEN (altem) Stand verbinden -- Befund Aaron
    # 09.10.2026: Update installiert, Oberflaeche trotzdem alt. Dann lieber
    # einen freien Port nehmen.
    port = freier_port(port)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    waited = 0.0
    while not server.started and waited < _STARTUP_TIMEOUT:
        time.sleep(_STARTUP_POLL)
        waited += _STARTUP_POLL
    if not server.started:
        raise SystemExit(f"Programm-Server startete nicht (Port {port}).")

    # maximiert: 1360x900 ist auf einem Laptop mit Skalierung groesser als der Bildschirm
    webview.create_window(title, f"http://127.0.0.1:{port}", width=1360, height=900,
                          maximized=True)
    webview.start()

    server.should_exit = True
    thread.join(timeout=2.0)
