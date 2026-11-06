"""Zentrale, prozessweite Sperre fuer JEDEN Zugriff auf pypdfium2.

pypdfium2 (die Python-Anbindung an die PDFium-C-Bibliothek) ist nicht
thread-sicher. Ohne Sperre stirbt der Prozess mit SIGSEGV/SIGABRT, sobald
zwei Threads gleichzeitig ein PDF oeffnen, eine Seite holen, rendern,
speichern oder schliessen -- reproduzierbar z.B. wenn der Browser mehrere
Seitenbilder parallel laedt (Starlette-Threadpool), waehrend eine
Hintergrund-OCR oder die Vorverarbeitung ebenfalls ein PDF anfasst
(Befund Emmy, 06.11.2026).

Jede Stelle im Code, die ``import pypdfium2`` schreibt, muss die
eigentlichen PDFium-Aufrufe (Oeffnen, Seite holen, rendern, speichern,
schliessen) innerhalb von :func:`sperre` ausfuehren -- auch das Schliessen
von Dokumenten/Seiten/Bitmaps gehoert hinein, es darf nicht dem Garbage
Collector ueberlassen werden (der GC kann ein Objekt in einem beliebigen
anderen Thread abraeumen, ausserhalb jeder Sperre).

Die Sperre ist bewusst eine einzige, einfache ``RLock`` (keine Prioritaet
fuer UI- vs. Hintergrund-Zugriffe): sie muss so eng wie moeglich um die
PDFium-Aufrufe gelegt werden, NIE um PNG-Kodierung oder einen
Tesseract-Lauf. Eine Hintergrund-OCR haelt die Sperre dadurch nur fuer
die Dauer je EINES Seiten-Renderns, nicht fuer den ganzen OCR-Lauf --
ein Seitenbild der gerade geoeffneten Rechnung wartet dadurch hoechstens
eine einzelne Renderzeit, nicht minutenlang auf eine komplette OCR."""

from __future__ import annotations

import contextlib
import threading

_LOCK = threading.RLock()


@contextlib.contextmanager
def sperre():
    """Kontextmanager um JEDEN pypdfium2-Aufruf (oeffnen/Seite holen/
    rendern/speichern/schliessen). So eng wie moeglich halten: PNG-
    Kodierung (``PIL.Image.save``) und OCR/Tesseract-Laeufe gehoeren NICHT
    hinein, sonst blockiert eine lange Hintergrundaufgabe die Oberflaeche."""
    with _LOCK:
        yield
