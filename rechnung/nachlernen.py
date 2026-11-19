"""Nachlernen aus bereits bestaetigten Rechnungen (Aaron 19.11.2026: "Setze
alles um" -- Hebel 1, um schneller zum autonomen Betrieb zu kommen).

Mehrere Verbesserungen am Lernen wirken nur bei NEU bestaetigten Rechnungen
(lange/mehrzeilige Positionsbeschreibungen, Netto/USt-Luecke, breitere
Tabellenkopf-Erkennung). Hier wird jede bestaetigte Rechnung des Testsatzes
(Original im Archiv + bestaetigte Soll-Werte, ``testsatz.py``) noch einmal
durch ``learn.learn_template`` geschickt -- dieselbe Pruefung mit Probelauf
und Gesamtprobe wie beim Bestaetigen.

Regeln:
- Gelernt werden nur die vom Menschen bestaetigten Werte (Soll-Werte), und nur
  Felder, die damals gelesen bzw. vom Menschen eingetragen wurden -- nichts
  aus Stammdaten, Textregeln oder der Rechenstufe.
- Positionen und USt-Aufstellung nur, wenn das Programm sie heute genau so
  liest wie bestaetigt (dann ist bekannt, welche Zellen gelesen und welche
  errechnet sind); sonst uebersprungen mit Grund.
- Eine gesicherte Vorlage wird nie stillschweigend umgeschrieben, nur
  ergaenzt (Regel aus ``learn.learn_template``).
- Bestaetigungen werden NICHT hochgezaehlt -- gesichert wird eine Vorlage
  weiterhin nur durch Klicks eines Menschen.
- Ohne ``ausfuehren`` wird nur angezeigt, was sich aendern wuerde (nichts
  gespeichert, auch das Spaltenwoerterbuch nicht).
"""

from __future__ import annotations

from collections import Counter

from . import rechnen, testsatz
from .identify import POSITIONSMODUS_EINE_POSITION
from .learn import LEARNABLE_TYPES, _active_template, _next_template_id, learn_template
from .store import Store
from .template import STANDARD_FIELDS, Template

# Herkunft eines bestaetigten Werts, die nie als Anker gelernt wird (wie
# ``learn.confirmed_values``)
_NICHT_LERNEN = ("missing", "library", "text", rechnen.ERRECHNET, rechnen.KOPF)


class _NurLesen(Store):
    """Anzeige-Lauf: das Spaltenwoerterbuch wird nicht hochgezaehlt."""

    def spaltenwort_lernen(self, *a, **k) -> None:  # noqa: D401
        return None


def _werte(fall: dict) -> dict:
    quellen = fall.get("quellen") or {}
    out = {}
    for name, wert in (fall.get("felder") or {}).items():
        typ = STANDARD_FIELDS.get(name, ("",))[0]
        if typ not in LEARNABLE_TYPES or wert in (None, ""):
            continue
        if quellen.get(name) in _NICHT_LERNEN:
            continue
        out[name] = wert
    return out


def _positionen(fall: dict, res, sup) -> tuple[list[dict] | None, str]:
    soll = fall.get("positionen") or []
    if not soll:
        return None, ""
    if sup is not None and sup.positionsmodus == POSITIONSMODUS_EINE_POSITION:
        return None, "Positionsmodus 'eine Position'"
    v = testsatz._positionen_vergleich(soll, res.line_items or [])
    if not (v["korrekt"] == v["soll"] == v["gefunden"]):
        return None, "Positionen werden heute anders gelesen als bestaetigt"
    return rechnen.nur_gelesene_werte(res.line_items) or None, ""


def _ust(fall: dict, res) -> tuple[list[dict] | None, str]:
    soll = fall.get("ust_aufstellung") or []
    if not soll:
        return None, ""
    fr = (res.fields or {}).get("ust_aufstellung")
    ist = fr.value if fr is not None and fr.source != "missing" and isinstance(fr.value, list) else []
    if testsatz._ust_vergleich(soll, ist) != testsatz.RICHTIG:
        return None, "USt-Aufstellung wird heute anders gelesen als bestaetigt"
    return rechnen.nur_gelesene_werte(ist) or None, ""


def _vorlage(store: Store, overlay: dict, sid: str, res, fall: dict
             ) -> tuple[Template | None, str, str | None]:
    """-> (bestehende Vorlage oder None, Variante, Id fuer eine neue)."""
    def aktuell(t: Template | None) -> Template | None:
        return overlay.get(t.id, t) if t is not None else None

    for tid in (fall.get("template_id"), res.template_id):
        t = aktuell(store.template(tid)) if tid else None
        if t is not None and t.supplier_id == sid and not t.stale:
            return t, t.variant, None
    aktive = [t for t in store.templates_for(sid) if not t.stale]
    neu = [t for t in overlay.values() if t.supplier_id == sid and not t.stale
           and t.id not in {a.id for a in aktive}]
    if len(aktive) + len(neu) == 1:
        t = aktuell(aktive[0]) if aktive else neu[0]
        return t, t.variant, None
    variant = res.billing_address_id or "standard"
    t = aktuell(_active_template(store, sid, variant))
    if t is None:
        t = next((x for x in neu if x.variant == variant), None)
    if t is not None:
        return t, variant, None
    return None, variant, _next_template_id(store, sid, variant)


