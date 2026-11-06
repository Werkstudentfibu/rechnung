"""PDF laden: Wörter mit Bounding-Boxes + Seitenmaße. Nur Textlayer (kein OCR)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pdfplumber


@dataclass
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    page: int

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass
class Page:
    index: int
    width: float
    height: float
    words: list[Word]


@dataclass
class Document:
    path: Path
    pages: list[Page]
    sha256: str

    @property
    def text(self) -> str:
        return "\n".join(" ".join(w.text for w in p.words) for p in self.pages)

    def all_words(self) -> list[Word]:
        return [w for p in self.pages for w in p.words]


def _load_pages(src) -> list[Page]:
    pages: list[Page] = []
    with pdfplumber.open(src) as pdf:
        for i, p in enumerate(pdf.pages):
            words = [
                Word(w["text"], float(w["x0"]), float(w["x1"]),
                     float(w["top"]), float(w["bottom"]), i)
                for w in p.extract_words(keep_blank_chars=False)
            ]
            pages.append(Page(i, float(p.width), float(p.height), words))
    return pages


def load(path: str | Path) -> Document:
    path = Path(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return Document(path, _load_pages(path), sha)


def load_bytes(content: bytes) -> Document:
    """Wie ``load``, aber ohne den Umweg ueber die Platte -- fuer den
    Dateinamen/Dokumenttitel-Check beim Postfach-Abholen (ingest.py,
    Postfach-Baustein 8), wo noch keine Datei existiert. ``Document.path``
    bleibt leer."""
    import io

    sha = hashlib.sha256(content).hexdigest()
    return Document(Path(""), _load_pages(io.BytesIO(content)), sha)


def has_text_layer(path: str | Path) -> bool:
    """False => vermutlich Scan, OCR nötig (v1 nicht unterstützt)."""
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages[:3]:
            if (p.extract_text() or "").strip():
                return True
    return False


def page_has_text(path: str | Path, index: int) -> bool:
    """Hat GENAU diese Seite eine Textebene? Fuer gemischte PDFs (eingescannte
    Seite zwischen Textseiten), SPEC Effizienz-Baustein 3: OCR soll nur die
    Seiten treffen, die es wirklich brauchen."""
    with pdfplumber.open(path) as pdf:
        if index >= len(pdf.pages):
            return False
        return bool((pdf.pages[index].extract_text() or "").strip())


PDF_PASSWORD = "PDF passwortgeschuetzt"


def password_protected(src: str | Path | bytes) -> bool:
    """Braucht das PDF ein Passwort zum Oeffnen? Ein PDF, das nur mit
    Rechte-Einschraenkungen (leeres Benutzerpasswort) verschluesselt ist,
    laesst sich lesen und zaehlt NICHT. Kaputte PDFs zaehlen ebenfalls
    nicht -- die faellt die normale Verarbeitung auf."""
    import pypdfium2 as pdfium

    from .pdfium_sperre import sperre

    with sperre():
        try:
            doc = pdfium.PdfDocument(src if not isinstance(src, Path) else str(src))
        except pdfium.PdfiumError as e:
            return "password" in str(e).lower()
        doc.close()
        return False
