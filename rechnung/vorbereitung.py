"""Vorbereiten im Hintergrund, Pruefen im Rutsch (Aaron 17.10.2026: "die
menschliche Fehlerueberpruefung kann nicht den ganzen Tag dasitzen und warten,
bis das Programm eine Rechnung fertig hat").

Gelernt wird weiterhin NUR aus menschlich bestaetigten Rechnungen ("Senden").
Dieses Modul erledigt die Rechenarbeit VORHER und macht sichtbar, was fertig ist:

* **Merkzettel je Rechnung** (``<data>/vorbereitet/<sha256>.json``): wird
  geschrieben, wenn das Auslesen (Texterkennung + KI, die eigentlichen Caches
  liegen in ``ocr/`` und ``ki-cache/``) komplett durch ist. Er ueberlebt einen
  Neustart; ein neuer Inhalt der Datei (anderer Hash) ist eine neue Rechnung.
  Daraus ergibt sich der Zustand je Datei: bereit / wird vorbereitet / wartet /
  Fehler (``zustaende``).
* **Eine Sperre je Datei** (``datei_sperre``, Lockdatei) zwischen Programm-
  fenster und dem Befehl ``rechnung vorbereiten``: dieselbe Rechnung wird nie
  von beiden gleichzeitig gerechnet.
* **Neu auswerten nach dem Lernen** (``neu_auswerten``): nur die schnelle
  Vorlagenstufe (ohne KI), die OCR-/KI-Caches bleiben unberuehrt. Es wird nichts
  bestaetigt oder gesendet.
* **Lernwert-Reihenfolge** (``lernwert_sortieren``): welche bereite Rechnung
  dem Menschen am meisten bringt (erste eines neuen Lieferanten zuerst).
* **Lernfortschritt** (``lernfortschritt``): je Lieferant bestaetigt / gesichert /
  Korrekturquote, nur zur Anzeige.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

ORDNER = "vorbereitet"
# Sperre gilt als liegengeblieben (Absturz), wenn sie aelter ist als die
# Zeitgrenze des Hintergrundlaufs plus Puffer
SPERRE_ALT_S = 900
FEHLER_GUELTIG_S = 6 * 3600      # danach darf es nochmal versucht werden
FAELLIG_TAGE = 3                 # Faelligkeit in den naechsten N Tagen -> nach oben
UNSICHER_VIELE = 1               # ab so vielen unsicheren Feldern "viele" (Stufe c)
BEHALTE_TAGE = 90

BEREIT, IN_ARBEIT, WARTET, FEHLER = "bereit", "vorbereitung", "wartet", "fehler"
ZUSTAND_TEXT = {BEREIT: "bereit zur Prüfung", IN_ARBEIT: "wird vorbereitet",
                WARTET: "wartet", FEHLER: "Fehler"}

STUFE_FAELLIG, STUFE_NEU, STUFE_UNGESICHERT, STUFE_UNSICHER, STUFE_REST = 0, 1, 2, 3, 4


def ordner(root) -> Path:
    return Path(root) / ORDNER


# ---- Dateihash (mit Zwischenspeicher: die Liste fragt oft) ------------------
_sha_cache: dict[tuple, str] = {}


def datei_sha(path) -> str:
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return ""
    key = (str(p), st.st_size, st.st_mtime_ns)
    sha = _sha_cache.get(key)
    if sha is None:
        try:
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            return ""
        if len(_sha_cache) > 5000:
            _sha_cache.clear()
        _sha_cache[key] = sha
    return sha


# ---- Merkzettel --------------------------------------------------------------
def _marker_pfad(root, sha: str) -> Path:
    return ordner(root) / f"{sha}.json"


def marker(root, sha: str) -> dict | None:
    if not sha:
        return None
    try:
        d = json.loads(_marker_pfad(root, sha).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _schreibe(root, sha: str, d: dict) -> None:
    from .store import _atomic_write

    ordner(root).mkdir(parents=True, exist_ok=True)
    _atomic_write(_marker_pfad(root, sha), json.dumps(d, ensure_ascii=False, indent=1))


def _jetzt() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _alter_s(zeit: str) -> float:
    try:
        return (datetime.now() - datetime.strptime(zeit, "%Y-%m-%dT%H:%M:%S")).total_seconds()
    except (TypeError, ValueError):
        return 0.0


def ist_bereit(root, path) -> bool:
    m = marker(root, datei_sha(path))
    return bool(m and m.get("ok"))


def unsichere_felder(d: dict) -> list[str]:
    """Felder, die der Mensch ansehen muss: niedrige Konfidenz, KI-Quelle,
    fehlendes Pflichtfeld, rote Pruefung. ``d`` = ``Result.to_dict()``."""
    from .human import _CHECK_FIELDS
    from .shadow import REQUIRED
    from .template import sichtbare_felder

    sichtbar = set(sichtbare_felder())
    out: set[str] = set()
    for c in d.get("checks") or []:
        if c.get("ok") is False:
            out.update(_CHECK_FIELDS.get(c.get("name"), ()))
    for n, f in (d.get("fields") or {}).items():
        if f.get("source") in ("library", "manual"):
            continue
        if f.get("source") == "missing" or f.get("value") in (None, ""):
            if n in REQUIRED:
                out.add(n)
        elif (f.get("confidence") or 0) < 0.7 or f.get("source") == "llm":
            out.add(n)
    return sorted(n for n in out if n in sichtbar or n in REQUIRED)


def zusammenfassung(res) -> dict:
    d = res.to_dict()
    f = (d.get("fields") or {}).get("faelligkeit") or {}
    faellig = f.get("value") if isinstance(f.get("value"), str) else ""
    return {"lieferant_id": d.get("supplier_id") or "", "lieferant_name": d.get("supplier_name") or "",
            "vorlage": bool(d.get("template_id")), "gesichert": bool(d.get("template_secured")),
            "status": d.get("status") or "", "unsicher": unsichere_felder(d),
            "faellig": faellig[:10]}


def markieren(root, path, res, *, neu_ausgewertet: bool = False) -> dict:
    sha = datei_sha(path)
    alt = marker(root, sha) or {}
    m = {"sha256": sha, "datei": Path(path).name, "ok": True, "fehler": "",
         "zeit": alt.get("zeit") or _jetzt(), **zusammenfassung(res)}
    if neu_ausgewertet:
        m["neu_ausgewertet"] = _jetzt()
    elif alt.get("neu_ausgewertet"):
        m["neu_ausgewertet"] = alt["neu_ausgewertet"]
    _schreibe(root, sha, m)
    return m


def fehler_markieren(root, path, text: str) -> None:
    sha = datei_sha(path)
    if sha:
        _schreibe(root, sha, {"sha256": sha, "datei": Path(path).name, "ok": False,
                              "fehler": text, "zeit": _jetzt()})


def aufraeumen(root, behalte_tage: int = BEHALTE_TAGE) -> int:
    """Alte Merkzettel (Rechnung laengst erledigt) entfernen."""
    n = 0
    grenze = time.time() - behalte_tage * 86400
    for p in ordner(root).glob("*.json"):
        try:
            if p.stat().st_mtime < grenze:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n


# ---- Sperre je Datei (Fenster <-> Befehl) -------------------------------------
@contextmanager
def datei_sperre(root, name: str, alt_nach_s: float = SPERRE_ALT_S):
    """``with datei_sperre(...) as frei``: ``frei`` ist False, wenn ein anderer
    Prozess (oder Thread) diese Datei gerade vorbereitet -- dann nichts tun."""
    ordner(root).mkdir(parents=True, exist_ok=True)
    p = ordner(root) / (hashlib.sha1(name.encode("utf-8")).hexdigest()[:20] + ".lock")
    frei = False
    for _ in range(2):
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - p.stat().st_mtime > alt_nach_s:
                    p.unlink(missing_ok=True)      # liegengeblieben
                    continue
            except OSError:
                continue
            break
        os.write(fd, json.dumps({"pid": os.getpid(), "datei": name}).encode("utf-8"))
        os.close(fd)
        frei = True
        break
    try:
        yield frei
    finally:
        if frei:
            p.unlink(missing_ok=True)


def sperre_auffrischen(root, name: str) -> None:
    """Lange Laeufe melden sich damit als lebendig (sonst gilt die Sperre nach
    ``SPERRE_ALT_S`` als liegengeblieben)."""
    p = ordner(root) / (hashlib.sha1(name.encode("utf-8")).hexdigest()[:20] + ".lock")
    try:
        os.utime(p)
    except OSError:
        pass


def ist_gesperrt(root, name: str) -> bool:
    p = ordner(root) / (hashlib.sha1(name.encode("utf-8")).hexdigest()[:20] + ".lock")
    try:
        return time.time() - p.stat().st_mtime <= SPERRE_ALT_S
    except OSError:
        return False


# ---- Vorbereiten einer Datei (Fenster-Thread UND Befehl) ---------------------
def vorbereite_datei(guard, store, path, erneut: bool = False) -> dict:
    """Eine Rechnung komplett auslesen (derselbe Aufruf wie beim Anklicken:
    Texterkennung + KI, Ergebnisse in den Caches auf der Platte) und den
    Merkzettel schreiben. -> ``{"ergebnis": "bereit"|"fertig"|"belegt"|"fehler", "fehler": str}``.
    Es wird nichts entschieden, bestaetigt, angelegt oder gesendet."""
    path = Path(path)
    root = store.root
    if not erneut and ist_bereit(root, path):
        return {"ergebnis": "fertig", "fehler": ""}
    with datei_sperre(root, path.name) as frei:
        if not frei:
            return {"ergebnis": "belegt", "fehler": ""}
        if not erneut and ist_bereit(root, path):
            return {"ergebnis": "fertig", "fehler": ""}
        try:
            res = guard.process(path, use_llm=True)
        except Exception as e:  # noqa: BLE001 -- eine kaputte Datei haelt nichts auf
            text = f"{type(e).__name__}: {e}"
            fehler_markieren(root, path, text)
            return {"ergebnis": "fehler", "fehler": text}
        if res.aborted:
            text = res.hold_reasons[0] if res.hold_reasons else "abgebrochen"
            fehler_markieren(root, path, text)
            return {"ergebnis": "fehler", "fehler": text}
        markieren(root, path, res)
        return {"ergebnis": "bereit", "fehler": ""}


def neu_auswerten(guard, store, path) -> bool:
    """Nach dem Lernen: eine schon vorbereitete, noch nicht bestaetigte Rechnung
    mit der schnellen Vorlagenstufe (ohne KI) neu rechnen und den Merkzettel
    auffrischen. Nur bei vorhandenem Merkzettel, nichts wird bestaetigt."""
    path = Path(path)
    root = store.root
    if not path.is_file() or not ist_bereit(root, path):
        return False
    with datei_sperre(root, path.name) as frei:
        if not frei:
            return False
        try:
            res = guard.process(path, use_llm=False)
        except Exception:  # noqa: BLE001
            return False
        if res.aborted:
            return False
        markieren(root, path, res, neu_ausgewertet=True)
        return True


# ---- Zustand je Datei ---------------------------------------------------------
def zustaende(root, paths, in_arbeit: set[str] | None = None) -> dict[str, dict]:
    """-> {Dateiname: {"zustand", "text", "marker"}}. ``in_arbeit``: Namen, die der
    Hintergrundlauf dieses Fensters gerade rechnet oder eingereiht hat."""
    in_arbeit = in_arbeit or set()
    out: dict[str, dict] = {}
    for p in paths:
        p = Path(p)
        m = marker(root, datei_sha(p))
        if m and m.get("ok"):
            z = BEREIT
        elif p.name in in_arbeit or ist_gesperrt(root, p.name):
            z = IN_ARBEIT
        elif m and not m.get("ok") and _alter_s(m.get("zeit", "")) < FEHLER_GUELTIG_S:
            z = FEHLER
        else:
            z = WARTET
        out[p.name] = {"zustand": z, "text": ZUSTAND_TEXT[z], "marker": m if z in (BEREIT, FEHLER) else None,
                       "fehler": (m or {}).get("fehler", "") if z == FEHLER else ""}
    return out


def zaehler(zst: dict[str, dict]) -> dict:
    n = {BEREIT: 0, IN_ARBEIT: 0, WARTET: 0, FEHLER: 0}
    for v in zst.values():
        n[v["zustand"]] += 1
    return {"bereit": n[BEREIT], "in_vorbereitung": n[IN_ARBEIT], "wartet": n[WARTET],
            "fehler": n[FEHLER], "gesamt": len(zst)}


# ---- Lernwert-Reihenfolge -----------------------------------------------------
def eintrag(r: dict, zst: dict, index: int) -> dict:
    """Sortier-Eintrag aus dem Listenergebnis ``r`` (``Result.to_dict()`` der
    schnellen Listen-Verarbeitung) und dem Zustand der Datei."""
    m = zst.get("marker") or {}
    name = Path(r["file"]).name
    if m.get("ok"):
        unsicher = list(m.get("unsicher") or [])
        faellig = m.get("faellig") or ""
    else:
        unsicher = unsichere_felder(r)
        f = (r.get("fields") or {}).get("faelligkeit") or {}
        faellig = f.get("value")[:10] if isinstance(f.get("value"), str) else ""
    from .dummy import ist_dummy_id

    sid = r.get("supplier_id")
    return {"file": r["file"], "name": name, "index": index, "zustand": zst["zustand"],
            "lieferant": None if (not sid or ist_dummy_id(sid)) else sid,
            "lieferant_name": r.get("supplier_name") or "",
            "ohne_vorlage": not r.get("template_id"), "gesichert": bool(r.get("template_secured")),
            "unsicher": unsicher, "faellig": faellig}


def _tage_bis(faellig: str, heute: date) -> int | None:
    try:
        return (date.fromisoformat(faellig[:10]) - heute).days
    except (TypeError, ValueError):
        return None


def _de_datum(iso: str) -> str:
    try:
        d = date.fromisoformat(iso[:10])
        return f"{d.day:02d}.{d.month:02d}."
    except ValueError:
        return iso


def lernwert_sortieren(eintraege: list[dict], alle: list[dict] | None = None,
                       heute: date | None = None) -> list[dict]:
    """Reihenfolge nach Lernwert (Aaron 17.10.2026: "schnell alles richtig, aber
    wenig menschlicher Aufwand"). Fuegt jedem Eintrag ``stufe``, ``grund`` hinzu:

    0. Faelligkeit in den naechsten ``FAELLIG_TAGE`` Tagen (oder ueberfaellig)
    1. erste Rechnung eines Lieferanten OHNE Vorlage -- je Lieferant nur eine,
       Lieferanten mit den meisten wartenden Rechnungen zuerst
    2. dasselbe fuer Lieferanten mit noch nicht gesicherter Vorlage
    3. Rechnungen mit unsicheren Feldern (meiste zuerst)
    4. Rest ("nur kurz ansehen")

    ``alle``: Grundgesamtheit fuer das Zaehlen wartender Rechnungen je Lieferant
    (Standard: ``eintraege``). Gleiche Rangstufe: Eingangsreihenfolge."""
    heute = heute or date.today()
    alle = alle if alle is not None else eintraege
    wartend: dict[str, int] = {}
    for e in alle:
        if e["lieferant"]:
            wartend[e["lieferant"]] = wartend.get(e["lieferant"], 0) + 1

    out = [dict(e) for e in sorted(eintraege, key=lambda x: x["index"])]
    vertreten: set[str] = set()
    for e in out:
        n_uns = len(e["unsicher"])
        tage = _tage_bis(e["faellig"], heute)
        e["rang"] = (0,)
        if tage is not None and tage <= FAELLIG_TAGE:
            e["stufe"] = STUFE_FAELLIG
            e["grund"] = ("überfällig seit " if tage < 0 else "fällig am ") + _de_datum(e["faellig"])
            e["rang"] = (tage, e["index"])
            continue
        sup = e["lieferant"]
        lehrreich = sup and not e["gesichert"] and sup not in vertreten
        if lehrreich:
            vertreten.add(sup)
            weitere = wartend[sup] - 1
            rest = f", {weitere} weitere warten" if weitere > 0 else ""
            if e["ohne_vorlage"]:
                e["stufe"], e["grund"] = STUFE_NEU, "neuer Lieferant" + rest
            else:
                e["stufe"], e["grund"] = STUFE_UNGESICHERT, "Vorlage noch nicht gesichert" + rest
            e["rang"] = (-wartend[sup], e["index"])
            continue
        if sup and not e["gesichert"]:
            warte = "erst die erste Rechnung dieses Lieferanten prüfen"
        else:
            warte = ""
        if n_uns >= UNSICHER_VIELE:
            e["stufe"] = STUFE_UNSICHER
            e["grund"] = f"{n_uns} unsichere{'s' if n_uns == 1 else ''} Feld{'' if n_uns == 1 else 'er'}"
            if warte:
                e["grund"] += " – " + warte
            e["rang"] = (-n_uns, e["index"])
        else:
            e["stufe"] = STUFE_REST
            e["grund"] = warte or "nur kurz ansehen"
            e["rang"] = (0 if warte else 1, e["index"])
    out.sort(key=lambda e: (e["stufe"], e["rang"]))
    return out


def reihenfolge(eintraege: list[dict], lernwert: bool = True, bereite_zuerst: bool = True,
                heute: date | None = None) -> list[dict]:
    """Listenreihenfolge: bereite zuerst (abschaltbar), darin nach Lernwert
    (umschaltbar auf Eingang). Jeder Eintrag bekommt ``stufe``/``grund``
    (leer, wenn nicht nach Lernwert sortiert oder nicht bereit)."""
    bereit = [e for e in eintraege if e["zustand"] == BEREIT]
    if lernwert:
        # Lernwert nur unter den bereiten; die uebrigen zaehlen nur beim Zaehlen mit
        sortiert = {e["name"]: e for e in lernwert_sortieren(bereit, eintraege, heute)}
        bereit_ordnung = list(sortiert.values())
    else:
        bereit_ordnung = [dict(e, stufe=None, grund="") for e in bereit]
    rest = [dict(e, stufe=None, grund="") for e in eintraege if e["zustand"] != BEREIT]
    if not bereite_zuerst:
        sortiert_alle = {e["name"]: e for e in bereit_ordnung + rest}
        return [sortiert_alle[e["name"]] for e in sorted(eintraege, key=lambda x: x["index"])]
    return bereit_ordnung + sorted(rest, key=lambda e: e["index"])


def gesichert_anteil(eintraege: list[dict]) -> dict:
    """Wie viele wartende Rechnungen kommen von Lieferanten mit gesicherter Vorlage?"""
    n = len(eintraege)
    g = sum(1 for e in eintraege if e["gesichert"])
    return {"wartend": n, "gesichert": g, "prozent": round(100 * g / n) if n else None}


# ---- Lernfortschritt (Reiter Qualitaet) ---------------------------------------
def lernfortschritt(store, wartend: dict[str, int] | None = None, letzte: int = 10) -> list[dict]:
    """Je Lieferant: Anzahl bestaetigt, Vorlage gesichert ja/nein, Korrekturquote
    der letzten ``letzte`` bestaetigten Rechnungen (Schattenbetrieb). Nur Anzeige."""
    wartend = wartend or {}
    je: dict[str, list[dict]] = {}
    neueste: dict[str, dict] = {}
    for r in store.shadow_log():
        neueste[r.get("sha256") or r.get("file")] = r      # letzter Eintrag je Rechnung
    for r in sorted(neueste.values(), key=lambda x: x.get("ts") or ""):
        je.setdefault(r.get("supplier_id") or "(unbekannt)", []).append(r)
    rows = []
    for s in store.suppliers():
        recs = je.get(s.id, [])
        tpls = store.templates_for(s.id)
        bestaetigt = max(len(recs), max((t.confirmations for t in tpls), default=0))
        if not bestaetigt and not tpls and not wartend.get(s.id):
            continue
        last = recs[-letzte:]
        korr = sum(1 for r in last if r.get("corrected"))
        rows.append({"lieferant": s.id, "name": s.name, "bestaetigt": bestaetigt,
                     "gesichert": any(t.secured for t in tpls), "vorlage": bool(tpls),
                     "korrekturquote": round(korr / len(last), 2) if last else None,
                     "in_stichprobe": len(last), "wartend": wartend.get(s.id, 0)})
    rows.sort(key=lambda r: (-r["wartend"], -r["bestaetigt"], r["name"].lower()))
    return rows


# ---- Befehl "rechnung vorbereiten" ---------------------------------------------
LAUF_SPERRE = "__vorbereiten__"
ZEITGRENZE_S = 600


def vorbereiten_lauf(store, samples, *, abholen: bool = True, zeitgrenze: float = ZEITGRENZE_S,
                     erneut: bool = False, max_dateien: int = 0, melde=lambda text: None) -> dict:
    """Alles im Eingang vorab auslesen -- ohne offenes Programmfenster. Gleicher
    Codepfad und gleiche Caches wie der Hintergrund-Thread (``vorbereite_datei``),
    eine Datei nach der anderen, Zeitgrenze je Datei. Ein zweiter Lauf
    (Aufgabenplanung alle 30 Minuten) bricht sofort ab, solange einer arbeitet;
    Dateien, die das Fenster gerade vorbereitet, werden uebersprungen.
    Es wird nichts bestaetigt, gesendet oder gelernt."""
    from . import semantic, split
    from .guard import Guard
    from .pipeline import invoice_files

    samples = Path(samples)
    root = store.root
    bericht = {"abgeholt": 0, "abhol_fehler": "", "bereit": 0, "schon_fertig": 0, "belegt": 0,
               "fehler": [], "dateien": 0, "uebersprungen_laeuft": False}
    with datei_sperre(root, LAUF_SPERRE, alt_nach_s=SPERRE_ALT_S) as frei:
        if not frei:
            bericht["uebersprungen_laeuft"] = True
            melde("Ein anderer Vorbereitungslauf arbeitet gerade – nichts zu tun.")
            return bericht
        semantic.set_cache_dir(root / "ki-cache")      # dieselben KI-Caches wie das Fenster
        if abholen:
            from .abholung import aus_konfiguration
            ab = aus_konfiguration(samples, store=store)
            if ab is not None:
                samples.mkdir(parents=True, exist_ok=True)
                try:
                    r = ab.nur_abholen()
                    bericht["abgeholt"] = len(r.saved)
                    bericht["abhol_fehler"] = r.error or ""
                    melde(f"Postfach: {len(r.saved)} neue Datei(en)" + (f", Fehler: {r.error}" if r.error else ""))
                except Exception as e:  # noqa: BLE001 -- Abholen darf das Vorbereiten nicht verhindern
                    bericht["abhol_fehler"] = f"{type(e).__name__}: {e}"
                    melde(f"Postfach: Fehler {bericht['abhol_fehler']}")
        if not samples.is_dir():
            return bericht
        with datei_sperre(root, "__ordner_vorbereiten__") as frei2:
            if frei2:
                split.prepare_folder(samples, store)      # Sammel-PDFs aufteilen wie die Liste
        dateien = invoice_files(samples)
        bericht["dateien"] = len(dateien)
        guard = Guard(root, timeout=zeitgrenze, mode="thread")
        gemacht = 0
        for p in dateien:
            if max_dateien and gemacht >= max_dateien:
                break
            sperre_auffrischen(root, LAUF_SPERRE)
            t0 = time.monotonic()
            r = vorbereite_datei(guard, store, p, erneut=erneut)
            dauer = time.monotonic() - t0
            if r["ergebnis"] == "bereit":
                bericht["bereit"] += 1
                gemacht += 1
                melde(f"{p.name}: bereit ({dauer:.0f} s)")
            elif r["ergebnis"] == "fertig":
                bericht["schon_fertig"] += 1
            elif r["ergebnis"] == "belegt":
                bericht["belegt"] += 1
                melde(f"{p.name}: wird gerade von einem anderen Prozess vorbereitet – übersprungen")
            else:
                bericht["fehler"].append(f"{p.name}: {r['fehler']}")
                gemacht += 1
                melde(f"{p.name}: FEHLER {r['fehler']}")
        aufraeumen(root)
    return bericht
