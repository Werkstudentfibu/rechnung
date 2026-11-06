"""Hilfsskript fuer test_pdfium_sperre.py: wird als eigener Subprozess
gestartet (mit ``-X faulthandler``) und loest viele parallele pypdfium2-
Zugriffe ueber verschiedene Module aus -- genau die Lage, die am
06.11.2026 beim Oeffnen einer Rechnung im Browser den Prozess mit SIGSEGV
abstuerzen liess (mehrere Seitenbilder gleichzeitig aus dem Threadpool von
Starlette plus Hintergrund-OCR/Vorverarbeitung).

Exit-Code 0 = kein Absturz. Ein von Python abgefangenes Signal (SIGSEGV,
SIGABRT, ...) zeigt sich als negativer Returncode des Subprozesses."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rechnung import ocr as ocrmod  # noqa: E402
from rechnung import pdf as pdfmod  # noqa: E402
from rechnung import render  # noqa: E402
from rechnung import swissqr  # noqa: E402
from rechnung.web.server import _render_png  # noqa: E402

N_THREADS = 24
ROUNDS = 4


def _work(path: Path, page_count: int, i: int) -> None:
    page = i % page_count
    for _ in range(ROUNDS):
        _render_png(path, page)
        render.render_page(path, page)
        ocrmod._page_images(path, [page])
        swissqr.read(path, pages=page_count)
        pdfmod.password_protected(path)


def main() -> int:
    path = Path(sys.argv[1])
    page_count = int(sys.argv[2])
    threads = [threading.Thread(target=_work, args=(path, page_count, i))
               for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
