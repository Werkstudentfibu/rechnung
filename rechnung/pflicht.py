"""Pflichtangaben einer Rechnung -- reine Hinweis-Pruefung (Entscheidung
Aaron 10.10.2026).

Prueft per Text und Feldern (keine KI), ob die Pflichtangaben einer
Rechnung erkennbar sind, und zeigt sie als eigene Pruefgruppe an. Nur
**Hinweis** (gelb): kein Rueckstellgrund, kein roter Check, solange Aaron
nichts anderes entscheidet. Bewusst kurz und nicht perfekt -- die Erkennung
ist textbasiert und kann irren; sie soll den Menschen auf Luecken aufmerksam
machen, nicht rechtlich entscheiden.

Regelwerke:

- **§ 14 Abs. 4 UStG** (Normalfall): Name und Anschrift des Leistenden und
  des Empfaengers, Steuernummer oder USt-IdNr des Leistenden,
  Ausstellungsdatum, Rechnungsnummer, Menge/Art der Lieferung bzw. Umfang/
  Art der Leistung, Zeitpunkt/Zeitraum der Leistung, Entgelt nach
  Steuersaetzen aufgeschluesselt, Steuersatz und Steuerbetrag bzw. Hinweis
  auf Steuerbefreiung/Reverse Charge.
- **§ 33 UStDV** (Kleinbetragsrechnung bis 250 EUR brutto): nur Name und
  Anschrift des Leistenden, Ausstellungsdatum, Menge/Art, Entgelt und
  Steuerbetrag in einer Summe, Steuersatz bzw. Hinweis auf Steuerbefreiung.
- **Art. 26 MWSTG** (Schweiz, CHF oder Schweizer MWST-Nummer): Name und Ort
  des Leistungserbringers mit MWST-Nummer (UID mit "MWST"), Name und Ort des
  Empfaengers, Datum/Zeitraum der Leistung (fehlt es, gilt das
  Rechnungsdatum), Art/Umfang, Entgelt, Steuersatz (und Steuerbetrag).
  Bis CHF 400 (Kassenzettel, Art. 26 Abs. 3) entfaellt der Empfaenger.
"""

from __future__ import annotations

import re

from .textnorm import find_ustids, parse_amount, parse_date

UStG = "§ 14 Abs. 4 UStG"
UStDV = "§ 33 UStDV (Kleinbetragsrechnung)"
MWSTG = "Art. 26 MWSTG (Schweiz)"
KLEINBETRAG_EUR = 250.0
KLEINBETRAG_CHF = 400.0

LEISTENDER = "Name und Anschrift des Leistenden"
EMPFAENGER = "Name und Anschrift des Empfängers"
STEUERNR = "Steuernummer oder USt-IdNr des Leistenden"
MWSTNR = "MWST-Nummer (UID mit MWST) des Leistungserbringers"
DATUM = "Ausstellungsdatum"
NUMMER = "Rechnungsnummer"
ART = "Menge und Art der Lieferung/Leistung"
LEISTUNG = "Zeitpunkt/Zeitraum der Leistung"
ENTGELT = "Entgelt nach Steuersätzen aufgeschlüsselt"
ENTGELT_SUMME = "Entgelt und Steuerbetrag in einer Summe"
STEUER = "Steuersatz und Steuerbetrag bzw. Hinweis auf Steuerbefreiung"
STEUERSATZ = "Steuersatz bzw. Hinweis auf Steuerbefreiung"

# PLZ + Ort (DE/AT 4-5 Ziffern, CH 4 Ziffern, optional "D-"/"CH-")
_PLZ_ORT_RE = re.compile(r"(?<![\d.,/])(?:[A-Z]{1,2}-)?(\d{4,5})\s+([A-ZÄÖÜ][A-Za-zÄÖÜäöüß.\-]+)")
_STNR_RE = re.compile(r"(?i)(?:steuer[- ]?n(?:umme)?r\.?|st\.?[- ]?nr\.?)\s*[:.]?\s*"
                      r"(\d{2,3}\s*/\s*\d{3,4}\s*/\s*\d{4,5}|\d{10,13})")
_MWST_NR_RE = re.compile(r"(?i)CHE[- ]?\d{3}[. ]?\d{3}[. ]?\d{3}\s*(?:MWST|TVA|IVA|VAT)")
_ART_RE = re.compile(r"(?i)\b(bezeichnung|beschreibung|artikel|leistung(?:en)?\s*:|"
                     r"menge|anzahl|stk\.?|stück|pauschal)")
