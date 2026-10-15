"""Excel-Stammdaten-Import: Kreditoren (DE/CH), Mandanten, Postfaecher
(Entscheidung Aaron 15.10.2026).

Grundsatz (Aaron, woertlich): "Wenn das Programm irgendwo nicht sicher ist,
soll es nicht raten, sondern mir Bescheid geben, dann kann ich ihm das
Richtige sagen, damit die Daten nicht falsch sind." Dieses Modul setzt das
dreistufig um:

1. **Spaltenzuordnung** (``spalten_vorschlag``): nur ein VORSCHLAG anhand
   einer Wortliste (DE/EN) je Spaltenueberschrift -- entschieden wird nie
   hier, sondern vom Menschen im Web-UI (Reiter "Stammdaten"). Einmal
   bestaetigte Zuordnungen werden je Layout (Menge der Spaltenueberschriften)
   gemerkt (``gelernte_zuordnung``/``zuordnung_lernen``), damit der naechste
   Import mit denselben Ueberschriften sie direkt vorschlaegt -- weiterhin
   nur ein Vorschlag, keine stille Uebernahme.
2. **Zeilenpruefung** (``zeilen_aufbauen``): jede Zeile bekommt eine Ampel
   (gruen/gelb/rot) und bei jeder Unsicherheit eine ``Frage`` mit Grund und
   Antwortmoeglichkeiten ("so uebernehmen"/"korrigieren auf ..."/"Zeile
   weglassen") -- NIE eine automatische Entscheidung ueber IBAN-Pruefziffer,
   UID-Format, Land/IBAN-Widerspruch, Dubletten oder einen Konflikt mit der
   bestehenden Bibliothek.
3. **Anwenden** (``anwenden``): uebernommen werden ausschliesslich gruene
   Zeilen und gelbe Zeilen, bei denen JEDE Frage explizit beantwortet ist --
   die komplette Pruefung laeuft dabei serverseitig NEU (nie wird nur eine
   vom Client mitgeschickte Ampel vertraut). Ein Treffer gegen einen
   vorhandenen Datensatz mit abweichenden Werten wird nie stillschweigend
   ueberschrieben (siehe ``_konflikt_mit_bibliothek``).

Zustandslos zwischen den Web-Requests: die geparsten Zeilen reist der Client
zwischen "Vorschau" und "Uebernehmen" mit (ueberschaubare Datenmengen -- ~50
Mandanten/Postfaecher, eine Handvoll hundert Kreditoren), gemerkt wird nur die
Spalten-Zuordnung je Layout (``data/stammdaten_layouts.json``) und natuerlich
das Ergebnis selbst (Lieferanten/Rechnungsadressen/Postfaecher)."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field

from .textnorm import che_valid, iban_valid, norm, norm_ustid

# ---- Arten (Blatt/Zeile) --------------------------------------------------
ART_KREDITOR_DE = "kreditor_de"
ART_KREDITOR_CH = "kreditor_ch"
ART_MANDANT = "mandant"
ART_POSTFACH = "postfach"
ART_GEMISCHT_LAND = "gemischt_land"   # Blatt-Vorschlag: Land je Zeile entscheidet DE/CH
ART_UNKLAR = "unklar"

ARTEN_KREDITOR = (ART_KREDITOR_DE, ART_KREDITOR_CH)
BLATT_ARTEN = (ART_KREDITOR_DE, ART_KREDITOR_CH, ART_MANDANT, ART_POSTFACH,
              ART_GEMISCHT_LAND, ART_UNKLAR)

STATUS_GRUEN = "gruen"
STATUS_GELB = "gelb"
STATUS_ROT = "rot"

ANTWORT_UEBERNEHMEN = "so_uebernehmen"
ANTWORT_KORRIGIEREN = "korrigieren"
ANTWORT_WEGLASSEN = "weglassen"

_STANDARD_ANTWORTEN = ["so übernehmen", "korrigieren auf …", "Zeile weglassen"]


# ---- Spaltenwoerterbuch (Wortliste DE/EN, erweiterbar) --------------------
ZIELFELDER: dict[str, list[str]] = {
    "name": ["name", "firma", "firmenname", "lieferant", "lieferantenname",
             "kreditor", "kreditorname", "company", "company name", "supplier",
             "supplier name", "vendor", "vendor name", "bezeichnung"],
    "strasse": ["strasse", "straße", "str", "adresse", "address", "street",
                "anschrift"],
    "plz": ["plz", "postleitzahl", "zip", "zip code", "postal code", "postcode"],
    "ort": ["ort", "stadt", "city", "town"],
    "land": ["land", "country", "staat", "country code", "laendercode"],
    "ustid": ["ustid", "ust-idnr", "ust-id", "ust id", "umsatzsteuer-id",
              "umsatzsteuer-identifikationsnummer", "vatid", "vat id", "vat",
              "uid", "mwst-nr", "mwst nr", "mwst", "tva", "iva"],
    "steuernummer": ["steuernummer", "steuer-nr", "steuernr", "tax number",
                     "tax no", "taxno", "glaeubiger-id", "gläubiger-id",
                     "glaeubigerid"],
    "iban": ["iban"],
    "bic": ["bic", "swift", "swift-bic", "swift code"],
    "kreditorennummer": ["kreditorennummer", "kreditor-nr", "kreditornr",
                         "kred-nr", "kredno", "lieferantennummer",
                         "lieferanten-nr", "vendor id", "vendor no",
                         "vendor number", "creditor id", "creditor no",
                         "lieferanten-id"],
    # Verweis auf einen Mandanten (bei Kreditor-/Postfach-Zeilen) ODER --
    # auf einem Mandanten-Blatt -- der eigene Mandanten-Code (s.
    # ``_mandant_zeile``). Dieselbe Spaltenueberschrift ("Mandant",
    # "Gesellschaft", ...) kann je nach Blatt beides bedeuten.
    "mandant_verweis": ["mandant", "gesellschaft", "business unit",
                        "geschaeftsbereich", "geschäftsbereich", "company code",
                        "buchungskreis", "firma (mandant)",
                        "mandant/gesellschaft", "kostenstelle"],
    "email": ["email", "e-mail", "e-mail-adresse", "mail", "benutzer", "user",
              "login", "postfach", "postfach-adresse", "mailadresse"],
    "passwort": ["passwort", "password", "kennwort", "pwd", "pass"],
    "server": ["server", "host", "imap", "imap-server", "imap server",
              "mailserver"],
    "ordner": ["ordner", "folder", "quellordner"],
}

_SYNONYM_TO_FIELD: dict[str, str] = {}
for _canon, _syns in ZIELFELDER.items():
    for _s in _syns:
        _SYNONYM_TO_FIELD[_s] = _canon


def _norm_header(h: str) -> str:
    h = unicodedata.normalize("NFKC", h or "").strip().lower()
    h = h.replace("ß", "ss")
    h = re.sub(r"[_/]+", " ", h)
    h = re.sub(r"[^a-z0-9%. -]", "", h)
    h = re.sub(r"\s+", " ", h).strip(" .-")
    return h


def vorschlag_fuer_spalte(header: str) -> str | None:
    """Eine Spaltenueberschrift -> vorgeschlagenes Zielfeld, oder ``None``
    (unbekannt -- der Mensch muss "ignorieren" oder ein Zielfeld waehlen)."""
    h = _norm_header(header)
    if not h:
        return None
    if h in _SYNONYM_TO_FIELD:
        return _SYNONYM_TO_FIELD[h]
    words = set(h.split())
    for syn, canon in _SYNONYM_TO_FIELD.items():
        syn_words = set(syn.split())
        if syn_words and syn_words <= words:
            return canon
    return None


def spalten_vorschlag(headers: list[str]) -> dict[str, str | None]:
    """Jede Spaltenueberschrift -> vorgeschlagenes Zielfeld (oder ``None``)."""
    return {h: vorschlag_fuer_spalte(h) for h in headers}


# ---- Layout-Gedaechtnis (SPEC: "Zuordnung pro Datei-Layout merken") -------

def layout_schluessel(headers: list[str]) -> str:
    """Stabiler Schluessel aus der MENGE der Spaltenueberschriften (Reihen-
    folge egal -- derselbe Export kann die Spalten in anderer Reihenfolge
    liefern)."""
    norm_set = sorted({_norm_header(h) for h in headers if h.strip()})
    return hashlib.sha256("|".join(norm_set).encode("utf-8")).hexdigest()[:16]


def gelernte_zuordnung(store, headers: list[str]) -> dict | None:
    """-> {"mapping": {...}, "art": "..."} der zuletzt bestaetigten Zuordnung
    fuer genau dieses Layout, oder ``None`` (neues Layout)."""
    daten = _layouts_lesen(store)
    return daten.get(layout_schluessel(headers))


def zuordnung_lernen(store, headers: list[str], mapping: dict[str, str | None],
                     art: str, operator: str = "") -> None:
    daten = _layouts_lesen(store)
    key = layout_schluessel(headers)
    daten[key] = {"mapping": mapping, "art": art}
    _layouts_schreiben(store, daten)
    store.audit("stammdaten_layout_gelernt", {"schluessel": key, "art": art}, operator)


def _layouts_pfad(store):
    return store.root / "stammdaten_layouts.json"


def _layouts_lesen(store) -> dict:
    try:
        return json.loads(_layouts_pfad(store).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _layouts_schreiben(store, daten: dict) -> None:
    p = _layouts_pfad(store)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(daten, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(p)


# ---- Art-Vorschlag je Blatt ------------------------------------------------

def art_vorschlag(sheet_name: str, mapping: dict[str, str | None]) -> str:
    """Vorschlag, welche Art Zeilen dieses Blatt enthaelt -- anhand des
    Blattnamens UND der zugeordneten Spalten. Reiner Vorschlag, im UI
    aenderbar (SPEC: "Ist das nicht eindeutig ... -> fragen")."""
    # Bewusst NICHT ueber ``_norm_header`` (die entfernt Umlaute ersatzlos --
    # "Postfächer" wuerde zu "postfcher" und das Wort waere nicht mehr
    # wiederzufinden); hier reicht Kleinschreibung, Umlaute bleiben erhalten.
    n = (sheet_name or "").strip().lower()
    felder = set(v for v in mapping.values() if v)
    if {"email", "passwort"} & felder or "server" in felder:
        return ART_POSTFACH
    if re.search(r"postfach|postfächer|mailbox|imap|ionos", n):
        return ART_POSTFACH
    if re.search(r"mandant|gesellschaft|business unit", n):
        return ART_MANDANT
    if re.search(r"schweiz|\bch\b|switzerland", n):
        return ART_KREDITOR_CH
    if re.search(r"deutschland|\bde\b|germany", n):
        return ART_KREDITOR_DE
    if "land" in felder:
        return ART_GEMISCHT_LAND
    if "iban" in felder or "ustid" in felder or "kreditorennummer" in felder:
        return ART_GEMISCHT_LAND if "land" in felder else ART_UNKLAR
    return ART_UNKLAR


# ---- Hilfsfunktionen --------------------------------------------------------

def _zeile_zu_werten(headers: list[str], row: list[str],
                     mapping: dict[str, str | None]) -> dict[str, str]:
    out: dict[str, str] = {}
    for h, v in zip(headers, row):
        feld = mapping.get(h)
        if not feld or not (v or "").strip():
            continue
        out[feld] = v.strip()
    return out


def _frage(feld: str, grund: str, vorschlag: str | None = None,
          antworten: list[str] | None = None) -> dict:
    return {"feld": feld, "grund": grund, "vorschlag": vorschlag,
            "antwortmoeglichkeiten": antworten or list(_STANDARD_ANTWORTEN)}


_LAND_NAMEN = {"DE": "DE", "DEUTSCHLAND": "DE", "GERMANY": "DE",
              "CH": "CH", "SCHWEIZ": "CH", "SWITZERLAND": "CH"}


def _land_aus_text(wert: str) -> str | None:
    return _LAND_NAMEN.get((wert or "").strip().upper())


def _land_aus_kennung(werte: dict[str, str]) -> str | None:
    """DE/CH aus IBAN oder UID ableiten, wenn keine Land-Spalte entscheidet."""
    iban = re.sub(r"\s+", "", werte.get("iban", "")).upper()
    if iban[:2] in ("DE", "CH"):
        return iban[:2]
    ustid = norm_ustid(werte.get("ustid", ""))
    if ustid.startswith("DE"):
        return "DE"
    if ustid.startswith("CHE"):
        return "CH"
    return None


def _ustid_format_ok(land: str, ustid: str) -> bool:
    if land == "DE":
        return bool(re.fullmatch(r"DE\d{9}", re.sub(r"[\s.-]", "", ustid.upper())))
    if land == "CH":
        if not re.search(r"\bCHE\b", ustid.upper()):
            return False
        return che_valid(norm_ustid(ustid))
    return True


def _plz_format_ok(land: str, plz: str) -> bool:
    if land == "DE":
        return bool(re.fullmatch(r"\d{5}", plz.strip()))
    if land == "CH":
        return bool(re.fullmatch(r"\d{4}", plz.strip()))
    return True


def _kreditor_land(art: str, werte: dict[str, str]) -> tuple[str | None, dict | None]:
    """-> (Land "DE"/"CH"/None, Frage falls unklar). Fuer ein Blatt mit
    fester Art (``kreditor_de``/``kreditor_ch``) entscheidet die Blatt-Art;
    eine abweichende Land-Spalte wird als eigene Frage gemeldet, nicht
    stillschweigend uebernommen."""
    fest = {ART_KREDITOR_DE: "DE", ART_KREDITOR_CH: "CH"}.get(art)
    land_spalte = _land_aus_text(werte.get("land", "")) if werte.get("land") else None
    if fest:
        if werte.get("land") and land_spalte is None:
            return fest, _frage("land", f"Land-Spalte nicht erkannt: '{werte['land']}'")
        if land_spalte and land_spalte != fest:
            return fest, _frage(
                "land",
                f"Land-Spalte sagt {land_spalte}, Blatt ist als "
                f"Kreditor {fest} eingeordnet -- bitte entscheiden")
        return fest, None
    if land_spalte:
        return land_spalte, None
    aus_kennung = _land_aus_kennung(werte)
    if aus_kennung:
        return aus_kennung, _frage(
            "land",
            f"Land nicht angegeben, aus IBAN/UID auf {aus_kennung} geschlossen -- bitte bestaetigen")
    return None, _frage("land", "Land nicht erkannt (weder Land-Spalte noch IBAN/UID "
                                "verweisen auf DE oder CH)")


def _kreditor_pruefen(art: str, werte: dict[str, str]) -> list[dict]:
    fragen: list[dict] = []
    land, land_frage = _kreditor_land(art, werte)
    if land_frage:
        fragen.append(land_frage)

    if not werte.get("name"):
        fragen.append(_frage("name", "Pflichtfeld fehlt: Name"))

    iban = werte.get("iban", "")
    if iban:
        kompakt = re.sub(r"\s+", "", iban).upper()
        if not iban_valid(kompakt):
            fragen.append(_frage("iban", f"IBAN-Pruefziffer ungueltig: {iban}"))
        elif land and kompakt[:2] in ("DE", "CH") and kompakt[:2] != land:
            fragen.append(_frage(
                "iban", f"Land passt nicht zur IBAN: Kreditor {land}, IBAN {kompakt[:2]}"))

    ustid = werte.get("ustid", "")
    if ustid and land:
        if not _ustid_format_ok(land, ustid):
            muster = "DE + 9 Ziffern" if land == "DE" else "CHE-123.456.789 MWST/TVA/IVA"
            fragen.append(_frage("ustid", f"USt-IdNr/UID-Format ungueltig fuer {land} "
                                          f"(erwartet {muster}): {ustid}"))
        else:
            nu = norm_ustid(ustid)
            if land == "DE" and nu.startswith("CHE"):
                fragen.append(_frage("ustid", f"Land passt nicht zur UID: Kreditor DE, UID {ustid}"))
            elif land == "CH" and nu.startswith("DE"):
                fragen.append(_frage("ustid", f"Land passt nicht zur UID: Kreditor CH, UID {ustid}"))

    plz = werte.get("plz", "")
    if plz and land and not _plz_format_ok(land, plz):
        erwartet = "5-stellig" if land == "DE" else "4-stellig"
        fragen.append(_frage("plz", f"PLZ-Format fuer {land} erwartet {erwartet}: '{plz}'"))

    return fragen


def _mandant_pruefen(werte: dict[str, str]) -> list[dict]:
    fragen = []
    if not werte.get("name") and werte.get("mandant_verweis"):
        # Blatt hat nur eine Spalte "Mandant"/"Gesellschaft" -- das ist hier
        # der eigene Name, kein Verweis auf einen anderen Mandanten.
        werte["name"] = werte.pop("mandant_verweis")
    if not werte.get("name"):
        fragen.append(_frage("name", "Pflichtfeld fehlt: Name/Mandant"))
    return fragen


def _postfach_pruefen(werte: dict[str, str]) -> list[dict]:
    fragen = []
    if not werte.get("email"):
        fragen.append(_frage("email", "Pflichtfeld fehlt: E-Mail-Adresse/Benutzer"))
    if not werte.get("mandant_verweis"):
        fragen.append(_frage("mandant_verweis", "Pflichtfeld fehlt: Mandant (jedes Postfach "
                                                "gehoert zu genau einem Mandanten)"))
    return fragen


# ---- Zeilen bauen -----------------------------------------------------------

@dataclass
class Zeilen:
    blatt: str
    art: str
    rows: list[dict] = field(default_factory=list)


def zeilen_aufbauen(store, blatt: str, headers: list[str], data_rows: list[list[str]],
                    mapping: dict[str, str | None], art: str) -> list[dict]:
    """Je Datenzeile: ``{blatt, zeile, art, werte, status, fragen, schluessel}``.
    Bibliotheks-/Dubletten-Pruefung (zeilenuebergreifend) macht ``vorschau()``
    danach -- hier nur die reine Zeilenpruefung."""
    out: list[dict] = []
    for i, row in enumerate(data_rows, start=1):
        werte = _zeile_zu_werten(headers, row, mapping)
        rohwerte = {h: v for h, v in zip(headers, row) if (v or "").strip()}
        schluessel = f"{blatt}:{i}"
        zeilen_art = art
        fragen: list[dict] = []
        if art == ART_GEMISCHT_LAND:
            land_spalte = _land_aus_text(werte.get("land", ""))
            if land_spalte:
                zeilen_art = ART_KREDITOR_DE if land_spalte == "DE" else ART_KREDITOR_CH
            else:
                aus_kennung = _land_aus_kennung(werte)
                if aus_kennung:
                    zeilen_art = ART_KREDITOR_DE if aus_kennung == "DE" else ART_KREDITOR_CH
                    fragen.append(_frage(
                        "land",
                        f"Land nicht angegeben, aus IBAN/UID auf {aus_kennung} "
                        "geschlossen -- bitte bestaetigen"))
                else:
                    zeilen_art = ART_UNKLAR
        if zeilen_art == ART_UNKLAR:
            fragen.append(_frage(
                "art", "Art der Zeile nicht erkannt (weder Land-Spalte noch "
                      "IBAN/UID verweisen auf DE oder CH, Blatt-Art unklar) -- "
                      "bitte Kreditor DE/CH, Mandant oder Postfach waehlen",
                antworten=["als Kreditor DE übernehmen", "als Kreditor CH übernehmen",
                          "Zeile weglassen"]))
        elif zeilen_art in ARTEN_KREDITOR:
            fragen += _kreditor_pruefen(zeilen_art, werte)
        elif zeilen_art == ART_MANDANT:
            fragen += _mandant_pruefen(werte)
        elif zeilen_art == ART_POSTFACH:
            fragen += _postfach_pruefen(werte)

        rot = zeilen_art == ART_UNKLAR or any(
            f["feld"] in ("name", "email", "mandant_verweis") and "Pflichtfeld fehlt" in f["grund"]
            for f in fragen)
        status = STATUS_ROT if rot else (STATUS_GELB if fragen else STATUS_GRUEN)
        out.append({
            "blatt": blatt, "zeile": i, "art": zeilen_art, "werte": werte,
            "rohwerte": rohwerte, "status": status, "fragen": fragen,
            "schluessel": schluessel,
        })
    return out


# ---- Dublette/Konflikt (zeilenuebergreifend + gegen die Bibliothek) -------

def _kreditor_dubletten_schluessel(werte: dict[str, str]) -> set[str]:
    keys = set()
    if werte.get("ustid"):
        keys.add("ustid:" + norm_ustid(werte["ustid"]))
    if werte.get("iban"):
        keys.add("iban:" + re.sub(r"\s+", "", werte["iban"]).upper())
    if werte.get("name"):
        keys.add("name:" + norm(werte["name"]))
    return keys


def _markiere_dubletten(zeilen: list[dict]) -> None:
    """Zwei Zeilen DIESES Imports mit gleichem Namen/UID/IBAN -> beide gelb,
    mit Frage und Verweis aufeinander (nie stillschweigend die eine
    bevorzugen)."""
    von_schluessel: dict[str, list[dict]] = {}
    for z in zeilen:
        if z["art"] not in ARTEN_KREDITOR:
            continue
        for k in _kreditor_dubletten_schluessel(z["werte"]):
            von_schluessel.setdefault(k, []).append(z)
    gemeldet: set[tuple[str, str]] = set()
    for k, treffer in von_schluessel.items():
        if len(treffer) < 2:
            continue
        for z in treffer:
            paar = (z["schluessel"], k)
            if paar in gemeldet:
                continue
            gemeldet.add(paar)
            andere = ", ".join(sorted(a["schluessel"] for a in treffer if a is not z))
            z["fragen"].append(_frage(
                "dublette", f"Dublette in dieser Datei ({k.split(':', 1)[0]}): "
                           f"auch in Zeile {andere}",
                antworten=["beide übernehmen (sind unterschiedlich)",
                          "diese Zeile übernehmen, andere weglassen",
                          "Zeile weglassen"]))
            if z["status"] != STATUS_ROT:
                z["status"] = STATUS_GELB


def _supplier_feld_diff(sup, werte: dict[str, str]) -> list[str]:
    """Welche Felder weichen zwischen vorhandenem Lieferanten und den neuen
    Werten ab (beide Seiten nicht leer)? Leer wird nie als Widerspruch
    gewertet -- das ist eine reine Ergaenzung."""
    diffs = []
    paare = [
        ("name", sup.name, werte.get("name", "")),
        ("iban", ";".join(sup.ibans), werte.get("iban", "")),
        ("ustid", ";".join(sup.ustids), werte.get("ustid", "")),
        ("steuernummer", sup.creditor_id, werte.get("steuernummer", "")),
        ("bic", sup.bic, werte.get("bic", "")),
    ]
    for feld, alt, neu in paare:
        if not alt or not neu:
            continue
        if feld in ("iban",):
            alt_n = re.sub(r"\s+", "", alt).upper()
            neu_n = re.sub(r"\s+", "", neu).upper()
            if neu_n not in alt_n.split(";"):
                diffs.append(feld)
        elif feld == "ustid":
            alt_n = {norm_ustid(x) for x in alt.split(";") if x}
            if norm_ustid(neu) not in alt_n:
                diffs.append(feld)
        elif norm(alt) != norm(neu):
            diffs.append(feld)
    return diffs


def _konflikt_mit_bibliothek(store, zeile: dict) -> None:
    """Treffer gegen einen bestehenden Lieferanten/Mandanten/Postfach MIT
    abweichenden Werten -> Frage "alt oder neu?", nie stillschweigend
    ueberschreiben. Ein Treffer OHNE Abweichung (reine Ergaenzung leerer
    Felder) bleibt gruen."""
    werte = zeile["werte"]
    if zeile["art"] in ARTEN_KREDITOR:
        treffer = None
        for s in store.suppliers():
            if werte.get("ustid") and norm_ustid(werte["ustid"]) in {norm_ustid(u) for u in s.ustids}:
                treffer = s
                break
            if werte.get("iban") and re.sub(r"\s+", "", werte["iban"]).upper() in \
                    {re.sub(r"\s+", "", i).upper() for i in s.ibans}:
                treffer = s
                break
        if treffer is None:
            return
        diffs = _supplier_feld_diff(treffer, werte)
        if not diffs:
            return
        zeile["fragen"].append(_frage(
            "bibliothek", f"Gibt es schon in der Bibliothek ({treffer.id}) mit "
                         f"anderen Werten bei: {', '.join(diffs)} -- alt oder neu?",
            antworten=["bestehenden Datensatz aktualisieren (neue Werte übernehmen)",
                      "als neuen, separaten Datensatz anlegen",
                      "Zeile weglassen (nichts ändern)"]))
        if zeile["status"] != STATUS_ROT:
            zeile["status"] = STATUS_GELB
    elif zeile["art"] == ART_MANDANT:
        for a in store.addresses():
            if norm(a.name) == norm(werte.get("name", "")) and a.name:
                if a.lines and werte.get("strasse") and norm(" ".join(a.lines)) != \
                        norm(werte.get("strasse", "")):
                    zeile["fragen"].append(_frage(
                        "bibliothek", f"Mandant '{a.name}' gibt es schon ({a.id}) mit "
                                     "anderer Anschrift -- alt oder neu?",
                        antworten=["bestehenden Mandanten aktualisieren",
                                  "als neuen, separaten Mandanten anlegen",
                                  "Zeile weglassen"]))
                    if zeile["status"] != STATUS_ROT:
                        zeile["status"] = STATUS_GELB
                break


def _mandant_referenz_pruefen(zeile: dict, alle_mandanten_namen: set[str], store) -> None:
    ref = zeile["werte"].get("mandant_verweis")
    if not ref or zeile["art"] not in (ART_KREDITOR_DE, ART_KREDITOR_CH, ART_POSTFACH):
        return
    bekannt = {norm(n) for n in alle_mandanten_namen} | {norm(a.name) for a in store.addresses()} \
        | {norm(a.id) for a in store.addresses()}
    if norm(ref) not in bekannt:
        zeile["fragen"].append(_frage(
            "mandant_verweis", f"Mandant-Verweis '{ref}' nicht auffindbar (weder in "
                               "dieser Datei noch in der Bibliothek)",
            antworten=["so übernehmen (als Text, ohne Verknüpfung)",
                      "korrigieren auf …", "Zeile weglassen"]))
        if zeile["status"] != STATUS_ROT:
            zeile["status"] = STATUS_GELB


# ---- Antworten einmischen ---------------------------------------------------

def _antwort_schluessel(zeile_schluessel: str, feld: str) -> str:
    return f"{zeile_schluessel}|{feld}"


def _antworten_einmischen(zeile: dict, antworten: dict) -> None:
    """``antworten``: {"<blatt>:<zeile>|<feld>": {"antwort": ..., "wert": ...}}.
    Setzt ``zeile['beantwortet']`` (ALLE Fragen der Zeile beantwortet? -- bei
    keiner offenen Frage automatisch ``True``, das macht gruene Zeilen ohne
    Sonderfall "bereit"), wendet "korrigieren" auf ``werte`` an und loest die
    paar Antworten auf, die mehr als nur "Wert so uebernehmen" bedeuten
    (Zeile weglassen, Art waehlen, als neuer Datensatz anlegen). Rot vs. gelb
    ist reine Anzeige-Schwere -- fuer die Uebernahme zaehlt ausschliesslich
    "beantwortet und nicht weggelassen" (SPEC: "nur gruene + von Aaron
    beantwortete Zeilen"), unabhaengig von der Ampelfarbe."""
    alle = True
    weglassen = False
    neu_anlegen = False
    for f in zeile["fragen"]:
        a = antworten.get(_antwort_schluessel(zeile["schluessel"], f["feld"]))
        if not a:
            alle = False
            continue
        antwort = (a.get("antwort") or "").strip()
        f["beantwortet"] = antwort
        low = antwort.lower()
        if low.startswith("korrigieren") and a.get("wert") is not None:
            zeile["werte"][f["feld"]] = a["wert"]
        elif "als neuen" in low or "separaten" in low:
            neu_anlegen = True
        elif f["feld"] == "art" and "kreditor de" in low:
            zeile["art"] = ART_KREDITOR_DE
        elif f["feld"] == "art" and "kreditor ch" in low:
            zeile["art"] = ART_KREDITOR_CH
        elif "zeile weglassen" in low:
            weglassen = True
    zeile["beantwortet"] = alle
    zeile["weglassen"] = weglassen
    zeile["neu_anlegen"] = neu_anlegen


# ---- Gesamtvorschau ---------------------------------------------------------

def vorschau(store, sheets: dict[str, dict], antworten: dict | None = None) -> dict:
    """``sheets``: {blattname: {"headers": [...], "rows": [[...], ...],
    "mapping": {...} oder None, "art": "..." oder None}}. Ohne ``mapping``/
    ``art`` werden sie vorgeschlagen (ggf. aus dem Layout-Gedaechtnis).
    -> {"blaetter": {name: {"mapping", "art", "art_vorschlag", "zeilen"}},
       "zusammenfassung": {gruen, gelb, rot}}."""
    antworten = antworten or {}
    blaetter: dict[str, dict] = {}
    alle_zeilen: list[dict] = []
    mandanten_namen: set[str] = set()

    for name, s in sheets.items():
        headers = s["headers"]
        gelernt = gelernte_zuordnung(store, headers) if not s.get("mapping") else None
        mapping = s.get("mapping") or (gelernt or {}).get("mapping") or spalten_vorschlag(headers)
        art_vorg = art_vorschlag(name, mapping)
        art = s.get("art") or (gelernt or {}).get("art") or art_vorg
        zeilen = zeilen_aufbauen(store, name, headers, s["rows"], mapping, art)
        blaetter[name] = {"mapping": mapping, "art": art, "art_vorschlag": art_vorg,
                          "gelernt": gelernt is not None, "zeilen": zeilen}
        alle_zeilen += zeilen
        if art == ART_MANDANT:
            mandanten_namen |= {z["werte"].get("name", "") for z in zeilen if z["werte"].get("name")}

    _markiere_dubletten(alle_zeilen)
    for z in alle_zeilen:
        _konflikt_mit_bibliothek(store, z)
        _mandant_referenz_pruefen(z, mandanten_namen, store)
        _antworten_einmischen(z, antworten)

    zaehler = {STATUS_GRUEN: 0, STATUS_GELB: 0, STATUS_ROT: 0}
    bereit = 0
    for z in alle_zeilen:
        zaehler[z["status"]] += 1
        if not z["weglassen"] and z["beantwortet"]:
            bereit += 1
    return {"blaetter": blaetter, "zusammenfassung": {**zaehler, "bereit_zum_uebernehmen": bereit}}


# ---- Anwenden ---------------------------------------------------------------

def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "x"


def _supplier_id(werte: dict[str, str]) -> str:
    if werte.get("kreditorennummer"):
        return werte["kreditorennummer"].strip()
    if werte.get("ustid"):
        return norm_ustid(werte["ustid"])
    if werte.get("iban"):
        return re.sub(r"\s+", "", werte["iban"]).upper()
    return "kreditor-" + _slug(werte.get("name", ""))


def _address_id(werte: dict[str, str]) -> str:
    if werte.get("mandant_verweis"):
        return _slug(werte["mandant_verweis"])
    return "mandant-" + _slug(werte.get("name", ""))


def _anwenden_kreditor(store, zeile: dict, operator: str) -> str:
    from .identify import Supplier

    werte = zeile["werte"]
    sid = _supplier_id(werte)
    if zeile.get("neu_anlegen") and store.supplier(sid) is not None:
        # Mensch hat bei der Bibliotheks-Konflikt-Frage "als neuen, separaten
        # Datensatz anlegen" gewaehlt -- der gefundene Treffer bleibt
        # unberuehrt, dieser Kreditor bekommt eine eigene, nicht
        # kollidierende Id statt den Treffer zu ueberschreiben.
        sid = f"{sid}-{zeile['schluessel'].replace(':', '-')}"
    sup = store.supplier(sid)
    if sup is None:
        sup = Supplier(id=sid, name=werte.get("name", ""))
    if werte.get("name"):
        sup.name = werte["name"]
    if werte.get("strasse") or werte.get("plz") or werte.get("ort"):
        zeile_adresse = [x for x in (
            werte.get("strasse", ""),
            " ".join(x for x in (werte.get("plz", ""), werte.get("ort", "")) if x),
        ) if x]
        if zeile_adresse:
            sup.address_lines = zeile_adresse
    if werte.get("ustid"):
        sup.ustids = sorted(set(sup.ustids) | {werte["ustid"]})
    if werte.get("iban"):
        sup.ibans = sorted(set(sup.ibans) | {re.sub(r"\s+", "", werte["iban"]).upper()})
    if werte.get("bic"):
        sup.bic = werte["bic"]
    if werte.get("steuernummer"):
        sup.creditor_id = werte["steuernummer"]
    if werte.get("kreditorennummer"):
        sup.external_id = werte["kreditorennummer"]
    sup.currency = "CHF" if zeile["art"] == ART_KREDITOR_CH else "EUR"
    if werte.get("mandant_verweis"):
        addr_id = _slug(werte["mandant_verweis"])
        if addr_id not in sup.addresses:
            sup.addresses = sorted(set(sup.addresses) | {addr_id})
    store.save_supplier(sup)
    store.audit("stammdaten_kreditor_import", {"id": sid, "blatt": zeile["blatt"],
                                               "zeile": zeile["zeile"]}, operator)
    return sid


def _anwenden_mandant(store, zeile: dict, operator: str) -> str:
    from .billing import BillingAddress

    werte = zeile["werte"]
    aid = _address_id(werte)
    if zeile.get("neu_anlegen") and store.address(aid) is not None:
        aid = f"{aid}-{zeile['schluessel'].replace(':', '-')}"
    addr = store.address(aid)
    if addr is None:
        addr = BillingAddress(id=aid, name=werte.get("name", ""))
    if werte.get("name"):
        addr.name = werte["name"]
        if werte["name"] not in addr.match_terms:
            addr.match_terms = sorted(set(addr.match_terms) | {werte["name"]})
    zeile_adresse = [x for x in (
        werte.get("strasse", ""),
        " ".join(x for x in (werte.get("plz", ""), werte.get("ort", "")) if x),
    ) if x]
    if zeile_adresse:
        addr.lines = zeile_adresse
        addr.match_terms = sorted(set(addr.match_terms) | set(zeile_adresse))
    if werte.get("mandant_verweis") and werte.get("mandant_verweis") != werte.get("name"):
        addr.company_code = werte["mandant_verweis"]
    store.save_address(addr)
    store.audit("stammdaten_mandant_import", {"id": aid, "blatt": zeile["blatt"],
                                              "zeile": zeile["zeile"]}, operator)
    return aid


def _anwenden_postfach(store, zeile: dict, operator: str) -> str:
    from . import secrets as secrets_mod
    from .postfach import Postfach

    werte = zeile["werte"]
    ref = werte.get("mandant_verweis", "")
    mandant_id = ""
    for a in store.addresses():
        if norm(a.name) == norm(ref) or norm(a.id) == norm(ref):
            mandant_id = a.id
            break
    pid = _slug(werte.get("email") or ref or werte.get("name", ""))
    pf = store.postfach(pid)
    if pf is None:
        pf = Postfach(id=pid)
    pf.mandant_id = mandant_id
    pf.mandant_name = ref
    pf.user = werte.get("email", pf.user)
    if werte.get("server"):
        server = werte["server"]
        if ":" in server:
            host, port = server.rsplit(":", 1)
            pf.host = host
            try:
                pf.port = int(port)
            except ValueError:
                pass
        else:
            pf.host = server
    if werte.get("ordner"):
        pf.source_folder = werte["ordner"]
    store.save_postfach(pf)
    if werte.get("passwort"):
        secrets_mod.set_password(store, pid, werte["passwort"], operator)
    store.audit("stammdaten_postfach_import", {"id": pid, "blatt": zeile["blatt"],
                                               "zeile": zeile["zeile"]}, operator)
    return pid


def anwenden(store, sheets: dict[str, dict], antworten: dict | None, operator: str = "") -> dict:
    """Komplette Pruefung NEU (nie dem Client vertrauen) -- uebernimmt nur
    gruene Zeilen und gelbe Zeilen mit vollstaendig beantworteten Fragen,
    die nicht per "Zeile weglassen" ausgeschlossen sind."""
    vor = vorschau(store, sheets, antworten)
    ergebnis = {"kreditoren": 0, "mandanten": 0, "postfaecher": 0,
               "uebersprungen": 0, "passwoerter_gesetzt": 0}
    for blatt in vor["blaetter"].values():
        for z in blatt["zeilen"]:
            if z["weglassen"] or not (
                    z["status"] == STATUS_GRUEN or (z["status"] == STATUS_GELB and z["beantwortet"])):
                ergebnis["uebersprungen"] += 1
                continue
            if z["art"] in ARTEN_KREDITOR:
                _anwenden_kreditor(store, z, operator)
                ergebnis["kreditoren"] += 1
            elif z["art"] == ART_MANDANT:
                _anwenden_mandant(store, z, operator)
                ergebnis["mandanten"] += 1
            elif z["art"] == ART_POSTFACH:
                _anwenden_postfach(store, z, operator)
                ergebnis["postfaecher"] += 1
                if z["werte"].get("passwort"):
                    ergebnis["passwoerter_gesetzt"] += 1
            else:
                ergebnis["uebersprungen"] += 1
    if any(blatt["zeilen"] and any(z["werte"].get("passwort") for z in blatt["zeilen"])
          for blatt in vor["blaetter"].values()):
        ergebnis["hinweis_passwoerter"] = (
            "Die Passwörter stehen noch im Klartext in der Excel-Datei -- "
            "Datei löschen oder Passwortspalte leeren.")
    return ergebnis
