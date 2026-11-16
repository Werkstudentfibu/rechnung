"""Lernmaterial gezielt aus den Postfaechern holen (80/15-Regel, Plan Aaron
12.11.2026; als Programmbefehl seit 16.11.2026: "rechnung lernmaterial").

Ziel: von jedem wichtigen Lieferanten genug Rechnungen zum Anlernen, ohne
das ganze Archiv zu holen.

1. **Durchsehen** (``scannen``): je Postfach und Ordner nur Kopfzeilen (From,
   Message-ID, Date, Subject) und die Struktur (PDF-/Bild-Anhang dabei?) --
   Ordner werden nur lesend geoeffnet (EXAMINE), nichts wird markiert,
   verschoben oder heruntergeladen. Ergebnis: ``<data>/absender_scan.json``.
2. **Auswaehlen** (``auswaehlen``): nur direkt schickende Lieferanten (keine
   Weiterleitungen eigener Hotels), nur Mails mit Anhang, jede Mail einmal
   (gleiche Message-ID in mehreren Ordnern). Absender nach Anzahl sortiert:
   die, die zusammen 80 % der Rechnungsmails schicken -> je ``je80`` (3),
   80-95 % -> je ``je95`` (1). Schon geholte Mails (``abgeholt.json``) werden
   angerechnet, die neuesten zuerst. Ergebnis: ``<data>/absender_auswahl.json``.
3. **Holen** (``holen``): die ausgewaehlten Mails per UID ueber denselben Weg
   wie die normale Abholung (``ingest.handle_mail``), nur lesend (BODY.PEEK),
   in den Eingang; bekannte Message-IDs werden in ``abgeholt.json`` gemerkt.
"""

from __future__ import annotations

import collections
import email
import email.header
import json
import re
import time
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import Callable

SCAN_DATEI = "absender_scan.json"
AUSWAHL_DATEI = "absender_auswahl.json"
SCAN_GUELTIG_TAGE = 7

# Eigene Hotels/Gesellschaften: deren Mails sind Weiterleitungen, keine
# Lieferanten (Befund Postfach-Scan 12.11.2026: 47 % der Mails)
INTERN_STANDARD = (r"althoff|ameron|fuerstenhof|congress-union|flora-hotel|hotel-speicherstadt|urbanloft|"
                   r"koenigshof|hotelregent|seehotel-ueberfahrt|schlossbensberg|villa-belrose|hotel-frankfurt|"
                   r"grandhotel|hotel-davos|bellerive|neckarvillen|fahrhuette|motorworld|hohenschwangau|regent")

_ANHANG = re.compile(rb'"application" "pdf"|"image" "|\.pdf"|\.jpe?g"|\.png"|\.tiff?"|'
                     rb'"application" "octet-stream"', re.I)


def _kopfwert(raw: str) -> str:
    out = []
    for text, enc in email.header.decode_header(raw or ""):
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or "utf-8", "replace"))
            except LookupError:
                out.append(text.decode("latin-1", "replace"))
        else:
            out.append(text)
    return "".join(out)


