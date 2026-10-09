"""Rauchtest Texterkennung (CI texterkennung-windows): findet das Programm
die portable Tesseract-Einrichtung und liest es einen Scan?"""

import io
import sys
import tempfile
from pathlib import Path

import pypdfium2 as pdfium

from rechnung import ocr
from rechnung.synth import make_invoice

print("tesseract:", ocr.tesseract_cmd())
assert ocr.tesseract_cmd() == str(ocr.portable_tesseract()), "portable Tesseract nicht gefunden"
assert ocr.available(), "Tesseract/deu nicht verfuegbar"
with tempfile.TemporaryDirectory() as d:
    src = make_invoice(Path(d) / "text.pdf")
    img = pdfium.PdfDocument(str(src))[0].render(scale=300 / 72).to_pil().convert("RGB")
    png = Path(d) / "scan.png"
    img.save(png)
    out = Path(d) / "out"
    ocr._run([ocr.tesseract_cmd(), str(png), str(out), "-l", ocr.LANG], 120)
    text = (out.with_suffix(".txt")).read_text("utf-8", "replace")
print(text[:400])
for wort in ("Rechnung", "Muster Handels GmbH", "Gesamtbetrag", "DE89370400440532013000"):
    assert wort in text, f"'{wort}' nicht erkannt"
print("OK")
sys.exit(0)
