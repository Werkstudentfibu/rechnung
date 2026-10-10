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
from datetime import datetime, timedelta

from .human import file_name

TYPE_PROCESSED = "verarbeitung"
TYPE_CORRECTION = "korrektur"
# Ordner "Menschliche Bearbeitung" (human.py): abgelegt / erledigt
TYPE_HUMAN = "menschlich"
HUMAN_PLACED = "abgelegt"
HUMAN_DONE = "erledigt"
# Lieferanten-Entwurf (pipeline.confirm_supplier/discard_supplier)
TYPE_SUPPLIER = "lieferant"

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
        "file": file_name(res.file),
        "sha256": res.sha256,
        "supplier_id": res.supplier_id,
        "supplier_name": res.supplier_name,
        "stages": _stages(res),
        "status": res.status,
        "hold_reasons": list(res.hold_reasons or []),
        "hinweise": list(res.hinweise or []),
        "duration_s": res.duration_s,
        "aborted": res.aborted,
        "stufenzeiten": dict(res.stufenzeiten or {}),
        "seiten": getattr(res, "seiten", 0),
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
                      file: str = "", template_id: str | None = None,
                      quelle_vorher: str | None = None) -> dict:
    """Ein nachtraeglich korrigiertes Feld anhaengen. ``alt`` ist der Wert der
    vorherigen Korrektur (``None``, wenn es die erste Korrektur dieser
    Rechnung/dieses Feldes ist) -- Grundlage fuer "haeufige Korrekturen" im
    Fehlerbericht. ``template_id``/``quelle_vorher`` (Herkunft des Werts vor
    der Korrektur, z.B. "template-anchor"/"llm"/"missing") sind optional --
    Fortschrittsmass fuers Selbstlernen (Postfach-Baustein 11c)."""
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
        "template_id": template_id,
        "quelle_vorher": quelle_vorher,
        "operator": operator or "?",
    }
    store.event_append(rec)
    return rec


def record_human(store, rec: dict, aktion: str, operator: str = "") -> dict:
    """Ordner "Menschliche Bearbeitung" (human.py): eine Rechnung wurde
    abgelegt (``HUMAN_PLACED``, mit Gruenden) bzw. hat ihn nach Bearbeitung
    wieder verlassen (``HUMAN_DONE``, mit korrigierten Feldern)."""
    ev = {
        "type": TYPE_HUMAN,
        "aktion": aktion,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sha256": rec.get("sha256"),
        "file": rec.get("datei", ""),
        "supplier_id": rec.get("lieferant"),
        "supplier_name": rec.get("lieferant_name"),
        "gruende": [{"art": g.get("art"), "grund": g.get("grund")}
                    for g in rec.get("gruende") or []],
        "korrigiert": rec.get("korrigiert") or [],
        "freigabe": rec.get("freigabe"),
        "operator": operator or "?",
    }
    store.event_append(ev)
    return ev


def record_supplier(store, supplier_id: str, supplier_name: str, aktion: str,
                    operator: str = "") -> dict:
    """Lieferanten-Entwurf bestaetigt/verworfen (``aktion``:
    "bestaetigt"/"verworfen")."""
    ev = {
        "type": TYPE_SUPPLIER,
        "aktion": aktion,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "supplier_id": supplier_id,
        "supplier_name": supplier_name,
        "operator": operator or "?",
    }
    store.event_append(ev)
    return ev


def _group(rows: dict, key: tuple, **defaults) -> dict:
    row = rows.get(key)
    if row is None:
        row = {**defaults, "anzahl": 0, "beispiele": []}
        rows[key] = row
    return row


def _add_example(row: dict, file: str, sha256: str, detail: str = "") -> None:
    if len(row["beispiele"]) < MAX_EXAMPLES:
        row["beispiele"].append({"datei": file, "sha256": sha256, "grund": detail})


