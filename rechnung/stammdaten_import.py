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

4. **Bekannte Layouts (16.10.2026, Aarons echte Listen)**: Kreditoren
   (Konto/Name1/Name2/...), Mandanten (Mandanten Nr./Bezeichnung/WJ/LM) und
   Postfaecher (MDT/E-Mail/Passwort/Bezeichnung/Name) werden an ihren
   Ueberschriften erkannt (``BEKANNTE_LAYOUTS``) -- die Spaltenzuordnung wird
   dann direkt vorgeschlagen, die Zeilenregeln (Name1+Name2, Kuerzel, PLZ)
   greifen automatisch. Weiterhin nur ein Vorschlag mit Vorschau; was unklar
   ist, bleibt eine Frage.

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
from .xlsx import text_saeubern

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
    "plz": ["plz", "postleitzahl", "zip", "zip code", "postal code", "postcode",
            "plzort", "plz ort"],
    "ort": ["ort", "stadt", "city", "town", "ortsbezeichnung"],
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
                         "lieferanten-id", "konto", "kreditorenkonto"],
    # Verweis auf einen Mandanten (bei Kreditor-/Postfach-Zeilen) ODER --
    # auf einem Mandanten-Blatt -- der eigene Mandanten-Code (s.
    # ``_mandant_zeile``). Dieselbe Spaltenueberschrift ("Mandant",
    # "Gesellschaft", ...) kann je nach Blatt beides bedeuten.
    "mandant_verweis": ["mandant", "gesellschaft", "business unit",
                        "geschaeftsbereich", "geschäftsbereich", "company code",
                        "buchungskreis", "firma (mandant)",
                        "mandant/gesellschaft", "kostenstelle", "mandanten nr",
                        "mandanten nummer", "mandant nr", "mandantennummer", "mdt"],
    "email": ["email", "e-mail", "e-mail-adresse", "mail", "benutzer", "user",
              "login", "postfach", "postfach-adresse", "mailadresse"],
    "passwort": ["passwort", "password", "kennwort", "pwd", "pass"],
    "server": ["server", "host", "imap", "imap-server", "imap server",
              "mailserver"],
    "ordner": ["ordner", "folder", "quellordner"],
    # Aarons echte Kreditorenliste (15./16.10.2026): Name steht in zwei festlangen
    # Feldern; "Bezeichnung 1" ist nur die auf 40 Zeichen gekuerzte Fassung
    # davon und dient ausschliesslich zur Gegenpruefung (siehe
    # ``_kreditor_aufbereiten``), nie als Name.
    "name1": ["name1", "name 1"],
    "name2": ["name2", "name 2"],
    "bezeichnung1": ["bezeichnung 1", "bezeichnung1"],
    # Postfach-Blatt: aus "Bezeichnung" nur das Kuerzel, aus "Name" der volle
    # Wert (Entscheidung Aaron 15.10.2026, 13:46) -- nur ueber das bekannte
    # Layout vorgeschlagen, daher keine allgemeinen Synonyme.
    "postfach_kuerzel": [],
    "postfach_name": [],
    # Rohwert der Spalte als Notiz ("Ueberschrift: Wert") -- nichts geht verloren,
    # nichts bekommt eine eigene Bedeutung (z. B. SammelKt).
    "notiz": ["sammelkt", "sammelkonto", "sammel konto", "sammelkto"],
}

# Ausdrueckliche Wahl "Spalte ignorieren" -- ``None`` heisst dagegen "noch nicht
# entschieden" (das Web-UI laesst nicht zu, dass eine Spalte so stehen bleibt).
IGNORIEREN = "ignorieren"

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


# Aarons echte Listen (docs/uebergabe-laptop/echte-spaltenkoepfe-15-10.md).
# Schluessel = ``_norm_header`` der Ueberschrift. ``pflicht``: diese
# Ueberschriften muessen alle vorhanden sein, weitere Spalten sind erlaubt
# (fuer sie gilt der allgemeine Vorschlag). WJ/LM: Aaron weiss nicht, was sie
# bedeuten -> ignorieren (Entscheidung 15.10.2026, 13:42).
BEKANNTE_LAYOUTS: list[dict] = [
    {"name": "Kreditoren (Konto / Name1 / Name2 ...)",
     "pflicht": {"konto", "name1", "name2"},
     "mapping": {"konto": "kreditorennummer", "bezeichnung 1": "bezeichnung1",
                 "name1": "name1", "name2": "name2", "sammelkt": "notiz",
                 "strasse": "strasse", "land": "land", "plzort": "plz",
                 "ortsbezeichnung": "ort"}},
    {"name": "Mandanten (Mandanten Nr. / Bezeichnung / WJ / LM)",
     "pflicht": {"mandanten nr", "bezeichnung"},
     "ohne": {"e-mail", "passwort"},
     "mapping": {"mandanten nr": "mandant_verweis", "bezeichnung": "name",
                 "wj": IGNORIEREN, "lm": IGNORIEREN}},
    {"name": "Postfächer (MDT / E-Mail / Passwort / Bezeichnung / Name)",
     "pflicht": {"mdt", "e-mail", "passwort"},
     "mapping": {"mdt": "mandant_verweis", "e-mail": "email", "passwort": "passwort",
                 "bezeichnung": "postfach_kuerzel", "name": "postfach_name"}},
]


def layout_erkennen(headers: list[str]) -> dict | None:
    """-> das bekannte Layout, zu dem die Ueberschriften passen, oder ``None``."""
    norm_set = {_norm_header(h) for h in headers if (h or "").strip()}
    for lay in BEKANNTE_LAYOUTS:
        if lay["pflicht"] <= norm_set and not (lay.get("ohne", set()) & norm_set):
            return lay
    return None


def spalten_vorschlag(headers: list[str]) -> dict[str, str | None]:
    """Jede Spaltenueberschrift -> vorgeschlagenes Zielfeld (oder ``None``).
    Passt die Kopfzeile zu einer von Aarons bekannten Listen, gilt deren feste
    Zuordnung; weitere Spalten bekommen den allgemeinen Wortlisten-Vorschlag."""
    out = {h: vorschlag_fuer_spalte(h) for h in headers}
    lay = layout_erkennen(headers)
    if lay:
        for h in headers:
            ziel = lay["mapping"].get(_norm_header(h))
            if ziel:
                out[h] = ziel
    return out


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
    if {"mandant_verweis", "name"} <= felder and not (
            {"land", "strasse", "plz", "ort", "iban", "ustid", "kreditorennummer",
             "email", "name1", "name2"} & felder):
        return ART_MANDANT   # nur Mandanten-Nr. + Name, z. B. Blatt "Tabelle1"
    if "land" in felder:
        return ART_GEMISCHT_LAND
    if "iban" in felder or "ustid" in felder or "kreditorennummer" in felder:
        return ART_GEMISCHT_LAND if "land" in felder else ART_UNKLAR
    return ART_UNKLAR


# ---- Hilfsfunktionen --------------------------------------------------------

def _zeile_zu_werten_ex(headers: list[str], row: list[str],
                        mapping: dict[str, str | None]):
    """-> (werte, kollisionen). Zwei Spalten, die auf dasselbe Feld zeigen und
    verschiedene Werte tragen, werden NICHT still ueberschrieben, sondern als
    Kollision gemeldet (der erste Wert bleibt bis zur Antwort stehen).
    ``ignorieren`` verwirft die Spalte; ``notiz`` sammelt "Ueberschrift: Wert"."""
    out: dict[str, str] = {}
    quelle: dict[str, str] = {}
    kollisionen: list[tuple] = []
    notizen: list[str] = []
    for h, v in zip(headers, row):
        feld = mapping.get(h)
        # Passwoerter nur trimmen: NFKC/Whitespace-Zusammenfassen wuerde ein
        # Passwort veraendern (z. B. zwei Leerzeichen hintereinander).
        v = (v or "").strip() if feld == "passwort" else text_saeubern(v)
        if not feld or feld == IGNORIEREN or not v:
            continue
        if feld == "notiz":
            notizen.append(f"{h}: {v}")
            continue
        if feld in out and out[feld] != v:
            kollisionen.append((feld, quelle[feld], out[feld], h, v))
            continue
        out[feld] = v
        quelle[feld] = h
    if notizen:
        out["notiz"] = "\n".join(notizen)
    return out, kollisionen


def _zeile_zu_werten(headers: list[str], row: list[str],
                     mapping: dict[str, str | None]) -> dict[str, str]:
    return _zeile_zu_werten_ex(headers, row, mapping)[0]


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
        vorschlag = "0" + plz.strip() if land == "DE" and re.fullmatch(r"\d{4}", plz.strip()) else None
        fragen.append(_frage("plz", f"PLZ-Format fuer {land} erwartet {erwartet}: '{plz}'"
                                    + (" (führende Null verloren?)" if vorschlag else ""),
                             vorschlag=vorschlag))

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
    if werte.get("email") and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", werte["email"]):
        fragen.append(_frage("email", f"E-Mail-Adresse sieht ungültig aus: '{werte['email']}'"))
    if not werte.get("mandant_verweis"):
        fragen.append(_frage("mandant_verweis", "Pflichtfeld fehlt: Mandant (jedes Postfach "
                                                "gehoert zu genau einem Mandanten)"))
    return fragen


# ---- Zeilenregeln fuer Aarons echte Listen -----------------------------------

def _ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _kuerzel_und_klartext(s: str) -> tuple[str, str]:
    """"ABC - Beispiel-Mandant" -> ("ABC", "Beispiel-Mandant"). Ohne Trenner
    (" - ", " – ", " — ") gibt es kein Kuerzel: ("", Text)."""
    s = _ws(s)
    for sep in (" - ", " – ", " — "):
        if sep in s:
            k, rest = s.split(sep, 1)
            return k.strip(), rest.strip()
    return "", s


def _kuerzel_aus_notiz(notes: str) -> str:
    m = re.search(r"Kürzel:\s*(\S+)", notes or "")
    return m.group(1) if m else ""


