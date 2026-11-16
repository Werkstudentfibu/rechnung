"""Positionstabelle ohne KI aus dem Wort-Layout erkennen (SPEC Effizienz-
Baustein 2, Schwerpunkt Aaron 12.10.2026: "Die Felder hat das Programm gut
erkannt und eingefuegt, nur die Tabelle ist noch nicht gut").

Arbeitet wie eine Vorlage, aber ohne Anlernen: eine Kopfzeile wird ueber
Schluesselwoerter (Beschreibung/Menge/Einzelpreis/Gesamt/MwSt/...) gefunden,
die Zeilen darunter werden im Wort-Layout geclustert (wie
``template.apply_line_items``) und je Spalte aus der x-Position gelesen.
Ohne erkennbare Kopfzeile (fehlt, Bild, verlesen) bleibt ein Block aus
Betragszeilen, dessen Spalten sich nur ueber die Rechnung selbst deuten
lassen (Summe = Netto, Menge x Preis = Betrag) -- siehe ``_ohne_kopf``.

Kein Raten: eine gefundene Tabelle wird nur uebernommen, wenn sie die
ohnehin vorhandene Rechen-Pruefung besteht (``validate.line_item_row`` je
Zeile ODER ``validate.check_line_items_total`` gegen Netto/Brutto) --
sonst lieber ``None`` (dann liest die KI bzw. bleibt die Tabelle leer) als
eine falsche Tabelle."""

from __future__ import annotations

from .pdf import Document, Page, Word
from .spaltenwoerter import hinweis as _woerterbuch_hinweis
from .template import FieldResult
from .textnorm import coerce, ist_seitenwechsel_wort, norm
from .validate import check_line_items_total, line_item_row

# Kopfzeilen-Schluesselwoerter je Spalte (de/en/fr/it), normalisiert ueber
# textnorm.norm() -- ein Tabellenkopf hat idR genau EIN Wort pro Spalte
# ("Menge"), anders als die Fliesstext-Schluesselwoerter in summen.py.
_HEADER_HINWEISE: dict[str, tuple[str, ...]] = {
    "pos": ("pos", "position", "nr", "posnr", "lfd", "lfdnr"),
    "beschreibung": ("beschreibung", "bezeichnung", "artikel", "leistung", "text",
                      "description", "item", "designation", "descrizione", "produkt",
                      "leistungen", "posten", "taetigkeit", "artikeltext"),
    "menge": ("menge", "anzahl", "anz", "stueck", "qty", "quantity", "quantite", "quantita",
              "stk", "stck", "std", "stunden", "mng", "pcs"),
    "einzelpreis_netto": ("einzelpreis", "preis", "ep", "unitprice", "price",
                           "prixunitaire", "prezzounitario", "stueckpreis",
                           "epreis", "einzel", "nettopreis", "vkpreis", "rate"),
    "gesamt_netto": ("gesamtpreis", "gesamtbetrag", "gesamt", "betrag", "summe",
                      "total", "amount", "montant", "importo", "nettobetrag",
                      "zeilensumme", "positionssumme",
                      "gpreis", "gespreis", "endpreis", "wert", "gesamtwert", "nettowert",
                      "linetotal", "netto", "brutto"),
    "mwst_satz": ("mwst", "ust", "vat", "steuersatz", "tax", "tva", "iva", "mwstsatz"),
    "waehrung": ("waehrung", "currency", "devise", "valuta"),
}
# Kopfwoerter einer Spalte, die NICHT uebernommen wird (Artikelnummer,
# Datum, Rabatt): sie bildet trotzdem eine eigene Spalte, damit ihr Inhalt
# nicht in die Nachbarspalte rutscht ("10234 Tomaten passiert" als
# Beschreibung, eine Artikelnummer als Positionsnummer). Zaehlt nicht als
# Tabellenspalte fuer die Mindestanzahl unten.
_KENNUNG = "_kennung"
_KENNUNG_WOERTER = (
    "artnr", "artikelnr", "artikelnummer", "bestellnr", "bestellnummer", "materialnr",
    "materialnummer", "ean", "sku", "itemno", "partno", "datum", "date", "lieferdatum",
    "leistungsdatum", "rabatt", "nachlass", "discount",
)
# Zusammengesetzte Kopfwoerter (Aaron 16.11.2026: "Tabellen werden oft gar
# nicht angelegt"): "Artikelbezeichnung", "Leistungsbeschreibung",
# "Liefermenge", "Verkaufspreis", "Nettowert" -- das Wortende traegt die
# Bedeutung. Laengere Endungen zuerst ("gesamtpreis" vor "preis").
_WORTENDEN: tuple[tuple[str, str], ...] = (
    ("beschreibung", "beschreibung"), ("bezeichnung", "beschreibung"),
    ("leistung", "beschreibung"), ("text", "beschreibung"),
    ("nummer", _KENNUNG), ("datum", _KENNUNG), ("nr", _KENNUNG),
    ("menge", "menge"), ("anzahl", "menge"),
    ("gesamtpreis", "gesamt_netto"), ("betrag", "gesamt_netto"), ("summe", "gesamt_netto"),
    ("wert", "gesamt_netto"), ("preis", "einzelpreis_netto"),
)
# Rang eines Kopfwort-Treffers -- trifft mehr als ein Wort derselben Zeile
# dieselbe Spalte, gewinnt der bessere Rang (dann das linke Wort).
_RANG_EXAKT, _RANG_SCHWACH, _RANG_WORTTEIL, _RANG_WORTENDE, _RANG_BRUTTO = 0, 1, 2, 3, 4
# Exakte, aber unspezifische Treffer: "Artikel" steht oft ueber der
# Artikelnummer, die echte Beschreibung heisst daneben "Bezeichnung".
_SCHWACHE_WOERTER = ("artikel", "item", "nr", "text", "netto")
# Eine Kopfzeile muss die Beschreibung UND mindestens _MIN_SPALTEN
# verschiedene Spalten treffen -- sonst waere z.B. die Summenzeile
# "Gesamtbetrag 1.190,00 EUR" (zufaellig "gesamt_netto" + "waehrung")
# faelschlich eine Kopfzeile. Ausnahme: Beschreibung + Betrag (+ hoechstens
# Kennung/Datum) -- "Bezeichnung | Betrag", "Datum | Text | Betrag"
# (Hotel-Folio). Solch eine Tabelle hat keine Zeilen-Mathematik und gilt
# deshalb NUR, wenn ihre Summe zum Netto/Brutto passt (``_validieren``).
_PFLICHT_SPALTEN = ("beschreibung",)
_MIN_SPALTEN = 3
_BETRAGS_SPALTEN = ("gesamt_netto", "einzelpreis_netto")

