"""Textnormalisierung und Wertparser (Beträge, Datum, IBAN, USt-IdNr)."""

from __future__ import annotations

import re
from datetime import date

from dateutil import parser as _dateparser

_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

_MONATE = {
    "januar": 1, "februar": 2, "maerz": 3, "marz": 3, "april": 4, "mai": 5,
    "juni": 6, "juli": 7, "august": 8, "september": 9, "oktober": 10,
    "november": 11, "dezember": 12,
}


def norm(s: str) -> str:
    """Kleinschreibung, Umlaute ersetzt, alles Nicht-Alphanumerische zu '-'."""
    s = s.lower().translate(_UMLAUT)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s


# Zeilen am Seitenwechsel einer Positionstabelle, die KEINE Position sind
# (Aaron 05.11.2026): "Uebertrag"/"Zwischensumme" (Praefix) und
# "Seite 2 von 3"/"Page 2" (nur als ganzes Wort, sonst waere "Seitenteil"
# eine Seitenzeile). Gemeinsam fuer tabelle.py und template.py.
SEITENWECHSEL_WOERTER = ("zwischensumme", "uebertrag", "ubertrag")
SEITENZUBEHOER_WOERTER = ("seite", "page", "blatt", "pagina")


def ist_seitenwechsel_wort(text: str) -> bool:
    """Erstes Wort einer Tabellenzeile -> Seitenwechsel-/Seitenzubehoer-Zeile?"""
    fw = norm(text)
    return fw.startswith(SEITENWECHSEL_WOERTER) or fw in SEITENZUBEHOER_WOERTER


_AMOUNT_RE = re.compile(r"-?\d[\d.\s ]*,\d{1,2}|-?\d[\d,\s ]*\.\d{1,2}|-?\d+")

# Schweizer Zahlenformat: Hochkomma als Tausendertrenner (1'234.50, 1’234.50)
# und ".–"/".-" fuer glatte Franken ("120.–")
_CH_THOUSANDS = re.compile(r"(?<=\d)['’ʼ](?=\d{3}(?!\d))")
_CH_DASH = re.compile(r"(?<=\d)\.[–—-](?![\d–—-])")


def ch_number(s: str) -> str:
    """Schweizer Schreibweise eines Betrags in die uebliche ueberfuehren."""
    return _CH_DASH.sub(".00", _CH_THOUSANDS.sub("", s))


