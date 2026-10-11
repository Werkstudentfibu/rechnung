"""Lernbericht an den Entwickler (Aaron 11.10.2026): "in regelmaessigen
Abstaenden werden die Daten, was richtig war und was verbessert wurde, an
dich geschickt, und du kannst daraufhin das Programm verbessern".

Inhalt je bestaetigter Rechnung seit dem letzten Bericht: Datei, Lieferant,
Extraktionsweg, die bestaetigten Werte und je Feld die Herkunft
(``manual`` = vom Menschen korrigiert/markiert), dazu jede Korrektur mit dem
Wert und der Quelle VOR der Korrektur, und die Fehlerstatistik. Die PDFs
selbst gehen nur mit, wenn ``RECHNUNG_BERICHT_MIT_PDF=1`` gesetzt ist.

Versand per SMTP ueber dasselbe IONOS-Postfach (Standard: Zugangsdaten der
Abholung). Aus, solange ``RECHNUNG_BERICHT_AN`` leer ist. Einmal je Tag ab
``RECHNUNG_BERICHT_STUNDE`` (Standard 17) und nur, wenn es Neues gibt;
zusaetzlich "jetzt senden" im Reiter Qualitaet.
"""

from __future__ import annotations

import json
import os
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
        "mit_pdf": os.environ.get("RECHNUNG_BERICHT_MIT_PDF", "0").strip() in ("1", "ja", "true"),
    }


def _stand(root: Path) -> dict:
    try:
        return json.loads((root / STAND_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def erstellen(store, seit: str = "") -> dict:
    """Bericht ab Zeitpunkt ``seit`` (ISO, leer = alles)."""
    from . import eventlog, testsatz

    rechnungen = []
    for fall in testsatz.bestaetigte(store):
        if seit and str(fall.get("bestaetigt", "")) <= seit:
            continue
        quellen = fall.get("quellen") or {}
        rechnungen.append({
            "datei": fall.get("datei"), "sha256": fall.get("sha256"),
            "bestaetigt": fall.get("bestaetigt"),
            "lieferant": fall.get("supplier_name") or fall.get("supplier_id"),
            "lieferant_id": fall.get("supplier_id"),
            "extraktion": fall.get("extraktion"),
            "werte": fall.get("felder") or {},
            "quellen": quellen,
            "korrigiert": sorted(k for k, q in quellen.items() if q == "manual"),
            "positionen": len(fall.get("positionen") or []),
        })
    shas = {r["sha256"] for r in rechnungen}
    korrekturen = [e for e in store.events()
                   if e.get("type") == eventlog.TYPE_CORRECTION
                   and (e.get("sha256") in shas or (seit and str(e.get("ts", "")) > seit))]
    felder = sum(len(r["werte"]) for r in rechnungen)
    korr = sum(len(r["korrigiert"]) for r in rechnungen)
    try:
        statistik = eventlog.fehlerstatistik(store)
    except Exception as e:  # noqa: BLE001 -- Bericht nie an der Statistik scheitern lassen
        statistik = {"fehler": f"{type(e).__name__}: {e}"}
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
        zeilen.append(f"- {r['datei']} | {r['lieferant'] or '?'} | {r['extraktion']} | "
                      f"korrigiert: {', '.join(r['korrigiert']) or '-'}")
    zeilen += ["", "Details (Werte, Quellen, Korrekturen, Fehlerstatistik) im Anhang lernbericht.json."]
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
    if cfg["mit_pdf"]:
        archiv = root / "archive"
        for r in b["rechnungen"]:
            for p in archiv.glob(f"{r['sha256']}.*") if r.get("sha256") else []:
                msg.add_attachment(p.read_bytes(), maintype="application",
                                   subtype="pdf" if p.suffix == ".pdf" else "octet-stream",
                                   filename=f"{r['sha256'][:12]}_{r['datei']}")
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
