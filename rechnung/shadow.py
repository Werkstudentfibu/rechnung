"""Schattenbetrieb-Messung: "haette das Programm diese Rechnung selbst
durchgelassen -- und haette es damit richtig gelegen?"

Grundlage fuer die langfristige Frage aus SPEC-v1.md §9 (Automatisierung
ohne Klick erst bei nachweislich niedriger Fehlerquote). Dieses Modul
ENTSCHEIDET NICHTS: es haelt pro Rechnung nur fest, was die Maschine
getan HAETTE, und vergleicht das beim Bestaetigen mit dem, was der Mensch
tatsaechlich bestaetigt (bzw. korrigiert) hat. Die Bestaetigungspflicht
(§7c) bleibt davon voellig unberuehrt -- auch eine Rechnung mit
``would_pass=True`` laeuft nie ohne Klick durch.

Die Entscheidung wird in ``pipeline.process()`` auf dem maschinellen Stand
VOR manuellen Korrekturen getroffen (``Result.shadow``) und beim Bestaetigen
per ``record()`` in ``data/shadow.jsonl`` (append-only) protokolliert.
"""

from __future__ import annotations

import time

from .human import file_name
from .template import STANDARD_FIELDS, FieldResult

REQUIRED = tuple(n for n, (_t, req) in STANDARD_FIELDS.items() if req)
# = pipeline.CONF_MIN (hier dupliziert, pipeline importiert shadow)
UNSURE_BELOW = 0.7


def decide(fields: dict[str, FieldResult], checks: list, new_supplier: bool) -> dict:
    """Wuerde die Maschine allein durchlassen? Alle vier Bedingungen:
    1. alle Pflichtfelder vorhanden,
    2. keine Pruefung rot (``ok is False``; "nicht pruefbar" zaehlt nicht rot),
    3. bei neuem Lieferanten (unbekannt/ohne Template) kein Pflichtfeld nur
       aus der KI-Extraktion -- dort gibt es noch nichts, woran sich das
       Modell bewaehrt haette.
    """
    from .config import iban_mode

    reasons = []
    present = {n: f for n, f in fields.items()
               if f.source != "missing" and f.value not in (None, "")}
    missing = [n for n in REQUIRED if n not in present]
    if missing:
        reasons.append("fehlt: " + ", ".join(missing))
    # Bei RECHNUNG_IBAN_ABWEICHUNG=warnen laeuft eine abweichende IBAN mit
    # Warnfeld durch (pipeline._apply_hold) -- dann auch hier nicht rot.
    ignored = {"iban-stammdaten"} if iban_mode() == "warnen" else set()
    red = [c.name for c in checks if c.ok is False and c.name not in ignored]
    if red:
        reasons.append("Pruefung rot: " + ", ".join(red))
    llm_only = [n for n in REQUIRED if n in present and present[n].source == "llm"]
    if new_supplier and llm_only:
        reasons.append("neuer Lieferant, nur KI: " + ", ".join(llm_only))
    # 4. kein gelesenes Pflichtfeld unter der Mindestkonfidenz -- dieselbe
    #    Grenze, ab der human.py die Rechnung zum Menschen gibt.
    unsure = [n for n in REQUIRED if n in present and present[n].source not in
              ("library", "manual") and present[n].confidence < UNSURE_BELOW]
    if unsure:
        reasons.append("unsicher: " + ", ".join(unsure))
    return {
        "would_pass": not reasons,
        "reasons": reasons,
        "new_supplier": new_supplier,
        "values": {n: f.value for n, f in present.items()},
        "sources": {n: f.source for n, f in present.items()},
    }


def _same(a, b) -> bool:
    if a is None or b is None:
        return a is b
    try:
        return abs(float(a) - float(b)) < 0.005
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def corrected_fields(res, store) -> list[str]:
    """Was hat der Mensch an dieser Rechnung geaendert?
    - manuelle Korrektur mit anderem Wert als die Maschine geliefert hatte
    - Anker an genau dieser Rechnung (nach)trainiert (Audit ``template_trained``)
      -- dann stimmt der maschinelle Stand nur, WEIL der Mensch eingegriffen hat
    """
    machine = (res.shadow or {}).get("values", {})
    out = {n for n, f in res.fields.items()
           if f.source == "manual" and not _same(machine.get(n), f.value)}
    for a in store.audit_log():
        if a.get("action") == "template_trained" and \
                (a.get("detail") or {}).get("sha256") == res.sha256:
            out.update(a["detail"].get("fields") or [])
    return sorted(out)


