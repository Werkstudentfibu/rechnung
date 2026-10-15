"""Belegart erkennen (Postfach-Bausteine 6 und 8).

Nur woertliche Treffer, keine KI-Klassifikation:

- ``aus_titel``: Titelbereich der ersten Seite (dubletten.title_text) --
  ein Wort zaehlt, wenn es einem Begriff entspricht oder als Zusammensetzung
  darauf endet ("Sammelrechnung", "Stornorechnung"), nicht wenn es nur damit
  beginnt ("Rechnungsnummer" ist kein Titel "Rechnung").
- ``aus_dateiname``: Dateiname eines Mail-Anhangs (Baustein 8).

Belegarten (``BELEGARTEN``, ueber ``RECHNUNG_BELEGARTEN`` einschraenkbar)
mit deutschen und englischen Synonymen; spezielle Arten (Gutschrift,
Rechnungskorrektur, Storno, Mahnung, Abschlags-/Schlussrechnung) gehen vor
der allgemeinen "Rechnung" ("Gutschrift zur Rechnung 4711" ist eine
Gutschrift). Zwei verschiedene spezielle Arten zugleich -> unklar ->
Mensch. Daneben bekannte Nicht-Beleg-Beschriftungen (AGB, Lieferschein,
Auftragsbestaetigung, Datenblatt ...), die in Baustein 8 als Anlage an den
Beleg derselben Mail gehaengt werden.

Vorzeichen: Gutschrift und Storno werden im Ergebnis (Journal, Ausgabe,
JobRouter) einheitlich mit NEGATIVEN Betraegen gefuehrt, egal ob das PDF
"1.190,00" oder "-1.190,00" druckt (``signed``). Die Feldwerte selbst
bleiben wie gedruckt (Lernen/Wiederfinden im PDF). Gemischte Vorzeichen
zwischen Netto/USt/Brutto -> Pruefung ``vorzeichen`` rot -> Mensch.
"""

from __future__ import annotations

import os
import re

from .textnorm import norm
from .validate import Check

RECHNUNG = "Rechnung"
GUTSCHRIFT = "Gutschrift"
KORREKTUR = "Rechnungskorrektur"
STORNO = "Storno"
MAHNUNG = "Mahnung"
ABSCHLAG = "Abschlagsrechnung"
SCHLUSS = "Schlussrechnung"

# Belegart -> Begriffe (norm()-Form; mehrere Woerter mit "-")
SYNONYME: dict[str, tuple[str, ...]] = {
    GUTSCHRIFT: ("gutschrift", "gutschriftsanzeige", "credit-note", "creditnote", "credit-memo",
                 "avoir"),
    KORREKTUR: ("rechnungskorrektur", "korrekturrechnung", "korrigierte-rechnung",
                "corrected-invoice", "correction-invoice", "invoice-correction"),
    STORNO: ("storno", "stornorechnung", "stornierung", "rechnungsstorno", "cancellation-invoice",
             "cancellation"),
    MAHNUNG: ("mahnung", "zahlungserinnerung", "zahlungsaufforderung", "payment-reminder",
              "reminder", "overdue-notice"),
    ABSCHLAG: ("abschlagsrechnung", "anzahlungsrechnung", "teilrechnung",
               "vorauszahlungsrechnung", "abschlag", "advance-invoice", "down-payment-invoice",
               "progress-invoice", "partial-invoice", "interim-invoice"),
    SCHLUSS: ("schlussrechnung", "endabrechnung", "endrechnung", "final-invoice"),
    RECHNUNG: ("rechnung", "invoice", "faktura", "facture", "fattura", "rg", "re"),
}
# Kurze/mehrdeutige Begriffe nur als ganzes Wort im Dateinamen ("RE_4711.pdf",
# "AB_123.pdf"), nie im Titel ("gueltig ab", "terms of payment")
_NUR_DATEINAME = {"rg", "re", "ab", "order", "terms", "conditions", "quote", "offer"}

BELEGARTEN = (RECHNUNG, GUTSCHRIFT, KORREKTUR, STORNO, MAHNUNG, ABSCHLAG, SCHLUSS)
GUTSCHRIFT_ARTEN = (GUTSCHRIFT, STORNO)     # Betraege negativ fuehren

