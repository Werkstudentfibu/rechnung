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

from . import rechnen
from .identify import POSITIONSMODUS_EINE_POSITION
from .pdf import Document, Word
from .template import (STANDARD_FIELDS, ausgeblendet, FieldResult, FieldSpec, LineItemSpec, Template,
                       apply_line_items, apply_template, build_field_spec,
                       build_line_item_spec)
from .textnorm import MAX_SPAN, ist_seitenwechsel_wort, norm, parse_amount, parse_date, parse_tax_summary, same_value

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
    "faelligkeit": (("faellig", "faelligkeit", "faelligkeitsdatum", "zahlbar", "zahlungsziel",
                     "due"), ("bis", "spaetestens")),
    "skonto_prozent": (("skonto", "skontosatz"), ("abzueglich", "abzgl")),
    "skonto_frist": (("skontofrist", "skonto"), ("bis",)),
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


# ---- Positionstabelle + USt-Block selbst ableiten (Postfach-Baustein 11a) --
# Heute lernt dieses Modul nur Kopffelder automatisch -- Positionstabelle und
# USt-Aufstellung mussten von Hand angeklickt werden. Ziel: aus bestaetigten
# Positionen/einer bestaetigten Aufstellung dieselbe Spec ableiten wie ein
# Mensch sie anklicken wuerde, und nur speichern, wenn ein Probelauf die
# bestaetigten Werte exakt reproduziert -- sonst weglassen statt raten.

def _value_types(value) -> list[str]:
    """Welche(r) Spaltentyp(en) zu einem bestaetigten Wert passen koennten,
    probierte Reihenfolge. ``same_value``/``coerce`` unterscheiden "amount"
    (Betrag, braucht Nachkommastellen -- sonst waere jede Hausnummer ein
    Betrag) und "number" (Rohtext, z.B. Menge/MwSt-Satz als blanke Ziffer
    "2"/"19") -- ein bestaetigter Wert allein (float 2.0 vs. String "2")
    sagt nicht sicher, welcher Spaltentyp im PDF tatsaechlich gedruckt
    steht, deshalb wird mit Fallback probiert."""
    if isinstance(value, bool):
        return ["text"]
    if isinstance(value, float):
        return ["amount", "number"] if value == int(value) else ["amount"]
    if isinstance(value, int):
        return ["number", "amount"]
    if isinstance(value, str):
        s = value.strip()
        # Beschreibung mit Ziffern ("Artikel 0001", "Schulung 2 Tage") ist Text,
        # kein Betrag -- sonst lernt die Beschreibungsspalte als "number"/
        # "amount", liest nur das erste Wort und der Probelauf scheitert
        # (Aaron 05.11.2026: gruendlich).
        if re.search(r"[^\W\d_]{2,}", re.sub(r"\b(?:EUR|CHF|USD|GBP)\b", "", s, flags=re.I)):
            return ["text"]
        if re.fullmatch(r"-?\d+", s):
            return ["number", "amount"]
        if parse_amount(s) is not None:
            return ["amount", "number"]
    return ["text"]


def _value_equiv(a, b) -> bool:
    """Zahlenwerte tolerant vergleichen (0,01), sonst als Text -- unabhaengig
    davon, ob ein Wert als float oder als Rohtext-String vorliegt (eine
    gelernte Spalte liest je nach Typ mal so, mal so)."""
    try:
        return abs(float(a) - float(b)) <= 0.01
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _items_equiv(got: list[dict], want: list[dict]) -> bool:
    if len(got) != len(want):
        return False
    return all(all(_value_equiv(g.get(k), w.get(k)) for k in w if not k.startswith("_"))
              for g, w in zip(got, want))


def _spans_in_line(line: list[Word], value, value_type: str) -> list[list[Word]]:
    """Wie ``find_value_spans``, aber nur innerhalb einer schon ermittelten
    Textzeile -- fuer die Positions-Zeilensuche (_find_row)."""
    hits: list[list[Word]] = []
    for i in range(len(line)):
        for n in range(1, MAX_SPAN + 1):
            if i + n > len(line):
                break
            span = line[i:i + n]
            if same_value(" ".join(w.text for w in span), value, value_type):
                hits.append(span)
                break
    return hits


