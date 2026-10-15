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
    def __init__(self, dest: Path, minuten: float, fetch: Callable | None = None,
                 bei_neuen: Callable | None = None,
                 mailboxes: list[tuple[str, object, str | None]] | None = None,
                 store=None):
        self.dest = Path(dest)
        # z.B. Vorverarbeitung im Hintergrund anstossen (vorverarbeitung.py)
        self.bei_neuen = bei_neuen
        self.minuten = minuten
        self._fetch = fetch
        # Mehrere Postfaecher (Entscheidung Aaron 15.10.2026, ~50 IONOS-
        # Postfaecher): [(Anzeigename, ImapConfig, Mandant-Id oder None)].
        # ``None`` (Standardfall, ein einzelnes ENV-Postfach) laesst das
        # Verhalten/den Status-Umfang unveraendert wie vor dieser Erweiterung.
        self.mailboxes = mailboxes
        self.store = store
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
        if self.mailboxes:
            return self._fetch_alle_postfaecher()
        from .ingest import fetch
        return fetch(dest=self.dest, warten=False)

    def _fetch_alle_postfaecher(self):
        """Alle konfigurierten Postfaecher NACHEINANDER (nicht parallel --
        schwacher Laptop, SPEC-Vorgabe). Ein kaputtes Postfach (Login falsch,
        Server weg) blockiert die anderen nicht -- der Fehler wird je
        Postfach vermerkt (``self.status['postfaecher']``), die Schleife
        laeuft weiter."""
        from .ingest import FetchResult, fetch

        saved: list[str] = []
        human: list[str] = []
        mails = 0
        fehler_teile: list[str] = []
        je_postfach: list[dict] = []
        for label, cfg, mandant_id in self.mailboxes:
            try:
                r = fetch(cfg=cfg, dest=self.dest, warten=False, store=self.store,
                         mandant_address_id=mandant_id)
                je_postfach.append({"postfach": label, "ok": not r.error,
                                    "fehler": r.error, "neu": len(r.saved), "mails": r.mails})
                saved += r.saved
                human += r.human
                mails += r.mails
                if r.error:
                    fehler_teile.append(f"{label}: {r.error}")
            except Exception as e:  # noqa: BLE001 -- ein Postfach darf die anderen nie blockieren
                msg = f"{type(e).__name__}: {e}"
                je_postfach.append({"postfach": label, "ok": False, "fehler": msg,
                                    "neu": 0, "mails": 0})
                fehler_teile.append(f"{label}: {msg}")
        self.status["postfaecher"] = je_postfach
        return FetchResult(saved, mails, False, "; ".join(fehler_teile), human)

    def _jobrouter_nachschicken(self) -> None:
        """Freigegebene Rechnungen an JobRouter -- nur mit echter Anbindung
        (sonst bleiben sie in der Warteschlange, siehe push_queue)."""
        from .config import data_dir, jobrouter
        cfg = jobrouter()
        if not cfg.live:
            return
        from .jobrouter import push_queue
        r = push_queue(data_dir(), cfg, trockenlauf_aufheben=True)
        self.status["jobrouter_gesendet"] = self.status.get("jobrouter_gesendet", 0) + r["sent"]
        offen = [x for x in r["results"] if not x.get("sent") and not x.get("skipped")]
        self.status["jobrouter_fehler"] = (offen[0].get("error") or "") if offen else ""

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
                neu = [Path(s) for s in res.saved if not str(s).startswith("(dry)")]
                if neu and self.bei_neuen is not None:
                    self.bei_neuen(neu)
            except Exception as e:  # Netz weg, Login falsch ... -> naechster Takt
                self.status["fehler"] = f"{type(e).__name__}: {e}"
            if self._fetch is None:   # nur im echten Betrieb, nicht in Tests mit Attrappe
                try:
                    self._jobrouter_nachschicken()
                except Exception as e:  # noqa: BLE001
                    self.status["jobrouter_fehler"] = f"{type(e).__name__}: {e}"
                try:
                    from . import lernbericht
                    from .config import data_dir
                    from .store import Store
                    st = Store(data_dir())
                    from . import sicherung
                    if sicherung.faellig():
                        try:
                            self.status["sicherung"] = sicherung.sichern(st.root)
                        except OSError as e:
                            self.status["sicherung"] = {"ok": False, "detail": str(e)}
                    if lernbericht.faellig(st):
                        self.status["lernbericht"] = lernbericht.senden(st)
                except Exception as e:  # noqa: BLE001 -- Bericht darf Abholung nie stoeren
                    self.status["lernbericht"] = {"gesendet": False, "grund": f"{type(e).__name__}: {e}"}
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


def aus_konfiguration(dest: Path, store=None, bei_neuen: Callable | None = None) -> Abholer | None:
    """Abholer fuer die App -- nur, wenn mindestens ein Postfach eingerichtet
    ist (ENV-Postfach 1 und/oder in ``store`` gepflegte, Entscheidung Aaron
    15.10.2026). Ohne ``store`` (z.B. bestehende Tests) wird nur das alte
    ENV-Postfach geprueft -- unveraendertes Verhalten."""
    from .config import ImapConfig, imap_poll_minutes

    if store is not None:
        from .config import postfaecher as postfaecher_cfg
        mailboxes = postfaecher_cfg(store)
    else:
        mailboxes = [("Postfach 1 (ENV)", ImapConfig(), None)] if ImapConfig().configured else []
    if not mailboxes:
        return None
    minuten = imap_poll_minutes()
    if len(mailboxes) == 1 and mailboxes[0][2] is None:
        # Einzelnes ENV-Postfach ohne Mandant-Zuordnung -- Verhalten wie vor
        # der Mehrfach-Postfach-Erweiterung (bestehende Tests pruefen genau
        # dieses Verhalten ueber ``self.status`` ohne ``postfaecher``-Key).
        return Abholer(dest, minuten, bei_neuen=bei_neuen)
    return Abholer(dest, minuten, bei_neuen=bei_neuen, mailboxes=mailboxes, store=store)
