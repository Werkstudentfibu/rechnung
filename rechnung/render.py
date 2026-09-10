"""PDF-Seite als PNG rendern (fuer die Anlern-Oberflaeche).

pdfplumber liefert Wort-Koordinaten im PDF-Punkte-Raum (Ursprung oben links,
72 dpi). Die UI zeigt ein PNG mit hoeherer Aufloesung. `Rendered.scale` ist der
Faktor Punkt -> Pixel, damit die UI Klick-Pixel zurueck in PDF-Punkte rechnen
kann.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium


@dataclass
class Rendered:
    page: int
    png: bytes
    width_pt: float
    height_pt: float
    scale: float

    @property
    def data_uri(self) -> str:
        return "data:image/png;base64," + base64.b64encode(self.png).decode("ascii")


def render_page(path: str | Path, page: int = 0, dpi: int = 150) -> Rendered:
    scale = dpi / 72.0
    pdf = pdfium.PdfDocument(str(path))
    try:
        pg = pdf[page]
        w_pt, h_pt = pg.get_size()
        bitmap = pg.render(scale=scale)
        pil = bitmap.to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return Rendered(page, buf.getvalue(), float(w_pt), float(h_pt), scale)
    finally:
        pdf.close()


def page_count(path: str | Path) -> int:
    pdf = pdfium.PdfDocument(str(path))
    try:
        return len(pdf)
    finally:
        pdf.close()