# Zeilen, die die Tabelle BEENDEN (Summenblock/Bank/Zahlungsbedingungen) --
# geprueft gegen das LINKE (erste) Wort der Zeile, nicht den ganzen
# Zeilentext (sonst wuerde z.B. eine Position "Versandkosten" faelschlich
# als Summenzeile gelten, weil "versand" zufaellig in der Naehe steht).
_STOP_WOERTER = (
    "nettobetrag", "nettosumme", "warenwert", "subtotal", "gesamtbetrag",
    "rechnungsbetrag", "endbetrag", "zahlbetrag", "bruttobetrag",
    "gesamtsumme", "umsatzsteuer", "mehrwertsteuer", "steuerbetrag",
    "iban", "bic", "bankverbindung", "zahlbar", "zahlungsbedingungen",
    "skonto", "total", "mwst", "ust", "vat", "tva",
    # englisch (Testsatz "englische Rechnung", Aaron 12.10.2026)
    "net", "amount", "due", "balance", "grandtotal",
    # Summenblock (Aaron 16.11.2026): "Endsumme" wurde sonst als weitere
    # Position mitgelesen.
    "endsumme", "rechnungssumme", "nettowarenwert",
)
# Stoppen nur als GANZES erstes Wort UND wenn in der Zeile ein Betrag steht:
# "Summe 98,30", "Netto 98,30", "Gesamt netto 98,30", "zzgl. 7 % MwSt 6,88"
# beenden die Tabelle -- eine mehrzeilige Kopfzeile ("Betrag / Netto /
# EUR"), eine Fortsetzung "zzgl. Pfand" ohne Betrag oder eine Position
# "Gesamtpaket Wartung" nicht.
_STOP_MIT_BETRAG = ("summe", "summen", "netto", "brutto", "gesamt",
                    "zzgl", "zuzgl", "zuzueglich", "abzgl", "abzueglich")
# Kurze Stoppwoerter ("net", "due", "ust", "bic" ...) gelten nur als GANZES
# erstes Wort -- sonst beendet eine Position "Netzwerkkabel" oder "Duesenset"
# die Tabelle (Aaron 05.11.2026: gruendlich statt schnell). Erst ab dieser
# Laenge darf das erste Wort nur mit dem Stoppwort BEGINNEN ("Nettobetrag:",
# "Skontobetrag").
_STOP_PRAEFIX_MIN = 6
# Zwischensummen/Ueberträge VOR einem Seitenwechsel beenden die Tabelle
# NICHT -- die Zeile wird nur ignoriert, die Tabelle geht danach weiter
# (SPEC: "Zwischensummen/Ueberträge über Seitenwechsel ignorieren").

# Spalten, deren Zellinhalt Text bleibt (kein Betrag/Zahl-Parsing)
_TEXT_SPALTEN = ("beschreibung", "waehrung")


def _match_header_word(text: str, woerterbuch: dict | None = None) -> str | None:
    """Kopfwort -> kanonische Spalte. Rangfolge (SPEC Baustein 5): das
    globale, lieferantenuebergreifende Spaltenwoerterbuch (nur bestaetigte,
    nicht widersprochene Zuordnungen, siehe ``spaltenwoerter.hinweis``) VOR
    den fest eingebauten Schluesselwoertern -- wird bei einem unbekannten
    Lieferanten mitgegeben (``woerterbuch``), bei einem bereits gelernten
    Lieferanten bleibt der Aufruf ohne Woerterbuch (dessen eigene Spec
    entscheidet, siehe ``pipeline._tabelle_rangfolge``)."""
    treffer = _kopfwort(text, woerterbuch)
    # Kennung ist keine waehlbare Spalte, eine Brutto-Spalte nie ein
    # Nettopreis-Vorschlag (Raster im UI)
    if not treffer or treffer[0] == _KENNUNG or treffer[1] == _RANG_BRUTTO:
        return None
    return treffer[0]


def _kopfwort(text: str, woerterbuch: dict | None = None) -> tuple[str, int] | None:
    """Kopfwort -> (Spalte, Rang) oder ``None``. Spalte ist eine kanonische
    Spalte oder ``_KENNUNG`` (eigene Spalte, Inhalt wird verworfen). Reihen-
    folge: Spaltenwoerterbuch, exaktes Schluesselwort (auch ohne Bindestrich:
    "E-Preis", "Art.-Nr."), Wortteil ("Preis/Stk." -> Preis), Wortende
    ("Artikelbezeichnung" -> Beschreibung). Brutto-Spalten nur als letzte
    Wahl -- "Betrag netto | Betrag brutto" nimmt die Netto-Spalte."""
    n = norm(text)
    if not n:
        return None
    gelernt = _woerterbuch_hinweis(woerterbuch, text)
    if gelernt in _HEADER_HINWEISE:
        return gelernt, _RANG_EXAKT
    brutto = "brutto" in n or "gross" in n

    def _exakt(w: str) -> tuple[str, int] | None:
        if w in _KENNUNG_WOERTER:
            return _KENNUNG, _RANG_EXAKT
        for canon, keys in _HEADER_HINWEISE.items():
            if w in keys:
                return canon, (_RANG_SCHWACH if w in _SCHWACHE_WOERTER else _RANG_EXAKT)
        return None

    def _rang(canon: str, rang: int) -> tuple[str, int]:
        if brutto and canon in _BETRAGS_SPALTEN:
            return canon, _RANG_BRUTTO
        return canon, rang

    ganz = n.replace("-", "")
    t = _exakt(n) or _exakt(ganz)
    if t:
        return _rang(*t)
    teile = [p for p in n.split("-") if p]
    if len(teile) > 1:
        # "Preis/Stk." ist ein Preis, "Menge/ME" eine Menge: der erste
        # Wortteil mit Bedeutung zaehlt. Ein Nummern-Teil ("Kunden-Nr.")
        # macht daraus eine Kennung, ausser es ist die Positionsnummer.
        if (any(p in ("nr", "no", "nummer") for p in teile)
                and (_exakt(teile[0]) or ("", 0))[0] != "pos"):
            return _KENNUNG, _RANG_WORTTEIL
        for p in teile:
            t = _exakt(p)
            if t:
                return _rang(t[0], _RANG_WORTTEIL)
    canon = next((c for endung, c in _WORTENDEN
                  if len(ganz) > len(endung) and ganz.endswith(endung)), None)
    # "Gesamtnettopreis"/"Totalpreis" ist die Zeilensumme, kein Einzelpreis
    if ganz.startswith(("gesamt", "total")) and canon in (None, "einzelpreis_netto"):
        canon = "gesamt_netto"
    return _rang(canon, _RANG_WORTENDE) if canon else None


def _kopf_labels(line: list[Word]) -> list[list[Word]]:
    """Woerter einer Kopfzeile -> Spaltenbeschriftungen: Woerter mit
    normalem Wortabstand gehoeren zu EINER Beschriftung ("Unit price",
    "Einzelpreis netto", "Betrag EUR"), eine groessere Luecke trennt zwei
    Spalten."""
    ordered = sorted(line, key=lambda w: w.x0)
    luecke = _line_h(line) * 0.6
    labels: list[list[Word]] = []
    for w in ordered:
        if labels and w.x0 - labels[-1][-1].x1 <= luecke:
            labels[-1].append(w)
        else:
            labels.append([w])
    return labels