# ---------------------------------------------------------------- Durchsehen
def scannen(konten, verbinden: Callable, melde: Callable[[str], None] = print) -> list[dict]:
    """``konten``: [(Anzeigename, ImapConfig, Mandant-Id)] wie
    ``config.postfaecher``. -> eine Zeile je Mail (nur Kopfdaten)."""
    from .ingest import _ordner

    out: list[dict] = []
    t0 = time.time()
    for _label, cfg, mandant in konten:
        try:
            M = verbinden(cfg)
            M.login(cfg.user, cfg.password)
        except Exception as e:  # noqa: BLE001 -- ein Postfach haelt die anderen nicht auf
            melde(f"Postfach nicht erreichbar ({type(e).__name__})")
            continue
        for o in _ordner(cfg):
            typ, data = M.select(f'"{o}"', readonly=True)
            if typ != "OK":
                continue
            n = int((data or [b"0"])[0] or 0)
            for start in range(1, n + 1, 300):
                ende = min(n, start + 299)
                typ, resp = M.fetch(f"{start}:{ende}",
                                    "(UID BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS (FROM MESSAGE-ID DATE SUBJECT)])")
                if typ != "OK":
                    continue
                recs, rec = [], None
                for item in resp or []:
                    if isinstance(item, tuple):
                        rec = [item[0], item[1]]
                        recs.append(rec)
                    elif isinstance(item, bytes) and rec is not None:
                        rec[0] += item
                for meta, hdr in recs:
                    m = re.search(rb"UID (\d+)", meta or b"")
                    msg = email.message_from_bytes(hdr or b"")
                    try:
                        datum = parsedate_to_datetime(msg.get("Date")).strftime("%Y-%m-%d")
                    except Exception:  # noqa: BLE001
                        datum = ""
                    out.append({
                        "postfach": cfg.user, "mandant": mandant or "", "ordner": o,
                        "uid": int(m.group(1)) if m else None,
                        "mid": (msg.get("Message-ID") or "").strip(),
                        "von": parseaddr(_kopfwert(msg.get("From") or ""))[1].lower(),
                        "datum": datum,
                        "betreff": _kopfwert(msg.get("Subject") or "")[:120],
                        "anhang": bool(_ANHANG.search(meta or b"")),
                    })
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass
    melde(f"Durchgesehen: {len(out)} Mails in {time.time() - t0:.0f} s")
    return out