def _korrekturen_trend(events: list[dict]) -> list[dict]:
    """Je Lieferant: Korrekturen pro Rechnung ueber die Zeit (letzte 10 vs.
    davor) und die am haeufigsten korrigierten Felder -- Fortschrittsmass
    fuers Selbstlernen (Postfach-Baustein 11c): werden Vorlagen mit der
    Zeit tatsaechlich besser (weniger Korrekturen pro Rechnung)?"""
    per_invoice: dict[tuple[str, str], dict] = {}
    for e in events:
        if e.get("type") != TYPE_CORRECTION or not e.get("sha256") or not e.get("supplier_id"):
            continue
        key = (e["supplier_id"], e["sha256"])
        row = per_invoice.setdefault(key, {"ts": e.get("ts") or "", "anzahl": 0, "felder": {}})
        row["anzahl"] += 1
        ts = e.get("ts") or ""
        if ts and (not row["ts"] or ts < row["ts"]):
            row["ts"] = ts
        f = e.get("field") or "?"
        row["felder"][f] = row["felder"].get(f, 0) + 1

    by_supplier: dict[str, list[dict]] = {}
    for (sup, _sha), row in per_invoice.items():
        by_supplier.setdefault(sup, []).append(row)

    out = []
    for sup, rows in by_supplier.items():
        rows.sort(key=lambda r: r["ts"])
        letzte, davor = rows[-10:], rows[:-10]
        felder_gesamt: dict[str, int] = {}
        for r in rows:
            for f, n in r["felder"].items():
                felder_gesamt[f] = felder_gesamt.get(f, 0) + n
        top = sorted(felder_gesamt.items(), key=lambda kv: -kv[1])[:5]

        def _avg(rs: list[dict]) -> float | None:
            return round(sum(r["anzahl"] for r in rs) / len(rs), 2) if rs else None

        out.append({
            "lieferant": sup,
            "rechnungen_mit_korrektur": len(rows),
            "korrekturen_pro_rechnung_letzte_10": _avg(letzte),
            "korrekturen_pro_rechnung_davor": _avg(davor),
            "haeufigste_felder": [{"feld": f, "anzahl": n} for f, n in top],
        })
    out.sort(key=lambda r: -(r["korrekturen_pro_rechnung_letzte_10"] or 0))
    return out


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
    human: dict[tuple, dict] = {}

    for e in events:
        if e.get("type") == TYPE_HUMAN:
            if e.get("aktion") != HUMAN_PLACED:
                continue
            for g in e.get("gruende") or []:
                key = (g.get("art"), e.get("supplier_id") or "")
                row = _group(human, key, art=g.get("art"),
                            lieferant=e.get("supplier_id") or "(unbekannt)",
                            lieferant_name=e.get("supplier_name") or "")
                row["anzahl"] += 1
                _add_example(row, e.get("file", ""), e.get("sha256", ""), g.get("grund", ""))
            continue
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
        # Fortschritt je Lieferant: Korrekturen/Rechnung letzte 10 vs. davor
        # (Postfach-Baustein 11c)
        "korrekturen_trend": _korrekturen_trend(events),
        # an den Menschen gegeben (Ordner "Menschliche Bearbeitung") je Art+Lieferant
        "menschlich": sorted_rows(human),
    }


# ---- Fehlerstatistik je Lieferant und Feld (Aaron 10.10.2026) -------------
# Baut auf denselben Korrektur-Ereignissen auf wie ``report()``, zaehlt aber
# je Lieferant x Feld den ANTEIL der Rechnungen, an denen das Feld korrigiert
# wurde (nicht die Zahl der Klicks), mit Trend der letzten 4 Wochen -- und
# leitet daraus die "lohnendste Verbesserung" ab.

TREND_TAGE = 28
# ab hier lohnt sich ein Eingriff (Top-Liste): mindestens so viele
# korrigierte Rechnungen und mindestens dieser Anteil
LOHNEND_MIN_RECHNUNGEN = 2
LOHNEND_MIN_ANTEIL = 0.5
_STAMMDATEN_FELDER = ("iban", "ustid", "steuernummer", "adresse", "bic")


def _ts(s: str | None) -> datetime | None:
    """Zeitstempel der Ereignisse ("2026-10-10T13:12:13+0200") -> lokale
    Zeit ohne Zone (fuer den 4-Wochen-Vergleich genau genug)."""
    try:
        return datetime.fromisoformat((s or "")[:19]) if s else None
    except ValueError:
        return None


def _empfehlung(feld: str, hat_vorlage: bool) -> str:
    from .pipeline import POSITIONEN_KEY

    if feld == POSITIONEN_KEY:
        return "Positionstabelle anlernen bzw. Positionsmodus prüfen"
    if feld == "ust_aufstellung":
        return "USt-Block anlernen"
    if feld in _STAMMDATEN_FELDER:
        return "Stammdaten des Lieferanten pflegen"
    return "Vorlage nachtrainieren (Anker prüfen)" if hat_vorlage else "Vorlage anlernen"


