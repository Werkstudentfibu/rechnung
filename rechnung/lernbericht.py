"""Lernbericht an den Entwickler (Aaron 11.10.2026): "in regelmaessigen
Abstaenden werden die Daten, was richtig war und was verbessert wurde, an
dich geschickt, und du kannst daraufhin das Programm verbessern".

ANONYMISIERT (Aaron 11.10.2026: "echte, unzensierte Daten moechte ich
nicht herausgeben"): kein Lieferantenname, kein Betrag, keine IBAN, keine
Rechnungsnummer, kein Dateiname, keine PDFs. Stattdessen je Rechnung ein
Lieferanten-Pseudonym (gesalzener Hash, Salz bleibt auf dem Laptop; die
Zuordnung zeigt der Reiter "Qualitaet", damit Aaron auf Rueckfragen
antworten kann), Extraktionsweg, je Feld richtig/korrigiert + Quelle, und je
Korrektur nur die FEHLERART (z.B. Komma/Faktor 100, Tag/Monat vertauscht,
Ziffer falsch, leer) und die FORM des Werts (Ziffern -> 9, Buchstaben -> A).

Versand per SMTP ueber dasselbe IONOS-Postfach (Standard: Zugangsdaten der
Abholung). Aus, solange ``RECHNUNG_BERICHT_AN`` leer ist. Einmal je Tag ab
``RECHNUNG_BERICHT_STUNDE`` (Standard 17) und nur, wenn es Neues gibt;
zusaetzlich "jetzt senden" im Reiter Qualitaet.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import smtplib
import time
from email.message import EmailMessage
from pathlib import Path

STAND_NAME = "lernbericht-stand.json"


def _cfg() -> dict:
    from .config import ImapConfig

    imap = ImapConfig()
    return {
        "an": os.environ.get("RECHNUNG_BERICHT_AN", "").strip(),
        "host": os.environ.get("RECHNUNG_SMTP_HOST", "smtp.ionos.de"),
        "port": int(os.environ.get("RECHNUNG_SMTP_PORT", "587")),
        "user": os.environ.get("RECHNUNG_SMTP_USER", "") or imap.user,
        "password": os.environ.get("RECHNUNG_SMTP_PASSWORD", "") or imap.password,
        "stunde": int(os.environ.get("RECHNUNG_BERICHT_STUNDE", "17")),
    }


def _stand(root: Path) -> dict:
    try:
        return json.loads((root / STAND_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _salz(root: Path) -> str:
    p = root / "lernbericht-salz.txt"
    try:
        return p.read_text("utf-8").strip()
    except OSError:
        v = secrets.token_hex(16)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(v, "utf-8")
        return v


def pseudonym(root: Path, lieferant_id) -> str | None:
    if not lieferant_id:
        return None
    return "L-" + hashlib.sha256((_salz(root) + str(lieferant_id)).encode()).hexdigest()[:6]


def form(v) -> str:
    """Form ohne Inhalt: Ziffern -> 9, Buchstaben -> A (max. 24 Zeichen)."""
    t = re.sub(r"[0-9]", "9", str(v if v is not None else ""))
    t = re.sub(r"[^\W\d_]", "A", t)
    return t[:24]


def _zahl(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    t = str(v or "").strip().replace(" ", "")
    if re.fullmatch(r"-?[\d.]+,\d+", t):
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def fehlerart(alt, neu) -> str:
    """Art des Fehlers, ohne die Werte selbst preiszugeben."""
    if alt in (None, ""):
        return "fehlte"
    if neu in (None, ""):
        return "geleert"
    a, n = _zahl(alt), _zahl(neu)
    if a is not None and n is not None and a != 0 and n != 0:
        q = n / a
        for f in (10, 100, 1000):
            if abs(q - f) < 1e-6 or abs(q - 1 / f) < 1e-9:
                return f"Faktor {f} (Komma/Tausender)"
        if abs(abs(q) - 1) < 1e-9 and q < 0:
            return "Vorzeichen"
        if abs(a - n) <= 0.05:
            return "Rundung/Cent"
    sa, sn = str(alt), str(neu)
    d = re.findall(r"\d+", sa), re.findall(r"\d+", sn)
    if len(d[0]) == 3 and len(d[1]) == 3 and d[0] != d[1] and sorted(d[0]) == sorted(d[1]):
        return "Datumsteile vertauscht"
    if re.sub(r"\W", "", sa).upper() == re.sub(r"\W", "", sn).upper():
        return "nur Format/Leerzeichen"
    if len(sa) == len(sn):
        diff = sum(1 for x, y in zip(sa, sn) if x != y)
        if diff <= 2:
            return f"{diff} Zeichen falsch gelesen"
    if sn in sa:
        return "zu viel gelesen"
    if sa in sn:
        return "zu wenig gelesen"
    return "anderer Wert (falsche Stelle?)"


def erstellen(store, seit: str = "") -> dict:
    """Bericht ab Zeitpunkt ``seit`` (ISO, leer = alles)."""
    from . import eventlog, testsatz

    root = Path(store.root)
    rechnungen = []
    for fall in testsatz.bestaetigte(store):
        if seit and str(fall.get("bestaetigt", "")) <= seit:
            continue
        quellen = fall.get("quellen") or {}
        rechnungen.append({
            "nr": len(rechnungen) + 1,
            "_sha": fall.get("sha256"),
            "bestaetigt": str(fall.get("bestaetigt", ""))[:10],
            "lieferant": pseudonym(root, fall.get("supplier_id")),
            "extraktion": fall.get("extraktion"),
            "quellen": quellen,
            "korrigiert": sorted(k for k, q in quellen.items() if q == "manual"),
            "positionen": len(fall.get("positionen") or []),
        })
    nr = {r["_sha"]: r["nr"] for r in rechnungen}
    roh = [e for e in store.events()
           if e.get("type") == eventlog.TYPE_CORRECTION
           and (e.get("sha256") in nr or (seit and str(e.get("ts", "")) > seit))]
    # je Rechnung/Feld: erste Korrektur gegen den Programmwert (alt=None ->
    # Programmwert unbekannt), letzte Korrektur = Endwert
    korrekturen = []
    for e in roh:
        vorher = e.get("programmwert") if e.get("programmwert") is not None else e.get("alt")
        korrekturen.append({"rechnung": nr.get(e.get("sha256")), "feld": e.get("field"),
                            "lieferant": pseudonym(root, e.get("supplier_id")),
                            "quelle_vorher": e.get("quelle_vorher"),
                            "fehlerart": fehlerart(vorher, e.get("neu")),
                            "form_vorher": form(vorher), "form_nachher": form(e.get("neu"))})
    for r in rechnungen:
        r.pop("_sha", None)
    felder = sum(len(r["quellen"]) for r in rechnungen)
    korr = sum(len(r["korrigiert"]) for r in rechnungen)
    try:
        st = eventlog.fehlerstatistik(store)
        statistik = [{"lieferant": pseudonym(root, z.get("lieferant")), "feld": z.get("feld"),
                      "korrigiert": z.get("korrigiert"), "rechnungen": z.get("rechnungen"),
                      "anteil": z.get("anteil"), "trend": z.get("trend")}
                     for z in st.get("zeilen", [])]
    except Exception as e:  # noqa: BLE001 -- Bericht nie an der Statistik scheitern lassen
        statistik = {"fehler": f"{type(e).__name__}"}
    return {
        "erstellt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seit": seit or None,
        "rechnungen": rechnungen, "korrekturen": korrekturen,
        "zusammenfassung": {"rechnungen": len(rechnungen), "felder": felder,
                            "korrigiert": korr,
                            "richtig_anteil": round(1 - korr / felder, 3) if felder else None},
        "fehlerstatistik": statistik,
    }


def _text(b: dict) -> str:
    z = b["zusammenfassung"]
    zeilen = [f"Lernbericht Rechnungsprogramm ({b['erstellt'][:16]})",
              f"{z['rechnungen']} bestaetigte Rechnung(en), {z['felder']} Felder, "
              f"davon {z['korrigiert']} vom Menschen korrigiert"
              + (f" -> {100 * z['richtig_anteil']:.0f} % richtig gelesen" if z["richtig_anteil"] is not None else ""),
              ""]
    for r in b["rechnungen"]:
        zeilen.append(f"- Rechnung {r['nr']} | {r['lieferant'] or '?'} | {r['extraktion']} | "
                      f"korrigiert: {', '.join(r['korrigiert']) or '-'}")
    zeilen += ["", "Anonymisiert: keine Namen, Betraege, IBANs, Nummern oder PDFs.",
               "Details (Quellen, Fehlerarten, Fehlerstatistik) im Anhang lernbericht.json."]
    return "\n".join(zeilen)


def senden(store, erzwingen: bool = False, smtp_factory=smtplib.SMTP) -> dict:
    """Bericht seit dem letzten Versand schicken. ``erzwingen``: auch ohne
    Neues bzw. vor der Uhrzeit (Knopf "jetzt senden")."""
    cfg = _cfg()
    if not cfg["an"]:
        return {"gesendet": False, "grund": "RECHNUNG_BERICHT_AN nicht gesetzt"}
    if not (cfg["user"] and cfg["password"]):
        return {"gesendet": False, "grund": "keine SMTP-Zugangsdaten"}
    root = Path(store.root)
    stand = _stand(root)
    b = erstellen(store, stand.get("bis", ""))
    if not b["rechnungen"] and not b["korrekturen"] and not erzwingen:
        return {"gesendet": False, "grund": "nichts Neues"}
    msg = EmailMessage()
    msg["Subject"] = f"Rechnung Lernbericht {time.strftime('%d.%m.%Y')} ({b['zusammenfassung']['rechnungen']} Rechnungen)"
    msg["From"] = cfg["user"]
    msg["To"] = cfg["an"]
    msg.set_content(_text(b))
    msg.add_attachment(json.dumps(b, indent=2, ensure_ascii=False, default=str).encode("utf-8"),
                       maintype="application", subtype="json", filename="lernbericht.json")
    with smtp_factory(cfg["host"], cfg["port"], timeout=60) as s:
        s.starttls()
        s.login(cfg["user"], cfg["password"])
        s.send_message(msg)
    (root / STAND_NAME).write_text(json.dumps({"bis": b["erstellt"], "gesendet": b["erstellt"],
                                               "rechnungen": len(b["rechnungen"])}), "utf-8")
    return {"gesendet": True, "rechnungen": len(b["rechnungen"]), "an": cfg["an"]}


def faellig(store) -> bool:
    """Einmal je Tag ab der eingestellten Stunde."""
    cfg = _cfg()
    if not cfg["an"] or time.localtime().tm_hour < cfg["stunde"]:
        return False
    return str(_stand(Path(store.root)).get("gesendet", ""))[:10] != time.strftime("%Y-%m-%d")
