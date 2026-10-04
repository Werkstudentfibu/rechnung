"""Texterkennung fuer Scans (SPEC §7n): PDFs ohne Textebene und Bilder
(JPG/PNG/TIFF) werden per Tesseract (Sprache ``deu``) in ein PDF mit
Textebene gewandelt und laufen danach ganz normal durch die Pipeline.

Nur lokal: ``tesseract`` ist ein eigenes Programm auf demselben Rechner, es
geht nichts ins Netz. Ist es nicht installiert (oder fehlt das deutsche
Sprachpaket), degradiert das Programm sauber: die Rechnung wird mit dem
Grund ``OCR nicht verfuegbar`` zurueckgestellt, statt abzustuerzen.

Ablauf: Seiten mit pypdfium2 bzw. Pillow (beides ohnehin Abhaengigkeiten)
als 300-dpi-Bild -> ``tesseract <bild> <basis> -l deu pdf tsv`` liefert je
Seite ein durchsuchbares PDF (unsichtbare Textebene ueber dem Bild) plus die
Wortkonfidenzen -> Seiten-PDFs werden zu einem zusammengefuegt.

Das Original bleibt unveraendert (Archiv, sha256, Korrekturen und
Bestaetigung haengen am Original). Das OCR-PDF liegt als Arbeitskopie unter
``data/ocr/<sha>.pdf`` (+ ``.json`` mit den Konfidenzen) und wird pro
Original nur einmal erzeugt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock

from .textnorm import norm

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
OCR_UNAVAILABLE = "OCR nicht verfuegbar"
OCR_DISABLED = "OCR ausgeschaltet (RECHNUNG_OCR_ENABLED=0)"
LANG = "deu"
DPI = 300


def min_confidence() -> float:
    """Mittlere Wortkonfidenz (0-100), ab der eine OCR-Rechnung nicht mehr
    als unsicher gilt. Darunter: Pruefung ``ocr-konfidenz`` rot -> Mensch."""
    try:
        return float(os.environ.get("RECHNUNG_OCR_MIN_CONF", "80"))
    except ValueError:
        return 80.0


def word_min_confidence() -> float:
    """Wortkonfidenz, unter der ein einzelnes Wort als unsicher gilt -- ein
    Feld, dessen Wert aus so einem Wort stammt, geht an den Menschen."""
    try:
        return float(os.environ.get("RECHNUNG_OCR_WORD_MIN_CONF", "60"))
    except ValueError:
        return 60.0


def jobs() -> int:
    """Parallelitaet fuer die Texterkennung mehrseitiger Rechnungen (SPEC
    Effizienz-Baustein 3) -- ``RECHNUNG_OCR_JOBS``, Standard Kerne-1
    (mindestens 1). Auf einem knappen Laptop ueber die Umgebungsvariable
    sparsam einstellen (z.B. 1-2), siehe Laptop-Profil in
    ``rechnung.env.example``."""
    v = os.environ.get("RECHNUNG_OCR_JOBS", "").strip()
    if v:
        try:
            return max(1, int(v))
        except ValueError:
            pass
    return max(1, (os.cpu_count() or 2) - 1)


def kopf_fuss_seiten() -> tuple[int, int]:
    """Lange Scans (Postfach-Baustein 3): fuer die Feld-Extraktion reichen
    Kopf (erste Seiten: Nummer, Datum, Lieferant) und Summen-/Bankteil
    (letzte Seiten). ``RECHNUNG_OCR_KOPF``/``RECHNUNG_OCR_FUSS`` (Standard
    je 2). Hat ein Scan mehr Seiten als Kopf + Fuss + 1, werden innerhalb der
    Zeitgrenze nur diese Seiten erkannt, der Rest danach im Hintergrund
    (``complete_pending``)."""
    def _int(name: str, default: int) -> int:
        try:
            return max(1, int(os.environ.get(name, str(default))))
        except ValueError:
            return default
    return _int("RECHNUNG_OCR_KOPF", 2), _int("RECHNUNG_OCR_FUSS", 2)


def partial_pages(n: int) -> list[int] | None:
    """Seitenindizes fuer die schnelle Teil-Erkennung, ``None`` = alle."""
    kopf, fuss = kopf_fuss_seiten()
    if n <= kopf + fuss + 1:
        return None
    return list(range(kopf)) + list(range(n - fuss, n))


# Standard-Installationsorte unter Windows (UB-Mannheim-Installer, siehe
# build.md/INSTALL-WINDOWS.md) -- greifen nur, wenn weder RECHNUNG_TESSERACT
# gesetzt ist noch tesseract im PATH liegt.
_WINDOWS_CANDIDATES = (
    r"%ProgramFiles%\Tesseract-OCR\tesseract.exe",
    r"%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe",
    r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
)


def enabled() -> bool:
    """Texterkennung an/aus (``RECHNUNG_OCR_ENABLED``, Standard an). Aus
    (z.B. auf einem schwachen Laptop): Scans werden zurueckgestellt und gehen
    in den Ordner "Menschliche Bearbeitung"."""
    return os.environ.get("RECHNUNG_OCR_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off", "nein")


def _on_windows() -> bool:
    return os.name == "nt"


def _windows_fallback() -> str | None:
    if not _on_windows():
        return None
    for cand in _WINDOWS_CANDIDATES:
        # %VAR% selbst aufloesen (os.path.expandvars kann das nur unter Windows)
        expanded = re.sub(r"%([^%]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), cand)
        if "%" not in expanded and Path(expanded).is_file():
            return expanded
    return None


def tesseract_cmd() -> str | None:
    """``RECHNUNG_TESSERACT`` > ``tesseract`` im PATH > Windows-Standardpfad."""
    return (os.environ.get("RECHNUNG_TESSERACT") or shutil.which("tesseract")
            or _windows_fallback())


def _run(args: list[str], timeout: float | None, **kw):
    """subprocess.run ohne aufblitzendes Konsolenfenster unter Windows
    (das Programm laeuft dort als Fenster-Anwendung/geplante Aufgabe)."""
    if _on_windows():
        kw.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return subprocess.run(args, capture_output=True, timeout=timeout, **kw)


_avail: tuple[str, bool] | None = None


def available() -> bool:
    """Tesseract installiert UND Sprachpaket ``deu`` vorhanden? Gecacht je
    Programmpfad."""
    global _avail
    cmd = tesseract_cmd()
    if not cmd:
        return False
    if _avail is not None and _avail[0] == cmd:
        return _avail[1]
    try:
        # Aeltere Tesseract-Versionen schreiben die Liste nach stderr; die
        # Ausgabe explizit als UTF-8 lesen (Windows-Konsole ist sonst cp1252).
        p = _run([cmd, "--list-langs"], 15, text=True, encoding="utf-8", errors="replace")
        ok = LANG in ((p.stdout or "") + "\n" + (getattr(p, "stderr", "") or "")).split()
    except (OSError, subprocess.SubprocessError):
        ok = False
    _avail = (cmd, ok)
    return ok


@dataclass
class OcrResult:
    sha256: str
    pdf: Path | None = None
    confidence: float | None = None       # mittlere Wortkonfidenz 0-100
    low_words: list[str] = field(default_factory=list)  # norm() unsicherer Woerter
    pages: int = 0
    seconds: float = 0.0
    error: str = ""
    # Teil-Erkennung eines langen Scans: nur Kopf-/Fussseiten haben eine
    # Textebene, die uebrigen (0-basiert) folgen im Hintergrund
    partial: bool = False
    missing_pages: list[int] = field(default_factory=list)

    @property
    def low(self) -> bool:
        return self.confidence is not None and self.confidence < min_confidence()

    def to_dict(self) -> dict:
        return {"engine": "tesseract", "lang": LANG, "confidence": self.confidence,
                "min_confidence": min_confidence(), "low": self.low,
                "unsichere_woerter": len(self.low_words), "pages": self.pages,
                "seconds": self.seconds, "error": self.error,
                "teilweise": self.partial,
                "seiten_ohne_ocr": [i + 1 for i in self.missing_pages]}


def needs_ocr(path: str | Path) -> bool:
    from . import pdf as pdfmod

    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return True
    if suffix != ".pdf":
        return False
    return not pdfmod.has_text_layer(path)


def page_count(path: Path) -> int:
    from PIL import Image, ImageSequence

    if path.suffix.lower() in IMAGE_SUFFIXES:
        with Image.open(path) as im:
            return sum(1 for _ in ImageSequence.Iterator(im))
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        return len(doc)
    finally:
        doc.close()


def _page_images(path: Path, indices: list[int] | None = None):
    """Seiten als PIL-Bilder (300 dpi); bei ``indices`` nur diese (die
    uebrigen ``None`` -- ein langer Scan wird nicht komplett gerendert)."""
    from PIL import Image, ImageSequence

    if path.suffix.lower() in IMAGE_SUFFIXES:
        with Image.open(path) as im:
            # Mehrseitige TIFFs: jede Seite einzeln
            return [frame.convert("RGB") for frame in ImageSequence.Iterator(im)]
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        want = set(range(len(doc)) if indices is None else indices)
        return [doc[i].render(scale=DPI / 72).to_pil().convert("RGB") if i in want else None
                for i in range(len(doc))]
    finally:
        doc.close()


def _run_tesseract(image: Path, out_base: Path, timeout: float | None) -> None:
    """Eine Seite erkennen -> ``out_base.pdf`` + ``out_base.tsv``."""
    _run([tesseract_cmd(), str(image), str(out_base), "-l", LANG,
          "--dpi", str(DPI), "pdf", "tsv"], timeout, check=True)


def _parse_tsv(text: str, word_min: float) -> tuple[list[float], list[str]]:
    confs, low = [], []
    lines = text.splitlines()
    if not lines:
        return confs, low
    head = lines[0].split("\t")
    try:
        i_conf, i_text = head.index("conf"), head.index("text")
    except ValueError:
        return confs, low
    for line in lines[1:]:
        cols = line.split("\t")
        if len(cols) <= max(i_conf, i_text) or not cols[i_text].strip():
            continue
        try:
            c = float(cols[i_conf])
        except ValueError:
            continue
        if c < 0:
            continue
        confs.append(c)
        if c < word_min and norm(cols[i_text]):
            low.append(norm(cols[i_text]))
    return confs, low


def _merge_mixed(original: Path, page_pdfs: list[Path | None], skip: list[bool],
                 target: Path) -> None:
    """Fuegt das Ergebnis zusammen: Seiten mit eigener Textebene (``skip``)
    kommen unveraendert aus dem ORIGINAL -- keine OCR-Bildschicht darueber,
    keine OCR noetig gewesen (SPEC Effizienz-Baustein 3); die uebrigen aus
    der jeweils per Tesseract erkannten Seiten-PDF."""
    import pypdfium2 as pdfium

    out = pdfium.PdfDocument.new()
    src_orig = pdfium.PdfDocument(str(original)) if any(skip) else None
    try:
        for i, is_skip in enumerate(skip):
            if is_skip:
                out.import_pages(src_orig, pages=[i])
            else:
                src = pdfium.PdfDocument(str(page_pdfs[i]))
                out.import_pages(src)
                src.close()
        out.save(str(target))
    finally:
        out.close()
        if src_orig is not None:
            src_orig.close()


def _cached(target: Path, meta: Path, sha: str) -> OcrResult | None:
    if not (target.exists() and meta.exists()):
        return None
    try:
        m = json.loads(meta.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return OcrResult(sha, target, m.get("confidence"), m.get("low_words", []),
                     m.get("pages", 0), m.get("seconds", 0.0),
                     partial=bool(m.get("teilweise")),
                     missing_pages=list(m.get("fehlend") or []))


def ensure_text(path: str | Path, cache_dir: str | Path,
                timeout: float | None = None, full: bool = False) -> OcrResult:
    """Original -> OCR-PDF mit Textebene (aus dem Cache, falls schon erzeugt).
    ``timeout`` begrenzt die gesamte Erkennung (alle Seiten zusammen).

    SPEC Effizienz-Baustein 3: Seiten, die (bei einem PDF) bereits eine
    eigene Textebene haben, werden nicht per Tesseract erkannt, sondern
    unveraendert aus dem Original uebernommen -- typisch bei einer
    gemischten Rechnung (z.B. eingescannter Lieferschein zwischen
    Text-Seiten). Die verbleibenden Seiten laufen parallel ueber mehrere
    Prozessorkerne (``jobs()``, konfigurierbar, auf dem Laptop-Profil
    sparsam), statt streng nacheinander."""
    path = Path(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    cache_dir = Path(cache_dir)
    target, meta = cache_dir / f"{sha}.pdf", cache_dir / f"{sha}.json"
    hit = _cached(target, meta, sha)
    if hit is not None:
        return hit
    if not enabled():
        return OcrResult(sha, error=OCR_DISABLED)
    if not available():
        return OcrResult(sha, error=OCR_UNAVAILABLE)

    # Langer Scan (Postfach-Baustein 3): innerhalb der Zeitgrenze nur Kopf-
    # und Fussseiten erkennen; Seitenzahlen bleiben gleich (die uebrigen
    # Seiten kommen ohne Textebene aus dem Original), der Rest folgt im
    # Hintergrund (complete_pending). Nur fuer PDFs.
    is_pdf = path.suffix.lower() == ".pdf"
    selected = None
    if not full and is_pdf:
        try:
            selected = partial_pages(page_count(path))
        except Exception:  # noqa: BLE001 -- im Zweifel alles erkennen
            selected = None
    if selected is not None:
        target, meta = cache_dir / f"{sha}.teil.pdf", cache_dir / f"{sha}.teil.json"
        hit = _cached(target, meta, sha)
        if hit is not None:
            return hit

    t0 = time.monotonic()
    deadline = t0 + timeout if timeout else None
    try:
        images = _page_images(path, selected)
    except Exception as e:  # noqa: BLE001 -- defektes Bild/PDF
        return OcrResult(sha, error=f"OCR: Datei nicht lesbar ({type(e).__name__})")

    # Bilder/TIFFs haben nie eine Textebene -- jede Seite/jeder Frame braucht
    # OCR. Bei einem PDF je Seite einzeln pruefen (has_text_layer prueft nur
    # die ersten drei Seiten des gesamten Dokuments, das reicht hier nicht).
    from . import pdf as pdfmod

    try:
        skip = [is_pdf and pdfmod.page_has_text(path, i) for i in range(len(images))]
    except Exception:  # noqa: BLE001 -- im Zweifel lieber jede Seite erkennen
        skip = [False] * len(images)
    # nicht ausgewaehlte Seiten eines langen Scans: unveraendert aus dem Original
    missing = [i for i, im in enumerate(images) if im is None and not skip[i]]
    skip = [s or images[i] is None for i, s in enumerate(skip)]
    todo = [i for i, s in enumerate(skip) if not s]

    confs: list[float] = []
    low: list[str] = []
    page_pdfs: list[Path | None] = [None] * len(images)
    errors: list[str] = []
    lock = Lock()

    def run_one(i: int, tmp: Path) -> None:
        png = tmp / f"s{i:03d}.png"
        images[i].save(png, dpi=(DPI, DPI))
        base = tmp / f"s{i:03d}"
        remaining = None if deadline is None else max(1.0, deadline - time.monotonic())
        try:
            _run_tesseract(png, base, remaining)
        except subprocess.TimeoutExpired:
            with lock:
                errors.append("OCR: Zeitgrenze ueberschritten")
            return
        except (OSError, subprocess.SubprocessError) as e:
            with lock:
                errors.append(f"OCR fehlgeschlagen ({type(e).__name__})")
            return
        c, lw = _parse_tsv(base.with_suffix(".tsv").read_text("utf-8", "replace"),
                           word_min_confidence())
        with lock:
            confs.extend(c)
            low.extend(lw)
            page_pdfs[i] = base.with_suffix(".pdf")

    with tempfile.TemporaryDirectory(prefix="rechnung-ocr-") as tmp_dir:
        tmp = Path(tmp_dir)
        n_jobs = min(jobs(), len(todo)) if len(todo) > 1 else 1
        if n_jobs > 1:
            with ThreadPoolExecutor(max_workers=n_jobs) as ex:
                list(ex.map(lambda i: run_one(i, tmp), todo))
        else:
            for i in todo:
                run_one(i, tmp)
        if errors:
            return OcrResult(sha, error=errors[0])
        cache_dir.mkdir(parents=True, exist_ok=True)
        _merge_mixed(path, page_pdfs, skip, target)

    # Keine Seite brauchte OCR (todo leer): "Konfidenz" ist dann keine
    # sinnvolle Groesse -- None statt 0.0, sonst wuerde eine eigentlich
    # textbasierte Rechnung faelschlich als OCR-unsicher markiert.
    confidence = None if not todo else (round(sum(confs) / len(confs), 1) if confs else 0.0)
    res = OcrResult(sha, target, confidence, sorted(set(low)), len(images),
                    round(time.monotonic() - t0, 2), partial=bool(missing),
                    missing_pages=missing)
    meta.write_text(json.dumps({"confidence": res.confidence, "low_words": res.low_words,
                                "pages": res.pages, "seconds": res.seconds,
                                "source": path.name, "original": str(path.resolve()),
                                "teilweise": res.partial, "fehlend": missing},
                               ensure_ascii=False), "utf-8")
    return res


def pending(cache_dir: str | Path) -> list[dict]:
    """Lange Scans, deren uebrige Seiten noch auf die Texterkennung warten."""
    cache_dir = Path(cache_dir)
    out = []
    for meta in sorted(cache_dir.glob("*.teil.json")) if cache_dir.exists() else []:
        sha = meta.name.split(".", 1)[0]
        if (cache_dir / f"{sha}.pdf").exists():
            continue
        try:
            m = json.loads(meta.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append(dict(m, sha256=sha))
    return out


def complete_pending(cache_dir: str | Path, archive_dir: str | Path | None = None,
                     limit: int = 0) -> list[dict]:
    """Hintergrundschritt AUSSERHALB der Zeitgrenze je Rechnung (Ende von
    ``rechnung scan``, Nachtlauf): lange Scans vollstaendig erkennen
    (Positionen/Archiv). Danach liest ``pipeline.load_document`` die
    vollstaendige Arbeitskopie; die Teil-Erkennung wird entfernt. Original
    nicht mehr da (verschoben) -> Archivkopie ``<sha>.pdf``, sonst bleibt
    der Eintrag offen."""
    cache_dir = Path(cache_dir)
    done = []
    for m in pending(cache_dir)[: limit or None]:
        sha = m["sha256"]
        src = Path(m.get("original") or "")
        if not src.exists() and archive_dir is not None:
            src = Path(archive_dir) / f"{sha}.pdf"
        if not src.exists() or hashlib.sha256(src.read_bytes()).hexdigest() != sha:
            done.append({"sha256": sha, "ok": False, "grund": "Original nicht gefunden"})
            continue
        r = ensure_text(src, cache_dir, None, full=True)
        if r.error:
            done.append({"sha256": sha, "ok": False, "grund": r.error})
            continue
        (cache_dir / f"{sha}.teil.pdf").unlink(missing_ok=True)
        (cache_dir / f"{sha}.teil.json").unlink(missing_ok=True)
        done.append({"sha256": sha, "ok": True, "seiten": r.pages, "sekunden": r.seconds})
    return done