def _feld_label(feld: str) -> str:
    from .pipeline import POSITIONEN_KEY

    return "Positionen" if feld == POSITIONEN_KEY else feld


def fehlerstatistik(store, heute: datetime | None = None) -> dict:
    """Je Lieferant x Feld: Anteil korrigierter Rechnungen (gesamt, letzte 4
    Wochen, davor) und Top-Liste "lohnendste Verbesserung"."""
    from .testsatz import bestaetigte

    heute = heute or datetime.now()
    grenze = heute - timedelta(days=TREND_TAGE)
    events = store.events()

    # Zeitpunkt je Rechnung: Bestaetigung, sonst fruehestes Ereignis
    zeit: dict[str, datetime] = {}
    for e in events:
        sha, t = e.get("sha256"), _ts(e.get("ts"))
        if sha and t and (sha not in zeit or t < zeit[sha]):
            zeit[sha] = t
    rechnungen: dict[str, set[str]] = {}
    namen: dict[str, str] = {}
    for rec in bestaetigte(store):
        sid, sha = rec.get("supplier_id"), rec.get("sha256")
        if not sid or not sha:
            continue
        rechnungen.setdefault(sid, set()).add(sha)
        namen[sid] = namen.get(sid) or rec.get("supplier_name") or ""
        if (t := _ts(rec.get("bestaetigt"))):
            zeit[sha] = t
    korrigiert: dict[tuple[str, str], set[str]] = {}
    for e in events:
        if e.get("type") != TYPE_CORRECTION or not e.get("supplier_id") or not e.get("sha256"):
            continue
        sid = e["supplier_id"]
        korrigiert.setdefault((sid, e.get("field") or "?"), set()).add(e["sha256"])
        # korrigiert, aber (noch) nicht bestaetigt -- zaehlt trotzdem als
        # Rechnung dieses Lieferanten, sonst laege der Anteil ueber 100 %
        rechnungen.setdefault(sid, set()).add(e["sha256"])
        namen[sid] = namen.get(sid) or e.get("supplier_name") or ""

    def anteil(k: int, n: int) -> float | None:
        return round(k / n, 2) if n else None

    zeilen = []
    for (sid, feld), shas in korrigiert.items():
        alle = rechnungen.get(sid, set())
        neu = {s for s in alle if zeit.get(s) and zeit[s] >= grenze}
        alt = alle - neu
        a4, a0 = anteil(len(shas & neu), len(neu)), anteil(len(shas & alt), len(alt))
        trend = None
        if a4 is not None and a0 is not None:
            trend = "besser" if a4 < a0 else ("schlechter" if a4 > a0 else "gleich")
        sup = store.supplier(sid)
        zeilen.append({
            "lieferant": sid,
            "lieferant_name": (sup.name if sup else "") or namen.get(sid, ""),
            "feld": feld, "feld_label": _feld_label(feld),
            "korrigiert": len(shas), "rechnungen": len(alle),
            "anteil": anteil(len(shas), len(alle)),
            "anteil_4_wochen": a4, "rechnungen_4_wochen": len(neu),
            "anteil_davor": a0, "trend": trend,
        })
    zeilen.sort(key=lambda r: (-r["korrigiert"], -(r["anteil"] or 0), r["lieferant"], r["feld"]))

    top = []
    for r in zeilen:
        if r["korrigiert"] < LOHNEND_MIN_RECHNUNGEN or (r["anteil"] or 0) < LOHNEND_MIN_ANTEIL:
            continue
        hat_vorlage = any(not t.stale for t in store.templates_for(r["lieferant"]))
        tipp = _empfehlung(r["feld"], hat_vorlage)
        who = r["lieferant_name"] or r["lieferant"]
        top.append(dict(r, empfehlung=tipp,
                        text=f"{who}: {r['feld_label']} in {r['korrigiert']} von "
                             f"{r['rechnungen']} Rechnungen korrigiert → {tipp}"))
    return {"stand": heute.strftime("%Y-%m-%d"), "trend_tage": TREND_TAGE,
            "zeilen": zeilen, "lohnend": top[:10]}