def _kopf_treffer(line: list[Word], woerterbuch: dict | None = None) -> dict[str, Word] | None:
    """Sieht die Zeile wie eine Tabellen-Kopfzeile aus? -> Spalte ->
    Beschriftung (ein Wort ueber die ganze Beschriftung), sonst ``None``.
    Je Beschriftung zaehlt das Wort mit dem besten Rang (bei Gleichstand das
    erste: "Einzelpreis netto" ist ein Einzelpreis). Treffen zwei
    Beschriftungen dieselbe Spalte ("Artikel | Bezeichnung"), gewinnt der
    bessere Rang; die andere bleibt als Kennungs-Spalte stehen, damit ihr
    Inhalt nicht in die Nachbarspalte rutscht."""
    if _hat_betrag(line):
        return None
    kandidaten: list[tuple[str, int, Word]] = []
    for lab in _kopf_labels(line):
        def _guete(t: tuple[str, int]) -> tuple[bool, int]:
            return t[0] == _KENNUNG, t[1]
        best = _kopfwort(" ".join(w.text for w in lab), woerterbuch) if len(lab) > 1 else None
        for w in lab:
            t = _kopfwort(w.text, woerterbuch)
            if t and (best is None or _guete(t) < _guete(best)):
                best = t
        if best and best[0] in _BETRAGS_SPALTEN and any(
                b in norm(w.text) for w in lab for b in ("brutto", "gross", "inkl")):
            best = best[0], _RANG_BRUTTO
        if best:
            span = Word(" ".join(w.text for w in lab), min(w.x0 for w in lab),
                        max(w.x1 for w in lab), min(w.top for w in lab),
                        max(w.bottom for w in lab), lab[0].page)
            kandidaten.append((best[0], best[1], span))
    matches: dict[str, Word] = {}
    rang: dict[str, int] = {}
    extra = 0
    for canon, r, span in sorted(kandidaten, key=lambda k: (k[1], k[2].x0)):
        if canon == _KENNUNG or canon in matches:
            extra += 1
            matches[f"{_KENNUNG}{extra}"] = span
            continue
        matches[canon], rang[canon] = span, r
    werte = {c for c in matches if not c.startswith("_")}
    if not all(c in werte for c in _PFLICHT_SPALTEN):
        return None
    if len(werte) >= _MIN_SPALTEN or (len(werte) >= 2 and werte & set(_BETRAGS_SPALTEN)):
        return matches
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


def _header_candidates(page: Page, woerterbuch: dict | None = None
                       ) -> list[tuple[list[Word], dict[str, Word]]]:
    """Alle Zeilen der Seite, die wie eine Tabellen-Kopfzeile aussehen, oben
    zuerst."""
    if not page.words:
        return []
    line_h = _line_h(page.words)
    out: list[tuple[list[Word], dict[str, Word]]] = []
    for line in _cluster_rows(page.words, line_h):
        matches = _kopf_treffer(line, woerterbuch)
        if matches:
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
        if name.startswith("_"):  # Kennung/Datum/Rabatt: eigene Spalte, nicht uebernommen
            continue
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
    ganz = fw.replace("-", "")  # "Netto-Betrag" wie "Nettobetrag"
    if any(f == s or (len(s) >= _STOP_PRAEFIX_MIN and f.startswith(s))
           for s in _STOP_WOERTER for f in {fw, ganz}):
        return True
    return ganz in _STOP_MIT_BETRAG and _hat_betrag(line)


def _ist_seitenwechsel_zeile(line: list[Word]) -> bool:
    return ist_seitenwechsel_wort(min(line, key=lambda w: w.x0).text)


def _ist_header_zeile(line: list[Word]) -> bool:
    """Wie ``_header_candidates``, aber fuer eine einzelne (schon bekannte)
    Zeile: erkennt die auf Folgeseiten wiederholte Kopfzeile innerhalb eines
    per Rahmen markierten Bereichs (Baustein 2)."""
    return _kopf_treffer(line) is not None


def _ist_positionszeile(rec: dict | None, typisch: set[str]) -> bool:
    """Gleicht eine Zeile der Folgeseite mit dem Muster der Kopfseite ab: sie
    muss die Hauptspalte (Gesamt, sonst Einzelpreis) UND mindestens drei
    Viertel der dort ueblichen Zahlenspalten tragen. "Muster GmbH Seite 2
    von 3" liefert zufaellig zwei Zahlen, aber keinen Gesamtbetrag."""
    if not rec:
        return False
    zahlen = {k for k in rec if k not in _TEXT_SPALTEN}
    if not typisch:
        return bool(zahlen)
    haupt = "gesamt_netto" if "gesamt_netto" in typisch else (
        "einzelpreis_netto" if "einzelpreis_netto" in typisch else None)
    if haupt and haupt not in zahlen:
        return False
    return len(zahlen & typisch) >= max(1, -(-3 * len(typisch) // 4))


def _ohne_briefkopf(gelesen: list[tuple[Page, dict | None]], kopfseite: Page) -> list[dict]:
    """Zeilen je Seite -> Positionszeilen. Auf Folgeseiten ohne wiederholte
    Kopfzeile stehen oberhalb der Positionen Briefkopf/"Seite 2 von 3" --
    erst ab der ersten zum Muster der Kopfseite passenden Zeile zaehlt die
    Seite (SPEC: Kopf nur auf Seite 1)."""
    typisch: set[str] = set()
    kopf_recs = [r for p, r in gelesen if p is kopfseite and r]
    if kopf_recs:
        zaehler: dict[str, int] = {}
        for r in kopf_recs:
            for k in r:
                if k not in _TEXT_SPALTEN:
                    zaehler[k] = zaehler.get(k, 0) + 1
        typisch = {k for k, n in zaehler.items() if n >= 0.6 * len(kopf_recs)}
    out: list[dict] = []
    seite: Page | None = None
    vorspann = False
    for page, rec in gelesen:
        if page is not seite:
            seite, vorspann = page, page is not kopfseite
        if vorspann:
            if not _ist_positionszeile(rec, typisch):
                continue
            vorspann = False
        if rec:
            out.append(rec)
    return out


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
                text: str, quelle: str = "tabelle") -> list[dict] | None:
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
        it["_quelle"] = quelle
    return items


def pruefe_kandidat(items: list[dict] | None, netto: float | None, brutto: float | None,
                    text: str = "", quelle: str = "tabelle_vorlage") -> list[dict] | None:
    """Oeffentliche Form von ``_validieren`` fuer Tabellen-Kandidaten aus
    ANDEREN Quellen als der eigenen Kopfzeilen-Erkennung dieses Moduls --
    die gelernte Spec desselben Lieferanten (noch nicht aktiv, z.B. als
    veraltet markiert) oder eines aehnlichen Lieferanten der gleichen
    Layout-Familie (SPEC Baustein 5 "Rangfolge", siehe
    ``pipeline._tabelle_rangfolge``). Dieselbe Rechen-Pruefung wie
    ``extract()`` -- kein Sonderfall, nur ``None`` statt geraten."""
    if not items:
        return None
    return _validieren([dict(it) for it in items], netto, brutto, text, quelle)


def _finde_kopf(doc: Document, page_idxs: list[int], woerterbuch: dict | None = None
                ) -> tuple[int, list[Word], dict[str, Word]] | None:
    """Erste Kopfzeile ueber die gegebenen Seiten -> (Seitenindex, Kopfzeile,
    Spalten-Treffer), oder ``None``. Gemeinsam fuer ``extract()`` und
    ``tabelle_text()`` -- beide suchen dieselbe Kopfzeile."""
    for i in page_idxs:
        if i < 0 or i >= len(doc.pages):
            continue
        cands = _header_candidates(doc.pages[i], woerterbuch)
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
            text: str = "", pages: list[int] | None = None,
            woerterbuch: dict | None = None) -> list[dict] | None:
    """Positionstabelle deterministisch aus dem Wort-Layout lesen, oder
    ``None`` (keine stimmige Tabelle gefunden -- dann greift KI bzw. bleibt
    leer, SPEC Baustein 2).

    ``netto``/``brutto``: bereits bekannte Kopf-Betraege (z.B. aus
    ``summen.py``, ohne KI) fuer die Summenpruefung, falls die Zeilen
    selbst nicht alle pruefbar sind (z.B. keine Mengen-Spalte). ``pages``:
    nur diese Seiten durchsuchen (Positionsmodus "zusammenfassung" --
    Uebersichtstabelle steht am Rechnungsende). ``woerterbuch``: globales
    Spaltenwoerterbuch (SPEC Baustein 5, ``store.spaltenwoerter()``) als
    zusaetzlicher Hinweis bei einem Lieferanten ohne eigene Spec."""
    page_idxs = pages if pages is not None else list(range(len(doc.pages)))
    # Jede Kopfzeile der Reihe nach probieren, die erste stimmige gilt: eine
    # Zeile im Anschreiben, die zufaellig wie ein Kopf aussieht ("Leistung
    # ... Betrag"), darf die echte Tabelle darunter nicht verdecken.
    for header_page, header_line, matches in _alle_koepfe(doc, page_idxs, woerterbuch):
        cols = _spalten(matches, doc.pages[header_page].width)
        gelesen: list[tuple[Page, dict | None]] = []
        for page, line in _zeilen_unter_kopf(doc, page_idxs, header_page, header_line):
            if _ist_seitenwechsel_zeile(line):
                continue
            gelesen.append((page, _zeile_aus_woertern(line, cols, page.width)))
        rows_raw = _ohne_briefkopf(gelesen, doc.pages[header_page])
        items = _ohne_summenzeile(_merge_fortsetzungen(rows_raw))
        if items:
            gueltig = _validieren(items, netto, brutto, text)
            if gueltig:
                return gueltig
    return _ohne_kopf(doc, page_idxs, netto, brutto, text)


