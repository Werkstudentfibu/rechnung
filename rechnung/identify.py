"""Lieferanten-Erkennung anhand stabiler Merkmale + gepflegte Stammdaten."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .pdf import Document
from .textnorm import find_domains, find_iban, find_ibans, find_ustids, norm, norm_ustid

# Stammdaten-Felder, die als "fester Wert" pro Lieferant hinterlegt werden koennen
# (Name -> value_type). Werden von der Pipeline in die Extraktion gemischt, statt
# sie aus jeder Rechnung zu lesen.
FIXED_FIELD_TYPES: dict[str, str] = {
    "iban": "iban",
    "ustid": "ustid",
    "steuernummer": "text",
    "bestellnummer": "number",
    "ust_satz": "amount",
}

# Nur ein Startwert je Waehrung fuers Buchungsjournal (praeferiert vorbelegte
# Spalten, damit sie nicht erst nach der ersten passenden Rechnung auftauchen).
# Legt NICHTS fest -- tatsaechlich beobachtete Saetze kommen in pipeline.py
# unabhaengig davon dazu, egal ob mehr oder weniger als hier vorgeschlagen.
# CHF: aktuelle Saetze (seit 2024) 8,1/2,6/3,8 sowie die alten 7,7/2,5/3,7
# (Belege von vor 2024) -- beide Generationen bleiben gueltig, es wird kein
# Satz als "falsch" verworfen.
EXPECTED_RATES: dict[str, list[float]] = {
    "EUR": [19.0, 7.0, 0.0],
    "CHF": [8.1, 2.6, 3.8, 7.7, 2.5, 3.7, 0.0],
}
DEFAULT_CURRENCY = "EUR"


def valid_rates(currency: str | None) -> list[float]:
    """Gueltige USt-/MwSt-Saetze einer Waehrung -- gemeinsame Grundlage fuer
    ``rechnen.py`` (Kopf/Positionen ergaenzen) UND ``summen.py`` (Beträge aus
    dem Kontext herleiten, Teil A)."""
    return EXPECTED_RATES.get(currency or DEFAULT_CURRENCY, EXPECTED_RATES[DEFAULT_CURRENCY])


def nearest_valid_rate(satz: float, currency: str | None, tol: float = 0.5) -> float | None:
    """Naechster gueltiger Satz der Waehrung, wenn ``satz`` (z.B. aus USt/Netto
    zurueckgerechnet oder im Fliesstext gefunden) innerhalb der Rundungstoleranz
    ``tol`` (Prozentpunkte) liegt -- sonst ``None`` (kein gueltiger Satz, nicht
    raten)."""
    best, best_d = None, None
    for r in valid_rates(currency):
        d = abs(r - satz)
        if d <= tol and (best_d is None or d < best_d):
            best, best_d = r, d
    return best


# ---- Positionsmodus je Lieferant (Teil B, Entscheidung Aaron 10.10.2026) --
# Wie in Abbyy FlexiCapture entscheidet die bearbeitende Person beim Pruefen
# einer Rechnung, wie die Positionstabelle dieses Lieferanten zu lesen ist --
# der Modus haengt am Lieferanten (Supplier.positionsmodus), nicht an der
# einzelnen Rechnung, und bleibt stabil, bis ihn jemand aendert. Normalfall
# ist "einzeln" (unveraendertes Verhalten); nur wenige Lieferanten brauchen
# einen der anderen beiden Modi. Verschiedene MwSt-Saetze werden in KEINEM
# Modus zusammengefasst (immer eine Positionszeile je Steuersatz).
POSITIONSMODUS_EINZELN = "einzeln"
POSITIONSMODUS_ZUSAMMENFASSUNG = "zusammenfassung"
POSITIONSMODUS_EINE_POSITION = "eine_position"
POSITIONSMODI = (POSITIONSMODUS_EINZELN, POSITIONSMODUS_ZUSAMMENFASSUNG,
                 POSITIONSMODUS_EINE_POSITION)

# Pruefungs-Grund, wenn auf der Rechnung eine andere IBAN steht als im
# Lieferanten-Stammdatensatz -- klassisches Betrugsmuster (geaenderte
# Bankverbindung). Wortgleich in Pruefung, Rueckstellgrund und Fehlerbericht.
IBAN_DEVIATES = "IBAN weicht von Stammdaten ab (Betrugsschutz)"

# Rueckstellgrund fuer einen (automatisch angelegten oder von Hand noch
# nicht bestaetigten) Lieferanten-Entwurf (Supplier.bestaetigt=False,
# Entscheidung Aaron 09.10.2026) -- wortgleich in pipeline.py/human.py.
NEW_SUPPLIER_HOLD = "Neuer Lieferant -- wartet auf Bestaetigung"


@dataclass
class Supplier:
    id: str
    name: str
    ustids: list[str] = field(default_factory=list)
    ibans: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    fingerprint: list[str] = field(default_factory=list)
    # Rechnungsadressen (BillingAddress.id), denen dieser Lieferant zugeordnet
    # ist -- wie in Abbyy FlexiCapture (Vendors.BusinessUnitId): ein Lieferant
    # wird nur unter DIESEN Adressen gesucht, nie uebergreifend (Entscheidung
    # Aaron, 09.10.2026). Leer = gilt fuer alle Adressen (rueckwaertskompatibel
    # zu manuell angelegten Lieferanten vor dieser Aenderung).
    addresses: list[str] = field(default_factory=list)
    # Lieferantennummer im Fremdsystem (Abbyy-Spalte 'Id') -- bei Import mit
    # BusinessUnitId ist die interne ``id`` zusammengesetzt (siehe importers.py),
    # ``external_id`` bleibt die reine Fremd-Id fuer den Export (jobrouter.py).
    external_id: str = ""
    # Je Land ("DE"/"CH") die Fremd-Lieferantennummer, wenn ein Lieferant aus
    # einem DE- und einem CH-Kreditorensatz verschmolzen wurde (Stammdaten-
    # Import, Entscheidung Aaron 16.10.2026): ABBYY hat der DE- und der
    # CH-Seite derselben Firma ggf. verschiedene Nummern gegeben, JobRouter
    # braucht die zum Beleg passende (``external_id_fuer``). ``external_id``
    # bleibt als Rueckfall/Rueckwaertskompatibilitaet bestehen.
    external_ids: dict[str, str] = field(default_factory=dict)
    # Entwurfsstatus (Entscheidung Aaron 09.10.2026): ersetzt die bisherige
    # Regel "Lieferanten werden nie automatisch angelegt" (SPEC §7h) -- ein
    # beim Verarbeiten einer Rechnung automatisch angelegter, noch nicht
    # bestaetigter Lieferant hat ``bestaetigt=False``. Standard ``True``,
    # damit von Hand angelegte/importierte (Abbyy) Lieferanten sofort gelten
    # UND alte ``suppliers.json`` ohne dieses Feld beim Laden als bestaetigt
    # zaehlen (siehe ``from_dict``). ``entwurf_quelle``/``entwurf_am``: aus
    # welcher Rechnung (Dateiname/sha256) und wann der Entwurf entstand --
    # nur informativ, keine Fachlogik haengt daran.
    bestaetigt: bool = True
    entwurf_quelle: str = ""
    entwurf_am: str = ""
    # Der EINE Dummy-Lieferant (dummy.py, Aaron 17.10.2026): Platzhalter fuer
    # "Lieferant unbekannt". Taucht in der Lieferantenerkennung, im Lernen und
    # im Stammdaten-Import nie auf (Store.suppliers() blendet ihn aus).
    dummy: bool = False
    # --- gepflegte Stammdaten -------------------------------------------
    address_lines: list[str] = field(default_factory=list)
    bank_name: str = ""
    bic: str = ""
    bank_code: str = ""            # Bankleitzahl (BLZ) -- vor SEPA/IBAN ueblich,
                                    # manche Stammdatenlisten fuehren sie noch separat
    bank_account: str = ""         # alte Kontonummer (vor IBAN), dito
    creditor_id: str = ""          # Glaeubiger-ID / Steuernummer
    contact: str = ""              # Ansprechpartner / Telefon / Mail
    default_payment_terms: str = ""
    default_tax_rate: str = ""
    notes: str = ""
    currency: str = DEFAULT_CURRENCY
    # Feld -> fester Wert, gilt fuer alle Rechnungen dieses Lieferanten
    fixed_fields: dict[str, str] = field(default_factory=dict)
    # Positionsmodus (Teil B, Entscheidung Aaron 10.10.2026) -- Standard
    # "einzeln" (unveraendertes Verhalten); altes suppliers.json ohne dieses
    # Feld laedt ebenfalls als "einzeln" (siehe from_dict). "eine_position"
    # braucht zusaetzlich eine Bezeichnung (z.B. "Strom").
    positionsmodus: str = POSITIONSMODUS_EINZELN
    positionsbezeichnung: str = ""
    # War der Modus schon einmal eine bewusste menschliche Entscheidung
    # (POST .../positionsmodus, siehe pipeline.set_positionsmodus)? Nur dann
    # unterbleibt der "Langer Beleg"-Hinweis (pipeline._positionsmodus_hint)
    # -- unterscheidet "noch nie entschieden" vom Standardwert "einzeln".
    positionsmodus_gesetzt: bool = False
    # Inhalts-Fingerabdruck der zuletzt angewendeten Stammdaten-Import-Zeile
    # (stammdaten_import._zeilen_fingerabdruck) -- erlaubt, bei einem erneuten
    # Lauf (z.B. nach einem Neustart mitten in einem grossen Import)
    # unveraenderte Zeilen zu erkennen und zu ueberspringen. Fehlt das Feld
    # (alter Datensatz vor dieser Aenderung), gilt die Zeile IMMER als
    # geaendert -- siehe ``from_dict``. Nicht zu verwechseln mit
    # ``fingerprint`` oben (Rechnungserkennung, voellig anderer Zweck).
    stammdaten_import_hash: str = ""

    def external_id_fuer(self, land: str | None) -> str:
        """Fremd-Lieferantennummer fuer einen Beleg aus ``land`` ("DE"/"CH"/
        ``None`` = nicht erkennbar). Hat der Lieferant nur EINE Nummer (der
        Normalfall), gilt sie immer. Hat er mehrere verschiedene und das Land
        ist unbekannt (oder hat dafuer keine), wird NICHT geraten: ``""``."""
        if land and self.external_ids.get(land):
            return self.external_ids[land]
        verschieden = set(v for v in self.external_ids.values() if v)
        if self.external_id:
            verschieden.add(self.external_id)
        if len(verschieden) > 1:
            return ""
        return self.external_id or next(iter(verschieden), "")

    @property
    def external_id_mehrdeutig(self) -> bool:
        return len(set(v for v in self.external_ids.values() if v)
                   | ({self.external_id} if self.external_id else set())) > 1

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "ustids": self.ustids,
            "ibans": self.ibans, "domains": self.domains,
            "fingerprint": self.fingerprint,
            "addresses": self.addresses, "external_id": self.external_id,
            "external_ids": self.external_ids,
            "address_lines": self.address_lines, "bank_name": self.bank_name,
            "bic": self.bic, "bank_code": self.bank_code,
            "bank_account": self.bank_account, "creditor_id": self.creditor_id,
            "contact": self.contact,
            "default_payment_terms": self.default_payment_terms,
            "default_tax_rate": self.default_tax_rate, "notes": self.notes,
            "fixed_fields": self.fixed_fields, "currency": self.currency,
            "bestaetigt": self.bestaetigt, "entwurf_quelle": self.entwurf_quelle,
            "entwurf_am": self.entwurf_am, "dummy": self.dummy,
            "positionsmodus": self.positionsmodus,
            "positionsbezeichnung": self.positionsbezeichnung,
            "positionsmodus_gesetzt": self.positionsmodus_gesetzt,
            "stammdaten_import_hash": self.stammdaten_import_hash,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Supplier":
        return cls(
            id=d["id"], name=d.get("name", ""), ustids=d.get("ustids", []),
            ibans=d.get("ibans", []), domains=d.get("domains", []),
            fingerprint=d.get("fingerprint", []),
            addresses=d.get("addresses", []), external_id=d.get("external_id", ""),
            external_ids=dict(d.get("external_ids") or {}),
            address_lines=d.get("address_lines", []),
            bank_name=d.get("bank_name", ""), bic=d.get("bic", ""),
            bank_code=d.get("bank_code", ""), bank_account=d.get("bank_account", ""),
            creditor_id=d.get("creditor_id", ""), contact=d.get("contact", ""),
            default_payment_terms=d.get("default_payment_terms", ""),
            default_tax_rate=d.get("default_tax_rate", ""),
            notes=d.get("notes", ""),
            fixed_fields=d.get("fixed_fields", {}) or {},
            currency=d.get("currency") or DEFAULT_CURRENCY,
            # ``bestaetigt`` fehlt in altem ``suppliers.json`` (vor dieser
            # Aenderung) -- ``True`` als Default heisst: bereits vorhandene
            # Lieferanten laden unveraendert als bestaetigt.
            bestaetigt=d.get("bestaetigt", True),
            entwurf_quelle=d.get("entwurf_quelle", ""),
            entwurf_am=d.get("entwurf_am", ""),
            dummy=bool(d.get("dummy", False)),
            # Fehlt in altem suppliers.json (vor dieser Aenderung) --
            # "einzeln"/unentschieden als Standard (siehe Positionsmodus-Doku
            # oben).
            positionsmodus=d.get("positionsmodus") or POSITIONSMODUS_EINZELN,
            positionsbezeichnung=d.get("positionsbezeichnung", ""),
            positionsmodus_gesetzt=d.get("positionsmodus_gesetzt", False),
            stammdaten_import_hash=d.get("stammdaten_import_hash", ""),
        )


def _fingerprint(doc: Document, top_fraction: float = 0.35) -> set[str]:
    """Statische Tokens im Kopfbereich der ersten Seite."""
    if not doc.pages:
        return set()
    p = doc.pages[0]
    cutoff = p.height * top_fraction
    toks = {
        norm(w.text) for w in p.words
        if w.top <= cutoff and len(w.text) >= 4 and any(c.isalpha() for c in w.text)
    }
    return {t for t in toks if len(t) >= 4}


def extract_identifiers(doc: Document) -> dict:
    text = doc.text
    ibans = set()
    iban = find_iban(text)
    if iban:
        ibans.add(iban)
    return {
        "ustids": find_ustids(text),
        "ibans": ibans,
        "domains": find_domains(text),
        "fingerprint": _fingerprint(doc),
    }


# Namens-Erkennung (config.namenserkennung, Aaron 09.11.2026): kuerzere
# normalisierte Namen zaehlen nie als Namenstreffer ("ewz", "ada" ...).
MIN_NAMENSLAENGE = 4


def name_auf_rechnung(name: str | None, text_n: str) -> bool:
    """Steht der normalisierte Lieferantenname als GANZE Wortfolge im
    normalisierten Rechnungstext? ``norm`` trennt Woerter mit '-', deshalb
    wird an Wortgrenzen verglichen: "hotel" trifft nicht in "hotelbedarf",
    "gema" nicht in "allgemein". Zu kurze Namen (< MIN_NAMENSLAENGE)
    zaehlen nie."""
    n = norm(name or "")
    if len(n) < MIN_NAMENSLAENGE:
        return False
    return f"-{n}-" in f"-{text_n}-"


def empfaenger_namen(addresses) -> set[str]:
    """Normalisierte Namen und Suchbegriffe der Rechnungsadressen (Mandanten,
    ``billing.BillingAddress``). Ein Lieferant, der so heisst, zaehlt bei der
    Namens-Erkennung nie als Treffer: die eigenen Gesellschaften stehen als
    Empfaenger auf jeder Rechnung und teils zugleich als Lieferant in der
    Bibliothek (Befund Trockenlauf 09.11.2026: ein Versicherungsschreiben
    waere sonst dem Empfaenger als Lieferant zugeordnet worden)."""
    out: set[str] = set()
    for a in addresses or ():
        for s in [getattr(a, "name", "")] + list(getattr(a, "match_terms", None) or []):
            n = norm(s or "")
            if len(n) >= MIN_NAMENSLAENGE:
                out.add(n)
    return out


# Ab dieser Laenge gilt ein gemeinsamer Anfang als derselbe Name -- die
# Adressnamen aus dem Excel-Import sind oft abgeschnitten
# ("...Betreuungsgesellschaf" statt "...Betreuungsgesellschaft mbH").
_EMPFAENGER_PRAEFIX = 12


def _ist_empfaenger(name_n: str, empfaenger: set[str]) -> bool:
    for e in empfaenger:
        if e == name_n or f"-{e}-" in f"-{name_n}-" or f"-{name_n}-" in f"-{e}-":
            return True
        kurz, lang = sorted((e, name_n), key=len)
        if len(kurz) >= _EMPFAENGER_PRAEFIX and lang.startswith(kurz):
            return True
    return False


def namenstreffer(suppliers: list["Supplier"], text_n: str,
                  empfaenger: set[str] | None = None) -> list["Supplier"]:
    """Lieferanten, deren Name als ganze Wortfolge im normalisierten Text
    steht -- ohne Dummy und ohne Lieferanten, die wie eine Rechnungsadresse
    heissen (``empfaenger_namen``)."""
    emp = empfaenger or set()
    out = []
    for s in suppliers:
        if s.dummy or not name_auf_rechnung(s.name, text_n):
            continue
        if emp and _ist_empfaenger(norm(s.name), emp):
            continue
        out.append(s)
    return out


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def build_identifier_index(suppliers: list[Supplier]) -> dict[tuple[str, str], set[str]]:
    """USt-IdNr/IBAN -> Menge der Lieferanten-IDs, die diese Kennung fuehren.
    Einmal aus der Bibliothek aufgebaut und gecacht (``Store.supplier_index``,
    SPEC Effizienz-Baustein 4) -- erspart bei einem eindeutigen Treffer das
    Fingerprint-/Domain-/Namens-Scoring ueber die ganze Bibliothek. Die
    Steuernummer ist bewusst NICHT Teil dieses Index: ihr Format ist je
    Bundesland uneinheitlich, eine Freitext-Erkennung im Rechnungstext waere
    riskant (falsche Treffer) -- sie bleibt reiner Stammdaten-Abgleich
    (``cross_check``)."""
    idx: dict[tuple[str, str], set[str]] = {}
    for s in suppliers:
        for u in s.ustids:
            # normalisiert (Leerzeichen/Punkte/Bindestriche weg, Schweizer
            # UID "CHE-123.456.789 MWST" -> "CHE123456789") -- ein Stammdatum
            # darf anders geschrieben sein als der Fund im Rechnungstext.
            idx.setdefault(("ustid", norm_ustid(u)), set()).add(s.id)
        for i in s.ibans:
            idx.setdefault(("iban", i), set()).add(s.id)
    return idx


def kandidaten(suppliers: list[Supplier], address_id: str | None,
               has_addresses: bool) -> list[Supplier]:
    """Lieferanten, die unter der erkannten Rechnungsadresse gueltig sind --
    wie in Abbyy FlexiCapture wird ein Lieferant nur unter der Rechnungsadresse
    (BusinessUnit) gesucht, der er zugeordnet ist, nie uebergreifend
    (Entscheidung Aaron, 09.10.2026). ``Supplier.addresses`` leer = gilt fuer
    alle Adressen -- AUSSER bei einem unbestaetigten Entwurf (Entscheidung
    Aaron, 09.10.2026, automatischer Lieferanten-Entwurf): dessen leere
    ``addresses`` bedeutet "fuer DIESE Rechnung wurde keine Adresse erkannt",
    nicht "gilt global" -- sonst wuerde ein ohne Adresse angelegter Entwurf
    spaeter faelschlich auch Rechnungen MIT einer erkannten (anderen)
    Adresse treffen. Ein bestaetigter Lieferant ohne Adresse bleibt
    rueckwaertskompatibel global.

    Gibt es in der Bibliothek ueberhaupt Rechnungsadressen (``has_addresses``),
    wurde aber fuer DIESE Rechnung keine erkannt (``address_id`` ``None``),
    werden nur die globalen Lieferanten durchsucht (dort zaehlt ein Entwurf
    ohne Adresse mit) -- nie stillschweigend die ganze Bibliothek. Sind in
    der Bibliothek ueberhaupt keine Rechnungsadressen gepflegt
    (``has_addresses=False``), bleibt das Verhalten wie vor dieser Aenderung:
    alle Lieferanten sind Kandidaten."""
    if not has_addresses:
        return list(suppliers)
    if address_id is None:
        return [s for s in suppliers if not s.addresses]
    return [s for s in suppliers
           if (s.addresses and address_id in s.addresses)
           or (not s.addresses and s.bestaetigt)]


def _address_scope_reason(has_addresses: bool, address_id: str | None,
                          address_name: str, fallback: str) -> str:
    """Einheitlicher 'kein Lieferant'-Grund fuer ``identify_with_reason`` und
    die E-Rechnungs-Erkennung (``pipeline._process_einvoice``) -- macht
    erkennbar, dass nur je Rechnungsadresse gesucht wurde, statt eines
    generischen Grundes stehenzulassen."""
    if not has_addresses:
        return fallback
    if address_id is None:
        return "Rechnungsadresse nicht erkannt -- Lieferant wird nur je Adresse gesucht"
    label = address_name or address_id
    return f"{fallback} unter Rechnungsadresse {label}"


def identify(doc: Document, suppliers: list[Supplier], threshold: float = 0.5,
            min_margin: float = 0.15, index: dict | None = None,
            address_id: str | None = None, has_addresses: bool = False,
            address_name: str = "", einzelkandidat_namensschwelle: float | None = None,
            empfaenger: set[str] | None = None):
    """-> (Supplier, score) oder None. Kurzform von ``identify_with_reason``."""
    return identify_with_reason(doc, suppliers, threshold, min_margin, index,
                                address_id, has_addresses, address_name,
                                einzelkandidat_namensschwelle, empfaenger)[0]


def identify_with_reason(doc: Document, suppliers: list[Supplier], threshold: float = 0.5,
                         min_margin: float = 0.15, index: dict | None = None,
                         address_id: str | None = None, has_addresses: bool = False,
                         address_name: str = "",
                         einzelkandidat_namensschwelle: float | None = None,
                         empfaenger: set[str] | None = None):
    """-> ((Supplier, score) oder None, Grund).

    Die Lieferanten-Identitaet kommt ausschliesslich aus der Bibliothek
    (Abbyy-Import, Lieferanten-Tab, oder ein automatisch angelegter Entwurf,
    ``Supplier.bestaetigt=False`` -- siehe ``pipeline.process``,
    Entscheidung Aaron 09.10.2026) -- erkannt wird nur per Abgleich, nie per
    KI abgeleitet; diese Funktion selbst legt nie etwas an. Kein eindeutiger
    Treffer -> ``None`` und ein Grund fuer den Menschen (Rechnung wird
    zurueckgestellt). Ein Entwurf wird genauso gefunden wie ein bestaetigter
    Lieferant -- dass die Rechnung trotzdem beim Menschen bleibt, entscheidet
    ``pipeline.process`` ueber ``NEW_SUPPLIER_HOLD``, nicht diese Funktion.
    Bei einem Widerspruch (USt-IdNr/IBAN passen zu einem bestaetigten UND
    einem unbestaetigten Lieferanten) gilt wie bei jedem Mehrfachtreffer:
    ``mehrdeutig`` -> Mensch, keiner der beiden gewinnt stillschweigend.

    Lieferant je Rechnungsadresse (wie Abbyy FlexiCapture, Entscheidung Aaron
    09.10.2026): ``suppliers`` wird zuerst ueber ``kandidaten()`` auf die zur
    erkannten Rechnungsadresse (``address_id``) passenden Lieferanten
    eingeschraenkt, bevor ueberhaupt erkannt wird -- ein per USt-IdNr/IBAN
    eindeutiger Treffer bei einem Lieferanten einer ANDEREN Adresse zaehlt
    nicht (verhindert, dass dieselbe Abbyy-Id unter zwei BusinessUnits als
    "mehrdeutig" erscheint, siehe importers.py). ``index`` muss zur selben
    Kandidatenmenge passen (``Store.supplier_index(address_id)``).

    SPEC Effizienz-Baustein 4: harte Kennungen (USt-IdNr/IBAN) zuerst gegen
    einen Index abgleichen (``index``, Standard: einmalig aus den Kandidaten
    aufgebaut -- ``Store.supplier_index()`` cacht ihn ueber mehrere Aufrufe).
    Ein EINDEUTIGER Treffer entscheidet sofort, ohne das Fingerprint-/Domain-/
    Namens-Scoring ueber die ganze Bibliothek laufen zu lassen -- ein exakter
    Treffer soll ohnehin nie von einem bloss aehnlichen Kopf-Layout eines
    anderen Lieferanten ueberstimmt werden (Betrugsschutz: zeigen USt-IdNr
    und IBAN auf verschiedene Lieferanten, ist das ein Widerspruch -> Mensch,
    nie eine stille Entscheidung fuer einen von beiden). Ohne harten Treffer
    entscheidet wie bisher das Fingerprint-/Domain-/Namens-Scoring -- bei zu
    knappem Abstand zum zweitbesten Treffer (< ``min_margin``) lieber "kein
    sicherer Treffer" als eine mehrdeutige Zuordnung zu raten (verhindert
    eine stille Verwechslung zwischen zwei Lieferanten mit aehnlichem
    Kopf-Layout, z.B. derselben Buchhaltungssoftware).

    ``einzelkandidat_namensschwelle`` -- Namens-Erkennung (aktiv seit
    09.11.2026 ueber ``config.namenserkennung()``, Entscheidung Aaron; davor
    nur ein Vorschlag, siehe worker/bericht-lieferant-erkennung-1106.md
    Teil C). Befund Aaron 06.11.2026: Lieferanten aus dem Excel-
    Stammdatenimport (stammdaten_import.py) haben nur ``name`` (+ ggf.
    ``address_lines``), keine IBAN/USt-IdNr/Fingerprint/Domain -- und fast
    alle (11.218 von 11.219 am 09.11.2026) keine Rechnungsadresse, gelten
    also global: nach ``kandidaten()`` bleibt praktisch IMMER die ganze
    Bibliothek uebrig, ein reiner Namens-Teiltreffer bringt 0.2 Score und
    erreicht die globale ``threshold`` (0.5) nie. Ist dieser Wert gesetzt
    UND steht von allen Kandidaten GENAU EIN Lieferantenname als ganze
    Wortfolge auf der Rechnung (``name_auf_rechnung``, kein Dummy) UND ist
    dieser Lieferant der bestbewertete, gilt fuer IHN dieser (niedrigere)
    Wert statt ``threshold``. Stehen zwei oder mehr Lieferantennamen auf
    der Rechnung (z.B. "Muster Handels GmbH" und "Muster Handels"), bleibt
    die normale ``threshold``-/``min_margin``-Pruefung unveraendert streng
    -> Mensch. Der Mehrdeutigkeits-/Betrugsschutz-Pfad ueber USt-IdNr/IBAN
    weiter oben bleibt unberuehrt. pipeline.py vermerkt einen so erkannten
    Lieferanten als Hinweis (``NAME_HINWEIS``), damit der Mensch beim
    Bestaetigen weiss, dass nur der Name gepasst hat. ``empfaenger``
    (``empfaenger_namen(store.addresses())``): Lieferanten, die wie eine
    Rechnungsadresse heissen, zaehlen nie als Namenstreffer."""
    suppliers = kandidaten(suppliers, address_id, has_addresses)
    if not suppliers:
        if has_addresses:
            return None, _address_scope_reason(has_addresses, address_id, address_name,
                                               "kein Lieferant aus der Bibliothek passt")
        return None, "Bibliothek ist leer -- kein Lieferant hinterlegt"
    ids = extract_identifiers(doc)
    ids["ibans"] = ids["ibans"] | find_ibans(doc.text)
    idx = index if index is not None else build_identifier_index(suppliers)
    hard_ids: set[str] = set()
    for u in ids["ustids"]:
        hard_ids |= idx.get(("ustid", norm_ustid(u)), set())
    for i in ids["ibans"]:
        hard_ids |= idx.get(("iban", i), set())
    if len(hard_ids) > 1:
        names = ", ".join(sorted(hard_ids))
        return None, f"mehrdeutig: USt-IdNr/IBAN passen zu mehreren Lieferanten ({names})"
    text_n = norm(doc.text)
    if len(hard_ids) == 1:
        by_id = {s.id: s for s in suppliers}
        s = by_id[next(iter(hard_ids))]
        score = 0.0
        if {norm_ustid(u) for u in ids["ustids"]} & {norm_ustid(u) for u in s.ustids}:
            score += 0.6
        if ids["ibans"] & set(s.ibans):
            score += 0.5
        if ids["domains"] & set(s.domains):
            score += 0.3
        if name_auf_rechnung(s.name, text_n):
            score += 0.2
        score += 0.3 * _jaccard(ids["fingerprint"], set(s.fingerprint))
        return (s, round(score, 3)), ""

    # Kein harter Treffer: wie bisher per Fingerprint/Domain/Name scoren.
    scored: list[tuple[Supplier, float]] = []
    for s in suppliers:
        score = 0.0
        if ids["domains"] & set(s.domains):
            score += 0.3
        if name_auf_rechnung(s.name, text_n):
            score += 0.2
        score += 0.3 * _jaccard(ids["fingerprint"], set(s.fingerprint))
        scored.append((s, round(score, 3)))
    scored.sort(key=lambda x: x[1], reverse=True)
    best_s, best_score = scored[0]
    eff_threshold = threshold
    # Namens-Erkennung (siehe Docstring): nur wenn der Aufrufer sie anfordert
    # UND von allen Kandidaten GENAU EINER mit seinem Namen auf der Rechnung
    # steht -- und das ist der beste. Stehen zwei Namen auf der Rechnung oder
    # keiner, bleibt alles unveraendert streng.
    if einzelkandidat_namensschwelle is not None:
        treffer = namenstreffer([s for s, _ in scored], text_n, empfaenger)
        if len(treffer) == 1 and treffer[0] is best_s:
            eff_threshold = min(threshold, einzelkandidat_namensschwelle)
    if best_score < eff_threshold:
        return None, _address_scope_reason(has_addresses, address_id, address_name,
                                           "kein Lieferant aus der Bibliothek passt")
    if len(scored) > 1:
        second_s, second_score = scored[1]
        # Ein unbestaetigter Entwurf darf einen bestaetigten Lieferanten nie
        # verdraengen (Entscheidung Aaron 09.10.2026): liegt der Zweitplatzierte
        # innerhalb der normalen Mehrdeutigkeits-Spanne UND ist er bestaetigt,
        # waehrend der Erstplatzierte nur ein Entwurf ist, gewinnt der
        # bestaetigte -- statt "mehrdeutig" zu melden oder stillschweigend den
        # Entwurf zu nehmen. Dasselbe in umgekehrter Reihenfolge (Entwurf auf
        # Platz 2) bleibt unveraendert: dort entscheidet weiter die Spanne.
        if (not best_s.bestaetigt and second_s.bestaetigt
                and second_score >= threshold and (best_score - second_score) < min_margin):
            best_s, best_score = second_s, second_score
        elif (best_score - second_score) < min_margin:
            return None, (f"mehrdeutig: {best_s.id} / {second_s.id} "
                          f"(Score {best_score} vs. {second_score})")
    return (best_s, best_score), ""


def cross_check(doc: Document, supplier: Supplier) -> list[dict]:
    """Auf der Rechnung gefundene IBAN/USt-IdNr gegen die Stammdaten pruefen.
    -> Liste {kind, ok, detail}."""
    ids = extract_identifiers(doc)
    out = []
    if supplier.ibans:
        found = ids["ibans"] | find_ibans(doc.text)
        foreign = sorted(found - set(supplier.ibans))
        out.append({"kind": "iban-stammdaten", "ok": not foreign,
                    "detail": (f"{IBAN_DEVIATES}: {', '.join(foreign)}" if foreign else "ok")})
    if supplier.ustids:
        found = {norm_ustid(u) for u in ids["ustids"]}
        sup_ustids = {norm_ustid(u) for u in supplier.ustids}
        ok = (not found) or bool(found & sup_ustids)
        out.append({"kind": "ustid-stammdaten", "ok": ok,
                    "detail": ",".join(sorted(found - sup_ustids)) or "ok"})
    return out


def _detect_currency(text: str) -> str:
    """Grobe Waehrungs-Erkennung am Rechnungstext -- nur ein Vorschlag fuer
    einen neu angelegten Lieferanten, im Lieferanten-Tab jederzeit korrigierbar.
    CHF gewinnt nur, wenn es vorkommt und kein EUR/€-Hinweis auf der Rechnung
    steht (manche Schweizer Lieferanten fakturieren auch in Euro)."""
    t = text.upper()
    has_chf = bool(re.search(r"\bCHF\b", t))
    has_eur = bool(re.search(r"\bEUR\b", t) or "€" in text)
    if has_chf and not has_eur:
        return "CHF"
    return DEFAULT_CURRENCY


def supplier_from_doc(doc: Document, supplier_id: str, name: str) -> Supplier:
    ids = extract_identifiers(doc)
    return Supplier(
        id=supplier_id, name=name,
        ustids=sorted(ids["ustids"]), ibans=sorted(ids["ibans"]),
        domains=sorted(ids["domains"]), fingerprint=sorted(ids["fingerprint"]),
        currency=_detect_currency(doc.text),
    )


def identify_einvoice(inv, suppliers: list[Supplier]):
    """Lieferant einer E-Rechnung ueber die strukturierten Verkaeufer-Daten
    finden -> (Supplier, score) oder None. USt-IdNr/IBAN sind eindeutig; der
    Name allein zaehlt nur bei exakter (normalisierter) Gleichheit und nur,
    wenn genau ein Lieferant so heisst -- sonst lieber "unbekannt" als raten.

    ``suppliers`` soll bereits die zur erkannten Rechnungsadresse passende
    Kandidatenmenge sein (``kandidaten()``, vom Aufrufer vorgefiltert) --
    diese Funktion filtert selbst nicht nach Adresse, damit die Filterlogik
    nur an einer Stelle steht."""
    ustid = (inv.verkaeufer_ustid or "").replace(" ", "").upper()
    ibans = {i.replace(" ", "").upper() for i in inv.verkaeufer_iban}
    hits = [s for s in suppliers
            if (ustid and ustid in set(s.ustids)) or (ibans & set(s.ibans))]
    if len(hits) == 1:
        return hits[0], 1.0
    if len(hits) > 1:
        both = [s for s in hits if ustid in set(s.ustids) and ibans & set(s.ibans)]
        return (both[0], 1.0) if len(both) == 1 else None
    name = norm(inv.verkaeufer_name or "")
    if name:
        by_name = [s for s in suppliers if s.name and norm(s.name) == name]
        if len(by_name) == 1:
            return by_name[0], 0.6
    return None


def land_aus_kennungen(ustids=(), ibans=()) -> str | None:
    """"DE"/"CH" aus UIDs/IBANs eines Belegs, wenn sie GENAU ein Land
    ergeben; sonst ``None`` (nicht erkennbar oder widerspruechlich -- nie
    raten)."""
    laender = set()
    for u in ustids:
        n = norm_ustid(u)
        if n.startswith("CHE"):
            laender.add("CH")
        elif n.startswith("DE"):
            laender.add("DE")
    for i in ibans:
        k = re.sub(r"\s+", "", str(i or "")).upper()
        if k[:2] in ("DE", "CH"):
            laender.add(k[:2])
    return next(iter(laender)) if len(laender) == 1 else None


def land_aus_beleg_text(sup: "Supplier", text: str) -> str | None:
    """Land eines Belegs anhand der UIDs/IBANs des Lieferanten, die im Text
    stehen (Beleg einer DE-Seite nennt die DE-UID/-IBAN, einer CH-Seite die
    CH-Kennungen)."""
    kompakt = re.sub(r"\s+", "", (text or "").upper())
    ustids_im_text = find_ustids(text or "")
    ust = [u for u in sup.ustids if norm_ustid(u) in ustids_im_text]
    ib = [i for i in sup.ibans if re.sub(r"\s+", "", i).upper() in kompakt]
    return land_aus_kennungen(ust, ib)