def _mandant_key(s: str) -> str:
    """Vergleichsschluessel fuer Mandanten-Nummern: ``001`` und ``1`` sind
    derselbe Mandant (Excel verliert fuehrende Nullen bei Zahlenzellen)."""
    k = norm(s)
    return (k.lstrip("0") or "0") if k.isdigit() else k


def _notizen_ergaenzen(alt: str, neu: str) -> str:
    """Zeilenweise anhaengen, was noch nicht drinsteht -- mehrfaches Einspielen
    derselben Datei dupliziert nichts."""
    zeilen = (alt or "").splitlines()
    for z in (neu or "").splitlines():
        if z.strip() and z not in zeilen:
            zeilen.append(z)
    return "\n".join(zeilen).strip()


def _kreditor_aufbereiten(werte: dict[str, str], fragen: list[dict],
                          hinweise: list[str]) -> None:
    """Name1 + Name2 -> ``name``. "Bezeichnung 1" (auf 40 Zeichen gekuerzt) wird
    nur zur Gegenpruefung benutzt: passt sie zu keiner Zusammensetzung, ist es
    eine Frage -- nie ein stilles Raten."""
    n1, n2 = _ws(werte.pop("name1", "")), _ws(werte.pop("name2", ""))
    bez = _ws(werte.pop("bezeichnung1", ""))
    if not (n1 or n2) or werte.get("name"):
        return
    teile = [x for x in (n1, n2) if x]
    mit, ohne, b = " ".join(teile), "".join(teile), bez.lower()

    def passt(kandidat: str) -> bool:
        return not b or kandidat.lower().startswith(b)

    name, offen = mit, False
    if len(teile) == 1:
        offen = not passt(mit)
    elif not passt(mit):
        if passt(ohne):
            name = ohne
            hinweise.append("Name1 und Name2 ohne Leerzeichen zusammengesetzt "
                            "(nur so passt die Zeile zu „Bezeichnung 1“)")
        else:
            offen = True
    werte["name"] = name
    if offen:
        fragen.append(_frage(
            "name", f"„Bezeichnung 1“ ('{bez}') passt nicht zu Name1 + Name2 ('{mit}') "
                    "-- bitte den richtigen Namen angeben", vorschlag=mit))


_PLZ_ORT_RE = re.compile(r"^(?:[A-Za-z]{1,3}[- ])?(\d{4,5})\s+(\S.*)$")


def _plz_aufbereiten(werte: dict[str, str], fragen: list[dict], hinweise: list[str],
                     fuehrende_null_ok: bool = False) -> bool:
    """Spalte "PlzOrt" kann nur die PLZ ("12345") oder PLZ + Ort enthalten. Steht
    ein Ort darin, wird er mit "Ortsbezeichnung" abgeglichen (Widerspruch = Frage).
    ``fuehrende_null_ok`` (DE-Kreditor, Land sicher DE): eine rein numerische,
    genau 4-stellige PLZ hat in Excel ihre fuehrende Null verloren -- einzige
    Deutung, daher "0" voranstellen mit sichtbarem Hinweis. -> True, wenn ergaenzt."""
    plz = werte.get("plz", "")
    if plz and not plz.isdigit():
        m = _PLZ_ORT_RE.match(plz)
        if not m:
            return False   # ``_kreditor_pruefen`` fragt wegen des Formats
        werte["plz"], ort_aus_plz = m.group(1), m.group(2).strip()
        if not werte.get("ort"):
            werte["ort"] = ort_aus_plz
            hinweise.append(f"Ort '{ort_aus_plz}' aus der PLZ-Spalte übernommen")
        elif norm(werte["ort"]) != norm(ort_aus_plz):
            fragen.append(_frage(
                "ort", f"PLZ-Spalte nennt '{ort_aus_plz}', Ortsbezeichnung '{werte['ort']}' "
                       "-- bitte entscheiden", vorschlag=ort_aus_plz))
    plz = werte.get("plz", "")
    if fuehrende_null_ok and re.fullmatch(r"\d{4}", plz):
        werte["plz"] = "0" + plz
        hinweise.append(f"führende Null ergänzt (Excel hatte sie entfernt): {plz} -> 0{plz}")
        return True
    return False


def _postfach_aufbereiten(werte: dict[str, str], fragen: list[dict]) -> None:
    """Aus "Bezeichnung" nur das Kuerzel (steht vor " - "); ohne Trenner nur,
    wenn die Zelle ein einzelnes Wort ist -- sonst Frage mit Vorschlag."""
    roh = werte.pop("postfach_kuerzel", "")
    if not roh:
        return
    k, _ = _kuerzel_und_klartext(roh)
    if not k:
        erstes = _ws(roh).split(" ")[0]
        k = erstes
        if " " in _ws(roh):
            fragen.append(_frage(
                "kuerzel", f"Bezeichnung '{roh}' enthält keinen Trenner ' - ' -- Kürzel "
                           "nicht eindeutig", vorschlag=erstes))
    werte["kuerzel"] = k


# ---- Zeilen bauen -----------------------------------------------------------

@dataclass
class Zeilen:
    blatt: str
    art: str
    rows: list[dict] = field(default_factory=list)


_TRENNER_RE = re.compile(r"[-_=*#.~+\s]+")
_SUMME_RE = re.compile(r"(summe|gesamtsumme|gesamt|zwischensumme|total|übertrag|uebertrag)\b",
                       re.IGNORECASE)
_KEIN_INHALT = frozenset({"notiz", "land"})   # allein kein Datensatz (z. B. SammelKt-Summe)
_KENNUNG = ("kreditorennummer", "mandant_verweis", "email")


def _kopfzeile_wiederholt(headers: list[str], row: list[str]) -> bool:
    """Zeile == Kopfzeile (nach Saeubern/Kleinschreibung) -- Excel-Seitenumbruch
    oder zusammenkopierte Listen wiederholen die Ueberschriften mitten in den Daten."""
    soll = [text_saeubern(h).casefold() for h in headers]
    ist = [text_saeubern(v).casefold() for v in row][:len(soll)]
    ist += [""] * (len(soll) - len(ist))
    return any(soll) and ist == soll


def _leer_oder_summe(headers: list[str], row: list[str], werte: dict[str, str]) -> bool:
    """Trenn-, Summen- und Fusszeilen: kein Name und keine Kennung (Konto/Mandant/
    E-Mail) und sonst nichts Inhaltliches -- nie eine Zeile mit Konto oder IBAN."""
    zellen = [text_saeubern(v) for v in row]
    if not any(zellen) or all(not z or _TRENNER_RE.fullmatch(z) for z in zellen):
        return True
    if any(werte.get(k) for k in _KENNUNG):
        return False
    inhalt = {k for k in werte if k not in _KEIN_INHALT}
    if not inhalt:
        return True
    # "Summe: 3000" in einer Namensspalte, aber weder Konto noch sonst etwas
    name = " ".join(werte.get(k, "") for k in ("name", "name1", "name2")).strip()
    return bool(name) and bool(_SUMME_RE.match(name)) and not (inhalt - {"name", "name1", "name2",
                                                                          "bezeichnung1"})


def zeilen_aufbauen(store, blatt: str, headers: list[str], data_rows: list[list[str]],
                    mapping: dict[str, str | None], art: str,
                    uebersprungen: list[dict] | None = None) -> list[dict]:
    """Je Datenzeile: ``{blatt, zeile, art, werte, status, fragen, schluessel}``.
    Bibliotheks-/Dubletten-Pruefung (zeilenuebergreifend) macht ``vorschau()``
    danach -- hier nur die reine Zeilenpruefung. Wiederholte Kopfzeilen und
    Leer-/Summenzeilen werden nicht aufgenommen, sondern (falls ``uebersprungen``
    uebergeben) dort vermerkt: ``{blatt, zeile, grund}``."""
    out: list[dict] = []
    for i, row in enumerate(data_rows, start=1):
        if _kopfzeile_wiederholt(headers, row):
            if uebersprungen is not None:
                uebersprungen.append({"blatt": blatt, "zeile": i, "grund": "kopfzeile"})
            continue
        werte, kollisionen = _zeile_zu_werten_ex(headers, row, mapping)
        if _leer_oder_summe(headers, row, werte):
            if uebersprungen is not None:
                uebersprungen.append({"blatt": blatt, "zeile": i, "grund": "leerzeile"})
            continue
        if art in ARTEN_KREDITOR or art in (ART_GEMISCHT_LAND, ART_UNKLAR):
            from .dummy import ist_dummy_zeile
            if ist_dummy_zeile(werte):
                # Der Dummy-Lieferant gehoert dem Programm (dummy.py): nie
                # importieren, nie als Dublette/Konflikt fragen.
                if uebersprungen is not None:
                    uebersprungen.append({"blatt": blatt, "zeile": i, "grund": "dummy"})
                continue
        rohwerte = {h: text_saeubern(v) for h, v in zip(headers, row) if text_saeubern(v)}
        schluessel = f"{blatt}:{i}"
        zeilen_art = art
        fragen: list[dict] = []
        hinweise: list[str] = []
        auto: set[str] = set()
        for feld, h_alt, v_alt, h_neu, v_neu in kollisionen:
            fragen.append(_frage(
                feld, f"Zwei Spalten zeigen auf dasselbe Feld: '{h_alt}' = '{v_alt}', "
                      f"'{h_neu}' = '{v_neu}' -- bitte entscheiden", vorschlag=v_alt))
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
            _kreditor_aufbereiten(werte, fragen, hinweise)
            land_text = _land_aus_text(werte.get("land", "")) if werte.get("land") else None
            land_de_sicher = zeilen_art == ART_KREDITOR_DE and land_text in (None, "DE") and (
                art == ART_KREDITOR_DE or land_text == "DE")
            if _plz_aufbereiten(werte, fragen, hinweise, land_de_sicher):
                auto.add("fuehrende_null")
            fragen += _kreditor_pruefen(zeilen_art, werte)
        elif zeilen_art == ART_MANDANT:
            fragen += _mandant_pruefen(werte)
        elif zeilen_art == ART_POSTFACH:
            _postfach_aufbereiten(werte, fragen)
            fragen += _postfach_pruefen(werte)

        out.append({
            "blatt": blatt, "zeile": i, "art": zeilen_art, "werte": werte,
            "rohwerte": rohwerte, "fragen": fragen,
            "schluessel": schluessel, "hinweise": hinweise, "auto": auto,
        })
        out[-1]["status"] = _status_aus_fragen(out[-1])
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