# ---- Tabelle ohne erkannte Kopfzeile (Aaron 16.11.2026) ------------------
# Kopf fehlt, ist ein Bild oder hat die Texterkennung verlesen
# ("Bezeichnurg"): dann der Block aus Betragszeilen ueber dem Summenblock,
# Spalten aus den Wortluecken, Bedeutung der Spalten NUR aus der Rechnung
# selbst -- die Zeilensumme muss zum Netto passen (bei Bruttopreisen zum
# Brutto), Menge x Einzelpreis zum Zeilenbetrag. Ohne bekanntes Netto/Brutto
# gibt es hier nie eine Tabelle.
_BETRAG_VOLL = __import__("re").compile(r"^[-+]?\d[\d.,'’]*[.,]\d{2}-?$")
_STEUER_WOERTER = ("mwst", "ust", "umsatzsteuer", "mehrwertsteuer", "vat", "steuer",
                   "tax", "tva", "iva")


def _ist_betragszeile(line: list[Word]) -> bool:
    return any(_BETRAG_VOLL.match(w.text.strip()) for w in line)


def _betragsbloecke(zeilen: list[list[Word]]):
    """Laeufe aufeinanderfolgender Betragszeilen (hoechstens zwei Zeilen ohne
    Betrag dazwischen: mehrzeilige Beschreibung), beendet von Summenblock,
    Seitenwechsel oder Kopfzeile."""
    block: list[list[Word]] = []
    luecke = 0
    for z in zeilen:
        trenner = _ist_stop(z) or _ist_seitenwechsel_zeile(z) or _kopf_treffer(z) is not None
        if not trenner and _ist_betragszeile(z):
            block.append(z)
            luecke = 0
            continue
        if not trenner and block and luecke < 2:
            block.append(z)
            luecke += 1
            continue
        while block and not _ist_betragszeile(block[-1]):
            block.pop()
        if block:
            yield block
        block, luecke = [], 0
    while block and not _ist_betragszeile(block[-1]):
        block.pop()
    if block:
        yield block


def _zahl(text: str) -> float | None:
    if not text or any(ch.isalpha() for ch in text.replace("%", "")):
        return None
    v, ok = coerce(text, "amount")
    return float(v) if ok else None


def _ohne_kopf(doc: Document, page_idxs: list[int], netto: float | None,
               brutto: float | None, text: str) -> list[dict] | None:
    if netto is None and brutto is None:
        return None
    for i in page_idxs:
        if not (0 <= i < len(doc.pages)) or not doc.pages[i].words:
            continue
        page = doc.pages[i]
        for block in _betragsbloecke(_cluster_rows(page.words, _line_h(page.words))):
            items = _block_lesen(block, netto, brutto, text)
            if items:
                return items
    return None


def _block_lesen(block: list[list[Word]], netto: float | None, brutto: float | None,
                 text: str) -> list[dict] | None:
    betragszeilen = [z for z in block if _ist_betragszeile(z)]
    alle = [w for z in betragszeilen for w in z]
    x0, x1 = min(w.x0 for w in alle) - 1, max(w.x1 for w in alle) + 1
    spalten = _spaltenluecken(alle, x0, x1)
    if len(spalten) < 2:
        return None
    # Positionen: jede Betragszeile beginnt eine, Zeilen ohne Betrag danach
    # sind ihre Beschreibungs-Fortsetzung.
    positionen: list[tuple[list[str], list[str]]] = []
    for z in block:
        zellen = [_zelltext([z], a, b) for a, b in spalten]
        if _ist_betragszeile(z):
            positionen.append((zellen, []))
        elif positionen:
            positionen[-1][1].append(" ".join(c for c in zellen if c))
    n = len(positionen)
    werte = [[_zahl(p[0][j]) for p in positionen] for j in range(len(spalten))]
    # Beschreibung: die Spalte mit den meisten Buchstaben -- jede Position
    # braucht dort Text, sonst ist das keine Positionstabelle.
    buchstaben = [sum(sum(ch.isalpha() for ch in p[0][j]) for p in positionen)
                  for j in range(len(spalten))]
    bj = max(range(len(spalten)), key=lambda j: buchstaben[j])
    if not buchstaben[bj] or not all(any(ch.isalpha() for ch in p[0][bj]) for p in positionen):
        return None
    beschreibungen = [" ".join([p[0][bj]] + p[1]).strip() for p in positionen]
    if all(any(s in norm(b).split("-") for s in _STEUER_WOERTER) or "%" in b
           for b in beschreibungen):
        return None  # USt-Aufstellung, keine Positionen
    fields: dict = {}
    if netto is not None:
        fields["netto"] = FieldResult("netto", netto, str(netto), 1.0, "tabelle")
    if brutto is not None:
        fields["brutto"] = FieldResult("brutto", brutto, str(brutto), 1.0, "tabelle")
    # Zeilenbetrag: von rechts die erste Spalte mit einem Betrag in JEDER
    # Position, deren Summe zum Netto (bzw. Brutto) passt.
    for gj in reversed(range(len(spalten))):
        if gj == bj or not all(_BETRAG_VOLL.match(p[0][gj].strip()) for p in positionen):
            continue
        items = [{"beschreibung": b, "gesamt_netto": werte[gj][k]}
                 for k, b in enumerate(beschreibungen)]
        if check_line_items_total(fields, items, text).ok is not True:
            continue
        # Menge x Einzelpreis: zwei Zahlenspalten, deren Produkt in JEDER
        # Position den Zeilenbetrag ergibt.
        paar = next(((a, b) for a in range(len(spalten)) for b in range(a + 1, len(spalten))
                     if bj not in (a, b) and gj not in (a, b)
                     and all(werte[a][k] is not None and werte[b][k] is not None
                             and abs(round(werte[a][k] * werte[b][k], 2) - werte[gj][k]) <= 0.01
                             for k in range(n))), None)
        if paar:
            a, b = paar
            # Menge ist die Spalte ohne Nachkommastellen, sonst die linke
            if (all(float(v).is_integer() for v in werte[b])
                    and not all(float(v).is_integer() for v in werte[a])):
                a, b = b, a
            for k, it in enumerate(items):
                it["menge"], it["einzelpreis_netto"] = werte[a][k], werte[b][k]
        if n < 2 and not paar:
            return None  # eine einzelne Zeile nur mit eigener Rechenprobe
        for k, it in enumerate(items, 1):
            it["pos"] = k
            it["_quelle"] = "tabelle_ohne_kopf"
        return items
    return None


