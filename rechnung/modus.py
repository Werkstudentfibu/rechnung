"""Betriebsmodus: Testphase -> Autonomie, programmweit (SPEC §7v).

Bewusst schlank: jede Rechnung wird in JobRouter ohnehin noch einmal von
einem Menschen freigegeben -- das ist die eigentliche Sicherung. Deshalb
keine strengen Zusatzsicherungen, sondern:

- ``testphase`` (Standard): Bestaetigungspflicht wie bisher (§7c), der
  Schattenbetrieb (shadow.py) misst, ob das Programm allein richtig gelegen
  haette. ``fortschritt()`` zeigt, wie weit es vom Richtwert entfernt ist
  (Standard: 50 sichere Rechnungen in Folge ohne Korrektur von mindestens 10
  verschiedenen Lieferanten, kein Mindestzeitraum).
- ``autonom``: alles, was nicht unsicher ist (human.gruende leer), geht ohne
  Klick durch ``pipeline.finalize`` in die JobRouter-Warteschlange; nur
  Unsicheres landet im Ordner "Menschliche Bearbeitung".

Umgeschaltet wird **nur** per ausdruecklichem Befehl
``rechnung modus autonom|testphase --operator NAME`` mit Audit-Eintrag --
nie automatisch, auch nicht beim Erreichen des Richtwerts. Optional (Standard
aus): Stichprobe (``RECHNUNG_AUTONOM_STICHPROBE``, Prozent sicherer
Rechnungen, die trotzdem zum Menschen gehen) und automatischer Rueckfall in
die Testphase bei einer JobRouter-Korrektur (``RECHNUNG_AUTONOM_RUECKFALL``).
"""

from __future__ import annotations

import json
import os
import time

TESTPHASE = "testphase"
AUTONOM = "autonom"
MODES = (TESTPHASE, AUTONOM)
STATE_FILE = "betriebsmodus.json"

# Operator-Name fuer alles, was im autonomen Modus ohne Klick passiert
AUTONOM_OPERATOR = "autonom"


def _int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def ziel_in_folge() -> int:
    """Richtwert: sichere Rechnungen in Folge ohne Korrektur."""
    return _int_env("RECHNUNG_TESTPHASE_IN_FOLGE", 50)


def ziel_lieferanten() -> int:
    """Richtwert: so viele verschiedene Lieferanten in dieser Folge."""
    return _int_env("RECHNUNG_TESTPHASE_LIEFERANTEN", 10)


def stichprobe_prozent() -> int:
    """Optional: Anteil sicherer Rechnungen (0-100), die im autonomen Modus
    trotzdem zum Menschen gehen. Standard 0 = aus."""
    return min(100, _int_env("RECHNUNG_AUTONOM_STICHPROBE", 0))


def rueckfall_aktiv() -> bool:
    """Optional: bei einer JobRouter-Korrektur automatisch zurueck in die
    Testphase. Standard aus."""
    return os.environ.get("RECHNUNG_AUTONOM_RUECKFALL", "0").strip().lower() in (
        "1", "true", "yes", "on", "ja")


def _state_path(store):
    return store.root / STATE_FILE


def state(store) -> dict:
    p = _state_path(store)
    try:
        d = json.loads(p.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        d = {}
    if d.get("modus") not in MODES:
        d = {"modus": TESTPHASE, "seit": None, "operator": None}
    return d


def get(store) -> str:
    return state(store)["modus"]


def is_autonomous(store) -> bool:
    return get(store) == AUTONOM


def set_mode(store, modus: str, operator: str, grund: str = "") -> dict:
    """Modus ausdruecklich umschalten. ``operator`` ist Pflicht (Audit)."""
    from .store import _atomic_write

    if modus not in MODES:
        raise ValueError(f"unbekannter Modus {modus!r} (erlaubt: {', '.join(MODES)})")
    if not (operator or "").strip():
        raise ValueError("Umschalten nur mit Namen (--operator NAME)")
    alt = get(store)
    fp = fortschritt(store)
    new = {"modus": modus, "seit": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "operator": operator.strip(), "grund": grund}
    _atomic_write(_state_path(store), json.dumps(new, indent=2, ensure_ascii=False))
    store.audit("betriebsmodus", {
        "alt": alt, "neu": modus, "grund": grund,
        "richtwert_erreicht": fp["erreicht"], "in_folge": fp["in_folge"],
        "lieferanten_in_folge": fp["lieferanten_in_folge"],
    }, operator.strip())
    return dict(new, alt=alt, fortschritt=fp)


def stichprobe(sha256: str) -> bool:
    """Deterministisch (gleiche Rechnung -> gleiche Entscheidung): geht diese
    sichere Rechnung trotzdem zum Menschen?"""
    p = stichprobe_prozent()
    if p <= 0 or not sha256:
        return False
    try:
        return int(sha256[:8], 16) % 100 < p
    except ValueError:
        return False


def fortschritt(store) -> dict:
    """Wie weit ist der Richtwert erreicht? Grundlage ist der
    Schattenbetrieb (je Rechnung die erste Entscheidung, ggf. von einer
    spaeteren JobRouter-Rueckmeldung zur Fehlentscheidung gemacht):

    - sicher + ohne Korrektur -> zaehlt zur Folge,
    - Fehlentscheidung (haette durchgelassen, Mensch/JobRouter hat
      korrigiert) -> Folge beginnt von vorn,
    - unsicher (ging zum Menschen) -> zaehlt nicht, unterbricht aber auch
      nicht: "nicht sicher" zu sagen ist kein Fehler.
    """
    order: list[str] = []
    latest: dict[str, dict] = {}
    for r in store.shadow_log():
        key = r.get("sha256") or r.get("file") or ""
        if key not in latest:
            order.append(key)
        latest[key] = r
    streak: list[dict] = []
    last_wrong = None
    for key in order:
        r = latest[key]
        if r.get("wrong"):
            streak = []
            last_wrong = {"datei": r.get("file"), "lieferant": r.get("supplier_id"),
                          "korrigiert": r.get("corrected") or [], "ts": r.get("ts")}
        elif r.get("would_pass") and not r.get("corrected"):
            streak.append(r)
    lieferanten = {r.get("supplier_id") for r in streak if r.get("supplier_id")}
    z_folge, z_lief = ziel_in_folge(), ziel_lieferanten()
    st = state(store)
    return {
        "modus": st["modus"],
        "modus_seit": st.get("seit"),
        "modus_operator": st.get("operator"),
        "in_folge": len(streak),
        "ziel_in_folge": z_folge,
        "lieferanten_in_folge": len(lieferanten),
        "ziel_lieferanten": z_lief,
        "erreicht": len(streak) >= z_folge and len(lieferanten) >= z_lief,
        "letzte_fehlentscheidung": last_wrong,
        "rechnungen_gesamt": len(order),
    }


def text(fp: dict) -> str:
    """Einzeiler fuer Konsole/Bericht."""
    head = f"Modus: {fp['modus']}"
    prog = (f"{fp['in_folge']}/{fp['ziel_in_folge']} sichere Rechnungen in Folge ohne "
            f"Korrektur, {fp['lieferanten_in_folge']}/{fp['ziel_lieferanten']} Lieferanten")
    tail = " -- Richtwert erreicht" if fp["erreicht"] else ""
    return f"{head} · {prog}{tail}"
