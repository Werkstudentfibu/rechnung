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


def run(store: Store, samples: Path, port: int = 8000,
        title: str = "Rechnung") -> None:
    import uvicorn
    import webview

    from .web.server import create_app

    app = create_app(store, Path(samples))
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    waited = 0.0
    while not server.started and waited < _STARTUP_TIMEOUT:
        time.sleep(_STARTUP_POLL)
        waited += _STARTUP_POLL

    webview.create_window(title, f"http://127.0.0.1:{port}", width=1360, height=900)
    webview.start()

    server.should_exit = True
    thread.join(timeout=2.0)
