"""Zwischenspeicher fuer die Rechnungsliste (Befund Aaron 05.11.2026: nach dem
Neustart dauerte es Minuten, bis die Liste erschien).

Frueher rechnete ``GET /api/scan`` bei JEDEM Laden fuer JEDE Datei im Eingang
die komplette schnelle Verarbeitung (``_process`` + ``dispatch``) -- bei
einigen hundert Rechnungen auf dem Laptop Minuten, jedes Mal.

Jetzt merkt sich dieser Speicher das Listenergebnis je Datei (Schluessel:
sha256 des Inhalts) in ``data/liste-cache.json`` -- auch ueber den Neustart.
``abfrage`` liefert sofort, was aktuell ist; alles andere wird

1. zuerst in einem kleinen Zeitbudget direkt gerechnet (wenige Dateien sind
   dann wie frueher sofort richtig: die gerade bearbeitete, neue, kleine
   Eingaenge), und
2. was darueber hinausgeht, in einem Hintergrund-Thread NACHEINANDER (der
   Laptop hat keine Kerne zu verschenken) neu berechnet. Bis dahin steht der
   alte Stand bzw. "wird gelesen" in der Liste; die Oberflaeche laedt nach.

Ein Eintrag ist aktuell, solange sich nichts geaendert hat, was das Ergebnis
beeinflusst (``Listencache._stand``):

- Programmstand (Quelltexte, ``RECHNUNG_*``-Einstellungen) und Kalendertag,
- die globalen Stammdateien (Adressen, Kategorien, Spaltenwoerter,
  Stammdaten-Layouts, Betriebsmodus),
- der EIGENE Lieferant der Rechnung (Stammdatensatz, seine Vorlagen, Anzahl
  seiner uebernommenen Rechnungen = Dublettenpruefung),
- Korrekturen genau dieser Datei,
- bei Rechnungen ohne erkannten Lieferanten: alle bestaetigten Lieferanten,
- Alter (``MAX_ALTER_S``) als Sicherheitsnetz.
"""

from __future__ import annotations

import collections
import hashlib
import json
import os
import threading
import time
from datetime import date
from pathlib import Path
from typing import Callable

from . import vorbereitung

DATEI = "liste-cache.json"
FORMAT = 1
GLOBALE_DATEIEN = ("addresses.json", "categories.json", "spaltenwoerter.json",
                   "stammdaten_layouts.json", "betriebsmodus.json")
BUDGET_S = 2.0              # so lange darf ein Laden der Liste selbst rechnen
MAX_ALTER_S = 6 * 3600      # aelter -> im Hintergrund neu berechnen
ABBRUCH_NEU_S = 600         # abgebrochene Ergebnisse (Zeitgrenze) fruehestens dann erneut
SPEICHERN_ALLE = 25         # Eintraege zwischen zwei Schreibvorgaengen
STATUS_WIRD_GELESEN = "wird_gelesen"
LABEL_WIRD_GELESEN = "wird gelesen …"


def _sig(p: Path) -> tuple:
    try:
        st = p.stat()
    except OSError:
        return (0, 0)
    return (st.st_mtime_ns, st.st_size)


def _h(*teile) -> str:
    return hashlib.md5(repr(teile).encode("utf-8")).hexdigest()[:16]


def programmstand() -> str:
    """Aendert sich mit jedem Update des Programms (Quelltexte) und mit den
    ``RECHNUNG_*``-Einstellungen -- dann sind alte Ergebnisse nicht mehr gueltig."""
    from . import __version__

    pkg = Path(__file__).resolve().parent
    py = [_sig(p) for p in sorted(pkg.rglob("*.py"))]
    env = sorted((k, v) for k, v in os.environ.items() if k.startswith("RECHNUNG_"))
    return _h(__version__, py, env)


