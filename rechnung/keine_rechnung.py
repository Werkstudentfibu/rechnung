"""Nicht-Rechnungen aussortieren (Entscheidung Aaron 14.10.2026).

Dokumente, die keine Rechnungen sind, landen in einem eigenen Ordner
(``RECHNUNG_KEINE_RECHNUNG``, Standard ``<data>/keine-rechnung/`` -- ein
normaler Dateiordner, genau wie ``menschlich/``) statt gemischt mit echten
Problemrechnungen im Ordner "Menschliche Bearbeitung" (human.py). Dort sollen
nur noch Rechnungen liegen, an denen ein Mensch etwas tun muss.

Grundsatz: **nur woertliche, nachvollziehbare Regeln, keine KI-Entscheidung**.
Eine echte Rechnung darf nie hier landen -- im Zweifel bleibt alles wie
bisher bei "Menschliche Bearbeitung" (Fehlalarm-Freiheit vor Abdeckung).

Zwei Quellen:

- **Postfach** (``ingest.py``, ``pruefe_mail``): eine Mail ganz ohne Anhang
  und ohne Rechnungs-Hinweis im Text, oder eine Mail, deren Anhaenge ALLE
  eindeutig als Nicht-Beleg beschriftet sind (``belegart.ANLAGEN``) und kein
  einziger Beleg-Anhang dabei ist.
- **Pipeline** (``pipeline.py``, ``pruefe_pipeline``): ein verarbeitetes
  Dokument, dessen Titelbereich (Seite 1) eine bekannte Nicht-Beleg-
  Beschriftung traegt, OHNE Belegart im Titel, ohne E-Rechnung, ohne
  Schweizer QR-Zahlteil und ohne Rechnungsnummer-/Summen-Label im Text.

Nie loeschen (Postfach/Pipeline-Ablage), nie still: Datei + Begleitdatei
``<datei>.grund.json``, Ereignis im Ereignisprotokoll, Zaehler im
Tagesbericht. Geht nicht an JobRouter.

Nachtrag (Entscheidung Aaron 14.10.2026, 18:14): in diesem EINEN Ordner darf
der Mensch danach endgueltig loeschen (``loeschen``) -- das Postfach selbst
(IONOS/IMAP) wird dabei nie angefasst. Eine Datei mit demselben sha256 im
Archiv/als verarbeitete Rechnung bleibt davon unberuehrt (Loeschen ist strikt
auf Dateien innerhalb dieses Ordners begrenzt, siehe ``_sicherer_pfad``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path

from . import human

SIDECAR_SUFFIX = ".grund.json"

# Art der Begleitdatei -- fuer Anzeige/Fehlerbericht
HERKUNFT_POSTFACH = "postfach"
HERKUNFT_PIPELINE = "pipeline"
HERKUNFT_HAND = "hand"


def aktiv() -> bool:
    """``RECHNUNG_KEINE_RECHNUNG_ORDNER=0`` schaltet ab -- dann gilt wieder
    genau das alte Verhalten (alles nach "Menschliche Bearbeitung")."""
    v = os.environ.get("RECHNUNG_KEINE_RECHNUNG_ORDNER", "1").strip().lower()
    return v not in ("0", "", "nein", "false", "aus")


def ordner(data_root) -> Path:
    """Ordner fuer aussortierte Nicht-Rechnungen. ``RECHNUNG_KEINE_RECHNUNG``
    ueberschreibt den Standard ``<data>/keine-rechnung`` (wie
    ``human.human_dir``)."""
    v = os.environ.get("RECHNUNG_KEINE_RECHNUNG")
    return Path(v) if v else Path(data_root) / "keine-rechnung"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _read(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write(p: Path, data: dict) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), "utf-8")
    tmp.replace(p)


def _sidecars(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(folder.glob("*" + SIDECAR_SUFFIX))


def _target_name(folder: Path, name: str, sha256: str) -> str:
    """Wie ``human._target_name``: bei Namensgleichheit mit einer ANDEREN
    Datei wird der Hash-Anfang vorangestellt, nichts wird ueberschrieben."""
    if not (folder / name).exists() and not (folder / (name + SIDECAR_SUFFIX)).exists():
        return name
    other = _read(folder / (name + SIDECAR_SUFFIX))
    if other and other.get("sha256") == sha256:
        return name
    return f"{sha256[:8]}_{name}"


def _sicherer_pfad(folder: Path, name: str) -> Path | None:
    """Pfad einer Datei, die TATSAECHLICH in ``folder`` liegt -- kein
    Path-Traversal ueber die API (Nachtrag 14.10.2026: Sicherheitsnetz fuers
    Loeschen)."""
    p = (folder / human.file_name(name)).resolve()
    try:
        if p.exists() and p.is_file() and folder.resolve() in p.parents:
            return p
    except OSError:
        return None
    return None


# ---- Anzeige/Ordner ---------------------------------------------------

def offen(store) -> list[dict]:
    """Alle aussortierten Nicht-Rechnungen (aelteste zuerst)."""
    rows = [d for p in _sidecars(ordner(store.root)) if (d := _read(p))]
    return sorted(rows, key=lambda d: d.get("abgelegt") or "")


def finde(store, sha256: str) -> tuple[Path, dict] | None:
    if not sha256:
        return None
    for p in _sidecars(ordner(store.root)):
        d = _read(p)
        if d and d.get("sha256") == sha256:
            return p, d
    return None


def datei_pfad(store, name: str) -> Path | None:
    """Arbeitskopie im Ordner zu einem Dateinamen (fuer die Vorschau im
    Web-UI, wie ``human.datei_pfad``)."""
    folder = ordner(store.root)
    p = (folder / human.file_name(name)).resolve()
    if p.exists() and p.is_file() and folder.resolve() in p.parents \
            and not p.name.endswith(SIDECAR_SUFFIX):
        return p
    return None


# ---- Ablegen ------------------------------------------------------------

def ablegen_pipeline(store, res, operator: str = "") -> dict | None:
    """Ein von der Pipeline als Nicht-Rechnung erkanntes Dokument
    (``res.keine_rechnung`` gesetzt, ``pipeline.pruefe_pipeline``) aus dem
    Eingang IN diesen Ordner verschieben (nicht kopieren wie bei
    ``human.ablegen``: es ist keine Rechnung, bleibt also nicht als
    Karteileiche im Eingang liegen und wird bei jedem Scan neu verschoben)."""
    from . import eventlog
    from .config import operator as _operator

    if res.keine_rechnung is None or not res.sha256:
        return None
    folder = ordner(store.root)
    folder.mkdir(parents=True, exist_ok=True)
    found = finde(store, res.sha256)
    src = Path(res.file)
    name = found[1]["datei"] if found else _target_name(folder, human.file_name(res.file),
                                                         res.sha256)
    ziel = folder / name
    if src.exists() and src.resolve() != ziel.resolve():
        if not ziel.exists():
            shutil.move(str(src), str(ziel))
        else:
            src.unlink(missing_ok=True)
    rec = {
        "sha256": res.sha256,
        "datei": name,
        "original": str(res.file),
        "grund": res.keine_rechnung.get("grund"),
        "beschriftung": res.keine_rechnung.get("beschriftung"),
        "herkunft": {"quelle": HERKUNFT_PIPELINE},
        "abgelegt": found[1]["abgelegt"] if found else _now(),
        "aktualisiert": _now(),
    }
    _write(folder / (name + SIDECAR_SUFFIX), rec)
    if not found:
        op = operator or _operator()
        store.audit("keine_rechnung_aussortiert", {
            "sha256": res.sha256, "datei": name, "grund": rec["grund"]}, op)
        eventlog.record_keine_rechnung(store, rec, eventlog.KR_AUSSORTIERT, op)
    return rec


def ablegen_datei(store, name: str, content: bytes, *, grund: str, beschriftung: str | None = None,
                  beilagen: dict[str, bytes] | None = None, herkunft: dict | None = None,
                  operator: str = "") -> dict | None:
    """Etwas aus dem Postfach, das keine Rechnung ist (Mail ganz ohne
    Anhang, Mail deren Anhaenge ausschliesslich Nicht-Beleg-Beschriftungen
    tragen) direkt ablegen -- wie ``human.ablegen_datei``, nur in diesen
    Ordner. ``herkunft`` traegt Absender/Betreff/Message-ID fuer die
    Anzeige. Bereits abgelegt (gleicher Hash) -> Begleitdatei nur
    aktualisiert."""
    from . import eventlog
    from .config import operator as _operator

    if not content:
        return None
    sha = hashlib.sha256(content).hexdigest()
    folder = ordner(store.root)
    folder.mkdir(parents=True, exist_ok=True)
    found = finde(store, sha)
    name = found[1]["datei"] if found else _target_name(folder, human.file_name(name), sha)
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
        "grund": grund,
        "beschriftung": beschriftung,
        "beilagen": extra,
        "herkunft": dict(herkunft or {}, quelle=HERKUNFT_POSTFACH),
        "abgelegt": found[1]["abgelegt"] if found else _now(),
        "aktualisiert": _now(),
    }
    _write(folder / (name + SIDECAR_SUFFIX), rec)
    if not found:
        op = operator or _operator()
        store.audit("keine_rechnung_aussortiert", {"sha256": sha, "datei": name,
                                                    "grund": grund}, op)
        eventlog.record_keine_rechnung(store, rec, eventlog.KR_AUSSORTIERT, op)
    return rec


def von_hand_aussortieren(store, file: str, operator: str = "") -> dict | None:
    """Knopf "Keine Rechnung" in der Pruefmaske einer offenen Rechnung
    (Ordner "Menschliche Bearbeitung"): der Mensch sortiert von Hand aus --
    verlaesst ``menschlich/``, landet hier mit Grund "von Hand aussortiert
    (<Bearbeiter>)". ``file`` ist der Dateiname in "Menschliche Bearbeitung"
    (wie bei ``/api/entfernen``) -- ``None``, wenn er dort nicht (mehr)
    liegt."""
    from .config import operator as _operator

    name = human.file_name(file)
    hrec = next((r for r in human.offen(store) if r.get("datei") == name), None)
    if hrec is None:
        return None
    folder = human.human_dir(store.root)
    src = folder / hrec["datei"]
    content = src.read_bytes() if src.exists() else b""
    op = operator or _operator()
    rec = ablegen_datei(
        store, hrec["datei"], content, grund=f"von Hand aussortiert ({op})",
        herkunft={"original": hrec.get("original", ""), "lieferant": hrec.get("lieferant_name")},
        operator=op)
    if rec is None:
        return None
    human.erledigt_sha(store, hrec["sha256"], operator=op, notiz="aussortiert: keine Rechnung")
    return rec


def zurueck_in_eingang(store, sha256: str, inbox_dir, operator: str = "") -> dict | None:
    """Knopf "Doch eine Rechnung": die Regel lag falsch -- Datei zurueck in
    den Eingang, wird normal verarbeitet. Ereignis protokolliert (zaehlt im
    Tagesbericht/Qualitaet als "Fehlsortierung", Aaron 14.10.2026)."""
    from . import eventlog
    from .config import operator as _operator

    found = finde(store, sha256)
    if found is None:
        return None
    side, rec = found
    folder = side.parent
    src = folder / rec["datei"]
    if not src.exists():
        return None
    inbox_dir = Path(inbox_dir)
    inbox_dir.mkdir(parents=True, exist_ok=True)
    target = inbox_dir / src.name
    if target.exists():
        target = inbox_dir / f"{src.stem}_{int(time.time())}{src.suffix}"
    src.replace(target)
    for b in rec.get("beilagen") or []:
        (folder / b).unlink(missing_ok=True)
    side.unlink(missing_ok=True)
    op = operator or _operator()
    store.audit("keine_rechnung_zurueckgeholt", {
        "sha256": sha256, "datei": rec["datei"], "grund": rec.get("grund")}, op)
    eventlog.record_keine_rechnung(store, rec, eventlog.KR_ZURUECKGEHOLT, op)
    return {"ok": True, "ziel": str(target)}


def loeschen(store, namen: list[str], operator: str = "") -> dict:
    """Endgueltig loeschen -- NUR fuer Dateien, die tatsaechlich in diesem
    Ordner liegen (``_sicherer_pfad``, kein Path-Traversal). Entfernt Datei +
    Begleitdatei + Beilagen (.eml ...) unwiderruflich. Eine Datei mit
    demselben sha256 im Archiv/als verarbeitete Rechnung wird NICHT
    beruehrt -- geloescht wird strikt nur innerhalb dieses Ordners.
    Protokoll (Ereignis + Audit) bleibt, auch nach dem Loeschen: Dateiname,
    sha256, Grund, Bearbeiter, Zeitpunkt -- ohne Inhalt."""
    from . import eventlog
    from .config import operator as _operator

    folder = ordner(store.root)
    op = operator or _operator()
    geloescht, fehler = [], []
    for roh in namen:
        name = human.file_name(roh)
        p = _sicherer_pfad(folder, name)
        side = folder / (name + SIDECAR_SUFFIX)
        if p is None or not side.exists():
            fehler.append(name)
            continue
        rec = _read(side) or {}
        sha256 = rec.get("sha256", "")
        for b in rec.get("beilagen") or []:
            bp = _sicherer_pfad(folder, b)
            if bp is not None:
                bp.unlink(missing_ok=True)
        p.unlink(missing_ok=True)
        side.unlink(missing_ok=True)
        store.audit("keine_rechnung_geloescht", {
            "sha256": sha256, "datei": name, "grund": rec.get("grund")}, op)
        eventlog.record_keine_rechnung(store, dict(rec, datei=name), eventlog.KR_GELOESCHT, op)
        geloescht.append(name)
    return {"geloescht": geloescht, "fehler": fehler}


# ---- Pipeline-Regel (Baustein 2) ---------------------------------------

# "Rechnung Nr. 4711" / "Rechnungsnummer: RE-2026-00042" / "Invoice No.: X" --
# literales Label, keine KI. Bewusst nicht ueber belegart.aus_titel: das
# erkennt nur den DOKUMENTTITEL (und "Rechnungsnummer" zaehlt dort gerade
# NICHT als Titel "Rechnung", siehe belegart.py) -- hier geht es um ein
# Rechnungsnummer-LABEL irgendwo im Text.
_RECHNUNGSNUMMER_LABEL_RE = re.compile(
    r"(?i)\b(?:rechnung(?:s)?[-\s]?(?:nr|nummer)\.?|invoice[-\s]?(?:no|number)\.?|invoice[-\s]?#|"
    r"facture\s*n[o°]\.?|fattura\s*n\.?)\s*[:.]?\s*([A-Za-z0-9][A-Za-z0-9\-/.]{1,})"
)


def _hat_rechnungsnummer_label(text: str) -> bool:
    return bool(_RECHNUNGSNUMMER_LABEL_RE.search(text or ""))


def _hat_summen_label(text: str) -> bool:
    """Ein Bruttobetrag hinter einem Summen-Label -- deterministisch ueber
    ``summen.summen_aus_kontext`` (Teil A, kein Raten: nur ein eindeutiger
    Treffer zaehlt)."""
    from .summen import summen_aus_kontext

    return summen_aus_kontext(text or "").gefunden


def pruefe_pipeline(doc, qr) -> dict | None:
    """-> ``{"grund", "beschriftung"}``, wenn dieses Dokument nach
    keine-rechnung/ aussortiert werden soll, sonst ``None``. Nur fuer den
    PDF/Bild-Pfad (``pipeline._process``) -- eine E-Rechnung geht nie durch
    diese Funktion. Alle Bedingungen muessen zutreffen:

    - Titelbereich Seite 1 traegt eine bekannte Nicht-Beleg-Beschriftung
      (``belegart.anlage_aus_titel``),
    - KEINE Belegart im Titel (``belegart.aus_titel``),
    - kein Schweizer QR-Zahlteil,
    - weder eine Rechnungsnummer hinter einem Rechnungs-Label noch ein
      Bruttobetrag hinter einem Summen-Label im ganzen Text gefunden.

    Sonst (jeder Zweifel) -> ``None``, die Rechnung laeuft den normalen Weg."""
    from . import belegart, dubletten

    if not aktiv() or qr is not None:
        return None
    title = dubletten.title_text(doc)
    art, why = belegart.aus_titel(title)
    if art or why:
        return None
    beschriftung = belegart.anlage_aus_titel(title)
    if not beschriftung:
        return None
    text = doc.text if doc is not None else ""
    if _hat_rechnungsnummer_label(text) or _hat_summen_label(text):
        return None
    return {"grund": f"Titel als '{beschriftung}' erkannt, kein Beleg-Merkmal "
                     "(Rechnungsnummer-/Summen-Label) im Text gefunden",
            "beschriftung": beschriftung}