def kopf_spalten(doc: Document, woerterbuch: dict | None = None) -> list[str]:
    """Spalten der ersten erkannten Kopfzeile (fuer die Diagnose "warum keine
    Positionen?" in der Bearbeitungsansicht), leer ohne Kopfzeile."""
    for _i, _line, matches in _alle_koepfe(doc, list(range(len(doc.pages))), woerterbuch):
        return [c for c in sorted(matches, key=lambda c: matches[c].x0) if not c.startswith("_")]
    return []


def _alle_koepfe(doc: Document, page_idxs: list[int], woerterbuch: dict | None = None):
    """Alle Kopfzeilen ueber die gegebenen Seiten, seitenweise von oben."""
    for i in page_idxs:
        if 0 <= i < len(doc.pages):
            for header_line, matches in _header_candidates(doc.pages[i], woerterbuch):
                yield i, header_line, matches


def _ohne_summenzeile(items: list[dict]) -> list[dict]:
    """Letzte "Position" ohne Menge/Einzelpreis, deren Betrag genau die Summe
    aller vorherigen ist, ist die Summenzeile mit unbekannter Beschriftung
    ("Positionen gesamt 98,30") -- keine eigene Position."""
    if len(items) < 3:
        return items
    letzte = items[-1]
    if any(k in letzte for k in ("menge", "einzelpreis_netto")):
        return items
    try:
        betrag = float(letzte.get("gesamt_netto"))
        vorher = sum(float(it["gesamt_netto"]) for it in items[:-1])
    except (TypeError, ValueError, KeyError):
        return items
    return items[:-1] if abs(betrag - vorher) <= 0.01 * len(items) else items


# ---- Tabelle per Rahmen markieren (Aaron 12.10.2026, Baustein 2) ---------
# Wo die automatische Kopfzeilen-Erkennung oben (``extract``) nicht
# anschlaegt (ungewohnte Spaltenwoerter, Scan ohne klares Layout, Tabelle
# zu ungewoehnlich), zieht der Mensch im UI einen Rahmen um die Tabelle
# (Kopfzeile bis letzte Position; bei mehrseitigen Tabellen je Seite ein
# Rahmen). Die Spalten werden hier NICHT ueber Schluesselwoerter in der
# Kopfzeile gefunden (die kann voellig frei formuliert sein), sondern ueber
# die Wortluecken quer durch den ganzen Rahmen (Projektionsprofil): eine
# Luecke, die auf JEDER Zeile frei bleibt, trennt zwei Spalten -- robuster
# als nur die Kopfzeile allein, weil ein kurzes Kopfwort ("Menge") naeher an
# seinem Nachbarn stehen kann als die Datenspalte darunter breit ist.
_SPALTENLUECKE_MIN = 8.0


def _spaltenluecken(words: list[Word], x0: float, x1: float,
                    min_gap: float = _SPALTENLUECKE_MIN) -> list[tuple[float, float]]:
    """Spaltenbereiche [x0,x1) im Rahmen aus den Woertern ALLER Zeilen:
    Wort-Intervalle zusammenfuehren, jede verbleibende Luecke >= ``min_gap``
    wird (an ihrer Mitte) zur Spaltengrenze."""
    ivs = sorted((max(w.x0, x0), min(w.x1, x1)) for w in words if w.x1 > x0 and w.x0 < x1)
    merged: list[list[float]] = []
    for a, b in ivs:
        if merged and a <= merged[-1][1] + 0.5:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    bounds = [x0]
    for i in range(len(merged) - 1):
        if merged[i + 1][0] - merged[i][1] >= min_gap:
            bounds.append((merged[i][1] + merged[i + 1][0]) / 2)
    bounds.append(x1)
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def _words_in_box(doc: Document, box: dict) -> list[Word]:
    page = doc.pages[box["page"]]
    return [w for w in page.words
            if box["top"] <= w.cy <= box["bottom"] and box["x0"] <= w.cx <= box["x1"]]