def _dubletten_schluessel(z: dict) -> set[str]:
    w = z["werte"]
    if z["art"] in ARTEN_KREDITOR:
        keys = _kreditor_dubletten_schluessel(w)
        if w.get("kreditorennummer"):
            keys.add("konto:" + w["kreditorennummer"].strip())
        return keys
    if z["art"] == ART_MANDANT and w.get("mandant_verweis"):
        return {"mandant:" + _mandant_key(w["mandant_verweis"])}
    if z["art"] == ART_POSTFACH and w.get("email"):
        return {"email:" + norm(w["email"])}
    return set()


def _merge_id(werte: dict[str, str]) -> str:
    """Gemeinsame interne Lieferanten-Id eines verschmolzenen DE/CH-Paares:
    nur aus dem normalisierten Namen -- Kreditorennummer/UID/IBAN sind je
    Land verschieden und wuerden wieder zwei Ids ergeben."""
    return "kreditor-" + _slug(norm(werte.get("name", "")))


_DEDUP_ARTEN = (ART_KREDITOR_DE, ART_KREDITOR_CH, ART_MANDANT, ART_POSTFACH)
_GETRENNT_HINWEIS = ("gleicher Name wie Zeile {z}, andere Kontonummer -> getrennter "
                     "Lieferant (Entscheidung Aaron 17.10.)")


def _vgl(feld: str, v: str) -> str:
    """Vergleichswert eines Feldes (leer = ""): Gross-/Kleinschreibung,
    Leerzeichen, IBAN-/UID-/Mandantennummer-Schreibweise egal."""
    if feld == "passwort":
        return v
    if feld == "iban":
        return re.sub(r"\s+", "", v).upper()
    if feld == "ustid":
        return norm_ustid(v)
    if feld == "mandant_verweis":
        return _mandant_key(v)
    if feld == "kreditorennummer":
        return v.strip()
    return norm(v)


def _nw(werte: dict[str, str]) -> dict[str, str]:
    return {k: g for k, v in werte.items() if (g := _vgl(k, v))}


def _vertraeglich(a: dict[str, str], b: dict[str, str]) -> bool:
    """Kein Feld widerspricht: gleich oder auf einer Seite leer (wie
    ``_supplier_feld_diff`` -- leer ist nie ein Widerspruch)."""
    return all(a[k] == b[k] for k in a.keys() & b.keys())


def _status_aus_fragen(z: dict) -> str:
    rot = z["art"] == ART_UNKLAR or any(
        f["feld"] in ("name", "email", "mandant_verweis") and "Pflichtfeld fehlt" in f["grund"]
        for f in z["fragen"])
    return STATUS_ROT if rot else (STATUS_GELB if z["fragen"] else STATUS_GRUEN)


def _record(rows: list[dict]) -> dict:
    return {"rows": rows, "keeper": rows[0], "offen": set(), "getrennt": set(), "id": None}


def _record_werte(rec: dict) -> dict[str, str]:
    return rec["keeper"]["werte"]


def _record_keys(rec: dict) -> set[str]:
    k = rec["keeper"]
    return _dubletten_schluessel({"art": k["art"], "werte": k["werte"]})


def _nr(z: dict) -> str:
    return z["schluessel"]


def _zusammenfassen_identisch(keeper: dict, andere: list[dict]) -> None:
    for z in andere:
        z["zusammengefasst"] = {"art": "identisch", "mit": _nr(keeper)}
        z["fragen"] = []
        z["status"] = STATUS_GRUEN
        z["hinweise"].append(f"automatisch zusammengefasst (identisch mit Zeile {_nr(keeper)})")
    keeper["hinweise"].append("identische Zeile(n) automatisch zusammengefasst: "
                              + ", ".join(_nr(z) for z in andere))
    keeper.setdefault("identisch_mit", []).extend(_nr(z) for z in andere)


def _merge_records(recs: list[dict], ordnung: dict[str, int]) -> dict:
    """Mehrere vertraegliche Datensaetze -> EINER (Vereinigung der Felder, der
    erste bleibt "Hauptzeile"); die uebrigen Zeilen werden nicht uebernommen."""
    rows = sorted((z for r in recs for z in r["rows"]), key=lambda z: ordnung[_nr(z)])
    keeper, rest = rows[0], rows[1:]
    if not rest:
        return recs[0]
    merged = dict(keeper["werte"])
    for z in rest:
        for k, v in z["werte"].items():
            if not merged.get(k):
                merged[k] = v
    gesehen: set[str] = set()
    fragen: list[dict] = []
    for z in rows:
        for f in z["fragen"]:
            if "Pflichtfeld fehlt" in f["grund"] or f["feld"] in gesehen:
                continue
            gesehen.add(f["feld"])
            fragen.append(f)
    probe = dict(merged)
    if keeper["art"] in ARTEN_KREDITOR:
        pflicht = _kreditor_pruefen(keeper["art"], probe)
    elif keeper["art"] == ART_MANDANT:
        pflicht = _mandant_pruefen(probe)
    else:
        pflicht = _postfach_pruefen(probe)
    for f in pflicht:
        if "Pflichtfeld fehlt" in f["grund"] and f["feld"] not in gesehen:
            gesehen.add(f["feld"])
            fragen.append(f)
    for z in rest:
        for h in z["hinweise"]:
            if h not in keeper["hinweise"]:
                keeper["hinweise"].append(h)
        keeper["auto"] |= z["auto"]
        keeper.setdefault("identisch_mit", []).extend(z.get("identisch_mit", []))
        z["zusammengefasst"] = {"art": "ergaenzend", "mit": _nr(keeper)}
        z["fragen"] = []
        z["status"] = STATUS_GRUEN
        z["hinweise"].append(f"ergänzend zusammengeführt in Zeile {_nr(keeper)}")
    keeper["werte"] = probe   # (Mandant: Name ggf. aus dem Verweis uebernommen)
    keeper["fragen"] = fragen
    keeper["status"] = _status_aus_fragen(keeper)
    keeper["hinweise"].append("aus Zeilen " + ", ".join(_nr(z) for z in rows) + " zusammengeführt")
    keeper.setdefault("zusammengefuehrt_aus", []).extend(_nr(z) for z in rest)
    out = _record(rows)
    out["offen"] = set().union(*(r["offen"] for r in recs))
    return out


def _primaerschluessel(z: dict) -> str:
    w = z["werte"]
    if z["art"] in ARTEN_KREDITOR:
        return _vgl("kreditorennummer", w.get("kreditorennummer", ""))
    if z["art"] == ART_MANDANT:
        return _vgl("mandant_verweis", w.get("mandant_verweis", ""))
    return _vgl("email", w.get("email", ""))


def _gruppen_nach(items: list, schluessel) -> list[list]:
    """Union-Find-Gruppen: Elemente mit mindestens einem gemeinsamen Schluessel."""
    eltern = list(range(len(items)))

    def find(i: int) -> int:
        while eltern[i] != i:
            eltern[i] = eltern[eltern[i]]
            i = eltern[i]
        return i

    erste: dict[str, int] = {}
    for i, it in enumerate(items):
        for k in schluessel(it):
            if k in erste:
                eltern[find(i)] = find(erste[k])
            else:
                erste[k] = i
    gruppen: dict[int, list] = {}
    for i, it in enumerate(items):
        gruppen.setdefault(find(i), []).append(it)
    return list(gruppen.values())


def _datensaetze_bilden(rows: list[dict], ordnung: dict[str, int]) -> list[dict]:
    """Schritt (a) identische und (b) ergaenzende Zeilen derselben Art
    zusammenfassen -> Liste von Datensaetzen. Widersprechende Zeilen mit
    gleichem Primaerschluessel (Konto/Mandant/E-Mail) bleiben getrennte
    Datensaetze, ``offen`` verweist aufeinander (-> Frage, Schritt d)."""
    # (a) exakt gleiche Werte
    nach_sig: dict[tuple, list[dict]] = {}
    for z in rows:
        nw = _nw(z["werte"])
        z["_nw"] = nw
        nach_sig.setdefault(tuple(sorted(nw.items())), []).append(z)
    einzel: list[dict] = []
    for sig, gleiche in nach_sig.items():
        if sig and len(gleiche) > 1:
            _zusammenfassen_identisch(gleiche[0], gleiche[1:])
        einzel.append(gleiche[0])
    einzel.sort(key=lambda z: ordnung[_nr(z)])

    # (b) gleicher Primaerschluessel + keine Widersprueche
    mit_pk: dict[str, list[dict]] = {}
    ohne_pk: list[dict] = []
    for z in einzel:
        pk = _primaerschluessel(z)
        (mit_pk.setdefault(pk, []) if pk else ohne_pk).append(z)
    records: list[dict] = []
    for pk, gruppe in mit_pk.items():
        if len(gruppe) > 1 and all(_vertraeglich(a["_nw"], b["_nw"])
                                   for i, a in enumerate(gruppe) for b in gruppe[i + 1:]):
            records.append(_merge_records([_record([z]) for z in gruppe], ordnung))
            continue
        recs = [_record([z]) for z in gruppe]
        for r in recs:
            r["offen"] = {_nr(x["keeper"]) for x in recs if x is not r}
        records += recs
    # Zeilen ohne Primaerschluessel (z. B. Kreditor ohne Konto): untereinander ueber
    # die uebrigen Schluessel verwandt; vertraeglich -> zu einem Datensatz
    def schluessel_ohne_primaer(z: dict) -> set[str]:
        return {k for k in _dubletten_schluessel(z) if not k.startswith("konto:")}

    neue: list[dict] = []
    for gruppe in _gruppen_nach(ohne_pk, schluessel_ohne_primaer):
        if len(gruppe) > 1 and all(_vertraeglich(a["_nw"], b["_nw"])
                                   for i, a in enumerate(gruppe) for b in gruppe[i + 1:]):
            neue.append(_merge_records([_record([z]) for z in gruppe], ordnung))
        else:
            recs = [_record([z]) for z in gruppe]
            if len(recs) > 1:
                for r in recs:
                    r["offen"] = {_nr(x["keeper"]) for x in recs if x is not r}
            neue += recs
    # ... und ein solcher Datensatz ergaenzt GENAU EINEN verwandten, vertraeglichen
    # Datensatz mit Konto; sonst nicht raten.
    for k in neue:
        k_keys = schluessel_ohne_primaer(k["keeper"])
        kand = [r for r in records if r["keeper"]["art"] == k["keeper"]["art"]
                and (schluessel_ohne_primaer(r["keeper"]) & k_keys)]
        vertr = [r for r in kand if _vertraeglich(_nw(_record_werte(r)), _nw(_record_werte(k)))]
        if len(vertr) == 1 and len(kand) == 1 and not k["offen"] and not vertr[0]["offen"]:
            idx = records.index(vertr[0])
            records[idx] = _merge_records([vertr[0], k], ordnung)
        else:
            records.append(k)
            for r in kand:
                r["offen"].add(_nr(k["keeper"]))
                k["offen"].add(_nr(r["keeper"]))
    return records