# Bekannte Nicht-Beleg-Beschriftungen (Anlagen, Baustein 8)
ANLAGEN: dict[str, tuple[str, ...]] = {
    "AGB": ("agb", "agbs", "geschaeftsbedingungen", "allgemeine-geschaeftsbedingungen",
            "terms-and-conditions", "terms", "conditions", "lieferbedingungen",
            "verkaufsbedingungen", "einkaufsbedingungen"),
    "Lieferschein": ("lieferschein", "delivery-note", "packing-slip", "lieferavis",
                     "packliste", "packing-list"),
    "Auftragsbestaetigung": ("auftragsbestaetigung", "order-confirmation", "ab",
                             "bestellbestaetigung"),
    "Datenblatt": ("datenblatt", "datasheet", "data-sheet", "sicherheitsdatenblatt",
                   "produktdatenblatt", "technisches-datenblatt", "produktinformation"),
    "Angebot": ("angebot", "offer", "quotation", "quote", "kostenvoranschlag"),
    "Bestellung": ("bestellung", "purchase-order", "order"),
    "Leistungsnachweis": ("leistungsnachweis", "stundennachweis", "stundenzettel",
                          "timesheet", "aufmass", "abnahmeprotokoll", "leistungsverzeichnis"),
    "Zertifikat": ("zertifikat", "certificate", "zeugnis", "pruefbericht"),
    "Preisliste": ("preisliste", "price-list", "katalog", "catalogue", "broschuere", "brochure"),
    "Anschreiben": ("anschreiben", "begleitschreiben", "cover-letter"),
    "Widerrufsbelehrung": ("widerrufsbelehrung", "widerruf", "datenschutz",
                           "datenschutzerklaerung", "privacy"),
    # Erweiterung Nicht-Rechnung-Aussortierung (Entscheidung Aaron
    # 14.10.2026): nur woertliche Treffer, mit eigenen Tests.
    "Newsletter": ("newsletter", "rundschreiben", "mailing"),
    "Werbung": ("werbung", "werbeschreiben", "advertisement", "promotion", "aktion",
               "sonderangebot"),
    "Kontoauszug": ("kontoauszug", "bankauszug", "account-statement", "statement-of-account"),
    "Vertrag": ("vertrag", "vertragsunterlagen", "contract", "agreement", "vereinbarung"),
}

_BEZUG_RE = re.compile(
    r"(?i)(?:zu(?:r)?\s+rechnung|bezug\s*(?:auf|zu)?\s*:?\s*(?:rechnung)?|ursprungsrechnung"
    r"|urspr[üu]ngliche[nr]?\s+rechnung|original\s+invoice|korrektur\s+(?:der\s+|zur\s+)?rechnung"
    r"|storno\s+(?:der\s+|zur\s+)?rechnung|referenzrechnung|bezugsrechnung)"
    r"\s*(?:nr\.?|nummer|no\.?)?\s*:?\s*([A-Z0-9][A-Z0-9\-/.]*\d[A-Z0-9\-/.]*)")

# E-Rechnung: UNTDID-1001-Typcode -> Belegart
TYPE_CODES = {"380": RECHNUNG, "381": GUTSCHRIFT, "384": KORREKTUR, "389": GUTSCHRIFT,
              "326": ABSCHLAG, "386": ABSCHLAG, "875": ABSCHLAG, "876": ABSCHLAG,
              "877": SCHLUSS, "261": GUTSCHRIFT, "262": GUTSCHRIFT, "396": GUTSCHRIFT,
              "457": STORNO}

SOURCE = "text"


def aktive_belegarten() -> tuple[str, ...]:
    """Welche Belegarten verarbeitet werden (``RECHNUNG_BELEGARTEN``,
    kommagetrennt; Standard alle)."""
    v = os.environ.get("RECHNUNG_BELEGARTEN", "").strip()
    if not v:
        return BELEGARTEN
    want = {norm(x) for x in v.split(",") if x.strip()}
    return tuple(a for a in BELEGARTEN if norm(a) in want) or BELEGARTEN