def markiere_tabelle(doc: Document, boxes: list[dict], woerterbuch: dict | None = None) -> dict:
    """Rahmen (je ``{"page","x0","top","x1","bottom"}`` in PDF-Punkten) ->
    ``{"columns": [{"index","x0","x1","header_text","guess"}], "rows": [[zelltext,...]]}``.
    Spaltenbereiche (``x0``/``x1`` als Seitenanteile der ERSTEN Seite) aus
    ``_spaltenluecken`` ueber den ersten Rahmen; die oberste Zeile darin
    gilt als Kopfzeile. Weitere Rahmen (naechste Seite derselben Tabelle)
    liefern nur weitere Datenzeilen, keine neuen Spalten -- dieselben
    Spaltenanteile werden auf ihre jeweilige Seitenbreite angewendet.
    ``guess``: Kopfwort-Schluesselwort-Treffer (``_match_header_word``) als
    Vorschlag fuer die Spalten-Zuordnung im UI, oder ``None`` (Mensch
    waehlt per Dropdown)."""
    if not boxes:
        return {"columns": [], "rows": []}
    # Seitenreihenfolge, nicht Zieh-Reihenfolge: Spalten aus dem Rahmen der
    # ERSTEN Seite, Positionen in Seitenreihenfolge (Aaron 05.11.2026).
    boxes = sorted(boxes, key=lambda b: (b["page"], b["top"]))
    b0 = boxes[0]
    page0 = doc.pages[b0["page"]]
    words0 = sorted(_words_in_box(doc, b0), key=lambda w: (w.cy, w.x0))
    if not words0:
        return {"columns": [], "rows": []}
    line_h = _line_h(words0)
    zeilen0 = _cluster_rows(words0, line_h)
    header_line = zeilen0[0]
    daten0: list[list[Word]] = []
    for line in zeilen0[1:]:
        if _ist_stop(line):
            break
        if _ist_seitenwechsel_zeile(line):
            continue
        daten0.append(line)
    # Spaltenlagen nur aus Kopfzeile + den echten Datenzeilen (ohne Summen-
    # block): eine Betragszeile wie "Nettobetrag 1.000,00 EUR" liegt oft
    # quer ueber mehreren Datenspalten und wuerde sonst faelschlich zwei
    # Spalten zu einer verschmelzen (Aaron 12.10.2026).
    spalten_woerter = header_line + [w for line in daten0 for w in line]
    luecken = _spaltenluecken(spalten_woerter, b0["x0"], b0["x1"])
    cols = []
    for i, (cx0, cx1) in enumerate(luecken):
        hwords = sorted((w for w in header_line if cx0 <= w.cx < cx1), key=lambda w: w.x0)
        guess = next((g for w in hwords if (g := _match_header_word(w.text, woerterbuch))), None)
        cols.append({"index": i, "x0": round(cx0 / page0.width, 4), "x1": round(cx1 / page0.width, 4),
                    "header_text": " ".join(w.text for w in hwords), "guess": guess})
    # Inhaltsmitte je Spalte (nur Datenzeilen) -- Anker, um Folgeseiten mit
    # leicht verschobenem Raster derselben Spalte zuzuordnen.
    mitten: list[float | None] = []
    for c in cols:
        xs = [w.cx for line in daten0 for w in line if c["x0"] * page0.width <= w.cx < c["x1"] * page0.width]
        mitten.append(sum(xs) / len(xs) / page0.width if xs else None)

    def _zeilen_lesen(zeilen: list[list[Word]], bereiche: list[list[tuple[float, float]]],
                     skip_header: bool = False) -> list[list[str]]:
        out = []
        for line in zeilen:
            if _ist_stop(line):
                break
            if _ist_seitenwechsel_zeile(line):
                continue
            if skip_header and _ist_header_zeile(line):
                continue
            cells = []
            for rs in bereiche:
                cell = sorted((w for w in line if any(x0 <= w.cx < x1 for x0, x1 in rs)),
                              key=lambda w: w.x0)
                cells.append(" ".join(w.text for w in cell).strip())
            if any(cells):
                out.append(cells)
        return out

    def _bereiche_seite1(page_width: float) -> list[list[tuple[float, float]]]:
        return [[(c["x0"] * page_width, c["x1"] * page_width)] for c in cols]

    def _bereiche_folgeseite(zeilen: list[list[Word]], box: dict, page_width: float
                             ) -> list[list[tuple[float, float]]]:
        """Spaltenraster der Folgeseite aus DEREN Wortluecken, jedes Segment
        der Spalte von Seite 1 mit gleicher Reihenfolge (gleiche Segmentzahl)
        bzw. naechster Inhaltsmitte zugeordnet -- ein um ein paar Millimeter
        verschobenes Raster trifft sonst die falsche Spalte."""
        woerter = [w for line in zeilen if not _ist_stop(line)
                   and not _ist_seitenwechsel_zeile(line) for w in line]
        if not woerter or any(m is None for m in mitten):
            return _bereiche_seite1(page_width)
        segs = _spaltenluecken(woerter, box["x0"], box["x1"])
        if len(segs) == len(cols):
            return [[s] for s in segs]
        bereiche: list[list[tuple[float, float]]] = [[] for _ in cols]
        for sx0, sx1 in segs:
            mitte = (sx0 + sx1) / 2 / page_width
            ziel = min(range(len(cols)), key=lambda i: abs(mitten[i] - mitte))
            bereiche[ziel].append((sx0, sx1))
        return [b or [(c["x0"] * page_width, c["x1"] * page_width)]
                for b, c in zip(bereiche, cols)]

    rows: list[list[str]] = _zeilen_lesen(daten0, _bereiche_seite1(page0.width))
    for box in boxes[1:]:
        words = sorted(_words_in_box(doc, box), key=lambda w: (w.cy, w.x0))
        if not words:
            continue
        page = doc.pages[box["page"]]
        zeilen = _cluster_rows(words, _line_h(words))
        zeilen_ohne_kopf = [ln for ln in zeilen if not _ist_header_zeile(ln)]
        rows.extend(_zeilen_lesen(zeilen, _bereiche_folgeseite(zeilen_ohne_kopf, box, page.width),
                                  skip_header=True))
    return {"columns": cols, "rows": rows}


_BETRAG_RE = __import__("re").compile(r"\d[\d.,']*[.,]\d{2}(?!\d)")


def _hat_betrag(line: list[Word]) -> bool:
    return any(_BETRAG_RE.search(w.text) for w in line)


def _box_bandgrenzen(doc: Document, box: dict, ist_erste: bool) -> tuple[list[float], bool]:
    """Zeilengrenzen (y in PDF-Punkten, nur die inneren) einer per Rahmen
    markierten Tabelle -> (Grenzen, erste Zeile ist Kopfzeile). Eine neue
    Position beginnt mit einer Zeile, in der ein Betrag steht; Zeilen ohne
    Betrag (z.B. zweite Beschreibungszeile) gehoeren zur vorigen Position --
    dieselbe Annahme wie ``_merge_fortsetzungen``. Kopf-, Summen- und
    Seitenwechselzeilen sind eigene Baender."""
    words = sorted(_words_in_box(doc, box), key=lambda w: (w.cy, w.x0))
    if not words:
        return [], ist_erste
    zeilen = _cluster_rows(words, _line_h(words))
    hat = [_hat_betrag(z) for z in zeilen]
    # Kopfzeile: als solche erkannt, oder erster Rahmen und oben steht kein
    # Betrag (Rahmen ohne Kopf -> erste Zeile ist schon eine Position).
    kopf = _ist_header_zeile(zeilen[0]) or (ist_erste and not hat[0])
    # Mehrzeiliger Kopf (z.B. "Betrag / Netto / EUR" untereinander): alles bis
    # zur ersten Zeile mit Betrag gehoert zum Kopf -- ohne Betraege nur Zeile 1.
    erste_pos = next((i for i, z in enumerate(zeilen) if hat[i] or _ist_stop(z)), len(zeilen))
    kopf_ende = (max(1, erste_pos) if any(hat) else 1) if kopf else 0
    baender: list[list[list[Word]]] = []
    for i, line in enumerate(zeilen):
        if i < kopf_ende:
            if baender:
                baender[0].append(line)
            else:
                baender.append([line])
            continue
        eigenes_band = (
            not baender
            or i == kopf_ende
            or not any(hat)
            or _ist_stop(line) or _ist_seitenwechsel_zeile(line)
            or _ist_stop(baender[-1][0]) or _ist_seitenwechsel_zeile(baender[-1][0])
            or _hat_betrag(line)
        )
        if eigenes_band:
            baender.append([line])
        else:
            baender[-1].append(line)
    grenzen = []
    for a, b in zip(baender, baender[1:]):
        unten = max(w.bottom for line in a for w in line)
        oben = min(w.top for line in b for w in line)
        grenzen.append(round((unten + oben) / 2, 2))
    return grenzen, kopf


