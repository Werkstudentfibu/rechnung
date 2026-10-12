"""Positionstabelle ohne KI aus dem Wort-Layout erkennen (SPEC Effizienz-
Baustein 2, Schwerpunkt Aaron 12.10.2026: "Die Felder hat das Programm gut
erkannt und eingefuegt, nur die Tabelle ist noch nicht gut").

Arbeitet wie eine Vorlage, aber ohne Anlernen: eine Kopfzeile wird ueber
Schluesselwoerter (Beschreibung/Menge/Einzelpreis/Gesamt/MwSt/...) gefunden,
die Zeilen darunter werden im Wort-Layout geclustert (wie
``template.apply_line_items``) und je Spalte aus der x-Position gelesen.

Kein Raten: eine gefundene Tabelle wird nur uebernommen, wenn sie die
ohnehin vorhandene Rechen-Pruefung besteht (``validate.line_item_row`` je
Zeile ODER ``validate.check_line_items_total`` gegen Netto/Brutto) --
sonst lieber ``None`` (dann liest die KI bzw. bleibt die Tabelle leer) als
eine falsche Tabelle."""

from __future__ import annotations

from .pdf import Document, Page, Word
from .template import FieldResult
from .textnorm import coerce, norm
from .validate import check_line_items_total, line_item_row

# Kopfzeilen-Schluesselwoerter je Spalte (de/en/fr/it), normalisiert ueber
# textnorm.norm() -- ein Tabellenkopf hat idR genau EIN Wort pro Spalte
# ("Menge"), anders als die Fliesstext-Schluesselwoerter in summen.py.
_HEADER_HINWEISE: dict[str, tuple[str, ...]] = {
    "pos": ("pos", "position", "nr", "posnr", "lfd", "lfdnr"),
    "beschreibung": ("beschreibung", "bezeichnung", "artikel", "leistung", "text",
                      "description", "item", "designation", "descrizione", "produkt"),
    "menge": ("menge", "anzahl", "anz", "stueck", "qty", "quantity", "quantite", "quantita"),
    "einzelpreis_netto": ("einzelpreis", "preis", "ep", "unitprice", "price",
                           "prixunitaire", "prezzounitario", "stueckpreis"),
    "gesamt_netto": ("gesamtpreis", "gesamtbetrag", "gesamt", "betrag", "summe",
                      "total", "amount", "montant", "importo", "nettobetrag",
                      "zeilensumme", "positionssumme"),
    "mwst_satz": ("mwst", "ust", "vat", "steuersatz", "tax", "tva", "iva", "mwstsatz"),
    "waehrung": ("waehrung", "currency", "devise", "valuta"),
}
# Eine Kopfzeile muss mindestens diese Spalte UND mindestens _MIN_SPALTEN
# verschiedene Spalten treffen -- sonst waere z.B. die Summenzeile
# "Gesamtbetrag 1.190,00 EUR" (zufaellig "gesamt_netto" + "waehrung")
# faelschlich eine Kopfzeile.
_PFLICHT_SPALTEN = ("beschreibung",)
_MIN_SPALTEN = 3

# Zeilen, die die Tabelle BEENDEN (Summenblock/Bank/Zahlungsbedingungen) --
# geprueft gegen das LINKE (erste) Wort der Zeile, nicht den ganzen
# Zeilentext (sonst wuerde z.B. eine Position "Versandkosten" faelschlich
# als Summenzeile gelten, weil "versand" zufaellig in der Naehe steht).
_STOP_WOERTER = (
    "nettobetrag", "nettosumme", "warenwert", "subtotal", "gesamtbetrag",
    "rechnungsbetrag", "endbetrag", "zahlbetrag", "bruttobetrag",
    "gesamtsumme", "umsatzsteuer", "mehrwertsteuer", "steuerbetrag",
    "iban", "bic", "bankverbindung", "zahlbar", "zahlungsbedingungen",
    "skonto", "seite", "total", "mwst", "ust", "vat", "tva",
    # englisch (Testsatz "englische Rechnung", Aaron 12.10.2026)
    "net", "amount", "due", "balance", "grandtotal", "page",
)
# Zwischensummen/Ueberträge VOR einem Seitenwechsel beenden die Tabelle
# NICHT -- die Zeile wird nur ignoriert, die Tabelle geht danach weiter
# (SPEC: "Zwischensummen/Ueberträge über Seitenwechsel ignorieren").
_SEITENWECHSEL_WOERTER = ("zwischensumme", "uebertrag", "ubertrag")

# Spalten, deren Zellinhalt Text bleibt (kein Betrag/Zahl-Parsing)
_TEXT_SPALTEN = ("beschreibung", "waehrung")


def _match_header_word(text: str) -> str | None:
    n = norm(text)
    if not n:
        return None
    for canon, keys in _HEADER_HINWEISE.items():
        if n in keys:
            return canon
    return None