def _markiere_dubletten(zeilen: list[dict]) -> None:
    """Dubletten innerhalb DIESES Imports (Aaron 17.10.2026: ~7000 Fragen, vor
    allem Dubletten). Reihenfolge, immer nur ohne Informationsverlust und mit
    sichtbarem Hinweis an der Zeile:

    a) identische Zeilen -> eine Zeile, die anderen "automatisch zusammengefasst";
    b) gleicher Schluessel, kein Feld widerspricht (leer ist nie ein Widerspruch;
       andere Kontonummer ist einer) -> ein Datensatz mit der Vereinigung der Felder;
    c) gleicher Name, andere Kontonummer, sonst kein Widerspruch -> getrennte
       Lieferanten (Entscheidung Aaron 17.10.2026), keine Frage;
    d) echte Widersprueche -> EINE Frage je betroffener Zeile (nie N*(N-1)).
       Bei Kreditoren gibt es dort auch die Antwort „zusammenführen (gleicher
       Lieferant)“ (Entscheidung Aaron 19.10.2026, sammelfaehig), siehe
       ``_dubletten_zusammenfuehren``.

    Dazu die Ausnahme vom 16.10.2026: dieselbe Firma einmal im Kreditor-DE- und
    einmal im Kreditor-CH-Blatt, Treffer NUR ueber den Namen (genau ein DE- und
    ein CH-Datensatz, keine gemeinsame UID/IBAN/Konto) -> ``verschmelzen``-Eintrag
    an beiden Zeilen, ``anwenden`` legt EINEN Lieferanten an."""
    ordnung = {_nr(z): i for i, z in enumerate(zeilen)}
    records: list[dict] = []
    for art in _DEDUP_ARTEN:
        rows = [z for z in zeilen if z["art"] == art]
        if rows:
            records += _datensaetze_bilden(rows, ordnung)
    for z in zeilen:
        z.pop("_nw", None)

    keys = [_record_keys(r) for r in records]
    von_schluessel: dict[str, list[int]] = {}
    for i, ks in enumerate(keys):
        for k in ks:
            von_schluessel.setdefault(k, []).append(i)

    # Paare verwandter Datensaetze -> gemeinsame Schluessel
    paare: dict[tuple[int, int], set[str]] = {}
    for k, idxs in von_schluessel.items():
        for n, i in enumerate(idxs):
            for j in idxs[n + 1:]:
                paare.setdefault((i, j), set()).add(k)
    index_von_nr = {_nr(r["keeper"]): i for i, r in enumerate(records)}
    for i, r in enumerate(records):     # in ``offen`` vorgemerkte Widersprueche
        for nr_partner in r["offen"]:
            j = index_von_nr.get(nr_partner)
            if j is not None and i != j:
                paare.setdefault((min(i, j), max(i, j)), set()).update(keys[i] & keys[j])

    aufgeloest: set[tuple[int, int]] = set()
    # DE/CH-Paar nur ueber den Namen (alter Sonderfall)
    for k, idxs in von_schluessel.items():
        if not k.startswith("name:") or len(idxs) != 2:
            continue
        a, b = records[idxs[0]], records[idxs[1]]
        arts = {a["keeper"]["art"], b["keeper"]["art"]}
        if arts != {ART_KREDITOR_DE, ART_KREDITOR_CH}:
            continue
        pk = (min(idxs), max(idxs))
        if paare.get(pk) != {k} or _nr(b["keeper"]) in a["offen"]:
            continue
        de = a if a["keeper"]["art"] == ART_KREDITOR_DE else b
        ch = b if de is a else a
        mid = _merge_id(de["keeper"]["werte"])
        paar = "|".join(sorted((_nr(a["keeper"]), _nr(b["keeper"]))))
        for z, partner in ((de["keeper"], ch["keeper"]), (ch["keeper"], de["keeper"])):
            z["verschmelzen"] = {"id": mid, "partner": _nr(partner), "paar": paar}
        aufgeloest.add(pk)
    # (c) gleicher Name, andere Kontonummer, sonst vertraeglich -> getrennte Lieferanten
    for pk, gemeinsam in paare.items():
        if pk in aufgeloest:
            continue
        a, b = records[pk[0]], records[pk[1]]
        if a["keeper"]["art"] != b["keeper"]["art"] or a["keeper"]["art"] not in ARTEN_KREDITOR:
            continue
        wa, wb = _nw(_record_werte(a)), _nw(_record_werte(b))
        ka, kb = wa.pop("kreditorennummer", ""), wb.pop("kreditorennummer", "")
        if ka and kb and ka != kb and any(g.startswith("name:") for g in gemeinsam) \
                and _vertraeglich(wa, wb):
            for x, y in ((a, b), (b, a)):
                z = x["keeper"]
                z["hinweise"].append(_GETRENNT_HINWEIS.format(z=_nr(y["keeper"])))
                z["auto"].add("getrennt")
                z.setdefault("getrennt_von", []).append(_nr(y["keeper"]))
            aufgeloest.add(pk)
    # (d) alles uebrige: EINE Frage je Zeile
    offen: dict[int, dict[int, set[str]]] = {}
    for pk, gemeinsam in paare.items():
        if pk in aufgeloest:
            continue
        i, j = pk
        offen.setdefault(i, {})[j] = gemeinsam
        offen.setdefault(j, {})[i] = gemeinsam
    for i, partner in sorted(offen.items()):
        z = records[i]["keeper"]
        arten = sorted({g.split(":", 1)[0] for gem in partner.values() for g in gem})
        andere = ", ".join(sorted(_nr(records[j]["keeper"]) for j in partner))
        antworten = ["beide übernehmen (sind unterschiedlich)",
                     "diese Zeile übernehmen, andere weglassen",
                     "Zeile weglassen"]
        # „zusammenführen“ nur bei Kreditoren und nur mit Partnern derselben Art
        gleiche_art = sorted(_nr(records[j]["keeper"]) for j in partner
                             if records[j]["keeper"]["art"] == z["art"])
        if z["art"] in ARTEN_KREDITOR and gleiche_art:
            antworten.insert(1, ANTWORT_ZUSAMMENFUEHREN)
            z["dublette_partner"] = gleiche_art
        z["fragen"].append(_frage(
            "dublette", f"Dublette in dieser Datei ({'/'.join(arten)}): auch in Zeile {andere}",
            antworten=antworten))
        if z["status"] != STATUS_ROT:
            z["status"] = STATUS_GELB


ANTWORT_ZUSAMMENFUEHREN = "zusammenführen (gleicher Lieferant)"

# Felder, die das Modell als Liste fuehrt (``Supplier.ibans/ustids/addresses``):
# beim Zusammenfuehren bleiben ALLE Werte; der erste steht in ``werte``, die
# weiteren in ``zeile["zusatzwerte"]``.
_MEHRWERT_FELDER = ("iban", "ustid", "mandant_verweis")
_FELDNAMEN = {"name": "Name", "strasse": "Straße", "plz": "PLZ", "ort": "Ort",
              "land": "Land", "ustid": "USt-ID", "steuernummer": "Steuernummer",
              "iban": "IBAN", "bic": "BIC", "kreditorennummer": "Kreditorennummer",
              "mandant_verweis": "Mandant"}


def _fuelle(z: dict) -> int:
    return sum(1 for v in z["werte"].values() if (v or "").strip())


def _dubletten_zusammenfuehren(zeilen: list[dict], antworten: dict) -> None:
    """Antwort „zusammenführen (gleicher Lieferant)“ (Aaron 19.10.2026) auf die
    Dubletten-Frage. Wird aus den ENDGUELTIGEN Antworten berechnet (nie aus der
    Reihenfolge, in der geantwortet wurde) -> deterministisch, bei jeder Vorschau
    und in ``anwenden`` neu.

    Regeln:
    * Verbund = Zeilen, die ALLE „zusammenführen“ gewaehlt haben, gleiche Art
      (DE mit DE, CH mit CH) und ueber Dubletten-Beziehungen verbunden sind
      (transitiv, auch > 2 Zeilen). Der DE/CH-Sonderfall (``verschmelzen``) bleibt.
    * Mischantworten: nur Zeilen mit „zusammenführen“ auf BEIDEN Seiten werden
      vereinigt; „beide übernehmen“, „diese Zeile übernehmen, andere weglassen“
      und „Zeile weglassen“ nehmen eine Zeile aus dem Verbund (sie bleibt wie
      beantwortet; „weglassen“ wird nicht uebernommen und nicht hineingezogen).
    * EIN Lieferant: die vollstaendigere Zeile (mehr nicht-leere Felder,
      Gleichstand -> fruehere Zeile) gewinnt; leere Felder fuellen die uebrigen
      Zeilen in derselben Rangfolge auf.
    * Mehrwertfelder (IBAN, UID, Mandant) -> alle Werte bleiben (``zusatzwerte``);
      ein Zusatzwert, der die Pruefung nicht besteht, steht nur in der Notiz.
    * Einwertfelder: abweichende Werte der anderen Zeilen (auch deren
      Kreditorennummer; das Modell kennt keine Aliasse) stehen als Notiz am
      Lieferanten -- kein Informationsverlust.
    * Die uebrigen Zeilen: ``zusammengefasst`` (Art ``dublette``), wie bei (a)/(b)
      nie uebernommen; Hinweis an allen beteiligten Zeilen."""
    pos = {_nr(z): i for i, z in enumerate(zeilen)}
    wollen: dict[str, dict] = {}
    for z in zeilen:
        if z["art"] not in ARTEN_KREDITOR or z.get("zusammengefasst"):
            continue
        a = antworten.get(_antwort_schluessel(_nr(z), "dublette"))
        if a and (a.get("antwort") or "").strip().lower() == ANTWORT_ZUSAMMENFUEHREN.lower():
            wollen[_nr(z)] = z
    if len(wollen) < 2:
        return

    def kanten(nr: str) -> set[str]:
        return {"|".join(sorted((nr, p))) for p in wollen[nr].get("dublette_partner", ())
                if p in wollen and wollen[p]["art"] == wollen[nr]["art"]}

    for gruppe in _gruppen_nach(sorted(wollen, key=pos.__getitem__), kanten):
        if len(gruppe) > 1:
            _verbund_zusammenfuehren([wollen[nr] for nr in gruppe], zeilen, pos)


