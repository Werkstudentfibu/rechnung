"""Vorlagen automatisch lernen -- Stufe 1 aus bestaetigten Rechnungen.

Beim Bestaetigen im Training-UI kennt das Programm die korrekten Feldwerte
(Extraktion + manuelle Korrekturen, so wie der Mensch sie gesehen und per
Klick abgesegnet hat). Statt jeden Anker von Hand anzuklicken, sucht dieses
Modul die Werte im PDF wieder, nimmt das naechstgelegene sinnvolle Label-
Wort links/oberhalb als Anker und baut daraus dieselbe ``FieldSpec`` (Anker +
relative Region), die auch das manuelle Anlernen erzeugt
(``template.build_field_spec``).

Leitplanken:
- nur aus bestaetigten Rechnungen (``Store.is_confirmed``), nie aus
  ungepruefter KI-Ausgabe; E-Rechnungen brauchen kein Layout.
- kommt ein Wert mehrfach vor (Brutto = Zahlbetrag, USt-Satz in jeder
  Position), entscheidet die Label-Heuristik; bleibt es unentschieden, wird
  das Feld weggelassen statt geraten.
- das Ergebnis wird gegen die Quell-Rechnung angewendet und nur gespeichert,
  wenn es exakt die bestaetigten Werte reproduziert.
- ein neu gelerntes Template startet mit ``confirmations=0`` (nicht
  gesichert) und durchlaeuft den normalen Bestaetigungszaehler; ein bereits
  gesichertes Template wird nur ergaenzt, nie umgeschrieben.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdf import Document, Word
from .template import (STANDARD_FIELDS, FieldResult, FieldSpec, Template, apply_template,
                       build_field_spec)
from .textnorm import MAX_SPAN, norm, parse_amount, parse_date, same_value

# Felder, die als Anker+Region gelernt werden koennen. taxblock (USt-
# Aufstellung) ist ein Mehrzeilen-Block, dafuer reicht "Wert wiederfinden"
# nicht -- bleibt manuell.
LEARNABLE_TYPES = {"amount", "date", "number", "text"}

# Mindestkonfidenz, die ein gelerntes Feld beim Probelauf erreichen muss --
# dieselbe Schwelle wie pipeline.CONF_MIN (Import hier wuerde einen Zyklus
# erzeugen). Ein mehrdeutiger Anker (0,55) faellt damit raus.
CONF_MIN = 0.7

# Label-Hinweise je Feld: (starke, schwache) Stichwoerter, verglichen mit
# norm(label) per Gleichheit oder Praefix ("nettobetrag" beginnt mit "netto").
_LABEL_HINTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "rechnungsnummer": (("rechnungsnummer", "rechnungsnr", "rechnung", "invoice",
                         "belegnummer", "beleg-nr", "belegnr"),
                        ("nr", "nummer", "no", "number", "beleg")),
    "rechnungsdatum": (("rechnungsdatum", "datum", "date", "invoice-date"),
                       ("vom", "ausgestellt")),
    "netto": (("netto", "nettobetrag", "nettosumme", "zwischensumme", "net",
               "subtotal", "warenwert"), ("summe",)),
    "ust_betrag": (("umsatzsteuer", "ust", "mwst", "mehrwertsteuer", "vat", "tax"),
                   ("steuer", "steuerbetrag")),
    "ust_satz": (("umsatzsteuer", "ust", "mwst", "mehrwertsteuer", "vat", "tax",
                  "steuersatz"), ("steuer",)),
    "brutto": (("brutto", "bruttobetrag", "gesamtbetrag", "rechnungsbetrag", "endbetrag",
                "gesamtsumme", "total"), ("zahlbetrag", "summe", "betrag", "gesamt")),
    "bestellnummer": (("bestellnummer", "bestellnr", "bestellung", "auftragsnummer",
                       "auftrag", "order", "po"), ("ihre", "nr", "referenz")),
    "kostenstelle": (("kostenstelle", "kst"), ()),
}

_ABOVE_LINES = 3.0  # wie weit oberhalb (in Zeilenhoehen) ein Label stehen darf


@dataclass
class LearnOutcome:
    template_id: str | None = None
    saved: bool = False
    created: bool = False
    learned: list[str] = field(default_factory=list)   # neu/praezisiert
    kept: list[str] = field(default_factory=list)      # bestehende Spec reproduziert schon
    skipped: dict[str, str] = field(default_factory=dict)  # Feld -> Grund
    reason: str = ""                                    # warum gar nicht gelernt

    def to_dict(self) -> dict:
        return {"template_id": self.template_id, "saved": self.saved,
                "created": self.created, "learned": self.learned, "kept": self.kept,
                "skipped": self.skipped, "reason": self.reason}


# ---- Wert im Layout wiederfinden --------------------------------------
def _lines(doc: Document) -> list[list[Word]]:
    """Woerter je Seite zu Textzeilen buendeln (y-Mitte nah beieinander)."""
    out: list[list[Word]] = []
    for pg in doc.pages:
        cur: list[Word] = []
        cy = None
        for w in sorted(pg.words, key=lambda w: (w.cy, w.x0)):
            tol = max(2.0, (w.bottom - w.top) * 0.4)
            if cy is None or abs(w.cy - cy) <= tol:
                cur.append(w)
                cy = w.cy if cy is None else (cy + w.cy) / 2
            else:
                out.append(sorted(cur, key=lambda w: w.x0))
                cur, cy = [w], w.cy
        if cur:
            out.append(sorted(cur, key=lambda w: w.x0))
    return out


def find_value_spans(doc: Document, value, value_type: str) -> list[list[Word]]:
    """Alle minimalen Wortfolgen (eine Zeile, bis MAX_SPAN Woerter), die den
    Wert darstellen. "1000.00 EUR" zaehlt nicht extra, wenn "1000.00" allein
    schon passt."""
    hits: list[list[Word]] = []
    for line in _lines(doc):
        for i in range(len(line)):
            for n in range(1, MAX_SPAN + 1):
                if i + n > len(line):
                    break
                span = line[i:i + n]
                if same_value(" ".join(w.text for w in span), value, value_type):
                    hits.append(span)
                    break  # laengere Fenster ab i enthalten dieses
    # Fenster verwerfen, die ein anderes Treffer-Fenster komplett enthalten
    ids = [{id(w) for w in s} for s in hits]
    return [s for k, s in enumerate(hits)
            if not any(j != k and ids[j] < ids[k] for j in range(len(hits)))]


def _is_label(w: Word) -> bool:
    """Taugt das Wort als Anker? Mindestens zwei Buchstaben, kein Betrag/Datum,
    keine Waehrung."""
    n = norm(w.text)
    if sum(c.isalpha() for c in n) < 2 or n in ("eur", "euro", "chf", "usd"):
        return False
    return parse_date(w.text) is None and not (
        parse_amount(w.text) is not None and sum(c.isdigit() for c in n) > len(n) // 2)


def _hint_score(fname: str, label: Word) -> int:
    strong, weak = _LABEL_HINTS.get(fname, ((), ()))
    n = norm(label.text)
    if any(n == h or n.startswith(h) for h in strong):
        return 2
    if any(n == h or n.startswith(h) for h in weak):
        return 1
    return 0


def _label_candidates(doc: Document, span: list[Word]) -> list[tuple[float, bool, Word]]:
    """(Abstand, gleiche Zeile?, Wort) fuer Label-Kandidaten links in derselben
    Zeile bzw. oberhalb mit horizontaler Ueberdeckung."""
    v0, v1 = span[0], span[-1]
    h = max(v0.bottom - v0.top, 6.0)
    out = []
    for w in doc.pages[v0.page].words:
        if w in span or not _is_label(w):
            continue
        same_line = abs(w.cy - v0.cy) <= max(2.0, h * 0.4)
        if same_line and w.x1 <= v0.x0 + 1:
            out.append((v0.x0 - w.x1, True, w))
        elif (not same_line and w.bottom <= v0.top + 1
              and v0.top - w.bottom <= h * _ABOVE_LINES
              and w.x0 <= v1.x1 + 20 and w.x1 >= v0.x0 - 20):
            out.append(((v0.top - w.bottom) * 1.5 + abs(w.x0 - v0.x0) * 0.5, False, w))
    return out


def _page_count(doc: Document, w: Word) -> int:
    t = norm(w.text)
    return sum(1 for x in doc.pages[w.page].words if norm(x.text) == t)


def _best_label(doc: Document, fname: str, span: list[Word]) -> tuple[int, Word | None]:
    """-> (Label-Score, Ankerwort). Score: Stichwort-Treffer zaehlt doppelt,
    gleiche Zeile gibt einen Bonus. Als Anker nur Woerter, die auf der Seite
    genau einmal vorkommen -- sonst haengt die Extraktion am ``occurrence``-
    Index und landet bei 0,55 Konfidenz (template._find_anchor)."""
    cands = [(d, same, w) for d, same, w in _label_candidates(doc, span)
             if _page_count(doc, w) == 1]
    if not cands:
        return 0, None
    cands.sort(key=lambda c: (-_hint_score(fname, c[2]), not c[1], c[0]))
    d, same, w = cands[0]
    return _hint_score(fname, w) * 2 + (1 if same else 0), w


def locate(doc: Document, fname: str, value, value_type: str
           ) -> tuple[list[Word] | None, Word | None, str]:
    """Wert + Anker fuer ein Feld bestimmen. -> (Wertwoerter, Anker, Grund)."""
    spans = find_value_spans(doc, value, value_type)
    if not spans:
        return None, None, "nicht im PDF gefunden"
    scored = []
    for s in spans:
        score, anchor = _best_label(doc, fname, s)
        if anchor is not None:
            scored.append((score, s, anchor))
    if not scored:
        return None, None, "kein eindeutiges Label in der Naehe"
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None, None, f"mehrdeutig ({len(spans)} Vorkommen, Label entscheidet nicht)"
    if len(spans) > 1 and _hint_score(fname, scored[0][2]) == 0:
        # Mehrere Vorkommen und das gewaehlte Label passt nicht mal zum Feld:
        # das waere geraten.
        return None, None, f"mehrdeutig ({len(spans)} Vorkommen, kein passendes Label)"
    return scored[0][1], scored[0][2], ""


# ---- Template bauen + pruefen -----------------------------------------
def _reproduces(fr: FieldResult | None, want, value_type: str) -> bool:
    """Liefert die Extraktion exakt den bestaetigten Wert, mit voller Konfidenz?"""
    if fr is None or fr.source == "missing" or fr.confidence < CONF_MIN:
        return False
    if value_type == "amount":
        try:
            return abs(float(fr.value) - float(want)) < 0.005
        except (TypeError, ValueError):
            return False
    return str(fr.value).strip() == str(want).strip()


def confirmed_values(fields: dict[str, FieldResult]) -> dict[str, object]:
    """Lernbare, bestaetigte Werte aus einem (bereits bestaetigten) Result.
    Stammdaten/feste Werte (Quelle ``library``) brauchen keine Position."""
    out = {}
    for name, fr in fields.items():
        vtype = STANDARD_FIELDS.get(name, ("",))[0]
        if vtype not in LEARNABLE_TYPES or fr.source in ("missing", "library"):
            continue
        if fr.value is None or fr.value == "":
            continue
        out[name] = fr.value
    return out


def learn_template(doc: Document, supplier_id: str, values: dict[str, object],
                   existing: Template | None = None, variant: str = "standard",
                   template_id: str | None = None
                   ) -> tuple[Template | None, LearnOutcome]:
    """Aus bestaetigten Werten ein Template ableiten bzw. ``existing`` ergaenzen.
    Speichert nichts -- liefert das Template (oder None, wenn sich nichts
    Verwertbares ergibt) plus Protokoll. ``template_id`` erzwingt eine Id fuer
    ein neues Template (ohne ``existing``) -- genutzt, wenn eine als veraltet
    markierte Vorlage fuer dieselbe Variante schon die Standard-Id belegt
    (SPEC §7p: Layoutwechsel erkennen)."""
    tid = existing.id if existing else (template_id or f"{supplier_id}__{variant}")
    outcome = LearnOutcome(template_id=tid, created=existing is None)
    tpl = (Template.from_dict(existing.to_dict()) if existing
           else Template(id=tid, supplier_id=supplier_id, variant=variant))
    current = apply_template(doc, tpl) if existing else {}
    by_name = {f.name: f for f in tpl.fields}

    for fname, want in values.items():
        vtype, required = STANDARD_FIELDS[fname]
        if fname in by_name and _reproduces(current.get(fname), want, by_name[fname].value_type):
            outcome.kept.append(fname)
            continue
        if fname in by_name and tpl.secured:
            # Gesichertes Template nie stillschweigend umschreiben -- der
            # Abweichung soll ein Mensch nachgehen (Audit-Detail).
            outcome.skipped[fname] = "gesichertes Template liefert anderen Wert"
            continue
        value_words, anchor, why = locate(doc, fname, want, vtype)
        if value_words is None:
            outcome.skipped[fname] = why
            continue
        pg = doc.pages[value_words[0].page]
        spec = build_field_spec(fname, vtype, required, anchor, value_words,
                                pg.width, pg.height)
        # Probelauf nur mit diesem Feld: reproduziert es den Wert?
        probe = Template(id=tid, supplier_id=supplier_id, fields=[spec])
        if not _reproduces(apply_template(doc, probe).get(fname), want, vtype):
            outcome.skipped[fname] = "Probelauf liefert anderen Wert"
            continue
        by_name[fname] = spec
        outcome.learned.append(fname)

    if not outcome.learned:
        outcome.reason = outcome.reason or "nichts Neues gelernt"
        return None, outcome
    tpl.fields = list(by_name.values())

    # Gesamtprobe: das fertige Template muss alle bestaetigten Werte, die es
    # abdeckt, exakt reproduzieren -- sonst wird gar nichts gespeichert.
    final = apply_template(doc, tpl)
    for f in tpl.fields:
        if f.name in values and not _reproduces(final.get(f.name), values[f.name], f.value_type):
            outcome.reason = f"Gesamtprobe: {f.name} weicht ab"
            outcome.learned = []
            return None, outcome
    return tpl, outcome


# ---- Layoutwechsel erkennen (SPEC §7p) ---------------------------------
# Wie oft in Folge eine gesicherte Vorlage ein Pflichtfeld nicht findet bzw.
# eine Kernpruefung nicht aufgehen laesst, bevor sie als "veraltet" gilt.
STALE_THRESHOLD = 2
# Nur Kernpruefungen (Betraege) zaehlen als Layout-Indiz -- "iban-stammdaten"
# o.ae. haengt an der Bibliothek, nicht am Layout, und wuerde sonst eine
# intakte Vorlage faelschlich als veraltet markieren.
_LAYOUT_CHECKS = ("netto+ust=brutto", "betraege-plausibel")

_VERSION_RE = re.compile(r"__v(\d+)$")


def _template_ok(tpl: Template, res) -> tuple[bool, str]:
    """War die zuletzt BENUTZTE Vorlage auf dem maschinellen Stand (vor
    Korrekturen, ``res.shadow``) ok? Pflichtfelder gefunden und keine
    Kernpruefung rot -- sonst deutet das auf einen Layoutwechsel beim
    Lieferanten hin."""
    machine_sources = (res.shadow or {}).get("sources", {})
    required = [f.name for f in tpl.fields if f.required]
    missing = [n for n in required if machine_sources.get(n) in (None, "missing")]
    if missing:
        return False, f"Pflichtfeld(er) nicht gefunden: {', '.join(missing)}"
    red = [c.name for c in res.checks if c.ok is False and c.name in _LAYOUT_CHECKS]
    if red:
        return False, f"Pruefung(en) nicht aufgegangen: {', '.join(red)}"
    return True, ""


def track_template_health(store, tpl: Template, res, operator: str = "") -> None:
    """Zaehlt Fehlschlaege einer gesicherten Vorlage in Folge und markiert sie
    ab ``STALE_THRESHOLD`` als veraltet (Audit ``template_stale``) -- bleibt
    gespeichert, wird aber von ``pipeline._pick_template`` nicht mehr
    ausgewaehlt. Nur fuer bereits gesicherte Vorlagen relevant (§7p); eine
    noch nicht gesicherte Vorlage ist ohnehin in der Lernphase."""
    if not tpl.secured or tpl.stale:
        return
    ok, reason = _template_ok(tpl, res)
    if ok:
        if tpl.consecutive_failures:
            tpl.consecutive_failures = 0
            store.save_template(tpl)
        return
    tpl.consecutive_failures += 1
    if tpl.consecutive_failures >= STALE_THRESHOLD:
        tpl.stale = True
        store.save_template(tpl)
        store.audit("template_stale", {
            "template_id": tpl.id, "supplier_id": tpl.supplier_id,
            "sha256": res.sha256, "grund": reason,
            "fehlschlaege_in_folge": tpl.consecutive_failures,
        }, operator)
    else:
        store.save_template(tpl)


def _variant_templates(store, supplier_id: str, variant: str) -> list[Template]:
    base_id = f"{supplier_id}__{variant}"
    return [t for t in store.templates_for(supplier_id)
            if t.id == base_id or t.id.startswith(base_id + "__v")]


def _active_template(store, supplier_id: str, variant: str) -> Template | None:
    """Nicht als veraltet markiertes Template fuer (supplier, variant), falls
    vorhanden -- sonst ``None``, auch wenn fuer dieselbe Variante noch
    veraltete Versionen existieren (die bleiben erhalten, werden aber nicht
    mehr als Lern-Basis genommen)."""
    active = [t for t in _variant_templates(store, supplier_id, variant) if not t.stale]
    return active[0] if active else None


def _next_template_id(store, supplier_id: str, variant: str) -> str:
    """Freie Id fuer eine NEUE Vorlage derselben Variante, wenn die
    Standard-Id (``supplier__variant``) schon von einer veralteten Version
    belegt ist -- die alte bleibt unter ihrer Id erhalten (Nachvollziehbarkeit)."""
    versions = _variant_templates(store, supplier_id, variant)
    base_id = f"{supplier_id}__{variant}"
    if not versions:
        return base_id
    nums = [1]
    for t in versions:
        m = _VERSION_RE.search(t.id)
        nums.append(int(m.group(1)) if m else 1)
    return f"{base_id}__v{max(nums) + 1}"


def learn_from_confirmed(store, res, doc: Document, operator: str = "") -> LearnOutcome:
    """Einstieg fuer den Bestaetigungs-Pfad: lernt aus ``res`` (Ergebnis von
    ``pipeline.process()`` NACH dem Bestaetigungs-Klick, inkl. Korrekturen)
    und speichert bei Erfolg mit Audit-Eintrag ``template_learned``."""
    if not res.sha256 or not store.is_confirmed(res.sha256):
        return LearnOutcome(reason="nicht bestaetigt")
    if res.extraction == "einvoice":
        return LearnOutcome(reason="E-Rechnung (kein Layout noetig)")
    if not res.supplier_id:
        return LearnOutcome(reason="Lieferant unbekannt")

    used_tpl = store.template(res.template_id) if res.template_id else None
    if used_tpl is not None:
        track_template_health(store, used_tpl, res, operator)

    existing = used_tpl
    variant = existing.variant if existing else (res.billing_address_id or "standard")
    tid_override = None
    if existing is None:
        # Gibt es fuer die Variante schon eine nicht-veraltete Vorlage (nur
        # nicht gewaehlt, z.B. weil kein Treffer)? Sonst eine neue anlegen --
        # unter einer neuen Id, falls die Standard-Id noch von einer als
        # veraltet markierten Version belegt ist (§7p).
        existing = _active_template(store, res.supplier_id, variant)
        if existing is None:
            tid_override = _next_template_id(store, res.supplier_id, variant)
    if existing is not None:
        existing.confirmations = max(existing.confirmations,
                                     store.distinct_confirmations(existing.id))

    tpl, outcome = learn_template(doc, res.supplier_id, confirmed_values(res.fields),
                                  existing, variant, template_id=tid_override)
    if tpl is None:
        if outcome.reason.startswith("Gesamtprobe"):
            # Bestehendes (z.B. gesichertes) Template liefert auf einer
            # bestaetigten Rechnung einen anderen Wert -- nicht still
            # uebergehen, sondern fuer einen Menschen nachvollziehbar machen.
            store.audit("template_learn_rejected", {
                "sha256": res.sha256, "template_id": outcome.template_id,
                "reason": outcome.reason, "skipped": outcome.skipped,
            }, operator)
        return outcome
    store.save_template(tpl)
    outcome.saved = True
    store.audit("template_learned", {
        "sha256": res.sha256, "template_id": tpl.id, "created": outcome.created,
        "learned": outcome.learned, "kept": outcome.kept, "skipped": outcome.skipped,
        "anchors": {f.name: f.anchor.text for f in tpl.fields
                    if f.name in outcome.learned and f.anchor},
    }, operator)
    return outcome
