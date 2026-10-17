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
        v = (v or "").strip()
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


def _plz_aufbereiten(werte: dict[str, str], fragen: list[dict], hinweise: list[str]) -> None:
    """Spalte "PlzOrt" kann nur die PLZ ("12345") oder PLZ + Ort enthalten. Steht
    ein Ort darin, wird er mit "Ortsbezeichnung" abgeglichen (Widerspruch = Frage)."""
    plz = werte.get("plz", "")
    if not plz or plz.isdigit():
        return
    m = _PLZ_ORT_RE.match(plz)
    if not m:
        return   # ``_kreditor_pruefen`` fragt wegen des Formats
    werte["plz"], ort_aus_plz = m.group(1), m.group(2).strip()
    if not werte.get("ort"):
        werte["ort"] = ort_aus_plz
        hinweise.append(f"Ort '{ort_aus_plz}' aus der PLZ-Spalte übernommen")
    elif norm(werte["ort"]) != norm(ort_aus_plz):
        fragen.append(_frage(
            "ort", f"PLZ-Spalte nennt '{ort_aus_plz}', Ortsbezeichnung '{werte['ort']}' "
                   "-- bitte entscheiden", vorschlag=ort_aus_plz))


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


def zeilen_aufbauen(store, blatt: str, headers: list[str], data_rows: list[list[str]],
                    mapping: dict[str, str | None], art: str) -> list[dict]:
    """Je Datenzeile: ``{blatt, zeile, art, werte, status, fragen, schluessel}``.
    Bibliotheks-/Dubletten-Pruefung (zeilenuebergreifend) macht ``vorschau()``
    danach -- hier nur die reine Zeilenpruefung."""
    out: list[dict] = []
    for i, row in enumerate(data_rows, start=1):
        werte, kollisionen = _zeile_zu_werten_ex(headers, row, mapping)
        rohwerte = {h: v for h, v in zip(headers, row) if (v or "").strip()}
        schluessel = f"{blatt}:{i}"
        zeilen_art = art
        fragen: list[dict] = []
        hinweise: list[str] = []
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
            _plz_aufbereiten(werte, fragen, hinweise)
            fragen += _kreditor_pruefen(zeilen_art, werte)
        elif zeilen_art == ART_MANDANT:
            fragen += _mandant_pruefen(werte)
        elif zeilen_art == ART_POSTFACH:
            _postfach_aufbereiten(werte, fragen)
            fragen += _postfach_pruefen(werte)

        rot = zeilen_art == ART_UNKLAR or any(
            f["feld"] in ("name", "email", "mandant_verweis") and "Pflichtfeld fehlt" in f["grund"]
            for f in fragen)
        status = STATUS_ROT if rot else (STATUS_GELB if fragen else STATUS_GRUEN)
        out.append({
            "blatt": blatt, "zeile": i, "art": zeilen_art, "werte": werte,
            "rohwerte": rohwerte, "status": status, "fragen": fragen,
            "schluessel": schluessel, "hinweise": hinweise,
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


def _markiere_dubletten(zeilen: list[dict]) -> None:
    """Zwei Zeilen DIESES Imports mit gleichem Namen/UID/IBAN -> beide gelb,
    mit Frage und Verweis aufeinander (nie stillschweigend die eine
    bevorzugen).

    Ausnahme (Entscheidung Aaron 16.10.2026): dieselbe Firma einmal im
    Kreditor-DE- und einmal im Kreditor-CH-Blatt, Treffer NUR ueber den
    Namen (genau eine DE- und eine CH-Zeile, keine gemeinsame UID/IBAN) --
    ABBYY brauchte diese Trennung, wir nicht. Das ist keine Frage, sondern
    ein automatisch zu verschmelzendes Paar (``verschmelzen``-Eintrag an
    beiden Zeilen, ``anwenden`` legt EINEN Lieferanten an)."""
    von_schluessel: dict[str, list[dict]] = {}
    for z in zeilen:
        for k in _dubletten_schluessel(z):
            von_schluessel.setdefault(k, []).append(z)

    # Zeilenpaare, die zusaetzlich UID/IBAN teilen, bleiben normale Dubletten.
    teilt_kennung: set[frozenset] = set()
    for k, treffer in von_schluessel.items():
        if k.startswith("name:") or len(treffer) < 2:
            continue
        teilt_kennung.update(frozenset((a["schluessel"], b["schluessel"]))
                             for a in treffer for b in treffer if a is not b)

    verschmolzen: set[str] = set()
    for k, treffer in von_schluessel.items():
        if not k.startswith("name:") or len(treffer) != 2:
            continue
        de = [z for z in treffer if z["art"] == ART_KREDITOR_DE]
        ch = [z for z in treffer if z["art"] == ART_KREDITOR_CH]
        if len(de) != 1 or len(ch) != 1:
            continue
        if frozenset((de[0]["schluessel"], ch[0]["schluessel"])) in teilt_kennung:
            continue
        mid = _merge_id(de[0]["werte"])
        for z, partner in ((de[0], ch[0]), (ch[0], de[0])):
            z["verschmelzen"] = {"id": mid, "partner": partner["schluessel"],
                                 "paar": "|".join(sorted((z["schluessel"], partner["schluessel"])))}
            verschmolzen.add(z["schluessel"])

    gemeldet: set[tuple[str, str]] = set()
    for k, treffer in von_schluessel.items():
        if len(treffer) < 2:
            continue
        if k.startswith("name:") and all(z["schluessel"] in verschmolzen for z in treffer):
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
    bekannt = ({norm(n) for n in alle_mandanten_namen} | {norm(a.name) for a in store.addresses()}
               | {_mandant_key(a.id) for a in store.addresses()}
               | {_mandant_key(a.company_code) for a in store.addresses() if a.company_code}
               | set(zeile.get("_mandant_codes", ())))
    if norm(ref) not in bekannt and _mandant_key(ref) not in bekannt:
        zeile["fragen"].append(_frage(
            "mandant_verweis", f"Mandant-Verweis '{ref}' nicht auffindbar (weder in "
                               "dieser Datei noch in der Bibliothek)",
            antworten=["so übernehmen (als Text, ohne Verknüpfung)",
                      "korrigieren auf …", "Zeile weglassen"]))
        if zeile["status"] != STATUS_ROT:
            zeile["status"] = STATUS_GELB


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
        if z["art"] != ART_POSTFACH:
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
    return _vorschau_intern(store, sheets, antworten)[0]


def _vorschau_intern(store, sheets: dict[str, dict],
                     antworten: dict | None = None) -> tuple[dict, list[dict]]:
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
    _kuerzel_pruefen(alle_zeilen, store)
    mandanten_codes = {_mandant_key(z["werte"]["mandant_verweis"]) for z in alle_zeilen
                       if z["art"] == ART_MANDANT and z["werte"].get("mandant_verweis")}
    for z in alle_zeilen:
        z["_mandant_codes"] = mandanten_codes
    for z in alle_zeilen:
        _konflikt_mit_bibliothek(store, z)
        _mandant_referenz_pruefen(z, mandanten_namen, store)
        _antworten_einmischen(z, antworten)

    zaehler = {STATUS_GRUEN: 0, STATUS_GELB: 0, STATUS_ROT: 0}
    bereit = 0
    verschmolzen = [z for z in alle_zeilen if z.get("verschmelzen")]
    for z in alle_zeilen:
        z.pop("_mandant_codes", None)
        zaehler[z["status"]] += 1
        if not z["weglassen"] and z["beantwortet"]:
            bereit += 1
    gruppen = gruppen_bilden(alle_zeilen)
    vorschau_ergebnis = {"blaetter": blaetter, "zusammenfassung": {
        **zaehler, "bereit_zum_uebernehmen": bereit,
        # DE/CH-Namensdubletten, die ohne Rueckfrage zu EINEM Lieferanten
        # werden (Zeilen bzw. Paare) -- damit nachvollziehbar bleibt, was
        # automatisch passiert ist.
        "automatisch_verschmolzen_zeilen": len(verschmolzen),
        "automatisch_verschmolzen_paare": len(verschmolzen) // 2}}
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


def _anwenden_kreditor(store, zeile: dict, operator: str) -> str:
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
    if werte.get("ustid"):
        sup.ustids = sorted(set(sup.ustids) | {werte["ustid"]})
    if werte.get("iban"):
        sup.ibans = sorted(set(sup.ibans) | {re.sub(r"\s+", "", werte["iban"]).upper()})
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
        if (norm(a.name) == norm(ref) or _mandant_key(a.id) == _mandant_key(ref)
                or (a.company_code and _mandant_key(a.company_code) == _mandant_key(ref))):
            mandant_id = a.id
            break
    pid = _slug(werte.get("email") or ref or werte.get("name", ""))
    pf = store.postfach(pid)
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
               "uebersprungen": 0, "passwoerter_gesetzt": 0,
               "verschmolzene_lieferanten": 0}
    # Reihenfolge unabhaengig von der Blattreihenfolge: Postfaecher verweisen auf
    # Mandanten, die dafuer schon in der Bibliothek stehen muessen.
    reihenfolge = {ART_MANDANT: 0, ART_KREDITOR_DE: 1, ART_KREDITOR_CH: 1, ART_POSTFACH: 2}
    alle = [z for blatt in vor["blaetter"].values() for z in blatt["zeilen"]]
    alle.sort(key=lambda z: reihenfolge.get(z["art"], 9))
    kreditor_ids: set[str] = set()
    verschmolzen_ids: set[str] = set()
    for z in alle:
        if z["weglassen"] or not (
                z["status"] == STATUS_GRUEN or (z["status"] == STATUS_GELB and z["beantwortet"])):
            ergebnis["uebersprungen"] += 1
            continue
        if z["art"] in ARTEN_KREDITOR:
            merge = z.get("verschmelzen")
            sid0 = _supplier_id(z["werte"], merge)
            if sid0 in kreditor_ids and not merge:
                z["neu_anlegen"] = True   # gleiche Id in dieser Datei: nie ueberschreiben
            kreditor_ids.add(sid0)
            sid = _anwenden_kreditor(store, z, operator)
            ergebnis["kreditoren"] += 1
            if merge:
                verschmolzen_ids.add(sid)
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
    ergebnis["verschmolzene_lieferanten"] = len(verschmolzen_ids)
    if any(blatt["zeilen"] and any(z["werte"].get("passwort") for z in blatt["zeilen"])
          for blatt in vor["blaetter"].values()):
        ergebnis["hinweis_passwoerter"] = (
            "Die Passwörter stehen noch im Klartext in der Excel-Datei -- "
            "Datei löschen oder Passwortspalte leeren.")
    return ergebnis
