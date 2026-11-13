"""Lieferanten-Gedaechtnis + Plausibilitaet (Entscheidung Aaron 10.10.2026).

Aus den bestaetigten Rechnungen eines Lieferanten (``testsatz.bestaetigte``
-- dieselben Soll-Werte wie der Testsatz, nichts von Hand gepflegt) wird ein
Profil abgeleitet: uebliche Steuersaetze, Waehrung, Betragsspanne (Brutto
Median/min/max), Muster der Rechnungsnummer (Geruest aus Buchstaben-/
Ziffernbloecken, Praefix, Laenge je Block, fortlaufend steigend?) und das
uebliche Zahlungsziel. Aktiv erst ab ``MIN_RECHNUNGEN`` (3) bestaetigten
Rechnungen -- vorher ist "ungewoehnlich" nicht zu beurteilen.

Pruefungen (``checks``: rot = Rechnung geht zum Menschen wie jede andere
rote Pruefung, auch im Schattenbetrieb; ``hinweise``: gelb, blockiert nichts):

- Betrag > 3x Maximum bzw. < 1/3 Minimum -> Hinweis; > 10x bzw. < 1/10 ->
  rot (typischer Komma-/Stellenfehler beim Lesen).
- Rechnungsnummer passt nicht zum gelernten Muster -> rot (haeufiger OCR-/KI-
  Lesefehler: O statt 0, fehlende Stelle, anderes Praefix).
- Rechnungsnummer <= einer schon bestaetigten bei fortlaufendem Muster ->
  Hinweis.
- Steuersatz bzw. Waehrung, die der Lieferant nie verwendet -> Hinweis.
- Datum (immer, auch ohne Profil, ``validate.check_date`` +
  ``check_faelligkeit``): Rechnungsdatum mehr als 1 Tag in der Zukunft ->
  rot (mit Verdacht auf Jahreszahl-Dreher, z.B. 2062 statt 2026), aelter als
  1 Jahr -> Hinweis, Faelligkeit vor Rechnungsdatum -> rot.

Die Rechnung selbst (gleiche sha256) zaehlt nie zu ihrem eigenen Profil --
sonst waere eine erneut verarbeitete bestaetigte Rechnung "kleiner/gleich
einer bestaetigten".
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from datetime import date, timedelta

from .textnorm import parse_amount, parse_date
from .validate import Check

MIN_RECHNUNGEN = 3
FAKTOR_HINWEIS = 3.0
FAKTOR_ROT = 10.0

CHECK_BETRAG = "betrag-ueblich"
CHECK_NUMMER = "rechnungsnummer-muster"
CHECK_FAELLIGKEIT = "faelligkeit-plausibel"

_BLOCK_RE = re.compile(r"\d+|[A-Za-zÄÖÜäöüß]+|[^\dA-Za-zÄÖÜäöüß]")


# ---- Profil ----------------------------------------------------------------

def _betrag(v) -> float | None:
    if isinstance(v, bool) or v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return parse_amount(str(v))


def _datum(v) -> date | None:
    if isinstance(v, date):
        return v
    return parse_date(str(v)) if v not in (None, "") else None


def bloecke(nummer: str) -> list[str]:
    """"AL-2026-0001" -> ["AL", "-", "2026", "-", "0001"] (Leerzeichen raus)."""
    return _BLOCK_RE.findall(re.sub(r"\s+", "", nummer or ""))


def geruest(nummer: str) -> str:
    """Ziffernblock -> 9, Buchstabenblock -> A, Trenner bleiben:
    "AL-2026-0001" -> "A-9-9"."""
    out = []
    for b in bloecke(nummer):
        out.append("9" if b.isdigit() else ("A" if b.isalpha() else b))
    return "".join(out)


def _laufnummer(nummer: str) -> int | None:
    """Letzter Ziffernblock als Zahl (der hochzaehlende Teil)."""
    ziffern = [b for b in bloecke(nummer) if b.isdigit()]
    return int(ziffern[-1]) if ziffern else None


def _saetze(rec: dict) -> set[float]:
    out = set()
    for r in rec.get("ust_aufstellung") or []:
        s = _betrag(r.get("satz"))
        if s is not None:
            out.add(round(s, 1))
    s = _betrag((rec.get("felder") or {}).get("ust_satz"))
    if s is not None:
        out.add(round(s, 1))
    return out


def _nummer_muster(nummern: list[tuple[date | None, str]]) -> dict:
    gerueste = Counter(geruest(n) for _d, n in nummern)
    haupt = gerueste.most_common(1)[0][0] if gerueste else ""
    gleiche = [n for _d, n in nummern if geruest(n) == haupt]
    # Laenge je Block des Hauptgeruests (min/max) und feste Buchstaben-/
    # Trennerbloecke (Praefix "RE", "AL", ...)
    laengen: list[list[int]] = []
    fest: list[str | None] = []
    for n in gleiche:
        bl = bloecke(n)
        for i, b in enumerate(bl):
            if i >= len(laengen):
                laengen.append([len(b), len(b)])
                fest.append(b if not b.isdigit() else None)
            else:
                laengen[i] = [min(laengen[i][0], len(b)), max(laengen[i][1], len(b))]
                if fest[i] is not None and fest[i].upper() != b.upper():
                    fest[i] = None
    # fortlaufend: nach Rechnungsdatum sortiert steigt die Laufnummer streng
    mit_datum = sorted(((d, n) for d, n in nummern if d is not None and geruest(n) == haupt),
                       key=lambda x: x[0])
    laeufe = [_laufnummer(n) for _d, n in mit_datum]
    fortlaufend = (len(laeufe) >= MIN_RECHNUNGEN and None not in laeufe
                   and all(b > a for a, b in zip(laeufe, laeufe[1:])))
    return {
        "gerueste": dict(gerueste),
        "geruest": haupt,
        "block_laengen": laengen,
        "feste_bloecke": fest,
        "praefix": next((b for b in fest if b and b.isalpha()), None) if fest else None,
        "fortlaufend": fortlaufend,
        "hoechste": max((x for x in laeufe if x is not None), default=None),
        "beispiel": gleiche[-1] if gleiche else None,
    }


def profil_aus(recs: list[dict]) -> dict:
    """Profil aus bestaetigten Rechnungen EINES Lieferanten."""
    # Gutschriften (negativ) gehoeren nicht in die uebliche Betragsspanne
    brutto = [b for r in recs if (b := _betrag((r.get("felder") or {}).get("brutto"))) is not None
              and b > 0]
    saetze: Counter = Counter()
    for r in recs:
        saetze.update(_saetze(r))
    waehrungen = Counter(str(w).upper() for r in recs
                         if (w := (r.get("felder") or {}).get("waehrung")))
    nummern = [(_datum((r.get("felder") or {}).get("rechnungsdatum")),
                str((r.get("felder") or {}).get("rechnungsnummer")))
               for r in recs if (r.get("felder") or {}).get("rechnungsnummer")]
    ziele = []
    for r in recs:
        f = r.get("felder") or {}
        d, fa = _datum(f.get("rechnungsdatum")), _datum(f.get("faelligkeit"))
        if d and fa and fa >= d:
            ziele.append((fa - d).days)
    return {
        "rechnungen": len(recs),
        "aktiv": len(recs) >= MIN_RECHNUNGEN,
        "steuersaetze": sorted(saetze),
        "waehrung": waehrungen.most_common(1)[0][0] if waehrungen else None,
        "waehrungen": sorted(waehrungen),
        "brutto": ({"median": round(statistics.median(brutto), 2), "min": min(brutto),
                    "max": max(brutto), "anzahl": len(brutto)} if brutto else None),
        "rechnungsnummer": _nummer_muster(nummern) if nummern else None,
        "zahlungsziel_tage": int(statistics.median(ziele)) if ziele else None,
    }


_cache: dict = {}


def _sig(store) -> tuple:
    from .store import _file_sig

    d = store.root / "testsatz"
    try:
        stats = [p.stat().st_mtime_ns for p in d.glob("*.json")]
        dsig = (d.stat().st_mtime_ns, len(stats), max(stats, default=0))
    except OSError:
        dsig = (0, 0, 0)
    return (str(store.root), dsig, _file_sig(store.root / "processed.jsonl"),
            _file_sig(store.root / "confirmations.jsonl"))


def _alle(store) -> list[dict]:
    """Bestaetigte Rechnungen, gecacht bis sich Testsatz/Journal aendern --
    die Pruefungen laufen bei jeder Rechnung."""
    from .testsatz import bestaetigte

    # Signatur und Liste als EIN Eintrag, ersetzt in einem Zug: Vorverarbeitung,
    # Listen-Zwischenspeicher und Anklicken rechnen gleichzeitig. Vorher
    # (clear, sig, recs nacheinander) sah ein zweiter Thread die neue Signatur
    # vor der Liste -> KeyError 'recs', die Rechnung galt als "Fehler" statt
    # "bereit" (Befund 13.11.2026).
    sig = _sig(store)
    eintrag = _cache.get("eintrag")
    if eintrag is not None and eintrag[0] == sig:
        return eintrag[1]
    recs = bestaetigte(store)
    _cache["eintrag"] = (sig, recs)
    return recs


def profil(store, supplier_id: str | None, ausser_sha: str | None = None) -> dict | None:
    if not supplier_id:
        return None
    recs = [r for r in _alle(store)
            if r.get("supplier_id") == supplier_id and r.get("sha256") != ausser_sha]
    return profil_aus(recs) if recs else None


def profile(store) -> list[dict]:
    """Alle Lieferanten-Profile (Anzeige im Reiter Qualitaet)."""
    by: dict[str, list[dict]] = {}
    for r in _alle(store):
        if r.get("supplier_id"):
            by.setdefault(r["supplier_id"], []).append(r)
    out = []
    for sid, recs in by.items():
        p = profil_aus(recs)
        p["lieferant"] = sid
        p["lieferant_name"] = next((r.get("supplier_name") for r in recs
                                    if r.get("supplier_name")), "") or ""
        out.append(p)
    return sorted(out, key=lambda p: (-p["rechnungen"], p["lieferant"]))


# ---- Pruefungen ------------------------------------------------------------

def _wert(fields: dict, name: str):
    fr = fields.get(name)
    if fr is None or fr.source == "missing" or fr.value in (None, ""):
        return None
    return fr.value


def check_betrag(fields: dict, prof: dict | None) -> tuple[Check, list[str]]:
    from .belegart import is_credit

    b = _betrag(_wert(fields, "brutto"))
    if not prof or not prof["aktiv"] or not prof.get("brutto") or b is None:
        return Check(CHECK_BETRAG, None, "kein Lieferantenprofil"), []
    if b <= 0 or is_credit(fields):
        return Check(CHECK_BETRAG, None, "Gutschrift/Storno -- keine übliche Spanne"), []
    lo, hi = prof["brutto"]["min"], prof["brutto"]["max"]
    spanne = f"üblich {lo:.2f}–{hi:.2f} (Median {prof['brutto']['median']:.2f})"
    a = b
    if a > FAKTOR_ROT * hi or a < lo / FAKTOR_ROT:
        return Check(CHECK_BETRAG, False,
                     f"Brutto {b:.2f} weit außerhalb der üblichen Spanne ({spanne}) -- "
                     f"Komma/Stelle falsch gelesen?"), []
    if a > FAKTOR_HINWEIS * hi or a < lo / FAKTOR_HINWEIS:
        return (Check(CHECK_BETRAG, True, f"{b:.2f} ungewöhnlich ({spanne})"),
                [f"Betrag ungewöhnlich für diesen Lieferanten: Brutto {b:.2f}, {spanne}"])
    return Check(CHECK_BETRAG, True, spanne), []


def check_nummer(fields: dict, prof: dict | None) -> tuple[Check, list[str]]:
    num = _wert(fields, "rechnungsnummer")
    muster = (prof or {}).get("rechnungsnummer")
    if not prof or not prof["aktiv"] or not muster or num is None:
        return Check(CHECK_NUMMER, None, "kein Lieferantenprofil"), []
    num = str(num)
    beispiel = f"gelernt z.B. {muster['beispiel']}"
    g = geruest(num)
    if g not in muster["gerueste"]:
        return Check(CHECK_NUMMER, False,
                     f"{num} passt nicht zum Muster ({beispiel}) -- Lesefehler?"), []
    hinweise = []
    if g == muster["geruest"]:
        bl = bloecke(num)
        letzte_ziffer = max((i for i, b in enumerate(bl) if b.isdigit()), default=-1)
        for i, b in enumerate(bl):
            lo, hi = muster["block_laengen"][i]
            fest = muster["feste_bloecke"][i]
            if fest is not None and b.upper() != fest.upper():
                return Check(CHECK_NUMMER, False,
                             f"{num}: '{b}' statt '{fest}' ({beispiel}) -- Lesefehler?"), []
            # der hochzaehlende (letzte) Ziffernblock darf um eine Stelle wachsen
            hi_ok = hi + 1 if i == letzte_ziffer else hi
            if b.isdigit() and not (lo <= len(b) <= hi_ok):
                return Check(CHECK_NUMMER, False,
                             f"{num}: Block '{b}' hat {len(b)} Stellen, gelernt {lo}"
                             + (f"–{hi}" if hi != lo else "") + f" ({beispiel}) -- Lesefehler?"), []
        lauf = _laufnummer(num)
        if muster["fortlaufend"] and lauf is not None and muster["hoechste"] is not None \
                and lauf <= muster["hoechste"]:
            hinweise.append(f"Rechnungsnummer {num} ist nicht größer als eine schon "
                            f"bestätigte (Lieferant nummeriert fortlaufend, höchste bisher "
                            f"…{muster['hoechste']})")
    return Check(CHECK_NUMMER, True, "passt zum gelernten Muster"), hinweise


def _saetze_der_rechnung(fields: dict, line_items: list[dict] | None) -> set[float]:
    out = set()
    s = _betrag(_wert(fields, "ust_satz"))
    if s is not None:
        out.add(round(s, 1))
    tb = _wert(fields, "ust_aufstellung")
    for r in tb if isinstance(tb, list) else []:
        s = _betrag(r.get("satz")) if isinstance(r, dict) else None
        if s is not None:
            out.add(round(s, 1))
    for it in line_items or []:
        s = _betrag(it.get("mwst_satz"))
        if s is not None:
            out.add(round(s, 1))
    return out


def hinweise_satz_waehrung(fields: dict, line_items, prof: dict | None) -> list[str]:
    if not prof or not prof["aktiv"]:
        return []
    out = []
    bekannt = set(prof.get("steuersaetze") or [])
    neu = sorted(_saetze_der_rechnung(fields, line_items) - bekannt)
    if bekannt and neu:
        out.append("Steuersatz " + ", ".join(f"{s:g} %" for s in neu)
                   + " verwendet dieser Lieferant sonst nie (bisher "
                   + ", ".join(f"{s:g} %" for s in sorted(bekannt)) + ")")
    w = _wert(fields, "waehrung")
    if w and prof.get("waehrungen") and str(w).upper() not in prof["waehrungen"]:
        out.append(f"Währung {w} ungewöhnlich für diesen Lieferanten (bisher "
                   f"{', '.join(prof['waehrungen'])})")
    return out


def check_faelligkeit(fields: dict) -> Check:
    d, fa = _datum(_wert(fields, "rechnungsdatum")), _datum(_wert(fields, "faelligkeit"))
    if d is None or fa is None:
        return Check(CHECK_FAELLIGKEIT, None, "Datum/Fälligkeit fehlt")
    if fa < d:
        return Check(CHECK_FAELLIGKEIT, False,
                     f"Fälligkeit {fa.isoformat()} vor Rechnungsdatum {d.isoformat()}")
    return Check(CHECK_FAELLIGKEIT, True, f"{(fa - d).days} Tage")


def datum_hinweise(fields: dict, today: date | None = None) -> list[str]:
    d = _datum(_wert(fields, "rechnungsdatum"))
    today = today or date.today()
    if d is not None and today - timedelta(days=3650) <= d < today - timedelta(days=365):
        return [f"Rechnungsdatum {d.strftime('%d.%m.%Y')} ist älter als ein Jahr -- "
                f"richtig gelesen bzw. noch zu buchen?"]
    return []


def checks(fields: dict, supplier_id: str | None, store, sha: str | None = None
           ) -> list[Check]:
    """Rote/gruene Profil-Pruefungen fuer ``res.checks`` (ohne Profil: "nicht
    pruefbar") plus die Faelligkeits-Pruefung. Das Rechnungsdatum selbst
    prueft ``validate.check_date``."""
    prof = profil(store, supplier_id, sha) if store is not None else None
    return [check_betrag(fields, prof)[0], check_nummer(fields, prof)[0],
            check_faelligkeit(fields)]


def hinweise(fields: dict, line_items, supplier_id: str | None, store,
             sha: str | None = None, today: date | None = None) -> list[str]:
    """Gelbe Hinweise (blockieren nichts) fuer ``res.hinweise``."""
    prof = profil(store, supplier_id, sha) if store is not None else None
    return (check_betrag(fields, prof)[1] + check_nummer(fields, prof)[1]
            + hinweise_satz_waehrung(fields, line_items, prof) + datum_hinweise(fields, today))