def _zelltext(zeilen: list[list[Word]], x0: float, x1: float) -> str:
    """Text einer Zelle [x0, x1) -- Zeile fuer Zeile, je Zeile links nach rechts
    (Woerter einer Zeile haben oft leicht verschiedene Hoehen)."""
    teile = []
    for z in zeilen:
        cw = sorted((w for w in z if x0 <= w.cx < x1), key=lambda w: w.x0)
        if cw:
            teile.append(" ".join(w.text for w in cw))
    return " ".join(teile).strip()


def raster_vorschlag(doc: Document, boxes: list[dict], woerterbuch: dict | None = None,
                     grid: dict | None = None) -> dict:
    """Raster zum Anzeigen und Verschieben (Wunsch Aaron 12.11.2026):
    ``{"spalten": [innere Spaltengrenzen als Anteil der Seitenbreite],
    "zeilen": {"<seite>": [innere Zeilengrenzen in PDF-Punkten]},
    "kopf": {"<seite>": erste Zeile ist Kopfzeile}}``. Spalten wie
    ``markiere_tabelle`` (Wortluecken), Zeilen je Position."""
    if not boxes:
        return {"spalten": [], "zeilen": {}, "kopf": {}}
    grid = grid or markiere_tabelle(doc, boxes, woerterbuch)
    cols = grid["columns"]
    raster = {"spalten": [c["x1"] for c in cols[:-1]], "zeilen": {}, "kopf": {}}
    for i, box in enumerate(sorted(boxes, key=lambda b: (b["page"], b["top"]))):
        g, k = _box_bandgrenzen(doc, box, i == 0)
        raster["zeilen"][str(box["page"])] = g
        raster["kopf"][str(box["page"])] = k
    return raster


def raster_lesen(doc: Document, boxes: list[dict], raster: dict,
                 woerterbuch: dict | None = None) -> dict:
    """Tabelle nach einem (vom Menschen angepassten) Raster lesen -> wie
    ``markiere_tabelle`` ``{"columns", "rows"}`` plus ``"raster"`` (das
    tatsaechlich benutzte, ergaenzt um Seiten ohne eigene Zeilengrenzen).
    Spaltengrenzen gelten fuer alle Seiten; je Seite eigene Zeilengrenzen.
    Kopfband der ersten Seite liefert die Spaltenbeschriftung; Summen-
    zeilen beenden die Tabelle der Seite, Seitenwechselzeilen zaehlen nicht."""
    if not boxes:
        return {"columns": [], "rows": [], "raster": {"spalten": [], "zeilen": {}, "kopf": {}}}
    boxes = sorted(boxes, key=lambda b: (b["page"], b["top"]))
    spalten = sorted(float(s) for s in (raster.get("spalten") or []))
    zeilen = {str(k): [float(y) for y in v] for k, v in (raster.get("zeilen") or {}).items()}
    kopf = {str(k): bool(v) for k, v in (raster.get("kopf") or {}).items()}
    n = len(spalten) + 1
    kopf_woerter: list[list[Word]] = [[] for _ in range(n)]
    rows: list[list[str]] = []
    for bi, box in enumerate(boxes):
        seite = str(box["page"])
        if seite not in zeilen:
            g, k = _box_bandgrenzen(doc, box, bi == 0)
            zeilen[seite] = g
            kopf.setdefault(seite, k)
        ist_kopf = kopf.setdefault(seite, bi == 0)
        breite = doc.pages[box["page"]].width
        xs = ([box["x0"]] + [min(max(s * breite, box["x0"]), box["x1"]) for s in spalten]
              + [box["x1"]])
        ys = ([box["top"]] + sorted(y for y in zeilen[seite] if box["top"] < y < box["bottom"])
              + [box["bottom"]])
        woerter = _words_in_box(doc, box)
        for ri in range(len(ys) - 1):
            band = sorted((w for w in woerter if ys[ri] <= w.cy < ys[ri + 1]),
                          key=lambda w: (w.cy, w.x0))
            if not band:
                continue
            if ri == 0 and ist_kopf:
                if bi == 0:
                    for w in band:
                        j = next((j for j in range(n) if xs[j] <= w.cx < xs[j + 1]), n - 1)
                        kopf_woerter[j].append(w)
                continue
            # Summenzeile beendet die Tabelle -- auch wenn sie durch eine
            # entfernte Zeilengrenze im Band der letzten Position steckt;
            # Seitenwechselzeilen (Uebertrag) zaehlen nicht mit.
            band_zeilen = _cluster_rows(band, _line_h(band))
            stop = next((k for k, z in enumerate(band_zeilen) if _ist_stop(z)), None)
            if stop is not None:
                band_zeilen = band_zeilen[:stop]
            band_zeilen = [z for z in band_zeilen if not _ist_seitenwechsel_zeile(z)]
            cells = [_zelltext(band_zeilen, xs[j], xs[j + 1]) for j in range(n)]
            if any(cells):
                rows.append(cells)
            if stop is not None:
                break
    b0 = boxes[0]
    breite0 = doc.pages[b0["page"]].width
    xs0 = [b0["x0"] / breite0] + spalten + [b0["x1"] / breite0]
    columns = []
    benutzt: set[str] = set()
    for j in range(n):
        hw = kopf_woerter[j]
        hz = _cluster_rows(hw, _line_h(hw)) if hw else []
        header_text = " ".join(" ".join(w.text for w in sorted(z, key=lambda w: w.x0)) for z in hz)
        # Erster Treffer gewinnt: zwei Spalten mit demselben Vorschlag
        # ("Betrag Netto" / "Betrag Brutto", "Pos. Nr." / "Beleg Nr.")
        # wuerden sich sonst ueberschreiben; Brutto ist nie ein Nettopreis.
        guess = None
        for w in (w for z in hz for w in sorted(z, key=lambda w: w.x0)):
            g = _match_header_word(w.text, woerterbuch)
            if g and g not in benutzt:
                guess = g
                break
        if guess in ("gesamt_netto", "einzelpreis_netto") and "brutto" in norm(header_text):
            guess = None
        if guess:
            benutzt.add(guess)
        columns.append({"index": j, "x0": round(xs0[j], 4), "x1": round(xs0[j + 1], 4),
                        "header_text": header_text, "guess": guess})
    return {"columns": columns, "rows": rows,
            "raster": {"spalten": spalten, "zeilen": zeilen, "kopf": kopf}}


def anwenden_markierung(grid: dict, mapping: list[str | None] | None = None) -> list[dict]:
    """Rohraster aus ``markiere_tabelle`` + Spalten-Zuordnung (Liste, Index
    wie ``grid["columns"]``; ``None``/fehlend = Vorschlag ``guess``
    uebernehmen, ``"ignorieren"`` = Spalte verwerfen) -> Positionen in
    LI_STD-Spaltennamen. Mehrzeilige Beschreibungen werden wie bei der
    automatischen Erkennung zusammengefuehrt (Zeile ohne jeden Betrag =
    Fortsetzung der vorigen Position)."""
    cols = grid["columns"]
    # None/fehlend = Vorschlag (guess) der Spalte uebernehmen, wie oben beschrieben
    targets = [(m if m else c["guess"]) for m, c in zip(list(mapping or []) + [None] * len(cols), cols)]
    while len(targets) < len(cols):
        targets.append(None)
    rows_raw: list[dict] = []
    for raw_row in grid["rows"]:
        rec: dict = {}
        for cell, target in zip(raw_row, targets):
            if not target or target == "ignorieren" or not cell:
                continue
            if target in _TEXT_SPALTEN:
                rec[target] = (rec[target] + " " + cell).strip() if target in rec else cell
                continue
            vtype = "number" if target == "pos" else "amount"
            val, ok = coerce(cell, vtype)
            if ok:
                rec[target] = val
        if rec:
            rows_raw.append(rec)
    items = _merge_fortsetzungen(rows_raw)
    for i, it in enumerate(items, 1):
        it.setdefault("pos", i)
        it["_quelle"] = "tabelle_rahmen"
    return items