class _Kontext:
    """Momentaufnahme dessen, wovon die Ergebnisse abhaengen (je Abfrage einmal)."""

    def __init__(self, cache: "Listencache"):
        root = cache.root
        self.glob = _h(cache.programm, date.today().isoformat(),
                       [_sig(root / n) for n in GLOBALE_DATEIEN])
        self.lief, self.lief_fest = cache._lieferanten()
        self.vorlagen = cache._vorlagen()
        self.verarbeitet, self.verarbeitet_gesamt = cache._verarbeitet()

    def stand(self, sid: str) -> str:
        if sid and sid in self.lief:
            return _h(self.glob, sid, self.lief[sid], self.vorlagen.get(sid, ""),
                      self.verarbeitet.get(sid, 0))
        # kein (bekannter) Lieferant: haengt an allen bestaetigten Lieferanten
        return _h(self.glob, "", self.lief_fest, self.verarbeitet_gesamt)


class Listencache:
    def __init__(self, store, samples, rechne: Callable[[Path], dict | None], *,
                 pause: Callable[[], bool] | None = None, budget_s: float = BUDGET_S,
                 max_alter_s: float = MAX_ALTER_S):
        """``rechne(path)`` -> ``Result.to_dict()`` (schnelle Verarbeitung samt
        Entwurfs-Lieferant und ``dispatch``) oder ``None``, wenn die Datei weg ist.
        ``pause()``: True, solange der Mensch selbst rechnen laesst (Vorrang)."""
        self.store = store
        self.root = Path(store.root)
        self.samples = Path(samples)
        self._rechne = rechne
        self._pause = pause
        self.budget_s = budget_s
        self.max_alter_s = max_alter_s
        self.programm = programmstand()
        self.pfad = self.root / DATEI
        self._lock = threading.RLock()
        self._e: dict[str, dict] = {}          # sha -> Eintrag
        self._q: collections.deque[Path] = collections.deque()
        self._geplant: set[str] = set()
        self._aktuell = ""
        self._thread: threading.Thread | None = None
        self._ungespeichert = 0
        self._memo_lief: tuple | None = None
        self._memo_vorl: tuple | None = None
        self._vorl_datei: dict[tuple, str] = {}
        self._memo_proc: tuple | None = None
        self._ctx_lock = threading.Lock()      # die Merker oben nur ein Thread zugleich
        self.fehler = ""
        self._laden()

    # ---- Platte ----------------------------------------------------------
    def _laden(self) -> None:
        try:
            d = json.loads(self.pfad.read_text("utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(d, dict) and d.get("format") == FORMAT and isinstance(d.get("eintraege"), dict):
            self._e = {k: v for k, v in d["eintraege"].items()
                       if isinstance(v, dict) and isinstance(v.get("ergebnis"), dict)}

    def speichern(self) -> None:
        from .store import _atomic_write

        with self._lock:
            daten = {"format": FORMAT, "eintraege": {k: v for k, v in self._e.items()
                                                     if not v["ergebnis"].get("aborted")}}
            self._ungespeichert = 0
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                _atomic_write(self.pfad, json.dumps(daten, ensure_ascii=False))
            except OSError as e:
                self.fehler = f"Listenspeicher nicht schreibbar: {e}"

    def vergessen(self, sid: str) -> int:
        """Eintraege eines Lieferanten verwerfen (entwurf_uebernahme.py: der
        Entwurf ist weg, seine Rechnungen gehoeren jetzt zum Ziel) -> Anzahl.
        Ueber ``_Kontext.stand`` veralten sie ohnehin, so stehen sie aber
        sofort als "wird gelesen" statt mit dem alten Lieferanten."""
        with self._lock:
            weg = [sha for sha, e in self._e.items() if sid and e.get("sid") == sid]
            for sha in weg:
                del self._e[sha]
            self._ungespeichert += len(weg)
        if weg:
            self.speichern()
        return len(weg)

    # ---- Momentaufnahmen (je mit Merker nach Dateistand: billig) --------------
    def _lieferanten(self) -> tuple[dict[str, str], str]:
        p = self.root / "suppliers.json"
        sig = _sig(p)
        if self._memo_lief is None or self._memo_lief[0] != sig:
            try:
                rows = json.loads(p.read_text("utf-8"))
            except (OSError, ValueError):
                rows = []
            lief, fest = {}, []
            for d in rows if isinstance(rows, list) else []:
                if isinstance(d, dict) and d.get("id"):
                    lief[d["id"]] = _h(json.dumps(d, sort_keys=True, ensure_ascii=False))
                    if d.get("bestaetigt", True) is not False:
                        fest.append((d["id"], lief[d["id"]]))
            self._memo_lief = (sig, lief, _h(sorted(fest)))
        return self._memo_lief[1], self._memo_lief[2]

    def _vorlagen(self) -> dict[str, str]:
        ordner = self.root / "templates"
        try:
            dateien = sorted(ordner.glob("*.json"))
        except OSError:
            dateien = []
        sigs = [(p.name, _sig(p)) for p in dateien]
        if self._memo_vorl is None or self._memo_vorl[0] != sigs:
            je: dict[str, list] = {}
            for p, (name, sig) in zip(dateien, sigs):
                key = (name, sig)
                sid = self._vorl_datei.get(key)
                if sid is None:
                    try:
                        sid = str(json.loads(p.read_text("utf-8")).get("supplier_id") or "")
                    except (OSError, ValueError, AttributeError):
                        sid = ""
                    self._vorl_datei[key] = sid
                je.setdefault(sid, []).append(key)
            if len(self._vorl_datei) > 5000:
                self._vorl_datei = {k: v for k, v in self._vorl_datei.items() if k in set(sigs)}
            self._memo_vorl = (sigs, {sid: _h(v) for sid, v in je.items()})
        return self._memo_vorl[1]

    def _verarbeitet(self) -> tuple[dict[str, int], int]:
        from .store import _read_jsonl_safe

        p = self.root / "processed.jsonl"
        sig = _sig(p)
        if self._memo_proc is None or self._memo_proc[0] != sig:
            n: dict[str, int] = collections.Counter()
            rows = _read_jsonl_safe(p)
            for r in rows:
                n[r.get("supplier_id") or ""] += 1
            self._memo_proc = (sig, dict(n), len(rows))
        return self._memo_proc[1], self._memo_proc[2]

    def _korrektur(self, sha: str) -> tuple:
        return _sig(self.root / "corrections" / f"{sha}.json")

    def _kontext(self) -> _Kontext:
        with self._ctx_lock:
            return _Kontext(self)

    # ---- Abfrage ------------------------------------------------------------
    @staticmethod
    def platzhalter(p: Path) -> dict:
        return {"file": str(p), "status": STATUS_WIRD_GELESEN, "status_label": LABEL_WIRD_GELESEN,
                "hold_reasons": [], "warnungen": [], "hinweise": [], "fields": {},
                "supplier_id": None, "supplier_name": "", "template_id": None,
                "template_secured": False, "overall_confidence": 0, "line_items": []}

    def _frisch(self, e: dict | None, ctx: _Kontext, sha: str) -> bool:
        if not e or e.get("stand") is None:
            return False
        if e["stand"] != ctx.stand(e.get("sid", "")) or e.get("korr") != list(self._korrektur(sha)):
            return False
        alter = time.time() - e.get("zeit", 0)
        return alter < (ABBRUCH_NEU_S if e["ergebnis"].get("aborted") else self.max_alter_s)

    def abfrage(self, dateien: list[Path], heiss: set[str] = frozenset()) -> tuple[dict[str, dict], int]:
        """-> ({Dateiname: Listenergebnis}, Anzahl noch nicht aktueller Dateien).

        ``heiss``: Namen, die der Mensch gerade bearbeitet -- werden immer
        sofort neu gerechnet, wenn sie nicht aktuell sind."""
        ctx = self._kontext()
        out: dict[str, dict] = {}
        offen: list[tuple[int, int, Path, str]] = []
        shas: set[str] = set()
        for i, p in enumerate(dateien):
            sha = vorbereitung.datei_sha(p)
            shas.add(sha)
            with self._lock:
                e = self._e.get(sha)
            if self._frisch(e, ctx, sha):
                out[p.name] = dict(e["ergebnis"], file=str(p))
                continue
            # 0 gerade bearbeitet, 1 Korrektur dieser Datei geaendert (beide immer sofort), 2 neu, 3 veraltet
            klasse = (0 if p.name in heiss else
                      1 if e and e.get("korr") != list(self._korrektur(sha)) else
                      2 if not e else 3)
            offen.append((klasse, i, p, sha))
            out[p.name] = dict(e["ergebnis"], file=str(p)) if e else self.platzhalter(p)
        offen.sort(key=lambda t: (t[0], t[1]))

        from . import lock

        t0 = time.monotonic()
        rest: list[Path] = []
        nachtlauf = lock.is_locked(self.root)
        for klasse, _i, p, sha in offen:
            with self._lock:
                laeuft = p.name == self._aktuell
            if laeuft or nachtlauf or (klasse > 1 and time.monotonic() - t0 >= self.budget_s):
                rest.append(p)
                continue
            r = self.berechne(p)
            if r is not None:
                out[p.name] = dict(r, file=str(p))
            else:
                rest.append(p)
        with self._lock:
            # Eintraege geloeschter/verschobener Dateien verwerfen (nur wenn nicht gerade
            # ein Teil der Liste fehlt: die Abfrage bekommt immer den ganzen Eingang)
            for sha in [s for s in self._e if s not in shas]:
                del self._e[sha]
                self._ungespeichert += 1
        if rest:
            self.einreihen(rest)
        elif self._ungespeichert:
            self.speichern()
        return out, self.offen() if rest else 0

    # ---- Berechnen ------------------------------------------------------------
    def berechne(self, p: Path) -> dict | None:
        """Eine Datei jetzt rechnen und den Eintrag ablegen. ``None``: Datei weg."""
        sha = vorbereitung.datei_sha(p)
        vor = self._kontext()
        korr = list(self._korrektur(sha))
        r = self._rechne(p)
        if r is None or not p.is_file() or vorbereitung.datei_sha(p) != sha:
            return r
        nach = self._kontext()
        sid = r.get("supplier_id") or ""
        # Haben sich waehrend der Rechnung die globalen Stammdateien geaendert,
        # ist das Ergebnis verdaechtig -> gilt als veraltet (wird neu gerechnet)
        stand = None if vor.glob != nach.glob else nach.stand(sid)
        with self._lock:
            self._e[sha] = {"stand": stand, "sid": sid, "korr": korr, "zeit": time.time(),
                            "name": p.name, "ergebnis": r}
            self._ungespeichert += 1
            if self._ungespeichert >= SPEICHERN_ALLE:
                self.speichern()
        return r

    # ---- Hintergrund ---------------------------------------------------------
    def einreihen(self, paths) -> None:
        with self._lock:
            for p in paths:
                p = Path(p)
                if p.name in self._geplant or not p.is_file():
                    continue
                self._geplant.add(p.name)
                self._q.append(p)
            if self._q and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(target=self._schleife, name="listencache", daemon=True)
                self._thread.start()

    def offen(self) -> int:
        with self._lock:
            return len(self._geplant) + (1 if self._aktuell else 0)

    def _schleife(self) -> None:
        from . import lock
        from .vorverarbeitung import _niedrige_prioritaet

        _niedrige_prioritaet()
        while True:
            with self._lock:
                if not self._q:
                    self._thread = None
                    self._aktuell = ""
                    break
                p = self._q.popleft()
                self._geplant.discard(p.name)
                self._aktuell = p.name
            try:
                while ((self._pause is not None and self._pause())     # Klick des Menschen hat Vorrang
                       or lock.is_locked(self.root)):                  # Nachtlauf laeuft
                    time.sleep(0.2)
                sha = vorbereitung.datei_sha(p)
                with self._lock:
                    e = self._e.get(sha)
                if p.is_file() and not self._frisch(e, self._kontext(), sha):
                    self.berechne(p)
            except Exception as ex:  # noqa: BLE001 -- eine kaputte Datei haelt die Schlange nicht auf
                self.fehler = f"{p.name}: {type(ex).__name__}: {ex}"
            finally:
                with self._lock:
                    self._aktuell = ""
        self.speichern()

    def warte_bis_leer(self, timeout: float = 30.0) -> bool:
        """Fuer Tests: blockiert, bis nichts mehr geplant ist und nichts laeuft."""
        ende = time.monotonic() + timeout
        while time.monotonic() < ende:
            t = self._thread
            if self.offen() == 0 and (t is None or not t.is_alive()):
                return True
            time.sleep(0.02)
        return False