def laufen(store: Store, ausfuehren: bool = False, operator: str = "",
           lieferant: str | None = None, fortschritt=None) -> dict:
    """Alle bestaetigten Rechnungen (optional nur eines Lieferanten) nachlernen.
    ``ausfuehren=False``: nur anzeigen."""
    from . import semantic
    from .pipeline import load_document, process

    if ausfuehren and not operator:
        raise ValueError("Zum Ausfuehren ist --operator noetig (Audit)")
    # dieselben KI-Antworten wie beim Bestaetigen (Positionen, die die KI
    # gelesen hat, werden so ohne neue, langsame Modellanfrage wiedergefunden)
    semantic.set_cache_dir(store.root / "ki-cache")
    lesen = store if ausfuehren else _NurLesen(store.root)
    todo, skip = testsatz.faelle(store, lieferant)
    overlay: dict[str, Template] = {}   # in diesem Lauf (neu) gelernte Vorlagen
    faelle_out, uebersprungen = [], list(skip)
    for i, fall in enumerate(todo):
        if fortschritt:
            fortschritt(i, len(todo), fall.get("datei") or "")
        kurz = {"sha256": fall["sha256"], "datei": fall.get("datei"),
                "lieferant": fall.get("supplier_id"), "lieferant_name": fall.get("supplier_name")}
        sid = fall.get("supplier_id")
        if fall.get("hergeleitet"):
            uebersprungen.append(dict(kurz, grund="vor dem 10.10.2026 bestaetigt (ohne Herkunft)"))
            continue
        if fall.get("extraktion") == "einvoice":
            uebersprungen.append(dict(kurz, grund="E-Rechnung (keine Vorlage noetig)"))
            continue
        sup = store.supplier(sid) if sid else None
        if sup is None:
            uebersprungen.append(dict(kurz, grund="Lieferant nicht (mehr) in den Stammdaten"))
            continue
        doc, o = load_document(fall["_pfad"], store)
        if doc is None:
            uebersprungen.append(dict(kurz, grund=f"nicht lesbar ({o.error if o else '?'})"))
            continue
        res = process(fall["_pfad"], store, use_llm=False)
        werte = _werte(fall)
        items, grund_pos = _positionen(fall, res, sup)
        ust, grund_ust = _ust(fall, res)
        existing, variant, tid_neu = _vorlage(store, overlay, sid, res, fall)
        tpl, outcome = learn_template(doc, sid, werte, existing, variant, template_id=tid_neu,
                                      line_items=items, taxblock=ust, store=lesen)
        hinweise = [g for g in (grund_pos, grund_ust) if g]
        eintrag = dict(kurz, vorlage=outcome.template_id, neu=outcome.created and tpl is not None,
                       gelernt=list(outcome.learned), behalten=list(outcome.kept),
                       uebersprungen=dict(outcome.skipped), hinweise=hinweise,
                       grund=outcome.reason, gespeichert=False)
        if tpl is not None:
            overlay[tpl.id] = tpl
            if ausfuehren:
                store.save_template(tpl)
                store.audit("template_nachgelernt", {
                    "sha256": fall["sha256"], "template_id": tpl.id, "created": outcome.created,
                    "learned": outcome.learned, "kept": outcome.kept,
                    "skipped": outcome.skipped}, operator)
                eintrag["gespeichert"] = True
        faelle_out.append(eintrag)
    if fortschritt:
        fortschritt(len(todo), len(todo), "")
    geaendert = [f for f in faelle_out if f["gelernt"]]
    return {
        "ausgefuehrt": bool(ausfuehren),
        "filter": {"lieferant": lieferant or None},
        "rechnungen": len(faelle_out),
        "mit_aenderung": len(geaendert),
        "vorlagen_neu": len({f["vorlage"] for f in geaendert if f["neu"]}),
        "vorlagen_ergaenzt": len({f["vorlage"] for f in geaendert if not f["neu"]}),
        "positionstabellen_neu": len({f["vorlage"] for f in geaendert
                                      if "_positionen" in f["gelernt"]}),
        "felder_gelernt": dict(Counter(x for f in geaendert for x in f["gelernt"]).most_common()),
        "lieferanten": len({f["lieferant"] for f in geaendert}),
        "uebersprungen": uebersprungen,
        "faelle": faelle_out,
    }


def text(b: dict) -> str:
    kopf = ("Nachlernen ausgefuehrt" if b["ausgefuehrt"]
            else "Nachlernen -- nur Anzeige, nichts gespeichert")
    lines = [f"{kopf}: {b['rechnungen']} bestaetigte Rechnung(en) geprueft",
             f"  Wuerde lernen bei {b['mit_aenderung']} Rechnung(en) von {b['lieferanten']} "
             f"Lieferant(en)" if not b["ausgefuehrt"] else
             f"  Gelernt bei {b['mit_aenderung']} Rechnung(en) von {b['lieferanten']} Lieferant(en)",
             f"  Vorlagen neu: {b['vorlagen_neu']}, ergaenzt: {b['vorlagen_ergaenzt']}, "
             f"davon mit neuer Positionstabelle: {b['positionstabellen_neu']}"]
    if b["felder_gelernt"]:
        lines.append("  Felder: " + ", ".join(f"{k} {v}" for k, v in b["felder_gelernt"].items()))
    for f in b["faelle"]:
        if f["gelernt"]:
            who = f.get("lieferant_name") or f.get("lieferant") or "?"
            lines.append(f"    {who}: {', '.join(f['gelernt'])}"
                         + (" (neue Vorlage)" if f["neu"] else ""))
    gruende = Counter(s["grund"] for s in b["uebersprungen"])
    gruende.update(h for f in b["faelle"] for h in f["hinweise"])
    if gruende:
        lines.append("  Nicht nachgelernt:")
        lines.extend(f"    {n:4d}  {g}" for g, n in gruende.most_common())
    if not b["ausgefuehrt"] and b["mit_aenderung"]:
        lines.append("Zum Speichern: rechnung nachlernen --ausfuehren --operator NAME")
    return "\n".join(lines)