def _verbund_zusammenfuehren(rows: list[dict], zeilen: list[dict], pos: dict[str, int]) -> None:
    rang = sorted(rows, key=lambda z: (-_fuelle(z), pos[_nr(z)]))
    gewinner, verlierer = rang[0], sorted(rang[1:], key=lambda z: pos[_nr(z)])
    art = gewinner["art"]
    merged = dict(gewinner["werte"])
    for z in rang[1:]:
        for k, v in z["werte"].items():
            if k in _MEHRWERT_FELDER or k == "notiz":
                continue
            if not (merged.get(k) or "").strip() and (v or "").strip():
                merged[k] = v
    # Mehrwertfelder: erster Wert nach Rangfolge nach ``werte``, der Rest nach ``zusatzwerte``
    zusatz: dict[str, list[str]] = {}
    notizen: list[str] = []
    for k in _MEHRWERT_FELDER:
        gesehen = {_vgl(k, merged[k])} if (merged.get(k) or "").strip() else set()
        for z in rang:
            v = (z["werte"].get(k) or "").strip()
            if not v or _vgl(k, v) in gesehen:
                continue
            gesehen.add(_vgl(k, v))
            if not (merged.get(k) or "").strip():
                merged[k] = v
            elif k == "mandant_verweis" or not any(
                    f["feld"] == k for f in _kreditor_pruefen(art, {**merged, k: v})):
                zusatz.setdefault(k, []).append(v)
            else:
                notizen.append(f"zusammengeführt aus Zeile {_nr(z)}: {_FELDNAMEN[k]} = {v} "
                               "(Prüfung nicht bestanden, nicht als Wert übernommen)")
    # Abweichende Einwertfelder + Notizen der anderen Zeilen -> Notiz am Lieferanten
    for z in verlierer:
        abw = []
        for k, v in z["werte"].items():
            v = (v or "").strip()
            if k in _MEHRWERT_FELDER or k == "notiz" or not v:
                continue
            if _vgl(k, v) != _vgl(k, merged.get(k, "")):
                abw.append(f"{_FELDNAMEN.get(k, k)} = {v}")
        if abw:
            notizen.append(f"zusammengeführt aus Zeile {_nr(z)}: " + "; ".join(sorted(abw)))
        if (z["werte"].get("notiz") or "").strip():
            merged["notiz"] = _notizen_ergaenzen(merged.get("notiz", ""), z["werte"]["notiz"])
    if notizen:
        merged["notiz"] = _notizen_ergaenzen(merged.get("notiz", ""), "\n".join(sorted(notizen)))
    # Fragen: die des Gewinners (ohne „Pflichtfeld fehlt“) + Pruefung der vereinigten Werte
    fragen = [f for f in gewinner["fragen"] if "Pflichtfeld fehlt" not in f["grund"]]
    felder = {f["feld"] for f in fragen}
    for f in _kreditor_pruefen(art, merged):
        if f["feld"] not in felder:
            felder.add(f["feld"])
            fragen.append(f)
    alle = ", ".join(_nr(z) for z in sorted(rows, key=lambda x: pos[_nr(x)]))
    gewinner["werte"] = merged
    gewinner["fragen"] = fragen
    if zusatz:
        gewinner["zusatzwerte"] = zusatz
    gewinner["status"] = _status_aus_fragen(gewinner)
    gewinner["hinweise"].append(f"zusammengeführt (gleicher Lieferant) aus Zeilen {alle}")
    gewinner["zusammengefuehrt_dublette"] = [_nr(z) for z in verlierer]
    for z in verlierer:
        z["zusammengefasst"] = {"art": "dublette", "mit": _nr(gewinner)}
        z["fragen"] = []
        z["status"] = STATUS_GRUEN
        z["hinweise"].append(f"zusammengeführt (gleicher Lieferant) in Zeile {_nr(gewinner)}")
        gewinner["auto"] |= z["auto"]
        for y in zeilen:   # vorher still zusammengefasste Zeilen zeigen jetzt auf den Gewinner
            if y is not z and (y.get("zusammengefasst") or {}).get("mit") == _nr(z):
                y["zusammengefasst"]["mit"] = _nr(gewinner)


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


class _Bibliothek:
    """Einmal je Vorschau aufgebauter Index ueber die vorhandene Bibliothek
    (Lieferanten nach UID/IBAN, Mandanten nach Namen, bekannte Mandanten-
    Schluessel) -- statt je Zeile alle Datensaetze neu zu lesen/normalisieren."""

    def __init__(self, store) -> None:
        self.suppliers = store.suppliers()
        self.adressen = store.addresses()
        self._ustid: dict[str, int] = {}
        self._iban: dict[str, int] = {}
        for n, s in enumerate(self.suppliers):
            for u in s.ustids:
                self._ustid.setdefault(norm_ustid(u), n)
            for i in s.ibans:
                self._iban.setdefault(re.sub(r"\s+", "", i).upper(), n)
        self.mandant_nach_name: dict[str, object] = {}
        for a in self.adressen:
            if a.name:
                self.mandant_nach_name.setdefault(norm(a.name), a)
        self.mandant_namen = {norm(a.name) for a in self.adressen}
        self.mandant_schluessel = ({_mandant_key(a.id) for a in self.adressen}
                                   | {_mandant_key(a.company_code) for a in self.adressen
                                      if a.company_code})

    def treffer(self, werte: dict[str, str]):
        """Erster Lieferant (in Bibliotheks-Reihenfolge), dessen UID oder IBAN passt."""
        kandidaten = []
        if werte.get("ustid") and norm_ustid(werte["ustid"]) in self._ustid:
            kandidaten.append(self._ustid[norm_ustid(werte["ustid"])])
        if werte.get("iban"):
            n = self._iban.get(re.sub(r"\s+", "", werte["iban"]).upper())
            if n is not None:
                kandidaten.append(n)
        return self.suppliers[min(kandidaten)] if kandidaten else None


def _konflikt_mit_bibliothek(bib: "_Bibliothek", zeile: dict) -> None:
    """Treffer gegen einen bestehenden Lieferanten/Mandanten/Postfach MIT
    abweichenden Werten -> Frage "alt oder neu?", nie stillschweigend
    ueberschreiben. Ein Treffer OHNE Abweichung (reine Ergaenzung leerer
    Felder) bleibt gruen."""
    if zeile.get("zusammengefasst"):
        return
    werte = zeile["werte"]
    if zeile["art"] in ARTEN_KREDITOR:
        treffer = bib.treffer(werte)
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
        a = bib.mandant_nach_name.get(norm(werte.get("name", "")))
        if a is not None and a.lines and werte.get("strasse") and \
                norm(" ".join(a.lines)) != norm(werte.get("strasse", "")):
            zeile["fragen"].append(_frage(
                "bibliothek", f"Mandant '{a.name}' gibt es schon ({a.id}) mit "
                             "anderer Anschrift -- alt oder neu?",
                antworten=["bestehenden Mandanten aktualisieren",
                          "als neuen, separaten Mandanten anlegen",
                          "Zeile weglassen"]))
            if zeile["status"] != STATUS_ROT:
                zeile["status"] = STATUS_GELB


def _mandant_referenz_pruefen(zeile: dict, bekannt: set[str]) -> None:
    """``bekannt``: Mandanten-Namen/-Nummern aus Datei + Bibliothek (einmal je
    Vorschau gebildet, s. ``_vorschau_intern``)."""
    ref = zeile["werte"].get("mandant_verweis")
    if zeile.get("zusammengefasst") or not ref or zeile["art"] not in (
            ART_KREDITOR_DE, ART_KREDITOR_CH, ART_POSTFACH):
        return
    if norm(ref) not in bekannt and _mandant_key(ref) not in bekannt:
        zeile["fragen"].append(_frage(
            "mandant_verweis", f"Mandant-Verweis '{ref}' nicht auffindbar (weder in "
                               "dieser Datei noch in der Bibliothek)",
            antworten=["so übernehmen (als Text, ohne Verknüpfung)",
                      "korrigieren auf …", "Zeile weglassen"]))
        if zeile["status"] != STATUS_ROT:
            zeile["status"] = STATUS_GELB


def _zusatz_mandanten_pruefen(zeile: dict, bekannt: set[str]) -> None:
    """Beim Zusammenfuehren zusaetzlich behaltene Mandanten-Verweise: nur mit
    Verknuepfung, wenn bekannt; sonst nur als Notiz (nicht raten)."""
    extras = zeile.get("zusatzwerte", {}).get("mandant_verweis")
    if not extras:
        return
    ok = [r for r in extras if norm(r) in bekannt or _mandant_key(r) in bekannt]
    for r in extras:
        if r not in ok:
            zeile["werte"]["notiz"] = _notizen_ergaenzen(
                zeile["werte"].get("notiz", ""),
                f"zusammengeführt: Mandant = {r} (nicht auffindbar, nicht verknüpft)")
    zeile["zusatzwerte"]["mandant_verweis"] = ok