# ---------------------------------------------------------------- Auswaehlen
def auswaehlen(scan: list[dict], bekannte: set[str], *, je80: int = 3, je95: int = 1,
               intern: str = INTERN_STANDARD) -> tuple[list[dict], dict]:
    """-> (Auswahl, Kennzahlen). Reine Funktion, liest und schreibt nichts."""
    eind: dict[str, dict] = {}
    for m in scan:
        key = m.get("mid") or f'{m.get("postfach")}|{m.get("ordner")}|{m.get("uid")}'
        if key not in eind or (m.get("ordner") == "Done" and eind[key].get("ordner") != "Done"):
            eind[key] = m
    intern_re = re.compile(intern, re.I) if intern else None
    mails = [m for m in eind.values()
             if m.get("anhang") and m.get("von") and m.get("uid") is not None
             and not (intern_re and intern_re.search(m["von"].split("@")[-1]))]
    je: dict[str, list[dict]] = collections.defaultdict(list)
    for m in mails:
        je[m["von"]].append(m)
    absender = sorted(je.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    gesamt = len(mails)
    kum, g80, g95 = 0, 0, 0
    for i, (_, liste) in enumerate(absender, 1):
        kum += len(liste)
        if not g80 and gesamt and kum / gesamt >= 0.80:
            g80 = i
        if not g95 and gesamt and kum / gesamt >= 0.95:
            g95 = i
    auswahl: list[dict] = []
    holen80 = holen95 = schon_gesamt = 0
    for i, (von, liste) in enumerate(absender, 1):
        if i > g95:
            break
        soll = je80 if i <= g80 else je95
        schon = sum(1 for m in liste if m.get("mid") and m["mid"] in bekannte)
        schon_gesamt += min(schon, soll)
        neu = sorted((m for m in liste if not (m.get("mid") and m["mid"] in bekannte)),
                     key=lambda m: m.get("datum") or "", reverse=True)[:max(0, soll - schon)]
        for m in neu:
            auswahl.append(dict(m, gruppe="80" if i <= g80 else "95", rang=i))
        if i <= g80:
            holen80 += len(neu)
        else:
            holen95 += len(neu)
    kennzahlen = {"mails_mit_anhang": gesamt, "absender": len(absender), "absender_80": g80,
                  "absender_95": g95, "holen_80": holen80, "holen_95": holen95,
                  "schon_geholt": schon_gesamt}
    return auswahl, kennzahlen


# --------------------------------------------------------------------- Holen
def holen(store, auswahl: list[dict], ziel: Path, konten, verbinden: Callable,
          bekannte: set[str], merken: Callable[[set[str]], None],
          melde: Callable[[str], None] = print) -> dict:
    """Ausgewaehlte Mails in den Eingang holen. -> Kennzahlen."""
    from .ingest import _raw_from_fetch, handle_mail

    nach_konto = {cfg.user.lower(): (cfg, mandant) for _, cfg, mandant in konten}
    nach_ort: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for m in auswahl:
        nach_ort[(str(m.get("postfach") or "").lower(), m.get("ordner") or "INBOX")].append(m)
    st = {"geholt": 0, "dateien": 0, "mensch": 0, "fehler": 0, "nicht_eingerichtet": 0}
    t0 = time.time()
    for (postfach, ordner), liste in sorted(nach_ort.items()):
        cfg, mandant = nach_konto.get(postfach, (None, None))
        if cfg is None:
            st["nicht_eingerichtet"] += len(liste)
            continue
        try:
            M = verbinden(cfg)
            M.login(cfg.user, cfg.password)
            typ, _ = M.select(f'"{ordner}"', readonly=True)
            if typ != "OK":
                raise RuntimeError("Ordner nicht lesbar")
        except Exception:  # noqa: BLE001
            st["fehler"] += len(liste)
            continue
        for m in liste:
            if m.get("mid") and m["mid"] in bekannte:
                continue
            try:
                typ, data = M.uid("FETCH", str(m["uid"]), "(BODY.PEEK[])")
                raw = _raw_from_fetch(data) if typ == "OK" else None
                if raw is None:
                    st["fehler"] += 1
                    continue
                r = handle_mail(raw, cfg, Path(ziel), store, False, mandant_address_id=mandant)
            except Exception:  # noqa: BLE001 -- eine Mail haelt die anderen nicht auf
                st["fehler"] += 1
                continue
            st["geholt"] += 1
            st["dateien"] += len(r.get("saved") or [])
            st["mensch"] += len(r.get("human") or [])
            if m.get("mid"):
                bekannte.add(m["mid"])
                merken(bekannte)
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass
    melde(f"Geholt in {time.time() - t0:.0f} s")
    return st


# ------------------------------------------------------------------- Ablauf
def lauf(store, ziel: Path, *, neu_scannen: bool = False, je80: int = 3, je95: int = 1,
         nur_anzeigen: bool = False, konten=None, verbinden: Callable | None = None,
         melde: Callable[[str], None] = print) -> dict:
    """Durchsehen (oder gespeicherten Scan nehmen) -> auswaehlen -> holen."""
    from . import config
    from .ingest import _bekannte_laden, _bekannte_speichern, _verbinden

    root = Path(store.root)
    konten = config.postfaecher(store) if konten is None else konten
    verbinden = verbinden or _verbinden
    scan_pfad = root / SCAN_DATEI
    frisch = (scan_pfad.exists()
              and time.time() - scan_pfad.stat().st_mtime < SCAN_GUELTIG_TAGE * 86400)
    if neu_scannen or not frisch:
        scan = scannen(konten, verbinden, melde)
        scan_pfad.write_text(json.dumps(scan, ensure_ascii=False), "utf-8")
    else:
        scan = json.loads(scan_pfad.read_text("utf-8"))
        melde(f"Gespeicherten Postfach-Scan genommen ({len(scan)} Mails, juenger als "
              f"{SCAN_GUELTIG_TAGE} Tage; neu: --neu-scannen)")
    bekannte = _bekannte_laden()
    auswahl, kz = auswaehlen(scan, bekannte, je80=je80, je95=je95)
    (root / AUSWAHL_DATEI).write_text(json.dumps(auswahl, ensure_ascii=False, indent=1), "utf-8")
    ergebnis = dict(kz, ausgewaehlt=len(auswahl))
    if nur_anzeigen or not auswahl:
        return ergebnis
    st = holen(store, auswahl, ziel, konten, verbinden, bekannte, _bekannte_speichern, melde)
    return dict(ergebnis, **st)