def _line_h(words: list[Word]) -> float:
    if not words:
        return 10.0
    heights = sorted(w.bottom - w.top for w in words)
    return max(heights[len(heights) // 2], 6.0)


def _cluster_rows(words: list[Word], line_h: float, row_gap: float = 1.8) -> list[list[Word]]:
    """Wie ``template.apply_line_items``: Woerter nach (y, x) sortiert in
    y-Baender gruppieren -- eine Zeile im Layout, nicht im Fliesstext."""
    ordered = sorted(words, key=lambda w: (w.cy, w.x0))
    rows: list[list[Word]] = []
    cur: list[Word] = []
    anchor_y: float | None = None
    for w in ordered:
        if anchor_y is None or abs(w.cy - anchor_y) <= line_h * row_gap * 0.5:
            cur.append(w)
            anchor_y = w.cy if anchor_y is None else (anchor_y + w.cy) / 2
        else:
            rows.append(cur)
            cur = [w]
            anchor_y = w.cy
    if cur:
        rows.append(cur)
    return rows


def _header_candidates(page: Page) -> list[tuple[list[Word], dict[str, Word]]]:
    """Alle Zeilen der Seite, die wie eine Tabellen-Kopfzeile aussehen, oben
    zuerst."""
    if not page.words:
        return []
    line_h = _line_h(page.words)
    out: list[tuple[list[Word], dict[str, Word]]] = []
    for line in _cluster_rows(page.words, line_h):
        matches: dict[str, Word] = {}
        for w in line:
            canon = _match_header_word(w.text)
            if canon and canon not in matches:
                matches[canon] = w
        if len(matches) >= _MIN_SPALTEN and all(c in matches for c in _PFLICHT_SPALTEN):
            out.append((line, matches))
    out.sort(key=lambda t: min(w.top for w in t[0]))
    return out


def _spalten(matches: dict[str, Word], page_width: float) -> dict[str, tuple[float, float]]:
    """Kopfzeilen-Treffer -> Spaltenbereiche als Seitenanteile (0..1), ueber
    die gesamte Seitenbreite lueckenlos aneinandergereiht (Spaltengrenze =
    Mitte zwischen zwei benachbarten Kopfwoertern)."""
    items = sorted(matches.items(), key=lambda kv: kv[1].x0)
    out: dict[str, tuple[float, float]] = {}
    for i, (name, w) in enumerate(items):
        left = 0.0 if i == 0 else (items[i - 1][1].x1 + w.x0) / 2 / page_width
        right = 1.0 if i == len(items) - 1 else (w.x1 + items[i + 1][1].x0) / 2 / page_width
        out[name] = (left, right)
    return out


def _zeile_aus_woertern(line: list[Word], cols: dict[str, tuple[float, float]],
                        page_width: float) -> dict | None:
    rec: dict = {}
    for name, (x0f, x1f) in cols.items():
        x0, x1 = x0f * page_width, x1f * page_width
        cell = sorted((w for w in line if x0 <= w.cx < x1), key=lambda w: w.x0)
        if not cell:
            continue
        text = " ".join(w.text for w in cell).strip()
        if not text:
            continue
        if name in _TEXT_SPALTEN:
            rec[name] = text
            continue
        vtype = "number" if name == "pos" else "amount"
        val, ok = coerce(text, vtype)
        if ok:
            rec[name] = val
    return rec or None


def _ist_stop(line: list[Word]) -> bool:
    first = min(line, key=lambda w: w.x0)
    fw = norm(first.text)
    return any(fw == s or fw.startswith(s) for s in _STOP_WOERTER)


def _ist_seitenwechsel_zeile(line: list[Word]) -> bool:
    first = min(line, key=lambda w: w.x0)
    fw = norm(first.text)
    return any(fw == s or fw.startswith(s) for s in _SEITENWECHSEL_WOERTER)


def _merge_fortsetzungen(rows: list[dict]) -> list[dict]:
    """Mehrzeilige Beschreibungen zusammenfuehren: eine Zeile ohne jeden
    Betrag ist die Fortsetzung der Beschreibung der VORHERIGEN Position,
    keine eigene."""
    out: list[dict] = []
    for rec in rows:
        has_amount = any(k in rec for k in ("menge", "einzelpreis_netto", "gesamt_netto"))
        if not has_amount and out:
            zusatz = rec.get("beschreibung")
            if zusatz:
                bisher = out[-1].get("beschreibung", "")
                out[-1]["beschreibung"] = (bisher + " " + zusatz).strip()
            continue
        out.append(rec)
    return out


def _validieren(items: list[dict], netto: float | None, brutto: float | None,
                text: str) -> list[dict] | None:
    """Nur uebernehmen, wenn menge * einzelpreis ≈ gesamt (je pruefbarer
    Zeile) ODER die Summe der Gesamt-Spalte zum Netto (bzw. Brutto bei
    Bruttopreisen) passt -- dieselbe Rechen-Pruefung wie ``validate.py``,
    nicht neu erfunden."""
    pruefbar = [it for it in items if line_item_row(it)[0] is not None]
    zeilen_ok = bool(pruefbar) and all(line_item_row(it)[0] for it in pruefbar)
    summe_ok = False
    if not zeilen_ok:
        fields: dict = {}
        if netto is not None:
            fields["netto"] = FieldResult("netto", netto, str(netto), 1.0, "tabelle")
        if brutto is not None:
            fields["brutto"] = FieldResult("brutto", brutto, str(brutto), 1.0, "tabelle")
        summe_ok = check_line_items_total(fields, items, text).ok is True
    if not (zeilen_ok or summe_ok):
        return None
    for i, it in enumerate(items, 1):
        it.setdefault("pos", i)
        it["_quelle"] = "tabelle"
    return items


def _finde_kopf(doc: Document, page_idxs: list[int]
                ) -> tuple[int, list[Word], dict[str, Word]] | None:
    """Erste Kopfzeile ueber die gegebenen Seiten -> (Seitenindex, Kopfzeile,
    Spalten-Treffer), oder ``None``. Gemeinsam fuer ``extract()`` und
    ``tabelle_text()`` -- beide suchen dieselbe Kopfzeile."""
    for i in page_idxs:
        if i < 0 or i >= len(doc.pages):
            continue
        cands = _header_candidates(doc.pages[i])
        if cands:
            header_line, matches = cands[0]
            return i, header_line, matches
    return None


def _zeilen_unter_kopf(doc: Document, page_idxs: list[int], header_page: int,
                       header_line: list[Word]):
    """Generator ueber die Wort-Zeilen unterhalb der Kopfzeile, seitenweise,
    bis zum Stopp (Summenblock) -- stoppt den eigenen Lauf selbst, liefert
    aber dem Aufrufer jede Zeile einzeln (inkl. Stopp/Seitenwechsel-Status),
    damit ``extract()`` daraus Zellen liest und ``tabelle_text()`` nur den
    Text braucht."""
    line_h = _line_h(header_line)
    hdr_bottom = max(w.bottom for w in header_line)
    for i in page_idxs:
        if i < header_page or i >= len(doc.pages):
            continue
        page = doc.pages[i]
        if i == header_page:
            top = hdr_bottom + line_h * 0.3
        else:
            wiederholt = _header_candidates(page)
            top = max(w.bottom for w in wiederholt[0][0]) + line_h * 0.3 if wiederholt else 0.0
        band = [w for w in page.words if w.top >= top]
        for line in _cluster_rows(band, line_h):
            if _ist_stop(line):
                return
            yield page, line


def extract(doc: Document, netto: float | None = None, brutto: float | None = None,
            text: str = "", pages: list[int] | None = None) -> list[dict] | None:
    """Positionstabelle deterministisch aus dem Wort-Layout lesen, oder
    ``None`` (keine stimmige Tabelle gefunden -- dann greift KI bzw. bleibt
    leer, SPEC Baustein 2).

    ``netto``/``brutto``: bereits bekannte Kopf-Betraege (z.B. aus
    ``summen.py``, ohne KI) fuer die Summenpruefung, falls die Zeilen
    selbst nicht alle pruefbar sind (z.B. keine Mengen-Spalte). ``pages``:
    nur diese Seiten durchsuchen (Positionsmodus "zusammenfassung" --
    Uebersichtstabelle steht am Rechnungsende)."""
    page_idxs = pages if pages is not None else list(range(len(doc.pages)))
    kopf = _finde_kopf(doc, page_idxs)
    if kopf is None:
        return None
    header_page, header_line, matches = kopf
    cols = _spalten(matches, doc.pages[header_page].width)

    rows_raw: list[dict] = []
    for page, line in _zeilen_unter_kopf(doc, page_idxs, header_page, header_line):
        if _ist_seitenwechsel_zeile(line):
            continue
        rec = _zeile_aus_woertern(line, cols, page.width)
        if rec:
            rows_raw.append(rec)

    items = _merge_fortsetzungen(rows_raw)
    if not items:
        return None
    return _validieren(items, netto, brutto, text)


def tabelle_text(doc: Document, pages: list[int] | None = None) -> str:
    """Nur der Tabellenbereich als Text (Kopfzeile bis zum Stopp) -- fuer
    Variante B (``semantic.positions_block``, SPEC Baustein 3): die KI
    bekommt dort NICHT die ganze Rechnung, nur diesen Ausschnitt. Leer, wenn
    keine Kopfzeile gefunden wurde (auch wenn ``extract()`` selbst an der
    Summenpruefung scheitert, kann dieser Ausschnitt trotzdem etwas taugen
    -- er haengt nicht an der Validierung, nur am Kopfzeilen-Fund)."""
    page_idxs = pages if pages is not None else list(range(len(doc.pages)))
    kopf = _finde_kopf(doc, page_idxs)
    if kopf is None:
        return ""
    header_page, header_line, _matches = kopf
    zeilen = [" ".join(w.text for w in sorted(header_line, key=lambda w: w.x0))]
    for _page, line in _zeilen_unter_kopf(doc, page_idxs, header_page, header_line):
        zeilen.append(" ".join(w.text for w in sorted(line, key=lambda w: w.x0)))
    return "\n".join(zeilen)