def _kuerzel_pruefen(zeilen: list[dict], store) -> None:
    """Postfach-Bezeichnung (Kuerzel) gegen das Kuerzel des Mandanten, auf den
    der MDT zeigt -- faengt einen vertippten MDT ab (Rechnungen landeten sonst
    beim falschen Mandanten)."""
    kuerzel: dict[str, str] = {}
    for z in zeilen:
        if z["art"] == ART_MANDANT and z["werte"].get("mandant_verweis"):
            k = _kuerzel_und_klartext(z["werte"].get("name", ""))[0]
            if k:
                kuerzel[_mandant_key(z["werte"]["mandant_verweis"])] = k
    for a in store.addresses():
        k = _kuerzel_aus_notiz(a.notes)
        for c in (a.id, a.company_code):
            if k and c:
                kuerzel.setdefault(_mandant_key(c), k)
    for z in zeilen:
        if z["art"] != ART_POSTFACH or z.get("zusammengefasst"):
            continue
        pk, code = z["werte"].get("kuerzel"), z["werte"].get("mandant_verweis")
        soll = kuerzel.get(_mandant_key(code)) if code else None
        if pk and soll and norm(pk) != norm(soll):
            z["fragen"].append(_frage(
                "mandant_verweis",
                f"MDT {code} gehört laut Mandantenliste zum Kürzel '{soll}', die "
                f"Postfach-Bezeichnung sagt '{pk}' -- stimmt der MDT?",
                antworten=["so übernehmen (MDT stimmt)", "korrigieren auf …",
                           "Zeile weglassen"]))
            if z["status"] != STATUS_ROT:
                z["status"] = STATUS_GELB


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
    weglassen = bool(zeile.get("zusammengefasst"))   # automatisch zusammengefasst: nie uebernehmen
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
    return _vorschau_intern(store, sheets, antworten)[0]


def _auto_details(alle_zeilen: list[dict], uebersprungen: list[dict]) -> dict:
    """Was das Programm ohne Rueckfrage getan hat -- je Art eine Liste mit
    Zeilen-Schluessel (``<Blatt>:<Zeile>``), damit Aaron stichprobenartig
    nachsehen kann."""
    d: dict[str, list[dict]] = {k: [] for k in (
        "identisch", "ergaenzend", "zusammengefuehrt", "fuehrende_null", "getrennte_lieferanten",
        "leerzeilen", "kopfzeilen", "dummy")}
    for z in alle_zeilen:
        zus = z.get("zusammengefasst")
        if zus:
            d["identisch" if zus["art"] == "identisch" else
              "zusammengefuehrt" if zus["art"] == "dublette" else "ergaenzend"].append(
                {"zeile": z["schluessel"], "mit": zus["mit"]})
            continue
        if "fuehrende_null" in z["auto"]:
            d["fuehrende_null"].append({"zeile": z["schluessel"]})
        if z.get("getrennt_von"):
            d["getrennte_lieferanten"].append(
                {"zeile": z["schluessel"], "mit": ", ".join(z["getrennt_von"])})
    for u in uebersprungen:
        d["kopfzeilen" if u["grund"] == "kopfzeile" else
          "dummy" if u["grund"] == "dummy" else "leerzeilen"].append(
            {"zeile": f"{u['blatt']}:{u['zeile']}"})
    return d


def _vorschau_intern(store, sheets: dict[str, dict],
                     antworten: dict | None = None) -> tuple[dict, list[dict]]:
    """``sheets``: {blattname: {"headers": [...], "rows": [[...], ...],
    "mapping": {...} oder None, "art": "..." oder None}}. Ohne ``mapping``/
    ``art`` werden sie vorgeschlagen (ggf. aus dem Layout-Gedaechtnis).
    -> {"blaetter": {name: {"mapping", "art", "art_vorschlag", "zeilen"}},
       "zusammenfassung": {gruen, gelb, rot, ...}, "gruppen": [...],
       "automatisch": {...}}."""
    antworten = antworten or {}
    blaetter: dict[str, dict] = {}
    alle_zeilen: list[dict] = []
    mandanten_namen: set[str] = set()
    uebersprungen: list[dict] = []

    for name, s in sheets.items():
        headers = s["headers"]
        gelernt = gelernte_zuordnung(store, headers) if not s.get("mapping") else None
        mapping = s.get("mapping") or (gelernt or {}).get("mapping") or spalten_vorschlag(headers)
        art_vorg = art_vorschlag(name, mapping)
        art = s.get("art") or (gelernt or {}).get("art") or art_vorg
        zeilen = zeilen_aufbauen(store, name, headers, s["rows"], mapping, art, uebersprungen)
        blaetter[name] = {"mapping": mapping, "art": art, "art_vorschlag": art_vorg,
                          "gelernt": gelernt is not None, "zeilen": zeilen}
        alle_zeilen += zeilen
        if art == ART_MANDANT:
            mandanten_namen |= {z["werte"].get("name", "") for z in zeilen if z["werte"].get("name")}

    _markiere_dubletten(alle_zeilen)
    _dubletten_zusammenfuehren(alle_zeilen, antworten)
    _kuerzel_pruefen(alle_zeilen, store)
    bib = _Bibliothek(store)
    mandanten_codes = {_mandant_key(z["werte"]["mandant_verweis"]) for z in alle_zeilen
                       if z["art"] == ART_MANDANT and z["werte"].get("mandant_verweis")}
    bekannt = ({norm(n) for n in mandanten_namen} | bib.mandant_namen | bib.mandant_schluessel
               | mandanten_codes)
    for z in alle_zeilen:
        _konflikt_mit_bibliothek(bib, z)
        _mandant_referenz_pruefen(z, bekannt)
        _zusatz_mandanten_pruefen(z, bekannt)
        _antworten_einmischen(z, antworten)

    zaehler = {STATUS_GRUEN: 0, STATUS_GELB: 0, STATUS_ROT: 0}
    bereit = 0
    verschmolzen = [z for z in alle_zeilen if z.get("verschmelzen")]
    for z in alle_zeilen:
        if z.get("zusammengefasst"):
            continue   # nicht uebernommen, daher keine Ampel (eigener Zaehler unten)
        zaehler[z["status"]] += 1
        if not z["weglassen"] and z["beantwortet"]:
            bereit += 1
    auto = _auto_details(alle_zeilen, uebersprungen)
    for z in alle_zeilen:
        z["auto"] = sorted(z["auto"])   # JSON-faehig
    gruppen = gruppen_bilden(alle_zeilen)
    vorschau_ergebnis = {"blaetter": blaetter, "zusammenfassung": {
        **zaehler, "bereit_zum_uebernehmen": bereit,
        # DE/CH-Namensdubletten, die ohne Rueckfrage zu EINEM Lieferanten
        # werden (Zeilen bzw. Paare) -- damit nachvollziehbar bleibt, was
        # automatisch passiert ist.
        "automatisch_verschmolzen_zeilen": len(verschmolzen),
        "automatisch_verschmolzen_paare": len(verschmolzen) // 2,
        "identische_zeilen_zusammengefasst": len(auto["identisch"]),
        "zeilen_ergaenzend_zusammengefuehrt": len(auto["ergaenzend"]),
        "zeilen_dublette_zusammengefuehrt": len(auto["zusammengefuehrt"]),
        "fuehrende_nullen_ergaenzt": len(auto["fuehrende_null"]),
        "getrennte_lieferanten_gleicher_name": len(auto["getrennte_lieferanten"]),
        "leerzeilen_uebersprungen": len(auto["leerzeilen"]),
        "kopfzeilen_uebersprungen": len(auto["kopfzeilen"]),
        "dummy_zeilen_ignoriert": len(auto["dummy"])}}
    vorschau_ergebnis["automatisch"] = auto
    vorschau_ergebnis["gruppen"] = [{k: v for k, v in g.items() if not k.startswith("_")}
                                    for g in gruppen]
    return vorschau_ergebnis, alle_zeilen


def sammelantwort(store, sheets: dict[str, dict], antworten: dict | None,
                  gruppen_id: str, antwort: str = "", wert: str | None = None,
                  vorschlaege: bool = False) -> dict:
    """Neu pruefen, Gruppe ``gruppen_id`` suchen und ihre Antwort in eine KOPIE
    von ``antworten`` eintragen. -> {"antworten": ..., "gesetzt": n}. Schreibt
    nichts in die Bibliothek; uebernommen wird erst durch ``anwenden()``."""
    antworten = dict(antworten or {})
    _, zeilen = _vorschau_intern(store, sheets, antworten)
    neu = gruppen_antwort(gruppen_bilden(zeilen), gruppen_id, antwort, wert, vorschlaege)
    antworten.update(neu)
    return {"antworten": antworten, "gesetzt": len(neu)}


# ---- Fragen buendeln + Sammel-Antwort ---------------------------------------
# Reine Komfortebene (Aaron 17.10.2026: ~7000 offene Fragen kann niemand einzeln
# durchklicken): gleichartige UNBEANTWORTETE Fragen werden zu Gruppen
# zusammengefasst; eine Gruppen-Antwort traegt exakt denselben ``antworten``-
# Eintrag fuer jede Frage der Gruppe ein, den der Mensch sonst einzeln gesetzt
# haette. Entschieden wird weiterhin nur vom Menschen; ``anwenden()`` prueft
# alles serverseitig neu und ist davon unberuehrt.

_QUOTE_RE = re.compile(r"'[^']*'|[„“][^“”]*[“”]")
_KLAMMER_RE = re.compile(r"\s*\([^)]*\)")

# Antworten, die nur fuer EINE Zeile Sinn ergeben: "diese Zeile uebernehmen,
# andere weglassen" gaebe in einer Gruppe beiden Dubletten dieselbe Antwort.
NICHT_SAMMELFAEHIG = frozenset({"diese zeile übernehmen, andere weglassen"})