def record(store, res, operator: str = "") -> dict:
    """Beim Bestaetigen: Schatten-Entscheidung vs. tatsaechliches Ergebnis.

    Eine Entscheidung pro Rechnung: die erste Bestaetigung zaehlt. Ein
    erneutes Bestaetigen derselben Rechnung (sha256) wird nicht nochmal
    gewertet -- dort schlaegt u.a. die Dublettenpruefung gegen die Rechnung
    selbst an und wuerde das Bild verfaelschen."""
    prev = next((r for r in store.shadow_log() if r.get("sha256") == res.sha256), None)
    if prev is not None and res.sha256:
        return prev
    shadow = res.shadow or {"would_pass": False, "reasons": ["keine Entscheidung"]}
    # Zurueckgestellt (kein eindeutiger Lieferant/Rechnungsadresse aus der
    # Bibliothek, IBAN weicht von den Stammdaten ab, ...) zaehlt nie als
    # "wuerde durchlassen" -- unabhaengig davon, was decide() ergeben hatte.
    if getattr(res, "status", None) == "zurueckgestellt":
        shadow = dict(shadow, would_pass=False)
    corrected = corrected_fields(res, store)
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sha256": res.sha256,
        "file": file_name(res.file),
        "supplier_id": res.supplier_id,
        "supplier_name": res.supplier_name,
        "extraction": res.extraction,
        "would_pass": bool(shadow.get("would_pass")),
        "reasons": shadow.get("reasons", []),
        "corrected": corrected,
        # Fehlentscheidung: haette durchgelassen, Mensch musste aber eingreifen
        "wrong": bool(shadow.get("would_pass")) and bool(corrected),
        "operator": operator or "?",
    }
    store.shadow_append(rec)
    return rec


def record_feedback_correction(store, sha256: str, corrected_fields: list[str],
                               operator: str = "") -> dict | None:
    """Eine spaeter eintreffende Rueckmeldung (z.B. aus JobRouter, siehe
    ``jobrouter.apply_feedback``) als nachtraegliche Korrektur im
    Schattenbetrieb nachtragen -- eine vorher als "wuerde durchlassen"
    gewertete Rechnung, die sich im Nachhinein als falsch herausstellt,
    zaehlt dann als Fehlentscheidung. Haengt einen NEUEN Eintrag an
    (append-only wie ``record``); ``stats()`` wertet je sha256 den
    zuletzt angehaengten Eintrag."""
    prev = next((r for r in store.shadow_log() if r.get("sha256") == sha256), None)
    if prev is None or not corrected_fields:
        return None
    merged = sorted(set(prev.get("corrected") or []) | set(corrected_fields))
    rec = dict(prev, corrected=merged,
              wrong=bool(prev.get("would_pass")) and bool(merged),
              ts=time.strftime("%Y-%m-%dT%H:%M:%S%z"), operator=operator or "?")
    store.shadow_append(rec)
    return rec


def stats(store) -> dict:
    """Tabelle je Lieferant (ein Eintrag je Rechnung, siehe ``record``).
    Je sha256/Datei zaehlt der zuletzt angehaengte Eintrag -- eine spaetere
    Rueckmeldung (``record_feedback_correction``) kann die urspruengliche
    Entscheidung also nachtraeglich zu einer Fehlentscheidung machen."""
    latest: dict[str, dict] = {}
    for r in store.shadow_log():
        latest[r.get("sha256") or r.get("file")] = r
    rows: dict[str, dict] = {}
    for r in latest.values():
        key = r.get("supplier_id") or "(unbekannt)"
        row = rows.setdefault(key, {
            "lieferant": key, "name": r.get("supplier_name") or "",
            "rechnungen": 0, "wuerde_durchlassen": 0, "ohne_korrektur": 0,
            "fehlentscheidungen": 0,
        })
        row["name"] = row["name"] or r.get("supplier_name") or ""
        row["rechnungen"] += 1
        if r.get("would_pass"):
            row["wuerde_durchlassen"] += 1
            if not r.get("corrected"):
                row["ohne_korrektur"] += 1
        if r.get("wrong"):
            row["fehlentscheidungen"] += 1
    out = sorted(rows.values(), key=lambda x: (-x["rechnungen"], x["lieferant"]))
    totals = {k: sum(x[k] for x in out) for k in
              ("rechnungen", "wuerde_durchlassen", "ohne_korrektur", "fehlentscheidungen")}
    return {"rows": out, "totals": totals}
