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


def load(path: str | Path) -> Document:
    path = Path(path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    pages: list[Page] = []
    with pdfplumber.open(path) as pdf:
        for i, p in enumerate(pdf.pages):
            words = [
                Word(w["text"], float(w["x0"]), float(w["x1"]),
                     float(w["top"]), float(w["bottom"]), i)
                for w in p.extract_words(keep_blank_chars=False)
            ]
            pages.append(Page(i, float(p.width), float(p.height), words))
    return Document(path, pages, sha)


def has_text_layer(path: str | Path) -> bool:
    """False => vermutlich Scan, OCR nötig (v1 nicht unterstützt)."""
    with pdfplumber.open(path) as pdf:
        for p in pdf.pages[:3]:
            if (p.extract_text() or "").strip():
                return True
    return False
