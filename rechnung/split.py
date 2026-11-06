"""Sammel-PDF aufteilen, lange Rechnungen zusammenhalten (Postfach-Baustein 2).

Ein PDF kann mehrere Rechnungen enthalten (Sammelversand), viele Rechnungen
sind aber selbst mehrseitig (auch 10+ Seiten). Getrennt wird deshalb NUR an
sicheren Grenzen -- vor Seite i liegt eine Grenze nur, wenn ALLES zutrifft:

- Seite i nennt im Seitenkopf eine Rechnungsnummer, die sich von der
  Nummer der laufenden Rechnung unterscheidet,
- die Seite davor traegt einen Summenblock (Gesamtbetrag/Rechnungsbetrag/
  Zahlbetrag ...),
- der Seitenzaehler beginnt auf Seite i neu ("Seite 1 von N") und die Seite
  davor war die letzte ihrer Rechnung ("Seite N von N"). Hat das PDF gar
  keinen Seitenzaehler, wird nur getrennt, wenn JEDE Seite eine eigene
  einseitige Rechnung ist (Nummer + Summenblock auf jeder Seite).

Gibt es Anzeichen fuer eine Grenze (andere Rechnungsnummer im Kopf,
Seitenzaehler beginnt neu), die nicht ALLE Bedingungen erfuellen, wird
NICHT getrennt -- die Rechnung geht mit "moeglicherweise mehrere Rechnungen
in einem PDF" an den Menschen. Im Zweifel nie trennen.

Teil-PDFs entstehen per pypdfium2 (Seiten unveraendert uebernommen) im
Eingangsordner als ``<name>_teil<k>von<n>.pdf``; das Original wandert nach
``data/sammel/`` (bleibt unveraendert erhalten). Jeder Teil behaelt den
Verweis auf das Original: ``data/teile/<teil-sha>.json`` mit sha256/Name des
Originals und den Seiten -- ``pipeline.process`` haengt das als
``Result.sammel`` an, es geht mit ins Journal und nach JobRouter.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .textnorm import compact

MAYBE_MULTI = "moeglicherweise mehrere Rechnungen in einem PDF"
MULTI = "mehrere Rechnungen in einem PDF"

# Anteil der Seitenhoehe, in dem der Seitenkopf (Rechnungsnummer) gesucht wird
HEADER_FRACTION = 0.4

_NUM_RE = re.compile(
    r"(?i)(?:rechnungs?\s*-?\s*(?:nummer|nr\.?)|rechnung\s+nr\.?|invoice\s+(?:no\.?|number)"
    r"|beleg\s*-?\s*nr\.?|belegnummer|gutschrift\s*-?\s*nr\.?)\s*:?\s*"
    r"([A-Z0-9][A-Z0-9\-/.]*\d[A-Z0-9\-/.]*)")
_COUNTER_RE = re.compile(r"(?i)\b(?:seite|page|blatt)\s+(\d{1,3})\s*(?:von|of|/)\s*(\d{1,3})\b")
_COUNTER_ONLY_RE = re.compile(r"(?i)\b(?:seite|page|blatt)\s+(\d{1,3})\b(?!\s*(?:von|of|/))")
_TOTALS_RE = re.compile(
    r"(?i)\b(gesamtbetrag|rechnungsbetrag|zahlbetrag|endbetrag|gesamtsumme|bruttobetrag"
    r"|summe\s+brutto|total\s+(?:amount|due)|amount\s+due|zu\s+zahlen)\b")


@dataclass
class PageInfo:
    number: str | None          # Rechnungsnummer im Seitenkopf (compact)
    counter: tuple[int, int | None] | None   # (k, n) aus "Seite k von n"
    totals: bool                # Summenblock auf der Seite


@dataclass
class Analysis:
    segments: list[list[int]] = field(default_factory=list)  # Seitenindizes je Rechnung
    uncertain: str = ""        # Grund, falls moeglicherweise mehrere (nicht getrennt)

    @property
    def multiple(self) -> bool:
        return len(self.segments) > 1


def _lines(words) -> list[str]:
    rows: dict[int, list] = {}
    for w in words:
        rows.setdefault(round(w.top / 3), []).append(w)
    return [" ".join(x.text for x in sorted(r, key=lambda w: w.x0))
            for _k, r in sorted(rows.items())]


def page_info(page) -> PageInfo:
    head = [w for w in page.words if w.top <= page.height * HEADER_FRACTION]
    head_text = "\n".join(_lines(head))
    full_text = "\n".join(_lines(page.words))
    m = _NUM_RE.search(head_text)
    number = compact(m.group(1)) if m else None
    c = _COUNTER_RE.search(full_text)
    counter = (int(c.group(1)), int(c.group(2))) if c else None
    if counter is None:
        c1 = _COUNTER_ONLY_RE.search(full_text)
        counter = (int(c1.group(1)), None) if c1 else None
    return PageInfo(number, counter, bool(_TOTALS_RE.search(full_text)))


def analyse(doc) -> Analysis:
    """Seitengrenzen eines Dokuments bestimmen (siehe Moduldoku)."""
    infos = [page_info(p) for p in doc.pages]
    n = len(infos)
    if n <= 1:
        return Analysis([list(range(n))])
    has_counters = any(i.counter for i in infos)

    # Anzeichen fuer eine Grenze vor Seite i
    current = infos[0].number
    candidates = []
    for i in range(1, n):
        inf = infos[i]
        restart = bool(inf.counter and inf.counter[0] == 1)
        new_no = bool(inf.number and current and inf.number != current)
        if restart or new_no or (inf.number and not current):
            candidates.append(i)
        if inf.number:
            current = inf.number
    if not candidates:
        return Analysis([list(range(n))])

    if not has_counters:
        # Ohne Seitenzaehler nur der eindeutige Fall: jede Seite eine eigene
        # einseitige Rechnung mit Nummer und Summenblock.
        nums = [i.number for i in infos]
        if all(nums) and all(i.totals for i in infos) and all(
                nums[k] != nums[k - 1] for k in range(1, n)):
            return Analysis([[k] for k in range(n)])
        return Analysis([list(range(n))], f"{MAYBE_MULTI} (Rechnungsnummer wechselt, "
                                          f"kein Seitenzaehler)")

    segments: list[list[int]] = [[0]]
    seg_no = infos[0].number
    for i in range(1, n):
        inf, prev = infos[i], infos[i - 1]
        if i not in candidates:
            segments[-1].append(i)
            continue
        safe = (inf.number is not None and seg_no is not None and inf.number != seg_no
                and prev.totals
                and inf.counter is not None and inf.counter[0] == 1
                and prev.counter is not None and prev.counter[1] is not None
                and prev.counter[0] == prev.counter[1])
        if not safe:
            why = []
            if not (inf.number and seg_no and inf.number != seg_no):
                why.append("keine neue Rechnungsnummer im Kopf")
            if not prev.totals:
                why.append("kein Summenblock davor")
            if not (inf.counter and inf.counter[0] == 1 and prev.counter
                    and prev.counter[1] and prev.counter[0] == prev.counter[1]):
                why.append("Seitenzaehler beginnt nicht sauber neu")
            return Analysis([list(range(n))],
                            f"{MAYBE_MULTI} (vor Seite {i + 1}: {', '.join(why)})")
        segments.append([i])
        seg_no = inf.number
    # Jede Rechnung muss mit einem Summenblock enden
    if not all(infos[s[-1]].totals for s in segments):
        return Analysis([list(range(n))], f"{MAYBE_MULTI} (Teil ohne Summenblock)")
    return Analysis(segments)


# ---- Ordner vorbereiten --------------------------------------------------

def _registry(store) -> Path:
    return Path(store.root) / "teile"


def part_info(store, sha256: str) -> dict | None:
    """Verweis eines Teil-PDFs auf sein Sammel-Original, sonst None."""
    p = _registry(store) / f"{sha256}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_part(src: Path, pages: list[int], target: Path) -> None:
    import pypdfium2 as pdfium

    from .pdfium_sperre import sperre

    with sperre():
        orig = pdfium.PdfDocument(str(src))
        out = pdfium.PdfDocument.new()
        try:
            out.import_pages(orig, pages=pages)
            out.save(str(target))
        finally:
            out.close()
            orig.close()


def split_file(path: Path, store) -> list[Path]:
    """Ein Sammel-PDF sicher aufteilen -> Pfade der Teile (leer = nicht
    aufgeteilt: eine Rechnung, kein Textlayer, Zweifel oder Fehler)."""
    from . import pdf as pdfmod

    path = Path(path)
    if path.suffix.lower() != ".pdf":
        return []
    try:
        if pdfmod.password_protected(path) or not pdfmod.has_text_layer(path):
            return []
        doc = pdfmod.load(path)
    except Exception:  # noqa: BLE001 -- kaputtes PDF: die Pipeline meldet es
        return []
    a = analyse(doc)
    if not a.multiple:
        return []
    n = len(a.segments)
    parts: list[Path] = []
    reg = _registry(store)
    reg.mkdir(parents=True, exist_ok=True)
    for k, pages in enumerate(a.segments, start=1):
        target = path.with_name(f"{path.stem}_teil{k}von{n}.pdf")
        _write_part(path, pages, target)
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        (reg / f"{sha}.json").write_text(json.dumps({
            "original_sha256": doc.sha256, "original_datei": path.name,
            "teil": k, "von": n, "seiten": [p + 1 for p in pages],
        }, ensure_ascii=False, indent=2), "utf-8")
        parts.append(target)
    keep = Path(store.root) / "sammel"
    keep.mkdir(parents=True, exist_ok=True)
    dest = keep / f"{doc.sha256[:12]}_{path.name}"
    shutil.move(str(path), dest)
    store.audit("sammel_aufgeteilt", {"sha256": doc.sha256, "datei": path.name,
                                      "teile": [p.name for p in parts],
                                      "seiten": [[x + 1 for x in s] for s in a.segments]})
    return parts


def prepare_folder(folder: Path, store) -> list[Path]:
    """Alle Sammel-PDFs eines Eingangsordners vor der Verarbeitung sicher
    aufteilen. -> neu entstandene Teil-PDFs."""
    from .pipeline import invoice_files

    reg = _registry(store)
    seen_file = reg / "_geprueft.txt"
    seen = set(seen_file.read_text("utf-8").split()) if seen_file.exists() else set()
    out: list[Path] = []
    new_seen: list[str] = []
    for p in invoice_files(folder):
        if p.suffix.lower() != ".pdf":
            continue
        sha = hashlib.sha256(p.read_bytes()).hexdigest()
        if sha in seen or part_info(store, sha) is not None:
            continue
        parts = split_file(p, store)
        out += parts
        if not parts:
            new_seen.append(sha)   # eine Rechnung/Zweifel: nicht bei jedem Scan neu laden
    if new_seen:
        reg.mkdir(parents=True, exist_ok=True)
        with seen_file.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(new_seen) + "\n")
    return out