def _find_row(doc: Document, item: dict, lines: list[list[Word]], skip: set[int]
             ) -> tuple[int, dict[str, tuple[list[Word], str]]] | None:
    """Textzeile, in der ALLE (nicht-leeren) Werte einer Position als eigene,
    nicht ueberlappende Wortspannen vorkommen -> (Zeilenindex, Spalte ->
    (Wortspanne, Spaltentyp))."""
    for li, line in enumerate(lines):
        if li in skip:
            continue
        found: dict[str, tuple[list[Word], str]] = {}
        used: set[int] = set()
        ok = True
        for key, value in item.items():
            if key.startswith("_") or value in (None, ""):
                continue
            hit = None
            for vtype in _value_types(value):
                cands = [s for s in _spans_in_line(line, value, vtype)
                        if not ({id(w) for w in s} & used)]
                if cands:
                    hit = (cands[0], vtype)
                    break
            if hit is None:
                ok = False
                break
            found[key] = hit
            used |= {id(w) for w in hit[0]}
        if ok and found:
            return li, found
    return None


def _nearest_line(lines: list[list[Word]], page: int, ref_top: float, ref_bottom: float,
                  h: float, above: bool, max_gap_lines: float = 4.0) -> list[Word] | None:
    """Naechste Textzeile oberhalb (``above``) bzw. unterhalb einer
    Referenzposition auf derselben Seite -- fuer Kopfzeile/Stoppwort."""
    best, best_gap = None, None
    for line in lines:
        if not line or line[0].page != page:
            continue
        if above:
            bottom = max(w.bottom for w in line)
            if bottom > ref_top - 0.5:
                continue
            gap = ref_top - bottom
        else:
            top = min(w.top for w in line)
            if top < ref_bottom - 0.5:
                continue
            gap = top - ref_bottom
        if gap > h * max_gap_lines:
            continue
        if best_gap is None or gap < best_gap:
            best_gap, best = gap, line
    return best


def _match_header_words(header_line: list[Word], col_spans: dict[str, list[Word]]
                        ) -> dict[str, Word] | None:
    """Je Spalte das am naechsten (x-Mitte) liegende Wort der Kopfzeile --
    die Kopfzeile selbst wird nicht inhaltlich gedeutet, nur positionell den
    schon bekannten Datenspalten zugeordnet."""
    used: set[int] = set()
    out: dict[str, Word] = {}
    for key, span in col_spans.items():
        cx = (span[0].x0 + span[-1].x1) / 2
        cands = [w for w in header_line if id(w) not in used]
        if not cands:
            return None
        w = min(cands, key=lambda w: abs(w.cx - cx))
        out[key] = w
        used.add(id(w))
    return out


