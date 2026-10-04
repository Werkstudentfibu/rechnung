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
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .textnorm import norm

IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".tif", ".tiff")
OCR_UNAVAILABLE = "OCR nicht verfuegbar"
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


def tesseract_cmd() -> str | None:
    return os.environ.get("RECHNUNG_TESSERACT") or shutil.which("tesseract")


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
        out = subprocess.run([cmd, "--list-langs"], capture_output=True, text=True,
                             timeout=15).stdout
        ok = LANG in out.split()
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

    @property
    def low(self) -> bool:
        return self.confidence is not None and self.confidence < min_confidence()

    def to_dict(self) -> dict:
        return {"engine": "tesseract", "lang": LANG, "confidence": self.confidence,
                "min_confidence": min_confidence(), "low": self.low,
                "unsichere_woerter": len(self.low_words), "pages": self.pages,
                "seconds": self.seconds, "error": self.error}


def needs_ocr(path: str | Path) -> bool:
    from . import pdf as pdfmod

    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return True
    if suffix != ".pdf":
        return False
    return not pdfmod.has_text_layer(path)


def _page_images(path: Path):
    """Seiten als PIL-Bilder (300 dpi)."""
    from PIL import Image, ImageSequence

    if path.suffix.lower() in IMAGE_SUFFIXES:
        with Image.open(path) as im:
            # Mehrseitige TIFFs: jede Seite einzeln
            return [frame.convert("RGB") for frame in ImageSequence.Iterator(im)]
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        return [doc[i].render(scale=DPI / 72).to_pil().convert("RGB") for i in range(len(doc))]
    finally:
        doc.close()


def _run_tesseract(image: Path, out_base: Path, timeout: float | None) -> None:
    """Eine Seite erkennen -> ``out_base.pdf`` + ``out_base.tsv``."""
    subprocess.run([tesseract_cmd(), str(image), str(out_base), "-l", LANG,
                    "--dpi", str(DPI), "pdf", "tsv"],
                   check=True, capture_output=True, timeout=timeout)


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


def _merge(pdfs: list[Path], target: Path) -> None:
    import pypdfium2 as pdfium

    out = pdfium.PdfDocument.new()
    try:
        for p in pdfs:
            src = pdfium.PdfDocument(str(p))
            out.import_pages(src)
            src.close()
        out.save(str(target))
    finally:
        out.close()


def ensure_text(path: str | Path, cache_dir: str | Path,
                timeout: float | None = None) -> OcrResult:
    """Original -> OCR-PDF mit Textebene (aus dem Cache, falls schon erzeugt).
    ``timeout`` begrenzt die gesamte Erkennung (alle Seiten zusammen)."""
    path = Path(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    cache_dir = Path(cache_dir)
    target, meta = cache_dir / f"{sha}.pdf", cache_dir / f"{sha}.json"
    if target.exists() and meta.exists():
        try:
            m = json.loads(meta.read_text("utf-8"))
            return OcrResult(sha, target, m.get("confidence"), m.get("low_words", []),
                             m.get("pages", 0), m.get("seconds", 0.0))
        except (json.JSONDecodeError, OSError):
            pass
    if not available():
        return OcrResult(sha, error=OCR_UNAVAILABLE)

    t0 = time.monotonic()
    deadline = t0 + timeout if timeout else None
    try:
        images = _page_images(path)
    except Exception as e:  # noqa: BLE001 -- defektes Bild/PDF
        return OcrResult(sha, error=f"OCR: Datei nicht lesbar ({type(e).__name__})")
    confs: list[float] = []
    low: list[str] = []
    with tempfile.TemporaryDirectory(prefix="rechnung-ocr-") as tmp:
        tmp = Path(tmp)
        page_pdfs = []
        for i, img in enumerate(images):
            png = tmp / f"s{i:03d}.png"
            img.save(png, dpi=(DPI, DPI))
            base = tmp / f"s{i:03d}"
            remaining = None if deadline is None else max(1.0, deadline - time.monotonic())
            try:
                _run_tesseract(png, base, remaining)
            except subprocess.TimeoutExpired:
                return OcrResult(sha, error="OCR: Zeitgrenze ueberschritten")
            except (OSError, subprocess.SubprocessError) as e:
                return OcrResult(sha, error=f"OCR fehlgeschlagen ({type(e).__name__})")
            page_pdfs.append(base.with_suffix(".pdf"))
            c, lw = _parse_tsv(base.with_suffix(".tsv").read_text("utf-8", "replace"),
                               word_min_confidence())
            confs += c
            low += lw
        cache_dir.mkdir(parents=True, exist_ok=True)
        _merge(page_pdfs, target)
    res = OcrResult(sha, target, round(sum(confs) / len(confs), 1) if confs else 0.0,
                    sorted(set(low)), len(images), round(time.monotonic() - t0, 2))
    meta.write_text(json.dumps({"confidence": res.confidence, "low_words": res.low_words,
                                "pages": res.pages, "seconds": res.seconds,
                                "source": path.name}, ensure_ascii=False), "utf-8")
    return res