def parse_amount(s: str) -> float | None:
    """Erkennt '1.234,56', '1 234,56', '1234.56', '1234', Schweizer
    "1'234.56"/"1’234.56"/"120.–" und einstellige Nachkommabetraege wie
    '8,1' (Schweizer MwSt-Saetze 2,6/8,1)."""
    if not s:
        return None
    m = _AMOUNT_RE.search(ch_number(s.replace(" ", " ")))
    if not m:
        return None
    t = m.group(0).replace(" ", "")
    if "," in t:  # deutsches Format: Punkt = Tausender, Komma = Dezimal
        t = t.replace(".", "").replace(",", ".")
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def parse_date(s: str) -> date | None:
    if not s:
        return None
    s = s.strip()
    # deutsche Monatsnamen
    low = s.lower().translate(_UMLAUT)
    m = re.search(r"(\d{1,2})\.?\s+([a-z]+)\s+(\d{4})", low)
    if m and m.group(2) in _MONATE:
        try:
            return date(int(m.group(3)), _MONATE[m.group(2)], int(m.group(1)))
        except ValueError:
            return None
    # ISO (2026-09-15) zuerst: sonst greift unten die Tag-zuerst-Regel auf
    # den Teilstring "26-09-15" und macht daraus den 26.09.2015.
    m = re.search(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    m = re.search(r"\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}", s)
    if m:
        try:
            return _dateparser.parse(m.group(0), dayfirst=True).date()
        except (ValueError, OverflowError):
            return None
    return None


_IBAN_RE = re.compile(
    r"[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,3})?|[A-Z]{2}\d{2}[A-Z0-9]{11,30}"
)


def find_iban(s: str) -> str | None:
    """Erst pruefziffernvalide Kandidaten, sonst der erste syntaktische Treffer."""
    fallback = None
    for m in _IBAN_RE.finditer(s.upper()):
        cand = re.sub(r"\s+", "", m.group(0))
        if iban_valid(cand):
            return cand
        fallback = fallback or cand
    return fallback


def find_ibans(s: str) -> set[str]:
    """Alle pruefziffernvaliden IBANs im Text -- fuer den Betrugsschutz-
    Abgleich gegen die Stammdaten (eine zusaetzliche, fremde IBAN neben der
    hinterlegten darf nicht untergehen, nur weil die erste passt)."""
    out = set()
    for m in _IBAN_RE.finditer(s.upper()):
        cand = re.sub(r"\s+", "", m.group(0))
        if iban_valid(cand):
            out.add(cand)
    return out


# Laenderspezifische IBAN-Gesamtlaenge (Auswahl gaengiger DACH/EU-Laender)
_IBAN_LEN = {
    "DE": 22, "AT": 20, "CH": 21, "LI": 21, "NL": 18, "BE": 16, "FR": 27,
    "IT": 27, "ES": 24, "LU": 20, "PL": 28, "CZ": 24, "SK": 24, "DK": 18,
    "SE": 24, "FI": 18, "NO": 15, "PT": 25, "GB": 22, "IE": 22, "HU": 28,
}


def iban_valid(iban: str | None) -> bool:
    if not iban:
        return False
    iban = re.sub(r"\s+", "", iban).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        return False
    exp = _IBAN_LEN.get(iban[:2])
    if exp is not None and len(iban) != exp:
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


# Laenderspezifische Formate echter USt-IdNrn (EU/VIES, Nordirland "XI";
# die Schweiz separat ueber _CHE_RE mit Pruefziffer). Nur diese Muster gelten.
# Frueher reichte "2 Buchstaben + 8-12 Zeichen mit >= 2 Ziffern" -- dadurch
# galten BICs (COKSDE33XXX, MALADE51WOR, DEUTDEDB384), Filtermasse
# (AB1000X450MM) oder "TELEFON0511" als USt-IdNr, und ein Lieferanten-Entwurf
# "Unbekannt (COKSDE33XXX)" buendelte Rechnungen verschiedener Firmen, die
# zufaellig bei derselben Sparkasse sind (Befund Aaron 09.11.2026).
_USTID_FORMATS = {
    "AT": r"U\d{8}",
    "BE": r"[01]\d{9}",
    "BG": r"\d{9,10}",
    "CY": r"\d{8}[A-Z]",
    "CZ": r"\d{8,10}",
    "DE": r"\d{3}\s?\d{3}\s?\d{3}",            # auch gruppiert "DE 123 456 789"
    "DK": r"\d{2}\s?\d{2}\s?\d{2}\s?\d{2}",
    "EE": r"\d{9}",
    "EL": r"\d{9}",                            # Griechenland
    "ES": r"[A-Z0-9]\d{7}[A-Z0-9]",            # nicht beide Randzeichen Ziffern (unten)
    "FI": r"\d{8}",
    "FR": r"[A-HJ-NP-Z0-9]{2}\s?\d{9}",
    "GB": r"\d{3}\s?\d{4}\s?\d{2}(?:\s?\d{3})?|GD\d{3}|HA\d{3}",
    "HR": r"\d{11}",
    "HU": r"\d{8}",
    "IE": r"\d{7}[A-W][A-I]?|\d[A-Z+*]\d{5}[A-W]",
    "IT": r"\d{11}",
    "LT": r"\d{9}|\d{12}",
    "LU": r"\d{8}",
    "LV": r"\d{11}",
    "MT": r"\d{8}",
    "NL": r"\d{9}B\d{2}",
    "PL": r"\d{10}",
    "PT": r"\d{9}",
    "RO": r"\d{6,10}",                         # amtlich 2-10 Ziffern; kurze Formen zu riskant
    "SE": r"\d{10}01",
    "SI": r"\d{8}",
    "SK": r"\d{10}",
    "XI": r"\d{3}\s?\d{4}\s?\d{2}(?:\s?\d{3})?|GD\d{3}|HA\d{3}",
}
_USTID_RE = re.compile(
    r"\b(?:" + "|".join(rf"{cc}\s?(?:{fmt})" for cc, fmt in _USTID_FORMATS.items()) + r")\b")
# Beschriftung unmittelbar vor der Kennung, die sie als BIC ausweist
# ("BIC:", "SWIFT-BIC", "BIC/SWIFT:", "S.W.I.F.T.").
_BIC_LABEL_RE = re.compile(r"(?:BIC|SWIFT|S\.W\.I\.F\.T)[A-Z/.\-]*\s*[:.]?\s*$")
# ISO-3166-Laendercodes (fuer Stelle 5-6 eines BIC)
_ISO_COUNTRIES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ
BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM
DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS
GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN
KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ
MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM
PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV
SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI
VN VU WF WS XK YE YT ZA ZM ZW
""".split())
_BIC_RE = re.compile(r"[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}(?:[A-Z0-9]{3})?")
# Schweizer UID/MWST-Nummer: CHE-123.456.789 (MWST/TVA/IVA)
_CHE_RE = re.compile(r"\bCHE[-\s]*(\d{3})[.\s]?(\d{3})[.\s]?(\d{3})(?!\d)")


def looks_like_bic(s: str) -> bool:
    """Sieht die Folge wie ein BIC/SWIFT-Code aus? 8 oder 11 Zeichen,
    Stellen 1-4 Buchstaben (Bank), 5-6 ein ISO-Laendercode, 7-8 Ort,
    optional 9-11 Filiale. Ein BIC ist nie eine USt-IdNr."""
    c = re.sub(r"[^A-Z0-9]", "", str(s or "").upper())
    return bool(_BIC_RE.fullmatch(c)) and c[4:6] in _ISO_COUNTRIES


def che_valid(uid: str) -> bool:
    """Pruefziffer der Schweizer UID (Modulo 11, Gewichte 5 4 3 2 7 6 5 4)."""
    d = re.sub(r"\D", "", uid or "")
    if len(d) != 9:
        return False
    s = sum(int(a) * w for a, w in zip(d[:8], (5, 4, 3, 2, 7, 6, 5, 4)))
    chk = 11 - s % 11
    return chk != 10 and (0 if chk == 11 else chk) == int(d[8])


def norm_ustid(s) -> str:
    """USt-IdNr/UID vergleichbar machen: 'DE 123 456 789' -> 'DE123456789',
    'CHE-123.456.789 MWST' -> 'CHE123456789'."""
    c = re.sub(r"[^A-Z0-9]", "", str(s or "").upper())
    m = re.match(r"CHE(\d{9})", c)
    return "CHE" + m.group(1) if m else c


def find_ustids(s: str) -> set[str]:
    """USt-IdNrn im Text, streng: nur Laenderpraefix + landesspezifisches
    Format (``_USTID_FORMATS``), nie etwas, das wie ein BIC aussieht
    (``looks_like_bic``) oder hinter einer BIC/SWIFT-Beschriftung steht.
    Dazu die Schweizer UID/MWST-Nummer (CHE-123.456.789, nur mit gueltiger
    Pruefziffer) als 'CHE123456789'. Ergebnis kompakt (ohne Leerzeichen)."""
    out = set()
    up = s.upper()
    for m in _USTID_RE.finditer(up):
        cand = re.sub(r"\s+", "", m.group(0))
        if cand.startswith("ES") and cand[2].isdigit() and cand[-1].isdigit():
            continue   # spanisch: mindestens ein Randzeichen ist ein Buchstabe
        if looks_like_bic(cand):
            continue
        if _BIC_LABEL_RE.search(up[max(0, m.start() - 24):m.start()]):
            continue
        out.add(cand)
    che = {"CHE" + "".join(m.groups()) for m in _CHE_RE.finditer(up)}
    return out | {u for u in che if che_valid(u)}


_EMAIL_RE = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)", re.I)
_FREEMAIL = {"gmail.com", "googlemail.com", "web.de", "gmx.de", "gmx.net", "outlook.com", "hotmail.com", "t-online.de"}


def find_domains(s: str) -> set[str]:
    return {d.lower() for d in _EMAIL_RE.findall(s) if d.lower() not in _FREEMAIL}


_RATE_RE = re.compile(r"(\d{1,2}(?:[.,]\d{1,2})?)\s*%")
_AMOUNT_TOKEN_RE = re.compile(r"-?\d{1,3}(?:[.\s]\d{3})*,\d{2}|-?\d+,\d{2}|-?\d{1,3}(?:,\d{3})*\.\d{2}"
                              r"|-?\d+\.\d{2}")


def _amounts(s: str) -> list[float]:
    out = []
    for m in _AMOUNT_TOKEN_RE.finditer(ch_number(s or "")):
        v = parse_amount(m.group(0))
        if v is not None:
            out.append(v)
    return out


def parse_tax_summary(raw: str) -> list[dict]:
    """USt-Aufstellungs-Block -> Liste {satz, netto, ust}.

    Sucht je Prozent-Token die Betraege im Abschnitt bis zum naechsten
    Prozent-Token. Bei >=2 Betraegen gilt der erste als Netto-Basis, der
    zweite als Steuerbetrag; bei genau einem als Steuerbetrag.
    """
    if not raw:
        return []
    matches = list(_RATE_RE.finditer(raw))
    merged: dict[float, dict] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        amts = _amounts(raw[m.end():end])
        if not amts:
            continue
        rate = round(float(m.group(1).replace(",", ".")), 2)
        base, tax = (amts[0], amts[1]) if len(amts) >= 2 else (None, amts[0])
        merged.setdefault(rate, {"satz": rate, "netto": base, "ust": tax})
    return [merged[k] for k in sorted(merged)]


def coerce(raw: str, value_type: str):
    """Rohtext -> (Wert, ok?) je nach Feldtyp."""
    raw = (raw or "").strip()
    if not raw:
        return None, False
    if value_type == "taxblock":
        rows = parse_tax_summary(raw)
        return rows, bool(rows)
    if value_type == "amount":
        v = parse_amount(raw)
        return v, v is not None
    if value_type == "date":
        v = parse_date(raw)
        return (v.isoformat() if v else None), v is not None
    if value_type == "iban":
        v = find_iban(raw) or re.sub(r"\s+", "", raw)
        return v, iban_valid(v)
    if value_type == "ustid":
        ids = find_ustids(raw)
        v = next(iter(ids), re.sub(r"\s+", "", raw))
        return v, bool(ids)
    if value_type == "number":
        m = re.search(r"[A-Za-z0-9][\w\-/.]*", raw)
        return (m.group(0) if m else raw), bool(m)
    return raw, True


# ---- Wert-Abgleich: steht ein (bestaetigter/vorgeschlagener) Wert im PDF? ----
# Gemeinsame Normalisierung fuer das automatische Vorlagen-Lernen (learn.py:
# wo steht der bestaetigte Wert?) und die Halluzinations-Pruefung der KI-
# Extraktion (semantic.py: steht der Modellwert ueberhaupt im Text?). Beide
# muessen dieselbe Vorstellung von "gleicher Wert" haben -- sonst lernt das
# eine, was das andere verwirft.

# Ein Wort-Fenster gilt nur dann als Betrag, wenn es (nach Abzug von
# Waehrung/Prozent) ganz aus einer Zahl besteht -- sonst "findet" man den
# Betrag 2026 in "RE-2026-00042".
_AMOUNT_FULL_RE = re.compile(
    r"-?\d{1,3}(?:[.\s ]\d{3})*,\d{1,2}|-?\d{1,3}(?:,\d{3})*\.\d{1,2}"
    r"|-?\d+[.,]\d{1,2}|-?\d+"
)
_CURRENCY_RE = re.compile(r"(?i)^(eur|euro|chf|sfr\.?|fr\.|usd|€|\$|%)+|(eur|euro|chf|sfr\.?|fr\.|usd|€|\$|%)+$")
MAX_SPAN = 4  # laengste Wortfolge, die als ein Wert gilt ("14. Februar 2026")


def compact(s) -> str:
    """Nur Buchstaben/Ziffern, gross -- 'RE-2026/00042' == 're 2026 00042'."""
    return re.sub(r"[^A-Z0-9]", "", str(s or "").upper().translate(
        str.maketrans({"Ä": "AE", "Ö": "OE", "Ü": "UE"})))


def _amount_of(raw: str) -> float | None:
    t = ch_number(raw.strip().rstrip(":;"))
    orig = t
    for _ in range(2):
        t = _CURRENCY_RE.sub("", t).strip()
    if not t or not _AMOUNT_FULL_RE.fullmatch(t):
        return None
    # Ganze Zahl ohne Nachkommastellen nur mit Prozent-/Waehrungszeichen
    # ("19%", "1190 EUR") -- sonst waere jede Hausnummer/PLZ ein "Betrag".
    if not re.search(r"[.,]\d", t) and t == orig:
        return None
    return parse_amount(t)


def same_value(raw: str, value, value_type: str) -> bool:
    """Beschreibt der Rohtext ``raw`` (ein Wort oder eine kurze Wortfolge aus
    dem PDF) denselben Wert wie ``value``? Betraege/Datum werden geparst
    (1.190,00 == 1190.00, 14.02.2026 == 2026-02-14 == 14. Februar 2026),
    Nummern/IBAN/USt-IdNr ohne Trenn-/Leerzeichen verglichen."""
    if value is None or value == "" or not (raw or "").strip():
        return False
    if value_type == "amount":
        want = value if isinstance(value, (int, float)) else parse_amount(str(value))
        got = _amount_of(raw)
        return want is not None and got is not None and abs(got - float(want)) < 0.005
    if value_type == "date":
        want = value if isinstance(value, date) else parse_date(str(value))
        # parse_date sucht nur irgendwo im Text -- hier muss das Fenster
        # selbst datumsfoermig sein, keine Beschriftung drumherum.
        if not re.fullmatch(r"[\d.\-/\s]+|\d{1,2}\.?\s+[A-Za-zäöüÄÖÜ]+\.?\s+\d{4}",
                            raw.strip().rstrip(",;")):
            return False
        return want is not None and parse_date(raw) == want
    if value_type in ("number", "iban", "ustid"):
        # 19.0 (aus einer Betragsspalte gelesen) ist die Nummer "19", nicht "190"
        if isinstance(value, float) and value == int(value):
            value = int(value)
        a, b = compact(raw), compact(value)
        return bool(a) and a == b
    return bool(norm(raw)) and norm(raw) == norm(str(value))


def value_in_text(text: str, value, value_type: str) -> bool:
    """Kommt ``value`` irgendwo im Volltext vor (Wortfenster bis MAX_SPAN)?"""
    toks = (text or "").split()
    for i in range(len(toks)):
        for n in range(1, MAX_SPAN + 1):
            if i + n > len(toks):
                break
            if same_value(" ".join(toks[i:i + n]), value, value_type):
                return True
    return False