_LEISTUNG_LABEL = (r"(?:leistungsdatum|lieferdatum|liefertag|leistungstag|tag der (?:lieferung|"
                   r"leistung)|datum der (?:lieferung|leistung)|leistung erbracht am|"
                   r"ausführungsdatum|lieferung am|geliefert am)")
_LEISTUNG_DATUM_RE = re.compile(r"(?i)" + _LEISTUNG_LABEL + r"\s*[:.]?\s*(?:vom\s*)?"
                                r"(\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}|\d{4}-\d{2}-\d{2})")
_LEISTUNG_ZEITRAUM_RE = re.compile(
    r"(?i)(leistungszeitraum|lieferzeitraum|abrechnungszeitraum|zeitraum|leistungsmonat|"
    r"leistung(?:en)?\s+(?:im|vom|für)|lieferung(?:en)?\s+(?:im|vom))\s*[:.]?\s*"
    r"(\d{1,2}\.\s?\d{1,2}\.\s?\d{2,4}|[a-zäöü]+\s+\d{4}|\d{1,2}/\d{4})")
_LEISTUNG_GLEICH_RE = re.compile(
    r"(?i)(leistungs|liefer)(datum|zeitpunkt)\s*(=|entspricht|ist gleich|gleich)\s*(dem\s*)?"
    r"rechnungsdatum|rechnungsdatum\s*(=|entspricht|ist gleich|gleich)\s*(dem\s*)?"
    r"(leistungs|liefer)(datum|zeitpunkt)|datum\s+der\s+rechnung\s+entspricht")
_BEFREIUNG_RE = re.compile(
    r"(?i)steuerfrei|steuerbefreit|umsatzsteuerfrei|§\s*4\s*nr|§\s*13\s*b|reverse[- ]charge|"
    r"steuerschuldnerschaft des leistungsempf|innergemeinschaftliche\s+lieferung|"
    r"§\s*19\s*ustg|kleinunternehmer|ausfuhrlieferung|nicht steuerbar|von der steuer befreit|"
    r"exonéré|autoliquidation")

SOURCE = "text"


def _wert(fields: dict, name: str):
    fr = (fields or {}).get(name)
    if fr is None or fr.source == "missing" or fr.value in (None, ""):
        return None
    return fr.value


def _gelesen(fields: dict, name: str):
    """Wert, der auf der Rechnung steht (nicht aus der Bibliothek)."""
    fr = (fields or {}).get(name)
    if fr is None or fr.source in ("missing", "library") or fr.value in (None, ""):
        return None
    return fr.value


def _zahl(v) -> float | None:
    if isinstance(v, bool) or v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return parse_amount(str(v))


# ---- Leistungsdatum per Textregel -----------------------------------------

def leistungsdatum_aus_text(text: str) -> str | None:
    """"Leistungsdatum: 05.09.2026" / "Lieferdatum 2026-09-05" -> ISO-Datum,
    nur bei eindeutigem Fund (mehrere verschiedene Daten -> keins)."""
    funde = {d.isoformat() for m in _LEISTUNG_DATUM_RE.finditer(text or "")
             if (d := parse_date(re.sub(r"\s", "", m.group(1))))}
    return funde.pop() if len(funde) == 1 else None


def apply_leistungsdatum(fields: dict, text: str) -> None:
    """Feld ``leistungsdatum`` aus dem Text fuellen, wenn weder Vorlage noch
    Mensch einen Wert geliefert haben (kein Raten: nur hinter einem
    eindeutigen Label)."""
    from .template import FieldResult

    if _wert(fields, "leistungsdatum") is not None:
        return
    iso = leistungsdatum_aus_text(text)
    if iso:
        fields["leistungsdatum"] = FieldResult("leistungsdatum", iso, iso, 0.9, SOURCE)


# ---- Pruefung --------------------------------------------------------------

def _regelwerk(res, text: str) -> str:
    w = str(_wert(res.fields, "waehrung") or "").upper()
    brutto = _zahl(_wert(res.fields, "brutto"))
    schweiz = w == "CHF" or bool(_MWST_NR_RE.search(text or ""))
    if schweiz:
        return MWSTG
    if brutto is not None and 0 < brutto <= KLEINBETRAG_EUR and w in ("", "EUR"):
        return UStDV
    return UStG


def _plz_orte(text: str) -> set[str]:
    return {m.group(1) for m in _PLZ_ORT_RE.finditer(text or "")}


def _punkt(name: str, ok: bool | None, detail: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail}