def _words(text: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", norm(text)) if w]


def _hits(words: list[str], table: dict[str, tuple[str, ...]], dateiname: bool) -> set[str]:
    joined = "-" + "-".join(words) + "-"
    out = set()
    for art, syns in table.items():
        for syn in syns:
            if "-" in syn:
                if f"-{syn}-" in joined:
                    out.add(art)
            elif syn in _NUR_DATEINAME:
                if dateiname and syn in words:
                    out.add(art)
            elif any(w == syn or (len(syn) >= 6 and w.endswith(syn)) for w in words):
                out.add(art)
    return out


def _decide(arten: set[str]) -> tuple[str | None, str]:
    """-> (Belegart, Grund falls unklar)."""
    special = arten - {RECHNUNG}
    if len(special) > 1:
        # z.B. "Storno-Gutschrift", "Mahnung zur Abschlagsrechnung": nicht raten
        return None, "Belegart unklar: " + " / ".join(sorted(special))
    if special:
        return special.pop(), ""
    return (RECHNUNG, "") if RECHNUNG in arten else (None, "")


def aus_titel(title: str) -> tuple[str | None, str]:
    """Belegart aus dem Titelbereich der ersten Seite -> (Art|None, Grund)."""
    return _decide(_hits(_words(title), SYNONYME, False))


def aus_dateiname(name: str) -> tuple[str | None, str | None, str]:
    """Beschriftung eines Anhangs -> (Belegart|None, Anlagenart|None, Grund).
    Belegart UND Nicht-Beleg-Beschriftung zugleich ("Rechnung_und_AGB.pdf")
    oder zwei Belegarten -> beides None mit Grund (dann entscheidet der
    Titel auf Seite 1)."""
    stem = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name or "")
    # CamelCase und Ziffernfolgen trennen ("RechnungRE4711" -> rechnung re 4711)
    stem = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])", " ", stem)
    words = _words(stem)
    art, why = _decide(_hits(words, SYNONYME, True))
    anlagen = _hits(words, ANLAGEN, True)
    if why:
        return None, None, why
    if art and anlagen:
        return None, None, f"Beschriftung widerspruechlich: {art} / {', '.join(sorted(anlagen))}"
    if len(anlagen) > 1:
        return None, sorted(anlagen)[0], ""
    return art, (anlagen.pop() if anlagen else None), ""


def anlage_aus_titel(title: str) -> str | None:
    hits = _hits(_words(title), ANLAGEN, False)
    return sorted(hits)[0] if hits else None


def bezug(text: str, eigene_nummer: str | None = None) -> str | None:
    """Verweis auf die Ursprungsrechnung ("zur Rechnung RE-4711") oder None."""
    from .dubletten import norm_number

    own = norm_number(eigene_nummer) if eigene_nummer else ""
    for m in _BEZUG_RE.finditer(text or ""):
        num = m.group(1).rstrip(".")
        if norm_number(num) != own:
            return num
    return None


# ---- Pipeline ------------------------------------------------------------

def _amount(fields: dict, key: str) -> float | None:
    fr = fields.get(key)
    if fr is None or fr.source == "missing" or fr.value in (None, ""):
        return None
    try:
        return float(fr.value)
    except (TypeError, ValueError):
        return None


def detect(doc=None, title: str = "") -> tuple[str | None, str]:
    from .dubletten import title_text

    return aus_titel(title_text(doc) if doc is not None else title)