def tabelle_text(doc: Document, pages: list[int] | None = None,
                 woerterbuch: dict | None = None) -> str:
    """Nur der Tabellenbereich als Text (Kopfzeile bis zum Stopp) -- fuer
    Variante B (``semantic.positions_block``, SPEC Baustein 3): die KI
    bekommt dort NICHT die ganze Rechnung, nur diesen Ausschnitt. Leer, wenn
    keine Kopfzeile gefunden wurde (auch wenn ``extract()`` selbst an der
    Summenpruefung scheitert, kann dieser Ausschnitt trotzdem etwas taugen
    -- er haengt nicht an der Validierung, nur am Kopfzeilen-Fund)."""
    page_idxs = pages if pages is not None else list(range(len(doc.pages)))
    kopf = _finde_kopf(doc, page_idxs, woerterbuch)
    if kopf is None:
        return ""
    header_page, header_line, _matches = kopf
    zeilen = [" ".join(w.text for w in sorted(header_line, key=lambda w: w.x0))]
    for _page, line in _zeilen_unter_kopf(doc, page_idxs, header_page, header_line):
        zeilen.append(" ".join(w.text for w in sorted(line, key=lambda w: w.x0)))
    return "\n".join(zeilen)


# ---- Zeilen-/Spaltenzusammenhang nutzen (Aaron 12.10.2026, Baustein 3) ----
# "Wenn das Programm einen Wert als Teil eines Artikels erkennt, stehen sehr
# wahrscheinlich links und rechts davon in der gleichen Zeile die restlichen
# Informationen zu diesem Artikel." Markiert der Mensch per Rahmen EINE
# Zelle der Positionstabelle (Baustein 2, ``liZelleRahmen`` im UI), schlaegt
# ``zeilen_vorschlag`` die uebrigen LI_STD-Spalten DERSELBEN Zeile vor:
# Woerter auf derselben Grundlinie (+/- Toleranz) werden -- in der festen
# kanonischen Reihenfolge Beschreibung < Menge < Einzelpreis < Gesamt <
# MwSt-Satz < Waehrung -- den noch leeren Spalten links/rechts der
# bestaetigten Zelle zugeordnet, ueber dieselbe Wortluecken-Segmentierung
# wie beim Rahmen-Ziehen der ganzen Tabelle (``_spaltenluecken``). Ein
# Vorschlag ist nie sicher -- siehe ``zeilen_vorschlag_zulaessig`` (Baustein
# 4 muss ihn freigeben, bevor er im UI uebernehmbar angezeigt wird).
_REIHENFOLGE = ("beschreibung", "menge", "einzelpreis_netto", "gesamt_netto",
               "mwst_satz", "waehrung")


def _segmenttext(woerter: list[Word], x0: float, x1: float) -> str:
    return " ".join(w.text for w in sorted((w for w in woerter if x0 <= w.cx < x1),
                                           key=lambda w: w.x0)).strip()


def zeilen_vorschlag(doc: Document, page: int, indices: list[int], ziel_spalte: str,
                     bekannt: dict | None = None) -> dict[str, str]:
    """Ausgehend von den Woertern EINER per Rahmen bestaetigten Zelle
    (``indices`` in ``doc.pages[page].words``, Zielspalte ``ziel_spalte``)
    die uebrigen, noch leeren LI_STD-Spalten DERSELBEN Zeile vorschlagen (->
    Spaltenname -> Rohtext). Rein deterministisch aus dem Wort-Layout, keine
    Validierung hier (siehe ``zeilen_vorschlag_zulaessig``)."""
    if ziel_spalte not in _REIHENFOLGE or not (0 <= page < len(doc.pages)):
        return {}
    pg = doc.pages[page]
    zelle = [pg.words[i] for i in indices if 0 <= i < len(pg.words)]
    if not zelle:
        return {}
    bekannt_ok = {k for k, v in (bekannt or {}).items() if v not in (None, "")}
    cy = sum(w.cy for w in zelle) / len(zelle)
    h = _line_h(zelle)
    zx0, zx1 = min(w.x0 for w in zelle), max(w.x1 for w in zelle)
    zelle_ids = {id(w) for w in zelle}
    zeile = [w for w in pg.words if id(w) not in zelle_ids and abs(w.cy - cy) <= h * 0.9]
    idx = _REIHENFOLGE.index(ziel_spalte)
    out: dict[str, str] = {}

    links = sorted((w for w in zeile if w.x1 <= zx0 + 1), key=lambda w: w.x0)
    if links:
        zahl_cols = list(_REIHENFOLGE[1:idx])  # Spalten zwischen Beschreibung und Zelle
        segs = _spaltenluecken(links, links[0].x0, zx0) if zahl_cols else []
        k = min(len(zahl_cols), len(segs))
        zahl_segs = segs[len(segs) - k:] if k else []
        zahl_ziele = zahl_cols[len(zahl_cols) - k:] if k else []
        for (sx0, sx1), col in zip(zahl_segs, zahl_ziele):
            text = _segmenttext(links, sx0, sx1)
            if text:
                out[col] = text
        if idx > 0:  # es gibt ueberhaupt eine Beschreibungs-Spalte links der Zelle
            besch_grenze = zahl_segs[0][0] if zahl_segs else zx0
            besch = _segmenttext(links, links[0].x0 - 1, besch_grenze)
            if besch:
                out["beschreibung"] = besch

    rechts = sorted((w for w in zeile if w.x0 >= zx1 - 1), key=lambda w: w.x0)
    rechts_cols = list(_REIHENFOLGE[idx + 1:])
    if rechts and rechts_cols:
        segs = _spaltenluecken(rechts, zx1, rechts[-1].x1 + 1)
        for (sx0, sx1), col in zip(segs, rechts_cols):
            text = _segmenttext(rechts, sx0, sx1)
            if text:
                out[col] = text

    return {k: v for k, v in out.items() if k not in bekannt_ok}


def zeilen_vorschlag_zulaessig(vorschlag: dict, bekannt: dict, ziel_spalte: str, wert) -> bool:
    """Baustein-4-Gate (SPEC Baustein 3: "Vorschlaege nur uebernehmen, wenn
    die Zeilenpruefung aufgeht"): die bestaetigte Zelle + der Vorschlag +
    die schon bekannten Werte der Zeile zusammen duerfen die Rechen-Pruefung
    (``validate.line_item_row``) nicht WIDERLEGEN. ``None`` (nicht pruefbar,
    z.B. fehlt die Menge) gilt als zulaessig -- nur ein klares ``False``
    sperrt den Vorschlag."""
    probe = {**(bekannt or {}), **vorschlag, ziel_spalte: wert}
    ok, _grund = line_item_row(probe)
    return ok is not False