def learn_line_items(doc: Document, items: list[dict], store=None) -> LineItemSpec | None:
    """``LineItemSpec`` aus bestaetigten Positionen ableiten: jede Position
    im PDF wiederfinden (Werte -> Zeile), daraus die Kopfzeile (Label-
    Woerter oberhalb der ersten Position), Spalten-x-Bereiche
    (``template.build_line_item_spec``, Mitte zum Nachbarn) und das
    Stoppwort (erste Zeile unterhalb der letzten Position). Nur wenn der
    Probelauf (``apply_line_items``) die bestaetigten Positionen exakt
    reproduziert -- sonst ``None`` statt geraten.

    ``store``: optional (SPEC Baustein 5, Aaron 12.10.2026) -- gelingt das
    Lernen, wandert jedes Kopfwort der erkannten Spalten automatisch ins
    globale Spaltenwoerterbuch (``store.spaltenwort_lernen``); gilt als
    "bestaetigt", weil der Probelauf unten die Positionen exakt reproduziert
    (SPEC: "Einmal korrigieren = gelernt (ohne Extraschritt)")."""
    if not items:
        return None
    lines = _lines(doc)
    skip: set[int] = set()
    rows: list[dict[str, tuple[list[Word], str]]] = []
    for item in items:
        hit = _find_row(doc, item, lines, skip)
        if hit is None:
            return None
        li, found = hit
        skip.add(li)
        rows.append(found)

    first_words = [w for span, _t in rows[0].values() for w in span]
    last_words = [w for span, _t in rows[-1].values() for w in span]
    page = first_words[0].page
    last_page = last_words[0].page
    h = max(w.bottom - w.top for w in first_words)
    header_line = _nearest_line(lines, page, min(w.top for w in first_words), 0, h, above=True)
    if not header_line:
        return None
    header_map = _match_header_words(header_line, {k: span for k, (span, _t) in rows[0].items()})
    if header_map is None:
        return None
    # Stoppwort: erste Zeile unterhalb der letzten Position (auf deren Seite)
    # -- auch wenn der Summenblock etwas abgesetzt steht (Trennlinie,
    # Leerraum); ob das passt, entscheidet ohnehin der Probelauf unten.
    stop_line = _nearest_line(lines, last_page, 0, max(w.bottom for w in last_words), h,
                              above=False, max_gap_lines=12.0)

    page_w = doc.pages[page].width
    header_cols = [(header_map[key], key, rows[0][key][1]) for key in rows[0]]
    # Nur Label-Woerter der Stoppzeile ("Nettobetrag"), keine Betraege/
    # Waehrung -- "EUR" steht womoeglich auch in jeder Positionszeile und
    # wuerde die Tabelle schon nach der ersten Zeile beenden.
    stop_words = [w for w in (stop_line or []) if _is_label(w)]
    mehrseitig = last_page != page
    if mehrseitig:
        # "Uebertrag"/"Zwischensumme" steht auf JEDER Seite -- als Stopp-Wort
        # wuerde es die Tabelle schon auf Seite 1 beenden.
        stop_words = [w for w in stop_words if not ist_seitenwechsel_wort(w.text)]
    spec = build_line_item_spec(header_cols, stop_words, page_w)
    if mehrseitig:
        spec.folgeseiten = True
        # Beginn der Tabelle auf der zweiten Seite: oberster Positionswert dort
        alle_woerter = [w for row in rows for span, _t in row.values() for w in span]
        zweite_seite = min(w.page for w in alle_woerter if w.page > page)
        zweite = [w for w in alle_woerter if w.page == zweite_seite]
        if zweite:
            ph = doc.pages[zweite[0].page].height
            spec.folge_top = round(max(0.0, (min(w.top for w in zweite) - h * 0.4) / ph), 4)
    if not _items_equiv(apply_line_items(doc, spec), items):
        return None
    if store is not None:
        for key, w in header_map.items():
            store.spaltenwort_lernen(w.text, key)
    return spec


def _amt_close(a, b, tol: float = 0.02) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return False


def _tax_row_matches(a: dict, b: dict) -> bool:
    return (_amt_close(a.get("satz"), b.get("satz"), 0.05) and _amt_close(a.get("netto"), b.get("netto"))
           and _amt_close(a.get("ust"), b.get("ust")))


def _taxrows_equiv(got, want: list[dict]) -> bool:
    if not isinstance(got, list) or len(got) != len(want):
        return False
    g = sorted(got, key=lambda r: r.get("satz") or 0)
    w = sorted(want, key=lambda r: r.get("satz") or 0)
    return all(_tax_row_matches(x, y) for x, y in zip(g, w))


def _label_in_line(line: list[Word], fname: str) -> Word | None:
    cands = [w for w in line if _is_label(w) and _hint_score(fname, w) > 0]
    return min(cands, key=lambda w: w.x0) if cands else None