def _grund_muster(feld: str, grund: str) -> str:
    """Grund-Text ohne den eingebetteten konkreten Wert. Die Gruende stehen im
    Code als feste Saetze mit Wert NACH dem ':' bzw. in Hochkommata; die
    Bibliotheks-Frage nennt die Datensatz-ID in Klammern."""
    muster = grund.split(":", 1)[0]
    muster = _QUOTE_RE.sub("'…'", muster)
    if feld == "bibliothek":
        muster = _KLAMMER_RE.sub("", muster)
    return " ".join(muster.split())


def gruppen_bilden(zeilen: list[dict]) -> list[dict]:
    """Gruppiert alle noch unbeantworteten Fragen ueber alle Zeilen/Blaetter nach
    (Zeilen-Art, Feld, Grund-Muster, Antwortmoeglichkeiten). ``zeilen``: die
    Zeilen aus ``vorschau()`` (nach dem Einmischen der Antworten). -> absteigend
    nach Anzahl sortiert; ``beispiele`` nennt nur Zeilen-Schluessel (keine Werte)."""
    gruppen: dict[str, dict] = {}
    for z in zeilen:
        for f in z["fragen"]:
            if f.get("beantwortet"):
                continue
            antw = tuple(f["antwortmoeglichkeiten"])
            muster = _grund_muster(f["feld"], f["grund"])
            roh = "\x1f".join([z["art"], f["feld"], muster, *antw])
            gid = hashlib.sha1(roh.encode("utf-8")).hexdigest()[:12]
            g = gruppen.setdefault(gid, {
                "id": gid, "art": z["art"], "feld": f["feld"], "muster": muster,
                "anzahl": 0, "antwortmoeglichkeiten": list(antw),
                "sammelfaehig": [a for a in antw if a.lower() not in NICHT_SAMMELFAEHIG],
                "hat_vorschlag": False, "anzahl_vorschlaege": 0, "beispiele": [],
                "_eintraege": []})
            g["anzahl"] += 1
            g["_eintraege"].append((z["schluessel"], f))
            if f.get("vorschlag"):
                g["hat_vorschlag"] = True
                g["anzahl_vorschlaege"] += 1
            if len(g["beispiele"]) < 5:
                g["beispiele"].append(z["schluessel"])
    return sorted(gruppen.values(), key=lambda g: (-g["anzahl"], g["muster"], g["id"]))


def gruppen_antwort(gruppen: list[dict], gruppen_id: str, antwort: str,
                    wert: str | None = None, vorschlaege: bool = False) -> dict:
    """Erzeugt die ``antworten``-Eintraege (Format wie ``_antwort_schluessel``)
    fuer JEDE Frage der Gruppe. ``vorschlaege=True``: je Frage "korrigieren auf
    …" mit deren eigenem Vorschlag (Fragen ohne Vorschlag bleiben offen).
    ValueError bei unbekannter Gruppe / unzulaessiger Antwort."""
    g = next((x for x in gruppen if x["id"] == gruppen_id), None)
    if g is None:
        raise KeyError(gruppen_id)
    korr = next((a for a in g["sammelfaehig"] if a.lower().startswith("korrigieren")), None)
    out: dict[str, dict] = {}
    if vorschlaege:
        if not korr:
            raise ValueError("Diese Gruppe kennt keine Korrektur -- Vorschläge nicht übernehmbar")
        for schluessel, f in g["_eintraege"]:
            if f.get("vorschlag"):
                out[_antwort_schluessel(schluessel, f["feld"])] = {
                    "antwort": korr, "wert": f["vorschlag"]}
        if not out:
            raise ValueError("Diese Gruppe hat keine Vorschläge")
        return out
    if antwort not in g["sammelfaehig"]:
        raise ValueError("Antwort für diese Gruppe nicht zulässig")
    if antwort.lower().startswith("korrigieren") and not (wert or "").strip():
        raise ValueError("Für „korrigieren“ muss ein Wert angegeben werden")
    eintrag = {"antwort": antwort}
    if antwort.lower().startswith("korrigieren"):
        eintrag["wert"] = wert
    for schluessel, f in g["_eintraege"]:
        out[_antwort_schluessel(schluessel, f["feld"])] = dict(eintrag)
    return out


# ---- Anwenden ---------------------------------------------------------------

def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s or "x"


def _supplier_id(werte: dict[str, str], verschmelzen: dict | None = None) -> str:
    if verschmelzen:
        return verschmelzen["id"]
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


def _ohne_interne_werte(werte: dict[str, str]) -> dict[str, str]:
    """``werte`` ohne mit ``_`` beginnende Schluessel -- das sind interne,
    nur waehrend des Anwendens zwischen den Erweiterungen (siehe
    ``stammdaten_import_merge.py``) durchgereichte Marker wie
    ``_bibliothek_id``, keine echten Zeilenwerte aus der Excel-Datei. Ohne
    diesen Filter wuerde ``_anwenden_kreditor`` dieselbe Zeile faelschlich
    als "geaendert" werten, sobald der Kreditor erst NACH dem ersten Lauf in
    der Bibliothek auftaucht und die Bibliotheks-Erkennung deshalb erst beim
    zweiten Lauf anschlaegt -- der eigentliche Zeileninhalt ist dabei gleich
    geblieben."""
    return {k: v for k, v in werte.items() if not k.startswith("_")}


def _zeilen_fingerabdruck(payload: dict) -> str:
    """Inhalts-Fingerabdruck einer Stammdaten-Import-Zeile: deterministisch
    ueber eine stabile JSON-Serialisierung von ``payload`` (sortierte
    Schluessel), damit zwei Laeufe mit identischem Zeileninhalt denselben
    Fingerabdruck ergeben -- unabhaengig von Dict-Reihenfolge oder
    Programm-Neustart dazwischen. Dient NUR der Aenderungserkennung, nicht
    der Sicherheit (bei Postfaechern wird das Klartext-Passwort aus der
    Excel-Zeile mit eingerechnet, siehe ``_anwenden_postfach``)."""
    if "werte" in payload:
        payload = {**payload, "werte": _ohne_interne_werte(payload["werte"])}
    serial = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


def _anwenden_kreditor(store, zeile: dict, operator: str) -> tuple[str, bool]:
    """-> (sid, unveraendert). ``unveraendert=True`` heisst: die Zeile hat
    denselben Fingerabdruck wie beim letzten erfolgreichen Anwenden -- es
    wurde nichts geschrieben."""
    from .identify import Supplier

    werte = zeile["werte"]
    merge = zeile.get("verschmelzen")
    sid = _supplier_id(werte, merge)
    if merge and zeile.get("neu_anlegen") and store.supplier(sid) is not None:
        # beide Zeilen des Paares muessen auf denselben neuen Datensatz zeigen
        sid = f"{sid}-{merge['paar'].replace(':', '-').replace('|', '-')}"
    elif zeile.get("neu_anlegen") and store.supplier(sid) is not None:
        # Mensch hat bei der Bibliotheks-Konflikt-Frage "als neuen, separaten
        # Datensatz anlegen" gewaehlt -- der gefundene Treffer bleibt
        # unberuehrt, dieser Kreditor bekommt eine eigene, nicht
        # kollidierende Id statt den Treffer zu ueberschreiben.
        sid = f"{sid}-{zeile['schluessel'].replace(':', '-')}"
    land = "CH" if zeile["art"] == ART_KREDITOR_CH else "DE"
    sup = store.supplier(sid)
    fingerabdruck = _zeilen_fingerabdruck({
        "art": zeile["art"], "werte": werte, "zusatzwerte": zeile.get("zusatzwerte", {}),
        "verschmelzen": merge, "neu_anlegen": bool(zeile.get("neu_anlegen")),
    })
    if sup is not None and sup.stammdaten_import_hash and sup.stammdaten_import_hash == fingerabdruck:
        return sid, True
    if sup is None:
        sup = Supplier(id=sid, name=werte.get("name", ""))
    bestand = bool(merge) and bool(sup.address_lines or sup.ustids or sup.ibans)
    if werte.get("name") and not (merge and sup.name):
        sup.name = werte["name"]
    if werte.get("strasse") or werte.get("plz") or werte.get("ort"):
        zeile_adresse = [x for x in (
            werte.get("strasse", ""),
            " ".join(x for x in (werte.get("plz", ""), werte.get("ort", "")) if x),
        ) if x]
        if zeile_adresse:
            land_code = "CH" if zeile["art"] == ART_KREDITOR_CH else "DE"
            bestehende_adresse = (sup.address_lines[:-1]
                                  if sup.address_lines and sup.address_lines[-1] in ("DE", "CH")
                                  else sup.address_lines)
            if merge and bestehende_adresse and bestehende_adresse != zeile_adresse:
                # verschmolzenes Paar, Adressen beider Laender verschieden: die
                # zuerst angewendete bleibt Hauptanschrift, die andere geht nicht
                # verloren, sondern steht (einmalig) in den Notizen.
                vermerk = f"Anschrift {land}: {', '.join(zeile_adresse)}"
                if vermerk not in sup.notes:
                    sup.notes = (sup.notes + "\n" if sup.notes else "") + vermerk
            else:
                sup.address_lines = zeile_adresse + [land_code]
    if werte.get("notiz"):
        sup.notes = _notizen_ergaenzen(sup.notes, werte["notiz"])
    zusatz = zeile.get("zusatzwerte", {})   # beim Zusammenfuehren behaltene Mehrwerte
    ustids = [x for x in [werte.get("ustid", ""), *zusatz.get("ustid", [])] if x]
    if ustids:
        sup.ustids = sorted(set(sup.ustids) | set(ustids))
    ibans = [re.sub(r"\s+", "", x).upper() for x in [werte.get("iban", ""), *zusatz.get("iban", [])] if x]
    if ibans:
        sup.ibans = sorted(set(sup.ibans) | set(ibans))
    if werte.get("bic") and not (merge and bestand and sup.bic):
        sup.bic = werte["bic"]
    if werte.get("steuernummer") and not (merge and bestand and sup.creditor_id):
        sup.creditor_id = werte["steuernummer"]
    if werte.get("kreditorennummer"):
        nr = werte["kreditorennummer"]
        sup.external_ids = {**sup.external_ids, land: nr}
        if merge:
            # Rueckfallwert (Rueckwaertskompatibilitaet): deterministisch die
            # DE-Nummer, sonst die vorhandene -- massgeblich ist je Beleg
            # ``external_id_fuer(land)``.
            sup.external_id = sup.external_ids.get("DE") or sup.external_id or nr
        else:
            sup.external_id = nr
    sup.currency = "CHF" if zeile["art"] == ART_KREDITOR_CH else "EUR"
    for verweis in [werte.get("mandant_verweis", ""), *zusatz.get("mandant_verweis", [])]:
        if verweis:
            addr_id = _slug(verweis)
            if addr_id not in sup.addresses:
                sup.addresses = sorted(set(sup.addresses) | {addr_id})
    sup.stammdaten_import_hash = fingerabdruck
    store.save_supplier(sup)
    store.audit("stammdaten_kreditor_import", {"id": sid, "blatt": zeile["blatt"],
                                               "zeile": zeile["zeile"]}, operator)
    return sid, False


