""""Startklar?"-Check (Aaron 11.10.2026): alles, was fuer den Betrieb
gebraucht wird, auf einen Blick -- gruen/gelb/rot statt PowerShell-Befehle.
Nur lesende Pruefungen (Anmeldungen ohne Abholen/Senden)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

OK, WARN, FEHLER, AUS = "ok", "warn", "fehler", "aus"


def _p(name: str, status: str, detail: str = "", hilfe: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail, "hilfe": hilfe}


def pruefen(store, samples: Path) -> list[dict]:
    from . import ingest, jobrouter, lernbericht, ocr, selbstupdate, semantic, sicherung
    from .config import ImapConfig, LlmConfig, jobrouter as jr_cfg

    out = []
    v = selbstupdate.version()
    out.append(_p("Programmstand", OK if v.get("commit") else WARN,
                  f"{v.get('commit', '?')} vom {v.get('datum', '?')}"))

    imap = ImapConfig()
    if not imap.configured:
        out.append(_p("Postfach (IONOS)", FEHLER, "nicht eingerichtet", "RECHNUNG_IMAP_USER/_PASSWORD in rechnung.env"))
    else:
        c = ingest.check_connection(imap)
        out.append(_p("Postfach (IONOS)", OK if c.get("ok") else FEHLER, c.get("detail", "")))

    cfg = jr_cfg()
    if not cfg.live:
        out.append(_p("JobRouter", WARN, "noch nicht angebunden – gesendete Rechnungen warten in der Warteschlange",
                      "RECHNUNG_JOBROUTER_ENABLED=1, _URL, _TOKEN, _PROCESS, _VERSION in rechnung.env"))
    else:
        c = jobrouter.check_connection(cfg)
        out.append(_p("JobRouter", OK if c.get("ok") else FEHLER, c.get("detail", "")))
    mapping = jobrouter.load_mapping(cfg)
    out.append(_p("JobRouter-Feldzuordnung", OK if mapping else WARN,
                  f"{len(mapping)} Felder zugeordnet" if mapping else "fehlt – Feldnamen gehen unverändert raus",
                  f"Datei {cfg.mapping_path}"))
    warten = len(list(jobrouter.queue_dir(store.root).glob("*.json")))
    out.append(_p("Warteschlange JobRouter", OK if not warten else WARN, f"{warten} Rechnung(en) warten"))

    lb = lernbericht._cfg()
    if not lb["an"]:
        out.append(_p("Lernbericht", AUS, "kein Empfänger", "RECHNUNG_BERICHT_AN in rechnung.env"))
    else:
        try:
            import smtplib
            with smtplib.SMTP(lb["host"], lb["port"], timeout=20) as s:
                s.starttls()
                s.login(lb["user"], lb["password"])
            out.append(_p("Lernbericht (Mailversand)", OK, f"an {lb['an']}, Anmeldung bei {lb['host']} ok"))
        except Exception as e:  # noqa: BLE001
            out.append(_p("Lernbericht (Mailversand)", FEHLER, f"{type(e).__name__}: {e}"))

    if not ocr.enabled():
        out.append(_p("Texterkennung (Scans)", WARN, "ausgeschaltet", "RECHNUNG_OCR_ENABLED=1"))
    else:
        out.append(_p("Texterkennung (Scans)", OK if ocr.available() else FEHLER,
                      ocr.tesseract_cmd() or "Tesseract nicht gefunden",
                      "" if ocr.available() else "windows\\texterkennung-einrichten.ps1"))

    llm = LlmConfig()
    if not llm.enabled:
        out.append(_p("KI", WARN, "ausgeschaltet", "RECHNUNG_LLM_ENABLED=1"))
    else:
        out.append(_p("KI", OK if semantic.available() else FEHLER, llm.model,
                      "" if semantic.available() else "Ollama läuft nicht oder Modell fehlt"))
        # Positionen per KI (Befund Aaron 12.10.2026: Positionen leer, Einstellung
        # nicht sichtbar) -- zeigt den Wert, mit dem das Programm wirklich läuft
        out.append(_p("KI-Positionen", OK if llm.line_items else WARN,
                      f"an (Kontext bis {llm.num_ctx})" if llm.line_items else "aus",
                      "" if llm.line_items else "RECHNUNG_LLM_LINE_ITEMS=1 in rechnung.env"))

    frei = shutil.disk_usage(store.root).free / 1e9
    out.append(_p("Speicherplatz", OK if frei > 5 else (WARN if frei > 1 else FEHLER), f"{frei:.1f} GB frei"))
    try:
        t = Path(store.root) / ".schreibtest"
        t.write_text("x")
        t.unlink()
        out.append(_p("Datenordner", OK, str(store.root)))
    except OSError as e:
        out.append(_p("Datenordner", FEHLER, f"nicht beschreibbar: {e}"))

    z = sicherung.ziel()
    if z is None:
        out.append(_p("Datensicherung", WARN, "kein Ziel eingestellt", "RECHNUNG_SICHERUNG_ZIEL in rechnung.env"))
    else:
        l = sicherung.letzte(z)
        alter = (time.time() - l.stat().st_mtime) / 3600 if l else None
        out.append(_p("Datensicherung", OK if alter is not None and alter < 30 else WARN,
                      f"letzte: {l.name}" if l else f"noch keine in {z}"))

    n_sup = len(store.suppliers())
    n_adr = len(store.addresses())
    out.append(_p("Stammdaten", OK if n_sup and n_adr else WARN,
                  f"{n_sup} Lieferanten, {n_adr} Geschäftsbereiche",
                  "" if n_sup and n_adr else "Abbyy-Listen im Reiter „Lieferanten“ einlesen"))
    return out