def _leistender(res, text: str, orte: set[str], store) -> dict:
    name = res.supplier_name or (res.ki_lieferant or {}).get("name") \
        or (res.einvoice or {}).get("verkaeufer_name")
    if not name:
        return _punkt(LEISTENDER, False, "kein Lieferant erkannt")
    adresse = (res.einvoice or {}).get("verkaeufer_adresse")
    if adresse:
        return _punkt(LEISTENDER, True, "aus der E-Rechnung")
    sup = store.supplier(res.supplier_id) if (store is not None and res.supplier_id) else None
    sup_plz = {m.group(1) for line in (getattr(sup, "address_lines", None) or [])
               for m in _PLZ_ORT_RE.finditer(line)}
    if sup_plz and sup_plz & orte:
        return _punkt(LEISTENDER, True, "Anschrift des Lieferanten auf der Rechnung")
    if orte:
        return _punkt(LEISTENDER, True, "Name und eine Anschrift (PLZ/Ort) erkannt")
    return _punkt(LEISTENDER, False, "keine Anschrift (PLZ/Ort) erkannt")


def _empfaenger(res, text: str, orte: set[str]) -> dict:
    name = res.billing_address_name or (res.ki_empfaenger or {}).get("name") \
        or (res.einvoice or {}).get("kaeufer_name")
    if res.einvoice and name:
        return _punkt(EMPFAENGER, True, "aus der E-Rechnung")
    if not name:
        return _punkt(EMPFAENGER, False, "kein Rechnungsempfänger erkannt")
    # zwei verschiedene PLZ/Ort-Angaben: eine fuer den Leistenden, eine fuer
    # den Empfaenger (vereinfacht -- ein gemeinsamer Ort wird so verfehlt)
    if len(orte) >= 2:
        return _punkt(EMPFAENGER, True, f"{name} mit Anschrift")
    return _punkt(EMPFAENGER, False, f"{name}, aber keine zweite Anschrift (PLZ/Ort) erkannt")


def _steuernummer(res, text: str) -> dict:
    if _gelesen(res.fields, "ustid") or (res.einvoice or {}).get("verkaeufer_ustid"):
        return _punkt(STEUERNR, True, "USt-IdNr")
    if _gelesen(res.fields, "steuernummer") or (res.einvoice or {}).get("verkaeufer_steuernummer"):
        return _punkt(STEUERNR, True, "Steuernummer")
    if [u for u in find_ustids(text or "") if not u.startswith("CHE")]:
        return _punkt(STEUERNR, True, "USt-IdNr im Text")
    if _STNR_RE.search(text or ""):
        return _punkt(STEUERNR, True, "Steuernummer im Text")
    return _punkt(STEUERNR, False, "weder Steuernummer noch USt-IdNr erkannt")


def _mwst_nummer(text: str) -> dict:
    if _MWST_NR_RE.search(text or ""):
        return _punkt(MWSTNR, True, "UID mit MWST")
    if any(u.startswith("CHE") for u in find_ustids(text or "")):
        return _punkt(MWSTNR, False, "UID ohne Zusatz MWST")
    return _punkt(MWSTNR, False, "keine MWST-Nummer erkannt")


def _art(res, text: str) -> dict:
    if any(str(it.get("beschreibung") or "").strip() for it in res.line_items or []):
        return _punkt(ART, True, f"{len(res.line_items)} Position(en)")
    if (res.einvoice or {}).get("positionen"):
        return _punkt(ART, True, "Positionen der E-Rechnung")
    if _ART_RE.search(text or ""):
        return _punkt(ART, True, "Bezeichnung im Text")
    return _punkt(ART, False, "keine Positionen/Bezeichnung erkannt")


def _leistung(res, text: str, schweiz: bool) -> dict:
    if _wert(res.fields, "leistungsdatum"):
        return _punkt(LEISTUNG, True, "Leistungsdatum")
    if _LEISTUNG_GLEICH_RE.search(text or ""):
        return _punkt(LEISTUNG, True, "Hinweis Leistungsdatum = Rechnungsdatum")
    if _LEISTUNG_ZEITRAUM_RE.search(text or ""):
        return _punkt(LEISTUNG, True, "Leistungszeitraum im Text")
    if schweiz:
        return _punkt(LEISTUNG, True, "nicht angegeben -- gilt als Rechnungsdatum (Art. 26 Abs. 2 "
                                      "lit. c MWSTG)")
    if res.einvoice:
        return _punkt(LEISTUNG, None, "Lieferdatum der E-Rechnung wird nicht ausgewertet")
    return _punkt(LEISTUNG, False, "kein Leistungsdatum/-zeitraum und kein Hinweis "
                                   "'Leistungsdatum = Rechnungsdatum'")


