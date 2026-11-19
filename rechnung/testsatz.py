"""Testsatz aus bestaetigten Rechnungen (Goldstandard, Entscheidung Aaron
10.10.2026).

Jede vom Menschen im Training-UI **bestaetigte** Rechnung ist ein Testfall:
das unveraenderte Original (``data/archive/<sha256>.pdf|.xml``) plus die
bestaetigten Soll-Werte. ``rechnung testsatz`` verarbeitet alle Testfaelle
im Trockenlauf erneut und misst je Feld richtig / falsch / fehlt, dazu die
Positionen (Zeilen gefunden/korrekt) und die Zeit je Rechnung -- so wird
sichtbar, ob eine Programmaenderung (oder eine neue Extraktionsvariante)
besser oder schlechter liest.

Wo liegt was (nichts doppelt):

- Original: ``data/archive/`` -- dorthin legt ``merken()`` das Original beim
  Bestaetigen (``Store.archive_pdf`` kopiert nur, wenn es noch fehlt; bisher
  archivierte nur ``finalize()``).
- Soll-Werte: ``data/testsatz/<sha256>.json``. Bewusst eigene Datei statt
  ``processed.jsonl``: dort stehen nur fuenf Kopf-Felder, und ein erneutes
  Bestaetigen nach einer Korrektur haengt keinen neuen Eintrag an
  (``seen_sha_template``) -- die Soll-Werte waeren dann veraltet. Fehlt die
  Datei (vor dem 10.10.2026 bestaetigt), wird der Testfall aus
  ``processed.jsonl`` + Archiv hergeleitet (nur die Felder, die dort stehen).

Trockenlauf: keine Entwuerfe, kein Ereignisprotokoll, keine Ablage, kein
Export, kein Audit (auch nicht bei Zeitueberschreitung) -- Zeitgrenze wie
konfiguriert (``guard.py``). Die gespeicherten manuellen Korrekturen der
Rechnung werden dabei NICHT eingemischt (sonst waere jedes korrigierte Feld
automatisch "richtig"). Bestaetigte Vorlagen wirken dagegen ganz normal --
genau das soll gemessen werden.

Bericht: ``data/berichte/testsatz_<datum>_<zeit>.json`` (mit Uhrzeit, damit
ein zweiter Lauf am selben Tag den ersten nicht ueberschreibt) und Vergleich
mit dem letzten Lauf mit gleichem Filter ("schlechter geworden bei: ...").

Blindtest (``blind=True``, Aaron 19.11.2026: "Kann die KI von den angelernten
Lieferanten auf andere schliessen ... ohne dass ein Mensch drueber schauen
muss?"): jede Rechnung wird so gelesen, als waere von ihrem Lieferanten noch
nie eine Rechnung bearbeitet worden (``_BlindStore``): seine Vorlagen samt
Positionstabelle, sein gelernter Kopf-Fingerabdruck, sein Positionsmodus und
sein Gedaechtnis aus bestaetigten Rechnungen (plausi.py) sind ausgeblendet.
Stammdaten (Name, USt-IdNr, IBAN, Rechnungsadressen) bleiben -- sie kaemen bei
einem neuen Lieferanten aus dem Stammdaten-Import. Was ANDERE Lieferanten
beigebracht haben, wirkt normal: Layout-Familie, Spaltenwoerterbuch, KI.
Grenzen: Kopfwoerter, die (auch) bei diesem Lieferanten bestaetigt wurden,
bleiben im lieferantenuebergreifenden Spaltenwoerterbuch; ein Lieferant, der
erst aus einer Rechnung als Entwurf entstand, behaelt deren USt-IdNr/IBAN.
Zusaetzlich je Rechnung: waere sie heute ohne Mensch durchgegangen, und waere
sie es, wenn KI-Werte nicht gedeckelt waeren (``_ohne_ki_deckel``) -- und war
sie dann fehlerfrei? Gemessen wird nur, nichts wird umgestellt.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

from . import positionen as positionen_mod
from .store import Store, _atomic_write, _read_json_safe
from .template import STANDARD_FIELDS
from .textnorm import norm_ustid, parse_amount, parse_date
from .timing import distribution

TESTSATZ_DIR = "testsatz"
BERICHTE_DIR = "berichte"
BERICHT_PREFIX = "testsatz_"

RICHTIG, FALSCH, FEHLT = "richtig", "falsch", "fehlt"
# Betraege gelten als gleich bis zu einem Cent Abweichung (Vorgabe Aaron)
BETRAG_TOL = 0.01 + 1e-9

# Felder, die nicht verglichen werden: Stammdaten aus der Bibliothek waeren
# sonst immer "richtig" (sie werden nicht gelesen), Unterstrich-Schluessel
# sind keine Felder. Die USt-Aufstellung wird zeilenweise eigens verglichen.
_NICHT_VERGLEICHEN = {"ust_aufstellung"}
_NICHT_GELESEN = ("missing", "library")
# Pseudo-Feld: richtiger Lieferant aus der Bibliothek erkannt?
FELD_LIEFERANT = "lieferant"
FELD_UST = "ust_aufstellung"


# ---- Soll-Werte beim Bestaetigen merken ------------------------------------

def _dir(root: Path) -> Path:
    return Path(root) / TESTSATZ_DIR


def _jsonable(v):
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    return str(v)


def merken(store: Store, res, operator: str = "") -> Path | None:
    """Beim Bestaetigen im Training-UI: Original archivieren und die
    bestaetigten Werte als Soll-Werte ablegen. Ein erneutes Bestaetigen
    (z.B. nach einer Korrektur) ueberschreibt die Soll-Werte -- die letzte
    Entscheidung des Menschen gewinnt."""
    if not res.sha256 or res.aborted:
        return None
    original = store.archive_pdf(res.file, res.sha256) if Path(res.file).exists() else None
    felder = {}
    quellen = {}
    for name, fr in (res.fields or {}).items():
        if name.startswith("_") or name in _NICHT_VERGLEICHEN:
            continue
        if fr.source in _NICHT_GELESEN or fr.value in (None, ""):
            continue
        felder[name] = _jsonable(fr.value)
        quellen[name] = fr.source
    rec = {
        "sha256": res.sha256,
        "datei": Path(res.file).name,
        "original": original.name if original else None,
        "bestaetigt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "operator": operator or "?",
        "supplier_id": res.supplier_id,
        "supplier_name": res.supplier_name,
        "template_id": res.template_id,
        "extraktion": res.extraction,
        "felder": felder,
        "quellen": quellen,
        # vom Menschen ausdruecklich als richtig bestaetigte Felder (ohne Aenderung)
        "bestaetigte_felder": sorted(f for f in store.confirmed_fields(res.sha256) if f in felder),
        # Positionszeilen, die der Mensch einzeln bestaetigt hat (Aaron 05.11.2026)
        "bestaetigte_zeilen": sum(1 for z in positionen_mod.zeilen_status(
            res.line_items, store.zeilen(res.sha256)) if z == positionen_mod.ZEILE_BESTAETIGT),
        "positionen": _jsonable([{k: v for k, v in it.items() if not k.startswith("_")}
                                 for it in res.line_items or []]),
        "ust_aufstellung": _jsonable([{k: v for k, v in r.items() if not k.startswith("_")}
                                      for r in res.tax_breakdown or []]),
    }
    d = _dir(store.root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{res.sha256}.json"
    _atomic_write(p, json.dumps(rec, indent=2, ensure_ascii=False))
    return p


# ---- Testfaelle einsammeln -------------------------------------------------

def _archiv_datei(root: Path, sha: str, name: str | None = None) -> Path | None:
    arch = Path(root) / "archive"
    if name and (arch / name).exists():
        return arch / name
    if arch.is_dir():
        for p in arch.glob(f"{sha}.*"):
            if p.suffix != ".bak":
                return p
    return None


def _aus_processed(r: dict) -> dict:
    """Testfall aus einem Journal-Eintrag (vor dem 10.10.2026 bestaetigt) --
    nur die Felder, die ``processed.jsonl`` kennt."""
    felder = {k: r.get(k) for k in ("rechnungsnummer", "rechnungsdatum", "netto",
                                    "ust_betrag", "brutto") if r.get(k) not in (None, "")}
    ext = r.get("extraction") or ("template" if r.get("template_id") else None)
    return {
        "sha256": r.get("sha256"), "datei": r.get("file"), "original": None,
        "bestaetigt": None, "supplier_id": r.get("supplier_id"),
        "supplier_name": r.get("supplier_name"), "template_id": r.get("template_id"),
        "extraktion": ext, "felder": felder, "quellen": {},
        "positionen": r.get("line_items") or [], "ust_aufstellung": r.get("tax_breakdown") or [],
        "hergeleitet": True,
    }


def bestaetigte(store: Store) -> list[dict]:
    """Alle bestaetigten Rechnungen mit Soll-Werten (eine je sha256) --
    unabhaengig davon, ob das Original noch da ist. Grundlage auch fuer das
    Lieferanten-Gedaechtnis (profil.py) und die E-Rechnungs-Auswertung."""
    out: dict[str, dict] = {}
    d = _dir(store.root)
    if d.is_dir():
        for p in sorted(d.glob("*.json")):
            rec = _read_json_safe(p, None)
            if isinstance(rec, dict) and rec.get("sha256"):
                out[rec["sha256"]] = rec
    confirmed = None
    for r in store.processed():
        sha = r.get("sha256")
        if not sha or sha in out:
            continue
        if confirmed is None:
            confirmed = {c.get("sha256") for c in
                         _read_jsonl(store.root / "confirmations.jsonl")}
        # autonom uebernommene Rechnungen sind kein Goldstandard -- dort hat
        # kein Mensch draufgeschaut
        if sha in confirmed and r.get("freigabe") != "autonom":
            out[sha] = _aus_processed(r)
    return list(out.values())


def _read_jsonl(p: Path) -> list[dict]:
    from .store import _read_jsonl_safe

    return _read_jsonl_safe(p)


def _passt_lieferant(fall: dict, lieferant: str | None) -> bool:
    if not lieferant:
        return True
    q = lieferant.strip().casefold()
    return q == (fall.get("supplier_id") or "").casefold() or \
        q in (fall.get("supplier_name") or "").casefold()


def _ohne_ki(store: Store, fall: dict) -> bool:
    """Kommt dieser Testfall ohne KI aus? E-Rechnung, mit Vorlage bestaetigt,
    oder fuer den Lieferanten gibt es inzwischen eine aktive Vorlage (dann
    liest der Trockenlauf ueber die Vorlage). ``extraktion`` allein reicht
    nicht: eine von Hand eingetippte Rechnung ohne Vorlage traegt dort
    ebenfalls "template" (Standardwert in pipeline.Result)."""
    if fall.get("extraktion") == "einvoice":
        return True
    if fall.get("extraktion") == "llm":
        return False
    if fall.get("template_id"):
        return True
    sid = fall.get("supplier_id")
    return bool(sid) and any(not t.stale for t in store.templates_for(sid))


def faelle(store: Store, lieferant: str | None = None,
           nur_ohne_ki: bool = False) -> tuple[list[dict], list[dict]]:
    """-> (Testfaelle mit Original im Archiv, uebersprungene mit Grund)."""
    ok, skip = [], []
    for f in bestaetigte(store):
        if not _passt_lieferant(f, lieferant):
            continue
        if nur_ohne_ki and not _ohne_ki(store, f):
            continue
        p = _archiv_datei(store.root, f["sha256"], f.get("original"))
        if p is None:
            skip.append({"sha256": f["sha256"], "datei": f.get("datei"),
                         "grund": "Original nicht im Archiv"})
            continue
        if not f.get("felder"):
            skip.append({"sha256": f["sha256"], "datei": f.get("datei"),
                         "grund": "keine Soll-Werte"})
            continue
        ok.append(dict(f, _pfad=str(p)))
    return ok, skip


def anzahl(root: str | Path, nur_ohne_ki: bool = False) -> int:
    """Wie viele Testfaelle gibt es (fuer den Selbst-Update-Schutz)?"""
    return len(faelle(Store(root), nur_ohne_ki=nur_ohne_ki)[0])


# ---- Vergleich -------------------------------------------------------------

def _typ(feld: str) -> str:
    if feld in ("iban",):
        return "iban"
    if feld in ("ustid",):
        return "ustid"
    if feld in ("rechnungsnummer", "bestellnummer"):
        return "nummer"
    return STANDARD_FIELDS.get(feld, ("text",))[0]


def _betrag(v) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return parse_amount(str(v)) if v not in (None, "") else None


def normalisiert(feld: str, v):
    """Vergleichsform: Betraege als Zahl, Datum ISO, Rechnungsnummer ohne
    Leerzeichen, IBAN/USt-IdNr kompakt, sonstiger Text ohne Gross/Klein und
    doppelte Leerzeichen."""
    if v in (None, ""):
        return None
    t = _typ(feld)
    if t == "amount":
        return _betrag(v)
    if t == "date":
        d = parse_date(str(v))
        return d.isoformat() if d else str(v).strip()
    if t == "nummer":
        return re.sub(r"\s+", "", str(v))
    if t == "iban":
        return re.sub(r"\s+", "", str(v)).upper()
    if t == "ustid":
        return norm_ustid(str(v))
    if isinstance(v, bool):
        return v
    return re.sub(r"\s+", " ", str(v)).strip().casefold()


def gleich(feld: str, soll, ist) -> bool:
    a, b = normalisiert(feld, soll), normalisiert(feld, ist)
    if a is None or b is None:
        return a is b
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= BETRAG_TOL
    return a == b


def _wert(res, feld: str):
    fr = (res.fields or {}).get(feld)
    if fr is None or fr.source == "missing" or fr.value in (None, ""):
        return None
    return fr.value


def _quelle(res, feld: str) -> str | None:
    """Woher der gelesene Wert kommt (FieldResult.source), None ohne Wert."""
    fr = (res.fields or {}).get(feld)
    if fr is None or fr.source == "missing" or fr.value in (None, ""):
        return None
    return fr.source


def _num(row: dict, key: str) -> float | None:
    return _betrag(row.get(key))


def _positionen_vergleich(soll: list[dict], ist: list[dict]) -> dict:
    """Zeile korrekt = Zeilensumme (gesamt_netto) gleich +-0,01 und, wo
    beide eine Menge haben, gleiche Menge. Zuordnung unabhaengig von der
    Reihenfolge, jede gelesene Zeile zaehlt hoechstens einmal."""
    frei = list(ist or [])
    korrekt = 0
    for s in soll or []:
        sg, sm = _num(s, "gesamt_netto"), _num(s, "menge")
        for i, z in enumerate(frei):
            zg, zm = _num(z, "gesamt_netto"), _num(z, "menge")
            if sg is None or zg is None or abs(sg - zg) > BETRAG_TOL:
                continue
            if sm is not None and zm is not None and abs(sm - zm) > 1e-6:
                continue
            korrekt += 1
            del frei[i]
            break
    return {"soll": len(soll or []), "gefunden": len(ist or []), "korrekt": korrekt}


def _ust_vergleich(soll: list[dict], ist: list[dict]) -> str | None:
    if not soll:
        return None
    if not ist:
        return FEHLT
    frei = list(ist)
    for s in soll:
        hit = None
        for i, z in enumerate(frei):
            ss, zs = _num(s, "satz"), _num(z, "satz")
            if (ss is None) != (zs is None) or (ss is not None and abs(ss - zs) > BETRAG_TOL):
                continue
            if all(_num(s, k) is None or (_num(z, k) is not None
                                          and abs(_num(s, k) - _num(z, k)) <= BETRAG_TOL)
                   for k in ("netto", "ust")):
                hit = i
                break
        if hit is None:
            return FALSCH
        del frei[hit]
    return RICHTIG


def vergleiche(fall: dict, res) -> dict:
    """Ein Testfall gegen das Ergebnis des Trockenlaufs -> je Feld
    ``{ergebnis, soll, ist}`` plus Positionen."""
    felder = {}
    for feld, soll in (fall.get("felder") or {}).items():
        if feld in _NICHT_VERGLEICHEN or soll in (None, ""):
            continue
        ist = _wert(res, feld)
        erg = FEHLT if ist is None else (RICHTIG if gleich(feld, soll, ist) else FALSCH)
        felder[feld] = {"ergebnis": erg, "soll": soll, "ist": _jsonable(ist),
                        "quelle": _quelle(res, feld)}
    if fall.get("supplier_id"):
        ist = res.supplier_id
        erg = FEHLT if not ist else (RICHTIG if ist == fall["supplier_id"] else FALSCH)
        felder[FELD_LIEFERANT] = {"ergebnis": erg, "soll": fall["supplier_id"], "ist": ist}
    ust = _ust_vergleich(fall.get("ust_aufstellung") or [], res.tax_breakdown or [])
    if ust is not None:
        felder[FELD_UST] = {"ergebnis": ust, "soll": len(fall["ust_aufstellung"]),
                            "ist": len(res.tax_breakdown or [])}
    return {"felder": felder,
            "positionen": _positionen_vergleich(fall.get("positionen") or [],
                                                res.line_items or [])}


# ---- Trockenlauf -----------------------------------------------------------

class _TrockenStore(Store):
    """Store fuer den Trockenlauf: manuelle Korrekturen werden nicht
    eingemischt -- gemessen wird, was die Maschine allein liest."""

    def corrections(self, sha: str) -> dict:  # noqa: D401
        return {}


class _BlindStore(_TrockenStore):
    """Store fuer den Blindtest: der Lieferant ``verborgen`` wirkt wie einer,
    von dem noch nie eine Rechnung bearbeitet wurde (siehe Modul-Doku).
    Liest nur anders, schreibt nichts anderes als ``_TrockenStore``."""

    def __init__(self, root, verborgen: str | None):
        super().__init__(root)
        self.verborgen = verborgen
        # plausi.profil: kein Gedaechtnis aus frueher bestaetigten Rechnungen
        self.ohne_gedaechtnis = frozenset({verborgen}) if verborgen else frozenset()

    def all_templates(self):
        return [t for t in super().all_templates() if t.supplier_id != self.verborgen]

    def template(self, tid: str):
        t = super().template(tid)
        return None if t is not None and t.supplier_id == self.verborgen else t

    def _alle_lieferanten(self):
        from .identify import POSITIONSMODUS_EINZELN

        return [dataclasses.replace(s, fingerprint=[], positionsmodus=POSITIONSMODUS_EINZELN,
                                    positionsmodus_gesetzt=False)
                if s.id == self.verborgen else s
                for s in super()._alle_lieferanten()]


# sha256 -> Lieferant des Testfalls, je Datenordner (der Arbeitsprozess
# bekommt nur den Pfad; neu aufgebaut, sobald eine sha fehlt)
_LIEFERANT_JE_SHA: dict[str, dict[str, str | None]] = {}


def _lieferant_des_falls(root, path) -> str | None:
    sha = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    key = str(root)
    m = _LIEFERANT_JE_SHA.get(key)
    if m is None or sha not in m:
        m = {f["sha256"]: f.get("supplier_id") for f in bestaetigte(Store(root))}
        _LIEFERANT_JE_SHA[key] = m
    return m.get(sha)


def verarbeiten_trocken(path, store, use_llm: bool = True,
                        create_draft_supplier: bool = False, variante: str = "A",
                        blind: bool = False):
    """Guard-Ziel (``guard.Guard(target=...)``): ``pipeline.process`` ohne
    Korrekturen und nie mit Lieferanten-Entwurf. ``blind``: ohne das, was
    das Programm vom Lieferanten dieser Rechnung gelernt hat (Blindtest)."""
    from . import semantic
    from .pipeline import process

    # Gleicher KI-Antwort-Cache wie die App: eine Rechnung mit unveraenderter
    # KI-Anfrage wird nicht erneut vom (langsamen) Modell gelesen; aendert
    # sich die Anfrage (neuer Prompt, andere Variante), ist es ein neuer
    # Schluessel und das Modell liest neu.
    semantic.set_cache_dir(Path(store.root) / "ki-cache")
    st = (_BlindStore(store.root, _lieferant_des_falls(store.root, path)) if blind
          else _TrockenStore(store.root))
    return process(path, st, use_llm=use_llm, create_draft_supplier=False, variante=variante)


TARGET = "rechnung.testsatz:verarbeiten_trocken"


def _quote(r: int, n: int) -> float | None:
    return round(100.0 * r / n, 1) if n else None


def _summe(faelle_out: list[dict]) -> tuple[dict, dict, float | None]:
    felder: dict[str, dict] = {}
    pos = {"soll": 0, "gefunden": 0, "korrekt": 0}
    for f in faelle_out:
        for feld, v in f["felder"].items():
            row = felder.setdefault(feld, {RICHTIG: 0, FALSCH: 0, FEHLT: 0})
            row[v["ergebnis"]] += 1
        for k in pos:
            pos[k] += f["positionen"][k]
    r_all = n_all = 0
    for row in felder.values():
        n = row[RICHTIG] + row[FALSCH] + row[FEHLT]
        row["quote"] = _quote(row[RICHTIG], n)
        r_all += row[RICHTIG]
        n_all += n
    pos["quote"] = _quote(pos["korrekt"], pos["soll"])
    return dict(sorted(felder.items())), pos, _quote(r_all, n_all)


def _verteilt(todo: list[dict], max_faelle: int) -> list[dict]:
    """Hoechstens ``max_faelle`` Testfaelle, reihum ueber die Lieferanten
    (erst je Lieferant einer, dann der zweite ...) -- eine Stichprobe soll
    moeglichst viele verschiedene Lieferanten enthalten."""
    gruppen: dict[str, list[dict]] = {}
    for f in todo:
        gruppen.setdefault(f.get("supplier_id") or "", []).append(f)
    out: list[dict] = []
    runde = 0
    while len(out) < max_faelle and any(len(g) > runde for g in gruppen.values()):
        for g in gruppen.values():
            if runde < len(g) and len(out) < max_faelle:
                out.append(g[runde])
        runde += 1
    return out


def _eintrag(fall: dict, res, blind: bool) -> dict:
    """Ergebnis EINES Testfalls (Vergleich + bei Blindtest die Bewertung)."""
    v = vergleiche(fall, res)
    eintrag = {
        "sha256": fall["sha256"], "datei": fall.get("datei"),
        "lieferant": fall.get("supplier_id"),
        "lieferant_name": fall.get("supplier_name") or "",
        "extraktion_soll": fall.get("extraktion"),
        "extraktion_ist": res.extraction,
        "hergeleitet": bool(fall.get("hergeleitet")),
        "dauer_s": res.duration_s, "abgebrochen": res.aborted,
        **v,
    }
    if blind:
        eintrag.update(_blind_bewertung(res, v))
    return eintrag


def _filter(lieferant, variante: str, nur_ohne_ki: bool, blind: bool, max_faelle: int,
            ki_aktiv: bool | None = None) -> dict:
    filt = {"lieferant": lieferant or None, "variante": variante,
            "nur_ohne_ki": bool(nur_ohne_ki)}
    # nur gesetzt, wenn benutzt -- sonst passt der Filter alter Berichte nicht
    # mehr und der Vergleich mit dem letzten Lauf ginge verloren
    if blind:
        filt["blind"] = True
        # Blindtest mit und ohne KI sind nicht vergleichbar
        filt["ki"] = bool(ki_aktiv)
    if max_faelle and max_faelle > 0:
        filt["max"] = int(max_faelle)
    return filt


def _bericht_bauen(out: list[dict], skip: list[dict], filt: dict, blind: bool,
                   dauer_s: float, ki: dict | None = None) -> dict:
    felder, pos, quote = _summe(out)
    bericht = {
        "art": "blindtest" if blind else "testsatz",
        "erstellt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "filter": filt,
        "anzahl": len(out),
        "uebersprungen": skip,
        "trefferquote": quote,
        "felder": felder,
        "positionen": pos,
        "zeit_s": distribution([f["dauer_s"] for f in out]),
        "abgebrochen": sum(1 for f in out if f["abgebrochen"]),
        "dauer_gesamt_s": round(dauer_s, 2),
        "faelle": out,
    }
    if blind:
        bericht["blind"] = _blind_summe(out, ki=ki or {"aktiv": False, "modell": None})
    return bericht


def laufen(store: Store, lieferant: str | None = None, variante: str = "A",
           nur_ohne_ki: bool = False, fortschritt=None, guard=None,
           blind: bool = False, max_faelle: int = 0) -> dict:
    """Alle Testfaelle im Trockenlauf verarbeiten und vergleichen.
    ``fortschritt(fertig, gesamt, datei)`` wird je Rechnung aufgerufen
    (Hintergrundlauf im UI). Speichert nichts -- siehe ``speichern``.

    ``blind``: Blindtest (siehe Modul-Doku); ``nur_ohne_ki`` heisst dann
    "alle Testfaelle, aber ohne KI lesen" (misst, was ohne KI von anderen
    Lieferanten uebertragen wird). ``max_faelle``: Stichprobe reihum ueber
    die Lieferanten (0 = alle)."""
    from .guard import Guard
    from .semantic import pruefe_variante

    variante = pruefe_variante(variante)
    todo, skip = faelle(store, lieferant, nur_ohne_ki and not blind)
    if max_faelle and max_faelle > 0:
        todo = _verteilt(todo, max_faelle)
    t0 = time.monotonic()
    own = guard is None
    extra = {"variante": variante, **({"blind": True} if blind else {})}
    guard = guard or Guard(store.root, target=TARGET, extra=extra, protokoll=False)
    out = []
    try:
        for i, fall in enumerate(todo):
            if fortschritt:
                fortschritt(i, len(todo), fall.get("datei") or "")
            # ohne KI: Faelle, die beim Bestaetigen ohne KI auskamen, auch
            # ohne KI nachlesen (schnell, und genau das wird dort gemessen)
            res = guard.process(fall["_pfad"], use_llm=not nur_ohne_ki)
            out.append(_eintrag(fall, res, blind))
    finally:
        if own:
            guard.close()
    if fortschritt:
        fortschritt(len(todo), len(todo), "")
    ki = _ki_stand(use_llm=not nur_ohne_ki) if blind else None
    filt = _filter(lieferant, variante, nur_ohne_ki, blind, max_faelle,
                   ki["aktiv"] if ki else None)
    return _bericht_bauen(out, skip, filt, blind, time.monotonic() - t0, ki)


# ---- Blindtest in Etappen (Leerlauf, leerlauf.py) ---------------------------
# Der Leerlauf hat je Durchgang nur ein begrenztes Zeitfenster und muss fuer
# neue Rechnungen sofort Platz machen -- der Blindtest laeuft deshalb in
# Etappen: Stand nach JEDER Rechnung in data/leerlauf/blindtest-laufend.json,
# der naechste Durchgang macht dort weiter. Erst wenn alle Faelle durch sind,
# entsteht ein normaler Blindtest-Bericht (mit Vergleich zum letzten).

LAUFEND_DATEI = "blindtest-laufend.json"


def _laufend_pfad(root) -> Path:
    return Path(root) / "leerlauf" / LAUFEND_DATEI


def blind_laufend(root) -> dict | None:
    if not _laufend_pfad(root).exists():
        return None
    d = _read_json_safe(_laufend_pfad(root), None)
    return d if isinstance(d, dict) and isinstance(d.get("offen"), list) else None


def blind_etappe(store: Store, *, bis: float, abbrechen=lambda: False,
                 max_faelle: int = 0, guard=None) -> dict:
    """Einen Teil des Blindtests rechnen, bis ``bis`` (time.monotonic) oder
    ``abbrechen()`` wahr ist. -> {"fertig", "erledigt", "offen", "bericht"}"""
    from .guard import Guard

    root = Path(store.root)
    stand = blind_laufend(root)
    alle, skip = faelle(store)
    by_sha = {f["sha256"]: f for f in alle}
    if stand is None:
        todo = _verteilt(alle, max_faelle) if max_faelle and max_faelle > 0 else alle
        stand = {"gestartet": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                 "max": int(max_faelle or 0),
                 "offen": [f["sha256"] for f in todo], "fertig": [],
                 "uebersprungen": skip, "dauer_s": 0.0}
    _laufend_pfad(root).parent.mkdir(parents=True, exist_ok=True)
    own = guard is None
    guard = guard or Guard(root, target=TARGET, extra={"variante": "A", "blind": True},
                           protokoll=False)
    t0 = time.monotonic()
    try:
        while stand["offen"] and time.monotonic() < bis and not abbrechen():
            sha = stand["offen"].pop(0)
            fall = by_sha.get(sha)
            if fall is not None:
                res = guard.process(fall["_pfad"], use_llm=True)
                stand["fertig"].append(_eintrag(fall, res, blind=True))
            _atomic_write(_laufend_pfad(root), json.dumps(stand, ensure_ascii=False))
    finally:
        if own:
            guard.close()
    stand["dauer_s"] = round(float(stand.get("dauer_s") or 0) + time.monotonic() - t0, 2)
    if stand["offen"]:
        _atomic_write(_laufend_pfad(root), json.dumps(stand, ensure_ascii=False))
        return {"fertig": False, "erledigt": len(stand["fertig"]), "offen": len(stand["offen"]),
                "bericht": None}
    ki = _ki_stand(use_llm=True)
    filt = _filter(None, "A", False, True, stand.get("max") or 0, ki["aktiv"])
    bericht = _bericht_bauen(stand["fertig"], stand.get("uebersprungen") or [], filt, True,
                             stand["dauer_s"], ki)
    bericht["leerlauf"] = {"gestartet": stand.get("gestartet")}
    bericht["vergleich"] = vergleich(letzter_bericht(root, filt), bericht)
    bericht["datei"] = str(speichern(root, bericht))
    _laufend_pfad(root).unlink(missing_ok=True)
    # die Sicherungskopie von _atomic_write mit -- sonst holt _read_json_safe
    # den abgeschlossenen Lauf daraus zurueck
    _laufend_pfad(root).with_name(LAUFEND_DATEI + ".bak").unlink(missing_ok=True)
    return {"fertig": True, "erledigt": len(stand["fertig"]), "offen": 0,
            "bericht": bericht["datei"]}


# ---- Blindtest: waere die Rechnung ohne Mensch durchgegangen? --------------

PFLICHTFELDER = tuple(n for n, (_t, req) in STANDARD_FIELDS.items() if req)

# Anzeige der Herkunft eines Werts (FieldResult.source)
QUELLEN_TEXT = {"template-anchor": "Vorlage", "template-abs": "Vorlage", "llm": "KI",
                "xml": "E-Rechnung", "einvoice": "E-Rechnung", "summen": "Betragsherleitung",
                "tabelle": "Tabelle", "manual": "Hand", "library": "Stammdaten"}


def _ki_stand(use_llm: bool) -> dict:
    """War die KI in diesem Lauf an und erreichbar? (fuer die Deutung)"""
    from . import semantic
    from .config import llm

    an = bool(use_llm) and semantic.available()
    return {"aktiv": an, "modell": llm().model if an else None}


def _ohne_ki_deckel(res):
    """Kopie des Ergebnisses, in der KI-Werte nicht mehr nur wegen des festen
    Deckels (semantic.ki_konfidenz) als unsicher gelten. Werte, deren
    Konfidenz aus einem anderen Grund niedriger ist (z.B. unsicher erkannte
    Woerter im Scan), bleiben unsicher; Pruefungen bleiben, wie sie sind."""
    from .pipeline import CONF_MIN
    from .semantic import ki_konfidenz

    deckel = ki_konfidenz()
    kopie = copy.copy(res)
    kopie.fields = {
        n: (dataclasses.replace(fr, confidence=max(fr.confidence, CONF_MIN))
            if fr.source == "llm" and abs(fr.confidence - deckel) < 1e-6 else fr)
        for n, fr in (res.fields or {}).items()}
    return kopie


def _mit_gegenpruefung(res):
    """Kopie des Ergebnisses, als waere die Gegenpruefung (gegenpruefung.py)
    eingeschaltet -- None, wenn sie bei dieser Rechnung nicht greifen wuerde
    (Lieferant nicht hart erkannt oder nicht alle Pflichtfelder belegt)."""
    from . import gegenpruefung

    g = getattr(res, "gegenpruefung", None) or {}
    if not (g.get("hart_erkannt") and g.get("vollstaendig")):
        return None
    kopie = copy.copy(res)
    kopie.fields = dict(res.fields or {})
    gegenpruefung.heben(kopie, g.get("belegt") or [])
    return kopie


def _blind_bewertung(res, v: dict) -> dict:
    """Je Rechnung: komplett richtig? waere sie heute / ohne KI-Deckel ohne
    Mensch durchgegangen? welche Felder waren falsch?"""
    from . import human

    felder = v["felder"]
    pflicht = [f for f in (*PFLICHTFELDER, FELD_LIEFERANT) if f in felder]
    pos = v["positionen"]
    ok = not res.aborted
    gruende = [g["grund"] for g in human.gruende(_ohne_ki_deckel(res))] if ok else ["abgebrochen"]
    return {
        "komplett_richtig": bool(pflicht) and all(felder[f]["ergebnis"] == RICHTIG
                                                  for f in pflicht),
        "falsche_felder": sorted(f for f, x in felder.items() if x["ergebnis"] == FALSCH),
        "sicher_heute": ok and human.ist_sicher(res),
        "sicher_ohne_ki_deckel": ok and not gruende,
        "gruende_ohne_ki_deckel": gruende,
        "sicher_mit_gegenpruefung": ok and (gp := _mit_gegenpruefung(res)) is not None
                                    and human.ist_sicher(gp),
        "positionen_abweichend": bool(pos["soll"]) and pos["korrekt"] < pos["soll"],
    }


def _blind_summe(out: list[dict], ki: dict) -> dict:
    n = len(out)
    komplett = sum(1 for f in out if f["komplett_richtig"])
    heute = [f for f in out if f["sicher_heute"]]
    ohne = [f for f in out if f["sicher_ohne_ki_deckel"]]
    fehlerhaft = [f for f in ohne if f["falsche_felder"]]
    gegen = [f for f in out if f.get("sicher_mit_gegenpruefung")]
    gegen_fehler = [f for f in gegen if f["falsche_felder"]]
    # Trefferquote je Feld und Herkunft des Werts
    nach_quelle: dict[str, dict[str, dict]] = {}
    for f in out:
        for feld, x in f["felder"].items():
            q = x.get("quelle")
            if not q:
                continue
            row = nach_quelle.setdefault(feld, {}).setdefault(q, {RICHTIG: 0, FALSCH: 0})
            if x["ergebnis"] in row:
                row[x["ergebnis"]] += 1
    for feld in nach_quelle.values():
        for row in feld.values():
            row["quote"] = _quote(row[RICHTIG], row[RICHTIG] + row[FALSCH])
    # Warum ging es trotz fehlendem Deckel nicht durch? (haeufigste Gruende)
    gruende = Counter(re.sub(r"\s*\(.*$", "", g) for f in out if not f["sicher_ohne_ki_deckel"]
                      for g in f.get("gruende_ohne_ki_deckel") or [])
    return {
        "ki": ki,
        "lieferanten": len({f["lieferant"] for f in out if f.get("lieferant")}),
        "komplett_richtig": komplett,
        "komplett_quote": _quote(komplett, n),
        "sicher_heute": len(heute),
        "sicher_heute_fehlerhaft": sum(1 for f in heute if f["falsche_felder"]),
        "sicher_ohne_ki_deckel": len(ohne),
        "davon_fehlerhaft": len(fehlerhaft),
        "fehler_felder": dict(Counter(x for f in fehlerhaft
                                      for x in f["falsche_felder"]).most_common()),
        "davon_positionen_abweichend": sum(1 for f in ohne if f["positionen_abweichend"]),
        "sicher_mit_gegenpruefung": len(gegen),
        "gegenpruefung_fehlerhaft": len(gegen_fehler),
        "gegenpruefung_fehler_felder": dict(Counter(x for f in gegen_fehler
                                                    for x in f["falsche_felder"]).most_common()),
        "nicht_durch_gruende": dict(gruende.most_common(8)),
        "nach_quelle": dict(sorted(nach_quelle.items())),
    }


def _blind_text(bl: dict, n: int) -> list[str]:
    ki = bl.get("ki") or {}
    lines = [f"  Lieferanten im Test: {bl['lieferanten']}",
             (f"  KI: an ({ki.get('modell')})" if ki.get("aktiv") else
              "  KI: aus -- gemessen wird nur, was ohne KI von anderen Lieferanten "
              "uebertragen wird")]
    q = bl.get("komplett_quote")
    lines.append(f"  Komplett richtig (Lieferant + Pflichtfelder): {bl['komplett_richtig']} von {n}"
                 + (f" ({q:.1f} %)" if q is not None else ""))
    lines.append(f"  Waeren heute ohne Mensch durchgegangen: {bl['sicher_heute']}"
                 f" (davon fehlerhaft: {bl['sicher_heute_fehlerhaft']})")
    fehl = ", ".join(f"{k} {v}" for k, v in bl["fehler_felder"].items())
    lines.append(f"  Waeren ohne KI-Deckel durchgegangen: {bl['sicher_ohne_ki_deckel']}, "
                 f"davon fehlerhaft: {bl['davon_fehlerhaft']}" + (f" ({fehl})" if fehl else ""))
    if bl.get("davon_positionen_abweichend"):
        lines.append(f"    davon mit abweichenden Positionen: {bl['davon_positionen_abweichend']}")
    gfehl = ", ".join(f"{k} {v}" for k, v in (bl.get("gegenpruefung_fehler_felder") or {}).items())
    lines.append(f"  Waeren mit Gegenpruefung durchgegangen: {bl.get('sicher_mit_gegenpruefung', 0)}, "
                 f"davon fehlerhaft: {bl.get('gegenpruefung_fehlerhaft', 0)}"
                 + (f" ({gfehl})" if gfehl else ""))
    if bl.get("nicht_durch_gruende"):
        lines.append("  Haeufigste Gruende, warum es trotzdem nicht durchging:")
        lines.extend(f"    {anz:4d}  {g}" for g, anz in bl["nicht_durch_gruende"].items())
    if bl.get("nach_quelle"):
        lines.append("  Trefferquote nach Herkunft des Werts:")
        for feld, quellen in bl["nach_quelle"].items():
            teile = []
            for qn, row in sorted(quellen.items(), key=lambda kv: -(kv[1][RICHTIG] + kv[1][FALSCH])):
                ges = row[RICHTIG] + row[FALSCH]
                teile.append(f"{QUELLEN_TEXT.get(qn, qn)} {row[RICHTIG]}/{ges}"
                             + (f" ({row['quote']:.0f} %)" if row["quote"] is not None else ""))
            lines.append(f"    {feld:18s} " + ", ".join(teile))
    return lines


# ---- Berichte speichern/vergleichen ----------------------------------------

def _berichte(root: Path) -> list[Path]:
    d = Path(root) / BERICHTE_DIR
    return sorted(d.glob(f"{BERICHT_PREFIX}*.json")) if d.is_dir() else []


def letzter_bericht(root: str | Path, filter_: dict | None = None) -> dict | None:
    """Juengster gespeicherter Lauf (mit gleichem Filter, falls angegeben).
    Ohne Filter (Anzeige "letzter Testsatz" im Programm) zaehlen Blindtests
    nicht -- sie messen etwas anderes und haben eine eigene Trefferquote."""
    for p in reversed(_berichte(Path(root))):
        b = _read_json_safe(p, None)
        if not isinstance(b, dict):
            continue
        if filter_ is None and (b.get("filter") or {}).get("blind"):
            continue
        if filter_ is not None and b.get("filter") != filter_:
            continue
        return dict(b, datei=str(p))
    return None


def vergleich(alt: dict | None, neu: dict) -> dict:
    """Was ist gegenueber dem letzten Lauf schlechter geworden? Je Feld
    (Quote gesunken) und je Rechnung (Feld war richtig, jetzt nicht)."""
    if not alt:
        return {"vorher": None, "trefferquote_vorher": None, "schlechter": [], "besser": []}
    schlechter, besser = [], []
    for feld, row in neu.get("felder", {}).items():
        a = (alt.get("felder") or {}).get(feld, {}).get("quote")
        b = row.get("quote")
        if a is None or b is None:
            continue
        if b < a:
            schlechter.append(f"{feld}: {a:.1f} % -> {b:.1f} %")
        elif b > a:
            besser.append(f"{feld}: {a:.1f} % -> {b:.1f} %")
    alt_faelle = {f["sha256"]: f for f in alt.get("faelle") or []}
    for f in neu.get("faelle") or []:
        af = alt_faelle.get(f["sha256"])
        if not af:
            continue
        weg = [feld for feld, v in f["felder"].items()
               if v["ergebnis"] != RICHTIG
               and (af.get("felder") or {}).get(feld, {}).get("ergebnis") == RICHTIG]
        if weg:
            who = f.get("lieferant_name") or f.get("lieferant") or "?"
            schlechter.append(f"{f.get('datei')} ({who}): {', '.join(weg)}")
    pa, pn = (alt.get("positionen") or {}).get("quote"), (neu.get("positionen") or {}).get("quote")
    if pa is not None and pn is not None and pn < pa:
        schlechter.append(f"Positionen: {pa:.1f} % -> {pn:.1f} %")
    return {"vorher": alt.get("datei"), "trefferquote_vorher": alt.get("trefferquote"),
            "schlechter": schlechter, "besser": besser}


def speichern(root: str | Path, bericht: dict) -> Path:
    d = Path(root) / BERICHTE_DIR
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{BERICHT_PREFIX}{time.strftime('%Y-%m-%d_%H%M%S')}.json"
    n = 1
    while p.exists():  # zwei Laeufe in derselben Sekunde (Tests)
        p = d / f"{BERICHT_PREFIX}{time.strftime('%Y-%m-%d_%H%M%S')}_{n}.json"
        n += 1
    _atomic_write(p, json.dumps(bericht, indent=2, ensure_ascii=False))
    return p


def pruefen(store: Store, speichern_: bool = True, **kw) -> dict:
    """Lauf + Vergleich mit dem letzten Lauf gleichen Filters + speichern."""
    neu = laufen(store, **kw)
    neu["vergleich"] = vergleich(letzter_bericht(store.root, neu["filter"]), neu)
    if speichern_:
        neu["datei"] = str(speichern(store.root, neu))
    return neu


def text(b: dict) -> str:
    """Kurzbericht fuer die Kommandozeile."""
    lines = []
    flt = b.get("filter") or {}
    blind = bool(flt.get("blind"))
    tag = ", ".join(x for x in (
        f"Lieferant {flt['lieferant']}" if flt.get("lieferant") else "",
        f"Variante {flt.get('variante')}",
        ("ohne KI" if blind else "nur ohne KI") if flt.get("nur_ohne_ki") else "",
        f"Stichprobe {flt['max']}" if flt.get("max") else "") if x)
    q = b.get("trefferquote")
    titel = ("Blindtest -- jeder Lieferant so gelesen, als waere er neu" if blind
             else "Testsatz")
    lines.append(f"{titel} ({tag}): {b['anzahl']} Rechnung(en), Trefferquote "
                 + (f"{q:.1f} %" if q is not None else "-"))
    for feld, r in b.get("felder", {}).items():
        lines.append(f"  {feld:18s} richtig {r[RICHTIG]:3d}  falsch {r[FALSCH]:3d}  "
                     f"fehlt {r[FEHLT]:3d}  ({r['quote']:.1f} %)" if r["quote"] is not None
                     else f"  {feld:18s} -")
    pos = b.get("positionen") or {}
    if pos.get("soll"):
        lines.append(f"  Positionen: {pos['korrekt']} von {pos['soll']} Zeilen korrekt, "
                     f"{pos['gefunden']} gefunden")
    z = b.get("zeit_s") or {}
    if z:
        lines.append(f"  Zeit je Rechnung: Median {z['median']:.1f} s, P90 {z['p90']:.1f} s, "
                     f"Max {z['max']:.1f} s")
    if b.get("abgebrochen"):
        lines.append(f"  {b['abgebrochen']} Rechnung(en) abgebrochen (Zeitgrenze/Fehler)")
    if b.get("blind"):
        lines.extend(_blind_text(b["blind"], b["anzahl"]))
    for s in b.get("uebersprungen") or []:
        lines.append(f"  uebersprungen {s.get('datei')}: {s['grund']}")
    v = b.get("vergleich") or {}
    if v.get("vorher"):
        qv = v.get("trefferquote_vorher")
        lines.append(f"Letzter Lauf: {Path(v['vorher']).name} "
                     f"({qv:.1f} %)" if qv is not None else f"Letzter Lauf: {v['vorher']}")
        if v["schlechter"]:
            lines.append("Schlechter geworden bei: " + "; ".join(v["schlechter"]))
        else:
            lines.append("Nichts schlechter geworden.")
    if b.get("datei"):
        lines.append(f"Bericht: {b['datei']}")
    return "\n".join(lines)
