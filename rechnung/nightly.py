"""Nachtlauf (SPEC §7r).

``run_nightly()`` (CLI ``rechnung nacht``): JobRouter-Rueckmeldungen holen
und einspielen (§7q), Layoutwechsel pruefen (§7p -- veraltete Vorlagen
auflisten; neu gelernt wird dabei ggf. schon ueber die eingespielten
Rueckmeldungen), Fehlerbericht (§7o) + Tagesbericht schreiben
(``data/berichte/<datum>_<zeit>.json``), Gesundheitschecks (IONOS
erreichbar, staut sich die JobRouter-Warteschlange, genug Plattenplatz).

Laeuft exklusiv (``lock.py``): die laufende Abholung (``ingest.fetch``)
und Stapellauf-Verarbeitung (``guard.Guard``) pausieren fuer die Dauer,
eine Verzoegerung ist dabei in Ordnung.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import statistics
import time
from pathlib import Path

from . import eventlog, jobrouter, lock, shadow
from .config import imap as _imap_cfg

# Schwellen fuer die Gesundheitschecks -- bewusst grosszuegig geschaetzt
# (noch keine echten Betriebsdaten zur Kalibrierung), siehe SPEC-v1.md.
QUEUE_WARN = 50
DISK_WARN_MB = 500
DISK_WARN_PCT = 5.0


def _today() -> str:
    return time.strftime("%Y-%m-%d")


def _check_ionos() -> dict:
    cfg = _imap_cfg()
    if not cfg.configured:
        return {"ok": None, "detail": "nicht konfiguriert (RECHNUNG_IMAP_*)"}
    import imaplib

    try:
        M = imaplib.IMAP4_SSL(cfg.host, cfg.port) if cfg.ssl else imaplib.IMAP4(cfg.host, cfg.port)
    except OSError as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    try:
        M.login(cfg.user, cfg.password)
        return {"ok": True, "detail": "Anmeldung erfolgreich"}
    except (imaplib.IMAP4.error, OSError) as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001 -- Gesundheitscheck darf nicht abstuerzen
            pass


def _check_queue(store) -> dict:
    offen = len(list(jobrouter.queue_dir(store.root).glob("*.json")))
    return {"ok": offen < QUEUE_WARN, "detail": f"{offen} Rechnung(en) offen"}


def _check_disk(store) -> dict:
    total, _used, free = shutil.disk_usage(store.root)
    frei_pct = (free / total * 100) if total else 100.0
    frei_mb = free // (1024 * 1024)
    ok = frei_mb >= DISK_WARN_MB and frei_pct >= DISK_WARN_PCT
    return {"ok": ok, "detail": f"{frei_mb} MB frei ({frei_pct:.1f} %)"}


def health_checks(store) -> dict:
    return {"ionos": _check_ionos(), "queue": _check_queue(store), "platte": _check_disk(store)}


def _duration_stats(durations: list[float]) -> dict:
    if not durations:
        return {}
    s = sorted(durations)
    return {"anzahl": len(s), "min": round(s[0], 2), "max": round(s[-1], 2),
            "median": round(statistics.median(s), 2), "mittel": round(sum(s) / len(s), 2)}


def _rueckstand(store, samples) -> int:
    """Wie viele Rechnungsdateien im Sample-Ordner liegen, die noch nie
    erfolgreich uebernommen wurden (kein Eintrag im Verarbeitungs-Log mit
    ihrem sha256) -- grobe Schaetzung fuer "staut sich die Warteschlange"."""
    if not samples:
        return 0
    samples = Path(samples)
    if not samples.exists():
        return 0
    from .pipeline import invoice_files

    done = {r.get("sha256") for r in store.processed() if r.get("sha256")}
    n = 0
    for p in invoice_files(samples):
        try:
            sha = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            continue
        if sha not in done:
            n += 1
    return n


def tagesbericht(store, samples=None, tag: str | None = None) -> dict:
    """Verarbeitet/an Mensch/Fehlerquote/Dauer-Verteilung/Rueckstand fuer
    einen Tag (Standard: heute, Serverzeit)."""
    tag = tag or _today()
    events = [e for e in store.events()
             if e.get("type") == eventlog.TYPE_PROCESSED and (e.get("ts") or "").startswith(tag)]
    an_mensch = sum(1 for e in events if e.get("status") != "auto")
    durations = [e["duration_s"] for e in events if e.get("duration_s")]
    totals = shadow.stats(store)["totals"]
    gesamt = totals.get("rechnungen") or 0
    fehler = totals.get("fehlentscheidungen") or 0
    return {
        "tag": tag,
        "verarbeitet": len(events),
        "an_mensch": an_mensch,
        "fehlerquote": round(fehler / gesamt, 3) if gesamt else None,
        "dauer": _duration_stats(durations),
        "rueckstand": _rueckstand(store, samples),
    }


def _write_report(store, bericht: dict) -> Path:
    out_dir = store.root / "berichte"
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / f"{time.strftime('%Y-%m-%d_%H%M%S')}.json"
    p.write_text(json.dumps(bericht, indent=2, ensure_ascii=False), "utf-8")
    return p


def run_nightly(store, samples: str | Path | None = None,
                operator: str = "nachtlauf") -> dict:
    """Kompletter Nachtlauf, siehe Modul-Docstring. Wirft ``RuntimeError``,
    wenn bereits ein anderer Nachtlauf laeuft (lock.py)."""
    with lock.acquire(store.root):
        t0 = time.monotonic()

        queued = jobrouter.queued_invoices(store.root)
        feedback = jobrouter.fetch_feedback(store.root, queued)
        applied = jobrouter.apply_feedback(store, feedback, operator="jobrouter")

        stale = [{"template_id": t.id, "supplier_id": t.supplier_id}
                 for t in store.all_templates() if t.stale]

        bericht = {
            "erstellt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "jobrouter_feedback": {"abgeholt": len(feedback), "eingespielt": applied},
            "layoutwechsel": {"veraltete_vorlagen": stale},
            "fehlerbericht": eventlog.report(store),
            "tagesbericht": tagesbericht(store, samples),
            "gesundheit": health_checks(store),
        }
        bericht["dauer_s"] = round(time.monotonic() - t0, 2)
        out = _write_report(store, bericht)
        bericht["datei"] = str(out)

        store.audit("nachtlauf", {
            "datei": str(out), "verarbeitet": bericht["tagesbericht"]["verarbeitet"],
            "veraltete_vorlagen": len(stale), "jobrouter_rueckmeldungen": len(feedback),
        }, operator)
        return bericht