def _anwenden_mandant(store, zeile: dict, operator: str) -> tuple[str, bool]:
    """-> (aid, unveraendert), siehe ``_anwenden_kreditor``."""
    from .billing import BillingAddress

    werte = zeile["werte"]
    aid = _address_id(werte)
    if zeile.get("neu_anlegen") and store.address(aid) is not None:
        aid = f"{aid}-{zeile['schluessel'].replace(':', '-')}"
    addr = store.address(aid)
    fingerabdruck = _zeilen_fingerabdruck({
        "art": zeile["art"], "werte": werte, "neu_anlegen": bool(zeile.get("neu_anlegen")),
    })
    if addr is not None and addr.stammdaten_import_hash and addr.stammdaten_import_hash == fingerabdruck:
        return aid, True
    if addr is None:
        addr = BillingAddress(id=aid, name=werte.get("name", ""))
    if werte.get("name"):
        addr.name = werte["name"]
        kuerzel, klartext = _kuerzel_und_klartext(werte["name"])
        if kuerzel and klartext:
            # "ABC - Beispiel-Mandant": auf einer Rechnung steht nur der Klartext
            addr.match_terms = sorted(set(addr.match_terms) | {klartext})
            addr.notes = _notizen_ergaenzen(addr.notes, f"Kürzel: {kuerzel}")
        elif werte["name"] not in addr.match_terms:
            addr.match_terms = sorted(set(addr.match_terms) | {werte["name"]})
    if werte.get("notiz"):
        addr.notes = _notizen_ergaenzen(addr.notes, werte["notiz"])
    zeile_adresse = [x for x in (
        werte.get("strasse", ""),
        " ".join(x for x in (werte.get("plz", ""), werte.get("ort", "")) if x),
    ) if x]
    if zeile_adresse:
        addr.lines = zeile_adresse
        addr.match_terms = sorted(set(addr.match_terms) | set(zeile_adresse))
    if werte.get("mandant_verweis") and werte.get("mandant_verweis") != werte.get("name"):
        addr.company_code = werte["mandant_verweis"]
    addr.stammdaten_import_hash = fingerabdruck
    store.save_address(addr)
    store.audit("stammdaten_mandant_import", {"id": aid, "blatt": zeile["blatt"],
                                              "zeile": zeile["zeile"]}, operator)
    return aid, False


def _anwenden_postfach(store, zeile: dict, operator: str) -> tuple[str, bool]:
    """-> (pid, unveraendert), siehe ``_anwenden_kreditor``. Der Fingerabdruck
    wird auf dem KLARTEXT-Passwort aus ``werte`` berechnet (vor der
    Verschluesselung) -- der verschluesselte Blob aendert sich bei jedem
    Lauf (neues IV/Salt), auch wenn das Passwort gleich bleibt, und waere
    als Vergleichsgrundlage unbrauchbar."""
    from . import secrets as secrets_mod
    from .postfach import Postfach

    werte = zeile["werte"]
    ref = werte.get("mandant_verweis", "")
    mandant_id = ""
    for a in store.addresses():
        if (norm(a.name) == norm(ref) or _mandant_key(a.id) == _mandant_key(ref)
                or (a.company_code and _mandant_key(a.company_code) == _mandant_key(ref))):
            mandant_id = a.id
            break
    pid = _slug(werte.get("email") or ref or werte.get("name", ""))
    pf = store.postfach(pid)
    fingerabdruck = _zeilen_fingerabdruck({"art": zeile["art"], "werte": werte})
    if pf is not None and pf.stammdaten_import_hash and pf.stammdaten_import_hash == fingerabdruck:
        return pid, True
    if pf is None:
        pf = Postfach(id=pid)
    pf.mandant_id = mandant_id
    pf.mandant_name = werte.get("postfach_name") or ref
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
    pf.stammdaten_import_hash = fingerabdruck
    store.save_postfach(pf)
    if werte.get("passwort"):
        secrets_mod.set_password(store, pid, werte["passwort"], operator)
    store.audit("stammdaten_postfach_import", {"id": pid, "blatt": zeile["blatt"],
                                               "zeile": zeile["zeile"]}, operator)
    return pid, False


def anwenden(store, sheets: dict[str, dict], antworten: dict | None, operator: str = "",
            fortschritt=None, vor: dict | None = None) -> dict:
    """Komplette Pruefung NEU (nie dem Client vertrauen) -- uebernimmt nur
    gruene Zeilen und gelbe Zeilen mit vollstaendig beantworteten Fragen,
    die nicht per "Zeile weglassen" ausgeschlossen sind. ``vor``: bereits
    berechnetes ``vorschau()``-Ergebnis wiederverwenden -- der Server braucht
    es ohnehin fuer das Lernen der Spaltenzuordnung, ohne ``vor`` wuerde die
    komplette Pruefung (Bibliotheksabgleich, Dublettenerkennung, ...) bei
    grossen Dateien unnoetig zweimal laufen. ``fortschritt(fertig, gesamt)``
    wird nach jeder verarbeiteten Zeile aufgerufen (Fortschrittsanzeige bei
    vielen Zeilen).

    Zeilen, deren Inhalt sich seit dem letzten erfolgreichen Anwenden nicht
    geaendert hat (Fingerabdruck-Vergleich, siehe ``_zeilen_fingerabdruck``),
    werden nicht neu geschrieben, sondern landen in
    ``ergebnis["unveraendert_uebersprungen"]`` -- wichtig bei einem Neustart
    mitten in einem Import mit >10.000 Zeilen (muss sonst komplett neu
    geschrieben werden, obwohl keine Dubletten entstehen wuerden)."""
    if vor is None:
        vor = vorschau(store, sheets, antworten)
    ergebnis = {"kreditoren": 0, "mandanten": 0, "postfaecher": 0,
               "uebersprungen": 0, "passwoerter_gesetzt": 0,
               "verschmolzene_lieferanten": 0, "zusammengefasst": 0,
               "dubletten_zusammengefuehrt": 0, "unveraendert_uebersprungen": 0}
    # Reihenfolge unabhaengig von der Blattreihenfolge: Postfaecher verweisen auf
    # Mandanten, die dafuer schon in der Bibliothek stehen muessen.
    reihenfolge = {ART_MANDANT: 0, ART_KREDITOR_DE: 1, ART_KREDITOR_CH: 1, ART_POSTFACH: 2}
    alle = [z for blatt in vor["blaetter"].values() for z in blatt["zeilen"]]
    alle.sort(key=lambda z: reihenfolge.get(z["art"], 9))
    gesamt = len(alle)
    kreditor_ids: set[str] = set()
    verschmolzen_ids: set[str] = set()
    for i, z in enumerate(alle):
        if z.get("zusammengefasst"):
            ergebnis["zusammengefasst"] += 1   # in der Hauptzeile enthalten
        elif z["weglassen"] or not (
                z["status"] == STATUS_GRUEN or (z["status"] == STATUS_GELB and z["beantwortet"])):
            ergebnis["uebersprungen"] += 1
        elif z["art"] in ARTEN_KREDITOR:
            merge = z.get("verschmelzen")
            sid0 = _supplier_id(z["werte"], merge)
            if sid0 in kreditor_ids and not merge:
                z["neu_anlegen"] = True   # gleiche Id in dieser Datei: nie ueberschreiben
            kreditor_ids.add(sid0)
            sid, unveraendert = _anwenden_kreditor(store, z, operator)
            if unveraendert:
                ergebnis["unveraendert_uebersprungen"] += 1
            else:
                ergebnis["kreditoren"] += 1
                if z.get("zusammengefuehrt_dublette"):
                    ergebnis["dubletten_zusammengefuehrt"] += 1
            if merge:
                verschmolzen_ids.add(sid)
        elif z["art"] == ART_MANDANT:
            _, unveraendert = _anwenden_mandant(store, z, operator)
            if unveraendert:
                ergebnis["unveraendert_uebersprungen"] += 1
            else:
                ergebnis["mandanten"] += 1
        elif z["art"] == ART_POSTFACH:
            _, unveraendert = _anwenden_postfach(store, z, operator)
            if unveraendert:
                ergebnis["unveraendert_uebersprungen"] += 1
            else:
                ergebnis["postfaecher"] += 1
                if z["werte"].get("passwort"):
                    ergebnis["passwoerter_gesetzt"] += 1
        else:
            ergebnis["uebersprungen"] += 1
        if fortschritt:
            fortschritt(i + 1, gesamt)
    ergebnis["verschmolzene_lieferanten"] = len(verschmolzen_ids)
    if any(blatt["zeilen"] and any(z["werte"].get("passwort") for z in blatt["zeilen"])
          for blatt in vor["blaetter"].values()):
        ergebnis["hinweis_passwoerter"] = (
            "Die Passwörter stehen noch im Klartext in der Excel-Datei -- "
            "Datei löschen oder Passwortspalte leeren.")
    return ergebnis
