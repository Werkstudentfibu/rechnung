"""Leerlauf-Arbeit (Aaron 19.11.2026: "wenn Kapazitaeten frei sind, weil alle
Rechnungen bearbeitet sind, die bisherigen Rechnungen analysieren").

Laeuft am Ende von ``rechnung vorbereiten`` (geplante Aufgabe), wenn im
Eingang nichts mehr wartet -- standardmaessig nur nachts. Zwei Aufgaben, in
dieser Reihenfolge:

1. **Offene Rechnungen neu lesen**, wenn sich der Lernstand seit ihrer
   Vorbereitung geaendert hat (neue/ergaenzte Vorlagen, Spaltenwoerterbuch,
   Stammdaten, bestaetigte Rechnungen): morgens zeigt die Liste, was die
   Bestaetigungen vom Vortag gebracht haben. Gleicher Weg wie das Neu-Auswerten
   nach dem Bestaetigen (``vorbereitung.neu_auswerten``).
2. **Blindtest in Etappen** (``testsatz.blind_etappe``), wenn der letzte
   vollstaendige Blindtest aelter ist als ``RECHNUNG_LEERLAUF_BLINDTEST_TAGE``
   (Standard 7): misst laufend, ob die Qualitaet steigt oder faellt -- das
   Ergebnis steht im Nachtbericht (``bericht``).

Grenzen (fest):
- Nur lesen und messen. Es wird nichts bestaetigt, gesendet, freigegeben oder
  gelernt -- Lernen bleibt an die Bestaetigung eines Menschen gebunden.
- Neue Rechnungen haben Vorrang: vor jeder einzelnen Rechnung wird geprueft,
  ob im Eingang etwas Neues wartet; dann hoert der Leerlauf sofort auf.
- Zeitfenster je Durchgang (``RECHNUNG_LEERLAUF_MINUTEN``, Standard 40 --
  kuerzer als der naechtliche Takt der Aufgabe, damit die naechste Abholung
  nicht wartet). Der Blindtest macht beim naechsten Durchgang weiter.

Einstellungen: ``RECHNUNG_LEERLAUF`` = Stunden "20-6" (Standard, nachts),
"immer" oder "aus".
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path

ORDNER = "leerlauf"
STAND_DATEI = "stand.json"


# ---- Einstellungen ------------------------------------------------------------

def _stunden() -> tuple[int, int] | None | str:
    v = os.environ.get("RECHNUNG_LEERLAUF", "20-6").strip().lower()
    if v in ("0", "aus", "nein", "false", "off", ""):
        return None
    if v in ("1", "immer", "an", "ja", "true", "on"):
        return "immer"
    try:
        a, b = (int(x) for x in v.split("-", 1))
        return a % 24, b % 24
    except ValueError:
        return 20, 6


def jetzt_erlaubt(jetzt: datetime | None = None) -> bool:
    s = _stunden()
    if s is None:
        return False
    if s == "immer":
        return True
    h = (jetzt or datetime.now()).hour
    a, b = s
    return a <= h < b if a < b else (h >= a or h < b)


def budget_s() -> float:
    try:
        return max(1.0, float(os.environ.get("RECHNUNG_LEERLAUF_MINUTEN", "40"))) * 60
    except ValueError:
        return 40 * 60.0


def blindtest_tage() -> int:
    try:
        return max(0, int(os.environ.get("RECHNUNG_LEERLAUF_BLINDTEST_TAGE", "7")))
    except ValueError:
        return 7


def blindtest_max() -> int:
    try:
        return max(0, int(os.environ.get("RECHNUNG_LEERLAUF_BLINDTEST_MAX", "0")))
    except ValueError:
        return 0


# ---- Lernstand ----------------------------------------------------------------

def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def lernstand(root) -> float:
    """Zeitpunkt der letzten Aenderung an allem, was das Lesen einer Rechnung
    beeinflusst (Vorlagen, Spaltenwoerterbuch, Stammdaten, Rechnungsadressen,
    bestaetigte Rechnungen fuer das Lieferanten-Gedaechtnis)."""
    root = Path(root)
    werte = [_mtime(root / n) for n in ("suppliers.json", "addresses.json", "spaltenwoerter.json")]
    for ordner in ("templates", "testsatz"):
        d = root / ordner
        werte.append(_mtime(d))
        if d.is_dir():
            werte.extend(_mtime(p) for p in d.glob("*.json"))
    return max(werte or [0.0])


def _ausgewertet(m: dict) -> float:
    zeit = m.get("neu_ausgewertet") or m.get("zeit") or ""
    try:
        return datetime.strptime(zeit, "%Y-%m-%dT%H:%M:%S").timestamp()
    except ValueError:
        return 0.0


def neu_zu_lesen(store, samples) -> list[Path]:
    """Vorbereitete, noch offene Rechnungen, die VOR der letzten Aenderung am
    Lernstand ausgewertet wurden -- die am laengsten nicht ausgewerteten zuerst."""
    from . import vorbereitung
    from .pipeline import invoice_files

    samples = Path(samples)
    if not samples.is_dir():
        return []
    # Merkzettel tragen die Zeit nur sekundengenau -- eine Aenderung in
    # derselben Sekunde zaehlt nicht als neuer (sonst wuerde alles, was direkt
    # nach dem Lernen vorbereitet wurde, gleich noch einmal gelesen)
    stand = float(int(lernstand(store.root)))
    out = []
    for p in invoice_files(samples):
        m = vorbereitung.marker(store.root, vorbereitung.datei_sha(p))
        if m and m.get("ok") and _ausgewertet(m) < stand:
            out.append((_ausgewertet(m), p))
    return [p for _t, p in sorted(out)]


def neue_warten(store, samples) -> bool:
    """Wartet im Eingang eine Rechnung, die noch gar nicht vorbereitet ist?"""
    from . import vorbereitung
    from .pipeline import invoice_files

    samples = Path(samples)
    if not samples.is_dir():
        return False
    return any(vorbereitung.marker(store.root, vorbereitung.datei_sha(p)) is None
               for p in invoice_files(samples))


def blindtest_faellig(store) -> bool:
    from . import testsatz

    tage = blindtest_tage()
    if tage <= 0:
        return False
    if testsatz.blind_laufend(store.root) is not None:
        return True
    if not testsatz.faelle(store)[0]:
        return False
    for p in reversed(testsatz._berichte(Path(store.root))):
        b = testsatz._read_json_safe(p, None)
        if isinstance(b, dict) and (b.get("filter") or {}).get("blind") \
                and not (b.get("filter") or {}).get("lieferant"):
            return time.time() - _mtime(p) >= tage * 86400
    return True


# ---- Lauf ---------------------------------------------------------------------

def _stand_pfad(root) -> Path:
    return Path(root) / ORDNER / STAND_DATEI


def letzter_stand(root) -> dict | None:
    try:
        d = json.loads(_stand_pfad(root).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def laufen(store, samples, *, jetzt: datetime | None = None, melde=lambda text: None,
           guard=None, blind_guard=None) -> dict:
    """Ein Leerlauf-Durchgang (siehe Modul-Doku). -> Zusammenfassung, auch
    unter data/leerlauf/stand.json abgelegt (fuer den Nachtbericht)."""
    from . import testsatz, vorbereitung
    from .guard import Guard

    erg = {"zeit": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "gelaufen": False, "grund": "",
           "neu_gelesen": 0, "neu_zu_lesen": 0, "blindtest": None}
    if not jetzt_erlaubt(jetzt):
        erg["grund"] = "ausserhalb der Leerlauf-Zeit"
        return erg
    if neue_warten(store, samples):
        erg["grund"] = "neue Rechnungen warten"
        return erg
    erg["gelaufen"] = True
    t_ende = time.monotonic() + budget_s()

    def abbrechen() -> bool:
        return time.monotonic() >= t_ende or neue_warten(store, samples)

    # 1. Offene Rechnungen mit dem neuen Lernstand neu lesen
    kandidaten = neu_zu_lesen(store, samples)
    erg["neu_zu_lesen"] = len(kandidaten)
    if kandidaten:
        own = guard is None
        guard = guard or Guard(store.root, mode="thread")
        try:
            for p in kandidaten:
                if abbrechen():
                    erg["grund"] = "Zeit um oder neue Rechnungen"
                    break
                vorbereitung.sperre_auffrischen(store.root, vorbereitung.LAUF_SPERRE)
                if vorbereitung.neu_auswerten(guard, store, p):
                    erg["neu_gelesen"] += 1
        finally:
            if own:
                guard.close()
        melde(f"Leerlauf: {erg['neu_gelesen']} von {len(kandidaten)} offenen Rechnung(en) "
              f"mit neuem Lernstand gelesen")

    # 2. Blindtest in Etappen
    if not abbrechen() and blindtest_faellig(store):
        def blind_abbrechen() -> bool:
            vorbereitung.sperre_auffrischen(store.root, vorbereitung.LAUF_SPERRE)
            return neue_warten(store, samples)

        b = testsatz.blind_etappe(store, bis=t_ende, abbrechen=blind_abbrechen,
                                  max_faelle=blindtest_max(), guard=blind_guard)
        erg["blindtest"] = b
        melde("Leerlauf: Blindtest " + (f"fertig ({b['erledigt']} Rechnungen)" if b["fertig"]
                                        else f"{b['erledigt']} erledigt, {b['offen']} offen"))
    if not erg["grund"] and abbrechen():
        erg["grund"] = "Zeit um oder neue Rechnungen"
    try:
        _stand_pfad(store.root).parent.mkdir(parents=True, exist_ok=True)
        from .store import _atomic_write

        _atomic_write(_stand_pfad(store.root), json.dumps(erg, ensure_ascii=False, indent=1))
    except OSError:
        pass
    return erg


# ---- Nachtbericht ---------------------------------------------------------------

def bericht(store) -> dict:
    """Abschnitt "leerlauf" fuer den Nachtbericht: letzter Durchgang, laufender
    bzw. letzter vollstaendiger Blindtest mit Vergleich."""
    from . import testsatz

    out: dict = {"letzter_durchgang": letzter_stand(store.root), "blindtest": None,
                 "blindtest_laufend": None}
    lauf = testsatz.blind_laufend(store.root)
    if lauf is not None:
        out["blindtest_laufend"] = {"gestartet": lauf.get("gestartet"),
                                    "erledigt": len(lauf.get("fertig") or []),
                                    "offen": len(lauf.get("offen") or [])}
    for p in reversed(testsatz._berichte(Path(store.root))):
        b = testsatz._read_json_safe(p, None)
        if not (isinstance(b, dict) and (b.get("filter") or {}).get("blind")
                and not (b.get("filter") or {}).get("lieferant")):
            continue
        bl = b.get("blind") or {}
        v = b.get("vergleich") or {}
        out["blindtest"] = {
            "datei": str(p), "erstellt": b.get("erstellt"), "anzahl": b.get("anzahl"),
            "ki": bl.get("ki"), "trefferquote": b.get("trefferquote"),
            "komplett_quote": bl.get("komplett_quote"),
            "sicher_ohne_ki_deckel": bl.get("sicher_ohne_ki_deckel"),
            "davon_fehlerhaft": bl.get("davon_fehlerhaft"),
            "sicher_mit_gegenpruefung": bl.get("sicher_mit_gegenpruefung"),
            "gegenpruefung_fehlerhaft": bl.get("gegenpruefung_fehlerhaft"),
            "trefferquote_vorher": v.get("trefferquote_vorher"),
            "schlechter": (v.get("schlechter") or [])[:5],
        }
        break
    return out