def learn_taxblock(doc: Document, rows: list[dict]) -> FieldSpec | None:
    """``FieldSpec`` (``value_type="taxblock"``) aus einer bestaetigten
    USt-Aufstellung ableiten: jede Zeile {satz, netto, ust} im PDF
    wiederfinden (``textnorm.parse_tax_summary`` je Textzeile), Anker aus
    einem Stichwort-Label INNERHALB der ERSTEN gefundenen Zeile (eine
    Steuerzeile traegt ihr Label meist selbst, z.B. "Steuersatz 19 % ..."),
    Blockgrenzen aus allen gefundenen Zeilen. Nur wenn der Probelauf exakt
    reproduziert -- sonst ``None``."""
    if not rows:
        return None
    remaining = list(rows)
    matched: list[list[Word]] = []
    for line in _lines(doc):
        parsed = parse_tax_summary(" ".join(w.text for w in line))
        hit = next((p for r in remaining for p in parsed if _tax_row_matches(p, r)), None)
        if hit is None:
            continue
        matched.append(line)
        remaining = [r for r in remaining if not _tax_row_matches(hit, r)]
    if remaining or not matched:
        return None
    matched.sort(key=lambda line: min(w.top for w in line))
    first_line = matched[0]
    block_words = [w for line in matched for w in line]
    anchor = _label_in_line(first_line, "ust_satz")
    if anchor is None:
        return None
    pg = doc.pages[block_words[0].page]
    spec = build_field_spec("ust_aufstellung", "taxblock", False, anchor, block_words,
                            pg.width, pg.height)
    probe = Template(id="_probe", supplier_id="_probe", fields=[spec])
    result = apply_template(doc, probe).get("ust_aufstellung")
    if result is None or result.source == "missing" or not _taxrows_equiv(result.value, rows):
        return None
    return spec


# ---- Template bauen + pruefen -----------------------------------------
# ---- Vorschlaege im PDF markieren (Postfach-Baustein 11b) ---------------
# Das UI zeigt Vorschlaege (KI/Vorlage/E-Rechnung) vorausgefuellt und im PDF
# umrahmt, damit der Mensch nur noch korrigiert, was falsch ist. Dieselbe
# Suche wie beim Lernen -- und genauso: mehrdeutig -> KEIN Rahmen statt
# eines geratenen.

def _box(words: list[Word]) -> dict:
    return {"page": words[0].page, "x0": min(w.x0 for w in words),
            "top": min(w.top for w in words), "x1": max(w.x1 for w in words),
            "bottom": max(w.bottom for w in words)}


def markierungen(doc: Document, current: dict[str, dict], line_items: list[dict] | None,
                 tax_rows: list[dict] | None) -> dict:
    """-> ``{"felder": {feld: box}, "positionen": [box|None], "ust": [box|None]}``.
    ``current``: Feld -> ``{"value", "source"}`` wie in /api/invoice. Ein
    Wert, der mehrfach im PDF steht und dessen Label nicht entscheidet,
    bekommt keinen Rahmen."""
    felder: dict[str, dict] = {}
    for name, cur in current.items():
        value, src = cur.get("value"), cur.get("source")
        vtype = STANDARD_FIELDS.get(name, ("text",))[0]
        if (src in ("missing", "library", None) or value in (None, "")
                or vtype not in LEARNABLE_TYPES | {"iban", "ustid"}):
            continue
        spans = find_value_spans(doc, value, vtype)
        if len(spans) == 1:
            felder[name] = _box(spans[0])
        elif len(spans) > 1:
            span, _anchor, _why = locate(doc, name, value, vtype)
            if span:
                felder[name] = _box(span)

    lines = _lines(doc)
    positionen: list[dict | None] = []
    skip: set[int] = set()
    for item in line_items or []:
        hit = _find_row(doc, item, lines, skip)
        if hit is None:
            positionen.append(None)
            continue
        skip.add(hit[0])
        positionen.append(_box(lines[hit[0]]))

    ust: list[dict | None] = []
    used: set[int] = set()
    for row in tax_rows or []:
        box = None
        for li, line in enumerate(lines):
            if li in used:
                continue
            parsed = parse_tax_summary(" ".join(w.text for w in line))
            if any(_tax_row_matches(p, row) for p in parsed):
                used.add(li)
                box = _box(line)
                break
        ust.append(box)
    return {"felder": felder, "positionen": positionen, "ust": ust}


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
        # "text": per Regel aus dem Text gelesen (zahlung.py) -- die Regel
        # findet den Wert auch kuenftig selbst; gelernt wird, was der Mensch
        # korrigiert hat bzw. was Vorlage/KI geliefert haben. "errechnet"
        # (rechnen.py, Teil A/B Selbstlernen 2 inkl. summen.py) steht an
        # KEINER festen Stelle im PDF -- ein zufaelliger Textfund waere kein
        # verlaesslicher Anker, nur ein Nebenprodukt der Formel/des
        # Kontexts; die Rechenstufe ergaenzt den Wert ohnehin bei jeder
        # kuenftigen Rechnung von selbst (Befund 10.10.2026).
        if vtype not in LEARNABLE_TYPES or fr.source in ("missing", "library", "text", rechnen.ERRECHNET):
            continue
        if fr.value is None or fr.value == "":
            continue
        out[name] = fr.value
    return out


