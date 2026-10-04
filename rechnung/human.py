"""Ordner "Menschliche Bearbeitung" (SPEC §7u).

Grundsatz: **nichts wird geraten**. Jede Rechnung, bei der das Programm nicht
sicher ist, landet als Kopie des Originals in einem eigenen Ordner
(``RECHNUNG_MENSCHLICH``, Standard ``<data>/menschlich/``) -- daneben eine
kleine Begleitdatei ``<datei>.grund.json`` mit den Gruenden, den betroffenen
Feldern und den Vorschlagswerten samt Herkunft. Der Ordner ist bewusst ein
normaler Dateiordner: man sieht im Explorer sofort, was liegen geblieben ist.

"Nicht sicher" heisst (``gruende()``):

- zurueckgestellt (kein eindeutiger Lieferant/keine eindeutige
  Rechnungsadresse aus der Bibliothek, IBAN weicht von den Stammdaten ab, OCR
  nicht verfuegbar, Zeitgrenze, Verarbeitungsfehler),
- ein Pflichtfeld fehlt,
- eine Pruefung ist rot (Summen, IBAN-Pruefziffer, Datum, Dublette, ...),
- OCR unsicher (Pruefung ``ocr-konfidenz`` rot),
- ein gelesenes Feld liegt unter der Mindestkonfidenz (z.B. reiner
  KI-Vorschlag, unsicher erkanntes OCR-Wort).

Bearbeitet und bestaetigt der Mensch die Rechnung im Training-UI, verlaesst
sie den Ordner (``erledigt()``): Kopie + Begleitdatei werden entfernt, die
Begleitdatei wandert mit Bearbeiter/Zeitpunkt/korrigierten Feldern nach
``menschlich/erledigt/<sha>.json``, ein Ereignis geht ins Fehlerprotokoll
(eventlog.py). Gelernt wird dabei wie bei jeder Bestaetigung (learn.py) --
die naechste Rechnung desselben Lieferanten kann das Programm dann selbst.

Die Originale im Eingangsordner bleiben unveraendert (Archiv/Hash haengen
immer am Original); der Ordner enthaelt nur Arbeitskopien.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

SIDECAR_SUFFIX = ".grund.json"
DONE_DIR = "erledigt"

# Arten von Gruenden (Feld ``art`` je Grund) -- fuer Anzeige und Fehlerbericht
ART_ZURUECKGESTELLT = "zurueckgestellt"
ART_ZEITGRENZE = "zeitgrenze"
ART_FEHLER = "fehler"
ART_OCR = "ocr"
ART_IBAN = "iban"
ART_PFLICHTFELD = "pflichtfeld"
ART_PRUEFUNG = "pruefung"
ART_UNSICHER = "unsicher"
ART_NICHT_LESBAR = "nicht_lesbar"
ART_MAIL = "mail"            # Postfach: Mail ohne verwertbaren Rechnungsanhang
ART_ANHANG = "anhang"        # Anhang verschluesselt/zu gross/nicht zuzuordnen

# Status eines Eintrags, der gar nicht erst durch die Pipeline lief
# (ablegen_datei: Mail ohne Anhang, verschluesselter Anhang, ...)
STATUS_NICHT_VERARBEITET = "nicht_verarbeitet"

# Welche Felder eine rote Pruefung betrifft (fuer "betroffene Felder")
_CHECK_FIELDS = {
    "netto+ust=brutto": ("netto", "ust_betrag", "brutto"),
    "betraege-plausibel": ("netto", "ust_betrag", "brutto"),
    "iban-pruefziffer": ("iban",),
    "iban-stammdaten": ("iban",),
    "ustid-stammdaten": ("ustid",),
    "rechnungsdatum-plausibel": ("rechnungsdatum",),
    "keine-dublette": ("rechnungsnummer",),
    "ust-aufstellung-summe": ("ust_aufstellung",),
    "positionen-summe": ("netto",),
    "pdf-xml-abgleich": ("rechnungsnummer", "brutto"),
}

# Quellen, deren Konfidenz nicht "gelesen" ist (Stammdaten/Mensch/fehlt)
_NOT_READ = ("library", "manual", "missing")


def human_dir(data_root) -> Path:
    """Ordner fuer die menschliche Bearbeitung. ``RECHNUNG_MENSCHLICH``
    ueberschreibt den Standard ``<data>/menschlich``."""
    v = os.environ.get("RECHNUNG_MENSCHLICH")
    return Path(v) if v else Path(data_root) / "menschlich"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def file_name(path) -> str:
    """Dateiname aus einem Pfad -- mit ``/`` UND ``\\`` als Trenner, damit ein
    unter Windows erzeugter Pfad auch anderswo richtig zerlegt wird."""
    return re.split(r"[\\/]", str(path or ""))[-1]


def _hold_art(reason: str) -> str:
    from .identify import IBAN_DEVIATES

    if IBAN_DEVIATES in reason:
        return ART_IBAN
    if reason.startswith("Zeitgrenze"):
        return ART_ZEITGRENZE
    if reason.startswith("Verarbeitungsfehler"):
        return ART_FEHLER
    if reason.startswith("OCR"):
        return ART_OCR
    if reason.startswith("PDF passwortgeschuetzt"):
        return ART_ANHANG
    return ART_ZURUECKGESTELLT


def gruende(res) -> list[dict]:
    """Warum ist das Programm bei dieser Rechnung nicht sicher? Leere Liste =
    sicher. Jeder Grund: ``{"art", "grund", "felder"}``."""
    from .config import iban_mode
    from .pipeline import CONF_MIN, DEFERRED, UNSUPPORTED
    from .shadow import REQUIRED

    out: list[dict] = []
    if res.status == UNSUPPORTED:
        return [{"art": ART_NICHT_LESBAR, "grund": "Datei weder lesbares PDF noch E-Rechnung",
                 "felder": []}]
    for why in res.hold_reasons or []:
        out.append({"art": _hold_art(why), "grund": why,
                    "felder": ["iban"] if _hold_art(why) == ART_IBAN else []})
    # Abbruch (Zeitgrenze/Fehler) oder Text gar nicht lesbar (OCR nicht
    # verfuegbar/aus): es gibt keine Felder -- die Rueckstellung ist der Grund.
    if res.aborted or (res.ocr and res.ocr.get("error")) or any(
            o["art"] == ART_ANHANG for o in out):
        return out
    if res.status == DEFERRED and not out:
        out.append({"art": ART_ZURUECKGESTELLT, "grund": "zurueckgestellt", "felder": []})

    covered = {"rechnungsadresse"} if res.hold_reasons else set()
    if iban_mode() == "warnen":
        # Abweichende IBAN geht mit Warnfeld durch (res.warnings), siehe
        # pipeline._apply_hold -- keine eigene Rueckfrage beim Menschen.
        covered.add("iban-stammdaten")
    if any(o["art"] == ART_IBAN for o in out):
        covered.add("iban-stammdaten")
    for c in res.checks or []:
        if c.ok is not False or c.name in covered:
            continue
        if c.name == "ocr-konfidenz":
            out.append({"art": ART_OCR, "grund": f"OCR unsicher ({c.detail})", "felder": []})
            continue
        out.append({"art": ART_PRUEFUNG,
                    "grund": f"Pruefung rot: {c.name}" + (f" ({c.detail})" if c.detail else ""),
                    "felder": list(_CHECK_FIELDS.get(c.name, ()))})

    fields = res.fields or {}
    missing = [n for n in REQUIRED
               if n not in fields or fields[n].source == "missing"
               or fields[n].value in (None, "")]
    if missing:
        out.append({"art": ART_PFLICHTFELD, "grund": "Pflichtfeld fehlt: " + ", ".join(missing),
                    "felder": missing})
    unsure = sorted(n for n, f in fields.items()
                    if n not in missing and f.source not in _NOT_READ
                    and f.value not in (None, "") and f.confidence < CONF_MIN)
    if unsure:
        out.append({"art": ART_UNSICHER,
                    "grund": f"Wert unsicher (Konfidenz < {CONF_MIN:g}): " + ", ".join(unsure),
                    "felder": unsure})
    return out


def ist_sicher(res) -> bool:
    return not gruende(res)


def vorschlaege(res) -> dict:
    """Vorschlagswerte mit Herkunft je Feld (was der Mensch vorfindet)."""
    d = res.to_dict()
    return {n: {"wert": f["value"], "herkunft": f["origin"], "konfidenz": f["confidence"],
                "via": f.get("via")}
            for n, f in d["fields"].items() if f["source"] != "missing"}


# ---- Ordner ---------------------------------------------------------------

def _sidecars(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(folder.glob("*" + SIDECAR_SUFFIX))


def _read(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write(p: Path, data: dict) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), "utf-8")
    tmp.replace(p)


def offen(store) -> list[dict]:
    """Alle Rechnungen, die gerade auf einen Menschen warten (aelteste zuerst)."""
    rows = [d for p in _sidecars(human_dir(store.root)) if (d := _read(p))]
    return sorted(rows, key=lambda d: d.get("abgelegt") or "")


def finde(store, sha256: str) -> tuple[Path, dict] | None:
    if not sha256:
        return None
    for p in _sidecars(human_dir(store.root)):
        d = _read(p)
        if d and d.get("sha256") == sha256:
            return p, d
    return None


def datei_pfad(store, name: str) -> Path | None:
    """Arbeitskopie im Ordner zu einem Dateinamen (fuer das Training-UI)."""
    folder = human_dir(store.root)
    p = (folder / file_name(name)).resolve()
    if p.exists() and p.is_file() and folder.resolve() in p.parents \
            and not p.name.endswith(SIDECAR_SUFFIX):
        return p
    return None


def _target_name(folder: Path, name: str, sha256: str) -> str:
    """Dateiname im Ordner -- bei Namensgleichheit mit einer ANDEREN Rechnung
    wird der Hash-Anfang vorangestellt, nichts wird ueberschrieben."""
    if not (folder / name).exists() and not (folder / (name + SIDECAR_SUFFIX)).exists():
        return name
    other = _read(folder / (name + SIDECAR_SUFFIX))
    if other and other.get("sha256") == sha256:
        return name
    return f"{sha256[:8]}_{name}"


def ablegen(store, res, grund_liste: list[dict] | None = None,
            operator: str = "") -> dict | None:
    """Unsichere Rechnung in den Ordner legen bzw. ihre Begleitdatei
    aktualisieren (erneute Verarbeitung). Sichere oder bereits vom Menschen
    bestaetigte Rechnungen werden nicht abgelegt -> ``None``."""
    from . import eventlog
    from .config import operator as _operator

    grund_liste = gruende(res) if grund_liste is None else grund_liste
    if not grund_liste or not res.sha256 or store.is_confirmed(res.sha256):
        return None
    folder = human_dir(store.root)
    folder.mkdir(parents=True, exist_ok=True)
    found = finde(store, res.sha256)
    name = found[1]["datei"] if found else _target_name(folder, file_name(res.file), res.sha256)
    src = Path(res.file)
    if src.exists() and not (folder / name).exists():
        shutil.copy2(src, folder / name)
    felder = sorted({f for g in grund_liste for f in g.get("felder") or []})
    rec = {
        "sha256": res.sha256,
        "datei": name,
        "original": str(res.file),
        "lieferant": res.supplier_id,
        "lieferant_name": res.supplier_name,
        "status": res.status,
        "gruende": grund_liste,
        "felder": felder,
        "vorschlaege": vorschlaege(res),
        "abgelegt": found[1].get("abgelegt") if found else _now(),
        "aktualisiert": _now(),
    }
    _write(folder / (name + SIDECAR_SUFFIX), rec)
    if not found:
        op = operator or _operator()
        store.audit("menschlich_abgelegt", {
            "sha256": res.sha256, "datei": name,
            "gruende": [g["grund"] for g in grund_liste]}, op)
        eventlog.record_human(store, rec, eventlog.HUMAN_PLACED, op)
    return rec


def ablegen_datei(store, name: str, content: bytes, grund_liste: list[dict], *,
                  beilagen: dict[str, bytes] | None = None, herkunft: dict | None = None,
                  status: str = STATUS_NICHT_VERARBEITET, operator: str = "") -> dict | None:
    """Etwas, das (noch) gar keine Rechnung im Sinne der Pipeline ist, an den
    Menschen geben -- z.B. eine Mail ohne Rechnungsanhang (als ``.eml``),
    eine im Mailtext stehende Rechnung (als PDF), ein verschluesselter oder
    nicht zuzuordnender Anhang. Gleiches Format wie ``ablegen`` (Datei +
    ``<datei>.grund.json``), ``sha256`` ist der Hash von ``content``.
    ``beilagen`` (Suffix -> Inhalt, z.B. ``{".eml": rohmail}``) liegen als
    ``<datei><suffix>`` daneben und verschwinden mit ``erledigt_sha``.
    Bereits abgelegt (gleicher Hash) -> Begleitdatei nur aktualisiert."""
    import hashlib

    from . import eventlog
    from .config import operator as _operator

    if not grund_liste or not content:
        return None
    sha = hashlib.sha256(content).hexdigest()
    folder = human_dir(store.root)
    folder.mkdir(parents=True, exist_ok=True)
    found = finde(store, sha)
    name = found[1]["datei"] if found else _target_name(folder, file_name(name), sha)
    if not (folder / name).exists():
        (folder / name).write_bytes(content)
    extra = []
    for suffix, data in (beilagen or {}).items():
        p = folder / (name + suffix)
        if not p.exists():
            p.write_bytes(data)
        extra.append(p.name)
    rec = {
        "sha256": sha,
        "datei": name,
        "original": (herkunft or {}).get("original", ""),
        "lieferant": None,
        "lieferant_name": None,
        "status": status,
        "gruende": grund_liste,
        "felder": [],
        "vorschlaege": {},
        "beilagen": extra,
        "herkunft": herkunft or {},
        "abgelegt": found[1].get("abgelegt") if found else _now(),
        "aktualisiert": _now(),
    }
    _write(folder / (name + SIDECAR_SUFFIX), rec)
    if not found:
        op = operator or _operator()
        store.audit("menschlich_abgelegt", {
            "sha256": sha, "datei": name,
            "gruende": [g["grund"] for g in grund_liste]}, op)
        eventlog.record_human(store, rec, eventlog.HUMAN_PLACED, op)
    return rec


def erledigt_sha(store, sha256: str, operator: str = "", notiz: str = "") -> dict | None:
    """Eintrag ohne Rechnungs-Ergebnis von Hand als erledigt markieren (z.B.
    Mail ohne Anhang gesichtet, Rechnung per Link geholt und in den Eingang
    gelegt). Wie ``erledigt``: Kopie + Beilagen weg, Begleitdatei mit
    Vermerk nach ``erledigt/``, Audit + Ereignis."""
    from . import eventlog
    from .config import operator as _operator

    found = finde(store, sha256)
    if found is None:
        return None
    side, rec = found
    folder = side.parent
    op = operator or _operator()
    rec = dict(rec, erledigt=_now(), bearbeiter=op, freigabe="mensch", notiz=notiz,
               korrigiert=[])
    done = folder / DONE_DIR
    done.mkdir(parents=True, exist_ok=True)
    _write(done / f"{sha256}.json", rec)
    for n in [rec["datei"], *(rec.get("beilagen") or [])]:
        (folder / n).unlink(missing_ok=True)
    side.unlink(missing_ok=True)
    store.audit("menschlich_erledigt", {"sha256": sha256, "datei": rec["datei"],
                                        "notiz": notiz}, op)
    eventlog.record_human(store, rec, eventlog.HUMAN_DONE, op)
    return rec


def erledigt(store, res, operator: str = "", freigabe: str = "mensch") -> dict | None:
    """Rechnung hat den Ordner verlassen (vom Menschen bestaetigt). Entfernt
    Kopie + Begleitdatei, legt die Begleitdatei mit Bearbeitungsvermerk unter
    ``erledigt/`` ab, Audit + Ereignis. ``None``, wenn die Rechnung gar nicht
    im Ordner lag."""
    from . import eventlog, shadow
    from .config import operator as _operator

    found = finde(store, res.sha256)
    if found is None:
        return None
    side, rec = found
    folder = side.parent
    op = operator or _operator()
    rec = dict(rec, erledigt=_now(), bearbeiter=op, freigabe=freigabe,
               korrigiert=shadow.corrected_fields(res, store))
    done = folder / DONE_DIR
    done.mkdir(parents=True, exist_ok=True)
    _write(done / f"{res.sha256}.json", rec)
    (folder / rec["datei"]).unlink(missing_ok=True)
    side.unlink(missing_ok=True)
    store.audit("menschlich_erledigt", {
        "sha256": res.sha256, "datei": rec["datei"], "korrigiert": rec["korrigiert"]}, op)
    eventlog.record_human(store, rec, eventlog.HUMAN_DONE, op)
    return rec
