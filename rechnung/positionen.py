"""Einheitliche Artikeltabelle (SPEC Baustein 1 "Selbstlernen 2").

Egal ob eine Position aus der Vorlage, der KI-Extraktion, einer E-Rechnung
oder einer Korrektur im Training-UI kommt -- am Ende stehen in jeder
Position dieselben Spaltennamen in derselben Reihenfolge:

    pos, beschreibung, menge, einzelpreis_netto, gesamt_netto, mwst_satz,
    waehrung, (einheit, falls vorhanden)

Diese Normalisierung passiert zentral in ``normalize_columns`` -- jede
Stelle, die Positionen entgegennimmt (Vorlage, KI, E-Rechnung, Tabellen-
Korrektur im UI), ruft sie einmal auf. CSV/UI/JobRouter lesen danach
ueberall dieselben Schluessel; ``validate.py`` braucht deshalb keine eigene
Alias-Suche mehr fuer diese Spalten.

Unbekannte Spalten (Rabatt, Kategorie-Vorschlag, interne ``_``-Felder, ...)
bleiben unveraendert stehen -- nur die bekannten Aliase werden umbenannt."""

from __future__ import annotations

from .textnorm import norm

# Feste Reihenfolge der Positionstabelle (SPEC Baustein 1). "einheit" ist
# optional und erscheint nur, wenn eine Position sie tatsaechlich hat.
STANDARD_COLUMNS: tuple[str, ...] = (
    "pos", "beschreibung", "menge", "einzelpreis_netto", "gesamt_netto",
    "mwst_satz", "waehrung", "einheit",
)

# norm(alias) -> kanonischer Spaltenname. ``norm`` macht "Einzelpreis"/
# "einzel-preis"/"EP" robust vergleichbar (Kleinschreibung, Umlaute, '-').
_ALIASES: dict[str, str] = {
    # beschreibung
    "bezeichnung": "beschreibung", "artikel": "beschreibung", "text": "beschreibung",
    "artikelbezeichnung": "beschreibung", "leistung": "beschreibung",
    "beschreibung": "beschreibung",
    # menge
    "anzahl": "menge", "stueck": "menge", "qty": "menge", "quantity": "menge",
    "menge": "menge",
    # einzelpreis_netto -- "preis"/"ep"/"einzelpreis" heissen so, SOLANGE sie
    # Nettopreise sind; eine Bruttopreis-Rechnung erkennt/entrechnet die
    # Rechenstufe (rechnen.py, Baustein 2), nicht diese Normalisierung.
    "preis": "einzelpreis_netto", "ep": "einzelpreis_netto",
    "einzelpreis": "einzelpreis_netto", "unitprice": "einzelpreis_netto",
    "unit-price": "einzelpreis_netto", "price": "einzelpreis_netto",
    "einzelpreis-netto": "einzelpreis_netto", "einzelpreis-net": "einzelpreis_netto",
    "nettopreis": "einzelpreis_netto", "einzelpreis-net.": "einzelpreis_netto",
    "einzelpreisnetto": "einzelpreis_netto",
    # gesamt_netto
    "netto": "gesamt_netto", "gesamt": "gesamt_netto", "gesamtpreis": "gesamt_netto",
    "summe": "gesamt_netto", "zeilensumme": "gesamt_netto", "betrag": "gesamt_netto",
    "gesamtnetto": "gesamt_netto", "nettobetrag": "gesamt_netto",
    "positionssumme": "gesamt_netto", "total": "gesamt_netto",
    "gesamt-netto": "gesamt_netto",
    # mwst_satz
    "ust-satz": "mwst_satz", "mwst": "mwst_satz", "ust": "mwst_satz",
    "steuersatz": "mwst_satz", "mwstsatz": "mwst_satz", "ustsatz": "mwst_satz",
    "vat": "mwst_satz", "tax": "mwst_satz",
    "mwst-satz": "mwst_satz",
    # waehrung
    "currency": "waehrung", "waehrg": "waehrung",
    # pos
    "position": "pos", "nr": "pos", "lfd": "pos", "posnr": "pos", "pos-nr": "pos",
    "laufendenr": "pos", "lfdnr": "pos",
    # einheit bleibt wie sie ist (keine Aliase bekannt, aber explizit erlaubt)
    "einheit": "einheit", "unit": "einheit",
}