def learn_template(doc: Document, supplier_id: str, values: dict[str, object],
                   existing: Template | None = None, variant: str = "standard",
                   template_id: str | None = None,
                   line_items: list[dict] | None = None,
                   taxblock: list[dict] | None = None,
                   store=None,
                   ) -> tuple[Template | None, LearnOutcome]:
    """Aus bestaetigten Werten ein Template ableiten bzw. ``existing`` ergaenzen.
    Speichert nichts -- liefert das Template (oder None, wenn sich nichts
    Verwertbares ergibt) plus Protokoll. ``template_id`` erzwingt eine Id fuer
    ein neues Template (ohne ``existing``) -- genutzt, wenn eine als veraltet
    markierte Vorlage fuer dieselbe Variante schon die Standard-Id belegt
    (SPEC §7p: Layoutwechsel erkennen). ``line_items``/``taxblock``:
    bestaetigte Positionstabelle/USt-Aufstellung -- Postfach-Baustein 11a,
    siehe ``learn_line_items``/``learn_taxblock``. ``store``: optional,
    durchgereicht an ``learn_line_items`` fuers globale Spaltenwoerterbuch
    (SPEC Baustein 5)."""
    tid = existing.id if existing else (template_id or f"{supplier_id}__{variant}")
    outcome = LearnOutcome(template_id=tid, created=existing is None)
    tpl = (Template.from_dict(existing.to_dict()) if existing
           else Template(id=tid, supplier_id=supplier_id, variant=variant))
    current = apply_template(doc, tpl) if existing else {}
    by_name = {f.name: f for f in tpl.fields}

    aus = ausgeblendet()
    for fname, want in values.items():
        if fname in aus or fname not in STANDARD_FIELDS:
            outcome.skipped[fname] = "Feld ausgeblendet/unbekannt"
            continue
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

    if taxblock:
        current_tb = by_name.get("ust_aufstellung") and current.get("ust_aufstellung")
        if current_tb is not None and current_tb.source != "missing" \
                and _taxrows_equiv(current_tb.value, taxblock):
            outcome.kept.append("ust_aufstellung")
        elif "ust_aufstellung" in by_name and tpl.secured:
            outcome.skipped["ust_aufstellung"] = "gesichertes Template liefert anderen Wert"
        else:
            spec_tb = learn_taxblock(doc, taxblock)
            if spec_tb is None:
                outcome.skipped["ust_aufstellung"] = "nicht reproduzierbar/nicht gefunden"
            else:
                by_name["ust_aufstellung"] = spec_tb
                outcome.learned.append("ust_aufstellung")

    if line_items:
        current_items = apply_line_items(doc, tpl.line_items) if tpl.line_items else []
        if tpl.line_items is not None and _items_equiv(current_items, line_items):
            outcome.kept.append("_positionen")
        elif tpl.line_items is not None and tpl.secured:
            outcome.skipped["_positionen"] = "gesichertes Template liefert andere Positionen"
        else:
            spec_li = learn_line_items(doc, line_items, store=store)
            if spec_li is None:
                outcome.skipped["_positionen"] = "nicht reproduzierbar/nicht gefunden"
            else:
                tpl.line_items = spec_li
                outcome.learned.append("_positionen")

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


