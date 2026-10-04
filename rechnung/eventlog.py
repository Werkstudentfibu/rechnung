"""Vollstaendiges Fehler-/Ereignisprotokoll + Fehlerbericht.

Jede Verarbeitung (CLI ``scan``, Bestaetigen im Training-UI) haengt ein
Ereignis an ``data/events.jsonl`` an: Datei, sha256, Lieferant, welche
Stufen Werte beigetragen haben, Herkunft/Konfidenz je Feld, alle
Pruefungen mit Ergebnis/Grund, Dauer, Endstatus. Eine spaeter im
Training-UI vorgenommene Korrektur (Feld, alter Wert, neuer Wert) haengt
sich als eigener Ereignis-Typ an. Rein additiv wie ``audit.jsonl``/
``shadow.jsonl`` -- nichts wird ueberschrieben, jeder Verarbeitungs-
Durchlauf einer Rechnung bekommt sein eigenes Ereignis.

``report()`` (CLI ``rechnung fehlerbericht``, ``GET /api/fehlerbericht``)
wertet das aus: haeufigste wiederkehrende Pruefungs-Fehlschlaege und
fehlende/unsichere Felder je Pruefung/Feld und Lieferant, sowie haeufige
Korrekturen je Feld -- jeweils mit Beispiel-Rechnungen, absteigend nach
Haeufigkeit sortiert.

Bewusst eigenstaendig von ``shadow.py``: Schattenbetrieb misst "haette die
Maschine durchgelassen", dieses Modul sammelt die rohen Ereignisse fuer die
Fehlersuche (welche Pruefung reisst bei welchem Lieferanten am haeufigsten,
welches Feld wird am haeufigsten korrigiert) -- unabhaengige Fragen auf
denselben Ergebnissen.
"""

from __future__ import annotations

import time

TYPE_PROCESSED = "verarbeitung"
TYPE_CORRECTION = "korrektur"

# wie viele Beispiel-Rechnungen je Haeufungs-Gruppe im Fehlerbericht stehen
MAX_EXAMPLES = 5


def _stages(res) -> list[str]:
    """Welche Stufen/Herkunftsquellen an diesem Ergebnis beteiligt waren."""
    from .pipeline import origin as _origin

    stages = {o for fr in (res.fields or {}).values() if (o := _origin(fr.source))}
    if res.ocr:
        stages.add("ocr")
    return sorted(stages)


def record(store, res, operator: str = "") -> dict:
    """Ein Verarbeitungs-Ereignis anhaengen -- Momentaufnahme von ``res``."""
    from .pipeline import origin as _origin

    rec = {
        "type": TYPE_PROCESSED,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "file": res.file.rsplit("/", 1)[-1] if res.file else "",
        "sha256": res.sha256,
        "supplier_id": res.supplier_id,
        "supplier_name": res.supplier_name,
        "stages": _stages(res),
        "status": res.status,
        "hold_reasons": list(res.hold_reasons or []),
        "duration_s": res.duration_s,
        "aborted": res.aborted,
        "fields": {
            name: {"origin": _origin(fr.source), "confidence": fr.confidence}
            for name, fr in (res.fields or {}).items()
        },
        "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in (res.checks or [])],
        "operator": operator or "?",
    }
    store.event_append(rec)
    return rec


def record_correction(store, sha256: str, field: str, alt, neu, operator: str = "",
                      supplier_id: str | None = None, supplier_name: str | None = None,
                      file: str = "") -> dict:
    """Ein nachtraeglich korrigiertes Feld anhaengen. ``alt`` ist der Wert der
    vorherigen Korrektur (``None``, wenn es die erste Korrektur dieser
    Rechnung/dieses Feldes ist) -- Grundlage fuer "haeufige Korrekturen" im
    Fehlerbericht."""
    rec = {
        "type": TYPE_CORRECTION,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sha256": sha256,
        "file": file,
        "field": field,
        "alt": alt,
        "neu": neu,
        "supplier_id": supplier_id,
        "supplier_name": supplier_name,
        "operator": operator or "?",
    }
    store.event_append(rec)
    return rec


def _group(rows: dict, key: tuple, **defaults) -> dict:
    row = rows.get(key)
    if row is None:
        row = {**defaults, "anzahl": 0, "beispiele": []}
        rows[key] = row
    return row


def _add_example(row: dict, file: str, sha256: str, detail: str = "") -> None:
    if len(row["beispiele"]) < MAX_EXAMPLES:
        row["beispiele"].append({"datei": file, "sha256": sha256, "grund": detail})


def report(store, since: str | None = None) -> dict:
    """Haeufigste wiederkehrende Fehler: rote Pruefungen je Pruefung+Lieferant,
    fehlende/unsichere Felder je Feld+Lieferant, Korrekturen je Feld+Lieferant
    -- jeweils mit Beispiel-Rechnungen, absteigend nach Haeufigkeit. ``since``
    (``YYYY-MM-DD``) filtert auf Ereignisse ab diesem Tag."""
    from .pipeline import CONF_MIN

    events = store.events()
    if since:
        events = [e for e in events if (e.get("ts") or "") >= since]

    checks: dict[tuple, dict] = {}
    fields: dict[tuple, dict] = {}
    corrections: dict[tuple, dict] = {}

    for e in events:
        if e.get("type") == TYPE_CORRECTION:
            key = (e.get("field"), e.get("supplier_id") or "")
            row = _group(corrections, key, feld=e.get("field"),
                        lieferant=e.get("supplier_id") or "(unbekannt)",
                        lieferant_name=e.get("supplier_name") or "")
            row["anzahl"] += 1
            _add_example(row, e.get("file", ""), e.get("sha256", ""),
                        f"{e.get('alt')!r} -> {e.get('neu')!r}")
            continue
        if e.get("type") != TYPE_PROCESSED:
            continue
        supplier_key = e.get("supplier_id") or ""
        supplier_label = e.get("supplier_id") or "(unbekannt)"
        supplier_name = e.get("supplier_name") or ""
        for c in e.get("checks") or []:
            if c.get("ok") is not False:
                continue
            key = (c.get("name"), supplier_key)
            row = _group(checks, key, pruefung=c.get("name"), lieferant=supplier_label,
                        lieferant_name=supplier_name)
            row["anzahl"] += 1
            _add_example(row, e.get("file", ""), e.get("sha256", ""), c.get("detail", ""))
        for fname, finfo in (e.get("fields") or {}).items():
            conf = finfo.get("confidence")
            if finfo.get("origin") is not None and conf is not None and conf >= CONF_MIN:
                continue
            key = (fname, supplier_key)
            row = _group(fields, key, feld=fname, lieferant=supplier_label,
                        lieferant_name=supplier_name)
            row["anzahl"] += 1
            _add_example(row, e.get("file", ""), e.get("sha256", ""),
                        "fehlt" if finfo.get("origin") is None else f"Konfidenz {conf:g}")

    def sorted_rows(d: dict) -> list[dict]:
        return sorted(d.values(), key=lambda r: (-r["anzahl"], r.get("lieferant", "")))

    return {
        "seit": since,
        "pruefungen": sorted_rows(checks),
        "felder": sorted_rows(fields),
        "korrekturen": sorted_rows(corrections),
    }