def apply(fields: dict, doc=None, text: str = "", title: str = "",
          hinweise: list[str] | None = None, type_code: str = "") -> None:
    """Felder ``belegart`` (falls nicht schon gesetzt, z.B. vom Menschen) und
    ``bezug_rechnungsnummer`` ergaenzen. Ohne Titelwort gilt "Rechnung"
    (bisheriges Verhalten), unklar -> kein Wert (Pruefung ``belegart`` rot)."""
    from .template import FieldResult

    cur = fields.get("belegart")
    if cur is None or cur.source == "missing" or not cur.value:
        if type_code and type_code in TYPE_CODES:
            fields["belegart"] = FieldResult("belegart", TYPE_CODES[type_code], type_code,
                                             1.0, "einvoice")
        else:
            art, why = detect(doc, title)
            if art:
                fields["belegart"] = FieldResult("belegart", art, art, 0.9, SOURCE)
            elif not why:
                fields["belegart"] = FieldResult("belegart", RECHNUNG, "", 0.75, SOURCE)
    art = (fields.get("belegart").value if fields.get("belegart") else None)
    body = doc.text if doc is not None else text
    if art in (GUTSCHRIFT, KORREKTUR, STORNO) and not (
            fields.get("bezug_rechnungsnummer") and fields["bezug_rechnungsnummer"].value):
        num = fields.get("rechnungsnummer")
        ref = bezug(body, num.value if num is not None else None)
        if ref:
            fields["bezug_rechnungsnummer"] = FieldResult("bezug_rechnungsnummer", ref, ref,
                                                          0.85, SOURCE)
        elif hinweise is not None:
            h = f"{art} ohne erkennbaren Verweis auf die Ursprungsrechnung"
            if h not in hinweise:
                hinweise.append(h)


def checks(fields: dict, doc=None, title: str = "") -> list[Check]:
    """Pruefungen ``belegart`` (eindeutig?) und ``vorzeichen`` (Netto/USt/
    Brutto mit gleichem Vorzeichen)."""
    out = []
    fr = fields.get("belegart")
    if fr is not None and fr.source in ("manual", "einvoice") and fr.value:
        out.append(Check("belegart", True, str(fr.value)))
    else:
        art, why = detect(doc, title)
        out.append(Check("belegart", False, why) if why else
                   Check("belegart", True, art or RECHNUNG))
    vals = [v for v in (_amount(fields, k) for k in ("netto", "ust_betrag", "brutto"))
            if v not in (None, 0)]
    if len({v > 0 for v in vals}) > 1:
        out.append(Check("vorzeichen", False, "Netto/USt/Brutto mit gemischten Vorzeichen"))
    else:
        out.append(Check("vorzeichen", True if vals else None, "einheitlich" if vals else ""))
    return out


def is_credit(fields: dict) -> bool:
    fr = fields.get("belegart")
    return fr is not None and fr.value in GUTSCHRIFT_ARTEN


BETRAGSFELDER = ("netto", "ust_betrag", "brutto", "zahlbetrag")


def signed(values: dict) -> dict:
    """Flache Feldwerte (Name -> Wert) fuer die Ausgabe: bei Gutschrift/
    Storno Betraege einheitlich negativ."""
    if values.get("belegart") not in GUTSCHRIFT_ARTEN:
        return values
    out = dict(values)
    for k in BETRAGSFELDER:
        v = out.get(k)
        if isinstance(v, (int, float)):
            out[k] = -abs(v)
    return out


# ---- Reverse Charge (Steuerschuldnerschaft des Leistungsempfaengers) --------
# Checkbox "Reversed Charge" der Abbyy-Maske (Aaron 09.10.2026). Reine
# Textregel -- der Vermerk ist auf der Rechnung Pflicht (§ 14a Abs. 5 UStG,
# Art. 196 MwStSystRL), also steht er im Text, wenn es so ist.
_RC_RE = re.compile(
    r"(?i)reverse[\s-]*charge|steuerschuldnerschaft\s+des\s+leistungsempf|"
    r"§\s*13\s*b\s+ustg|autoliquidation|inversione\s+contabile|"
    r"bezugsteuer|verlagerung\s+der\s+steuerschuld")


def reverse_charge(fields: dict, text: str) -> None:
    """Feld ``reverse_charge`` = "ja", wenn der Vermerk im Text steht.
    Eine Korrektur des Menschen (Haken gesetzt/entfernt) bleibt."""
    from .template import FieldResult

    cur = fields.get("reverse_charge")
    if cur is not None and cur.source == "manual":
        return
    m = _RC_RE.search(text or "")
    if m:
        fields["reverse_charge"] = FieldResult("reverse_charge", "ja", m.group(0), 0.9, SOURCE)