_SKIP_REASON_FIELD = "gesichertes Template liefert anderen Wert"
_SKIP_REASON_POSITIONEN = "gesichertes Template liefert andere Positionen"


def _track_field_corrections(store, tpl: Template, outcome: LearnOutcome, res,
                             operator: str = "") -> None:
    """Postfach-Baustein 11c: wird dasselbe Feld einer GESICHERTEN Vorlage
    wiederholt in Folge korrigiert (``learn_template`` markiert das als
    ``outcome.skipped[feld] == "gesichertes Template liefert anderen
    Wert"``), zaehlt das -- ab ``config.stale_feld_schwelle()`` Treffern in
    Folge wird die Vorlage ueber denselben Stale-/Versions-Mechanismus wie
    bei ``track_template_health`` neu gelernt (gesicherte Vorlagen werden
    dabei nie still umgeschrieben, nur eine neue Version entsteht)."""
    from .config import stale_feld_schwelle

    if tpl.stale:
        return
    tracked_names = [f.name for f in tpl.fields if f.name != "ust_aufstellung"]
    if any(f.name == "ust_aufstellung" for f in tpl.fields):
        tracked_names.append("ust_aufstellung")
    if tpl.line_items is not None:
        tracked_names.append("_positionen")

    changed = False
    for name in tracked_names:
        reason = _SKIP_REASON_POSITIONEN if name == "_positionen" else _SKIP_REASON_FIELD
        streak = tpl.field_correction_streak
        if outcome.skipped.get(name) == reason:
            streak[name] = streak.get(name, 0) + 1
            changed = True
        elif streak.get(name):
            streak[name] = 0
            changed = True

    schwelle = stale_feld_schwelle()
    ausgeloest = sorted(n for n, c in tpl.field_correction_streak.items() if c >= schwelle)
    if ausgeloest:
        tpl.stale = True
        store.save_template(tpl)
        store.audit("template_stale_feld", {
            "template_id": tpl.id, "supplier_id": tpl.supplier_id, "sha256": res.sha256,
            "felder": ausgeloest, "schwelle": schwelle,
        }, operator)
    elif changed:
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
    # Bestaetigt = Klick im Training-UI ODER im autonomen Modus ohne Klick
    # uebernommen (processed-Eintrag, SPEC §7v) -- eine spaetere JobRouter-
    # Korrektur einer autonom durchgelaufenen Rechnung soll neu lernen.
    if not res.sha256 or not (store.is_confirmed(res.sha256) or store.seen_sha(res.sha256)):
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

    # Nur GELESENE Werte werden als Anker gelernt -- ``errechnet``/``kopf``
    # (Rechenstufe, rechnung/rechnen.py) sind nicht im PDF belegt und duerfen
    # nie eine Tabellen-Spec/einen Taxblock-Anker begruenden (SPEC Baustein 2).
    tb_fr = res.fields.get("ust_aufstellung")
    taxblock = (rechnen.nur_gelesene_werte(tb_fr.value)
               if tb_fr is not None and tb_fr.source != "missing"
               and isinstance(tb_fr.value, list) and tb_fr.value else None)
    sup = store.supplier(res.supplier_id)
    # Positionsmodus 'eine_position' (SPEC Baustein 1, Aaron 12.10.2026):
    # die "Positionen" sind aus der USt-Aufstellung gebildet
    # (positionen.aus_summen), stehen nicht als eigene Tabellenzeile im PDF
    # -- eine Tabellen-Spec daraus zu lernen waere Zufallstreffer. Explizit
    # ausgelassen statt sich auf die Herkunfts-Filterung unten zu verlassen.
    gelesene_items = (rechnen.nur_gelesene_werte(res.line_items) or None
                      if (sup is None or sup.positionsmodus != POSITIONSMODUS_EINE_POSITION)
                      else None)
    tpl, outcome = learn_template(doc, res.supplier_id, confirmed_values(res.fields),
                                  existing, variant, template_id=tid_override,
                                  line_items=gelesene_items, taxblock=taxblock, store=store)

    if used_tpl is not None and used_tpl.secured:
        _track_field_corrections(store, used_tpl, outcome, res, operator)

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
