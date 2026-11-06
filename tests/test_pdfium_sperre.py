"""Absturz bei gleichzeitigem PDFium-Zugriff (Befund Emmy, 06.11.2026):
pypdfium2 ist nicht thread-sicher. Ohne zentrale Sperre stirbt der
Prozess mit SIGSEGV, sobald mehrere Threads (Seitenbilder im Browser,
Hintergrund-OCR/Vorverarbeitung, Abholung) gleichzeitig ein PDF oeffnen,
eine Seite rendern oder schliessen. Der Test startet dafuer einen echten
Subprozess (eigener Interpreter, ``-X faulthandler``), weil ein
Speicherzugriffsfehler den *Prozess* beendet -- innerhalb des Testlaeufers
waere das ein harter pytest-Absturz statt eines faulen Tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from rechnung import synth

WORKER = Path(__file__).resolve().parent / "_pdfium_crash_worker.py"


def test_parallele_pdfium_zugriffe_stuerzen_nicht_ab(tmp_path: Path) -> None:
    pdf_path = tmp_path / "mehrseitig.pdf"
    seiten = 3
    synth.make_multipage_table_invoice(pdf_path, seiten=seiten)

    proc = subprocess.run(
        [sys.executable, "-X", "faulthandler", str(WORKER), str(pdf_path), str(seiten)],
        capture_output=True, text=True, timeout=120,
    )

    assert proc.returncode == 0, (
        f"Subprozess abgestuerzt (returncode={proc.returncode}, "
        f"vermutlich Signal {-proc.returncode} wie SIGSEGV=11)\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