def canonical_name(raw: str) -> str:
    """Kanonischer Spaltenname zu einem (Vorlagen-/KI-/Freitext-)Namen, oder
    der unveraenderte Name, wenn er keinem Alias entspricht."""
    return _ALIASES.get(norm(raw), raw)


def normalize_columns(items: list[dict] | None) -> list[dict]:
    """Benennt die Spalten jeder Position auf die Standardnamen um (SPEC
    Baustein 1). Interne Felder (``_kategorie_text`` o.ae., Praefix ``_``)
    und unbekannte Spalten bleiben unveraendert -- es wird nur umbenannt,
    nie ein Wert veraendert oder verworfen. Kollidieren zwei Quellspalten auf
    denselben kanonischen Namen (sollte in der Praxis nicht vorkommen), gilt
    die zuerst gefundene."""
    out: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        row: dict = {}
        for k, v in it.items():
            if k.startswith("_"):
                row[k] = v
                continue
            canon = canonical_name(k)
            if canon in row and row[canon] not in (None, ""):
                continue
            row[canon] = v
        out.append(row)
    return out


# Herkunfts-Markierung einer aus den Summen gebildeten Position (Supplier.
# positionsmodus == "eine_position", Teil B, Entscheidung Aaron 10.10.2026)
# -- wie "errechnet"/"kopf" in rechnen.py, damit Pruefungen (positionen-
# summe, steuersatz-positionen) sie wie eine normale, aber abgeleitete
# Position behandeln und das UI sie gelb ("Vorschlag") zeigt.
HERKUNFT_EINE_POSITION = "eine_position"

_SUMMEN_SPALTEN = ("beschreibung", "menge", "einzelpreis_netto", "gesamt_netto",
                   "mwst_satz", "waehrung")


def aus_summen(tax_breakdown: list[dict], bezeichnung: str, currency: str | None) -> list[dict]:
    """Positionen aus der USt-Aufstellung bilden (Positionsmodus
    'eine_position', Teil B): eine Zeile JE STEUERSATZ -- verschiedene
    Saetze werden nie zusammengefasst (Entscheidung Aaron 10.10.2026,
    Punkt 1). Zeilen ohne Nettobetrag (z.B. eine unvollstaendige
    Steuerzeile) werden ausgelassen statt geraten."""
    out: list[dict] = []
    zeilen = sorted(
        (r for r in (tax_breakdown or []) if isinstance(r, dict) and r.get("netto") is not None),
        key=lambda r: r.get("satz") if r.get("satz") is not None else -1)
    for i, row in enumerate(zeilen, start=1):
        netto = row["netto"]
        out.append({
            "pos": i,
            "beschreibung": bezeichnung or "",
            "menge": 1,
            "einzelpreis_netto": netto,
            "gesamt_netto": netto,
            "mwst_satz": row.get("satz"),
            "waehrung": currency or "",
            "_herkunft": {k: HERKUNFT_EINE_POSITION for k in _SUMMEN_SPALTEN},
        })
    return out


def ordered_columns(items: list[dict]) -> list[str]:
    """Spaltenreihenfolge fuer CSV/UI: zuerst die Standardspalten (nur die,
    die tatsaechlich in mind. einer Position vorkommen), danach alle
    uebrigen Spalten alphabetisch -- stabil statt der zufaelligen
    ``set()``-Reihenfolge."""
    present = {k for it in items for k in it if not k.startswith("_")}
    cols = [c for c in STANDARD_COLUMNS if c in present]
    extra = sorted(present - set(cols))
    return cols + extra