def _saetze(res) -> list[float]:
    out = []
    for r in res.tax_breakdown or []:
        s = _zahl(r.get("satz"))
        if s is not None:
            out.append(s)
    s = _zahl(_wert(res.fields, "ust_satz"))
    if s is not None and s not in out:
        out.append(s)
    return out


def _befreit(res, text: str) -> bool:
    return _wert(res.fields, "reverse_charge") == "ja" or bool(_BEFREIUNG_RE.search(text or ""))


def _entgelt(res) -> dict:
    zeilen = [r for r in res.tax_breakdown or [] if _zahl(r.get("netto")) is not None
              and _zahl(r.get("satz")) is not None]
    if zeilen:
        return _punkt(ENTGELT, True, ", ".join(f"{_zahl(r['satz']):g} %" for r in zeilen))
    if _zahl(_wert(res.fields, "netto")) is not None and _saetze(res):
        return _punkt(ENTGELT, True, "Netto mit Steuersatz")
    return _punkt(ENTGELT, False, "Entgelt nicht nach Steuersätzen erkennbar")


def _steuer(res, text: str, name: str = STEUER, betrag_noetig: bool = True) -> dict:
    saetze = _saetze(res)
    ust = _zahl(_wert(res.fields, "ust_betrag"))
    if saetze and any(s > 0 for s in saetze):
        if betrag_noetig and ust is None:
            return _punkt(name, False, "Steuersatz, aber kein Steuerbetrag erkannt")
        return _punkt(name, True, ", ".join(f"{s:g} %" for s in saetze))
    if _befreit(res, text):
        return _punkt(name, True, "Hinweis auf Steuerbefreiung/Reverse Charge")
    if saetze:
        return _punkt(name, False, "0 % ohne Hinweis auf Steuerbefreiung/Reverse Charge")
    return _punkt(name, False, "kein Steuersatz erkannt")


def pruefen(res, text: str = "", store=None) -> dict | None:
    """-> ``{"regelwerk", "punkte": [{name, ok, detail}], "fehlt": [...]}``;
    ``None`` ohne verwertbares Ergebnis (abgebrochen, nicht lesbar)."""
    if res.aborted or not (res.fields or res.einvoice):
        return None
    text = text or ""
    regel = _regelwerk(res, text)
    orte = _plz_orte(text)
    datum = _punkt(DATUM, bool(_wert(res.fields, "rechnungsdatum")),
                   "" if _wert(res.fields, "rechnungsdatum") else "kein Rechnungsdatum")
    nummer = _punkt(NUMMER, bool(_wert(res.fields, "rechnungsnummer")),
                    "" if _wert(res.fields, "rechnungsnummer") else "keine Rechnungsnummer")
    if regel == UStDV:
        brutto = _zahl(_wert(res.fields, "brutto"))
        punkte = [_leistender(res, text, orte, store), datum, _art(res, text),
                  _punkt(ENTGELT_SUMME, brutto is not None, f"{brutto:.2f}" if brutto else ""),
                  _steuer(res, text, STEUERSATZ, betrag_noetig=False)]
    elif regel == MWSTG:
        brutto = _zahl(_wert(res.fields, "brutto"))
        punkte = [_leistender(res, text, orte, store), _mwst_nummer(text)]
        if not (brutto is not None and 0 < brutto <= KLEINBETRAG_CHF):
            punkte.append(_empfaenger(res, text, orte))
        # Steuerbetrag: bei Entgelt inkl. Steuer genuegt der Satz
        punkte += [datum, _leistung(res, text, schweiz=True), _art(res, text),
                   _punkt("Entgelt", brutto is not None or _wert(res.fields, "netto") is not None),
                   _steuer(res, text, "Steuersatz (und Steuerbetrag)", betrag_noetig=False)]
    else:
        punkte = [_leistender(res, text, orte, store), _empfaenger(res, text, orte),
                  _steuernummer(res, text), datum, nummer, _art(res, text),
                  _leistung(res, text, schweiz=False), _entgelt(res), _steuer(res, text)]
    fehlt = [p["name"] for p in punkte if p["ok"] is False]
    return {"regelwerk": regel, "punkte": punkte, "fehlt": fehlt}


def hinweis_text(ergebnis: dict | None) -> str | None:
    """Ein Satz fuer ``res.hinweise`` (geht als Hinweis mit nach JobRouter)."""
    if not ergebnis or not ergebnis["fehlt"]:
        return None
    return f"Pflichtangaben ({ergebnis['regelwerk']}) fehlt: " + ", ".join(ergebnis["fehlt"])
