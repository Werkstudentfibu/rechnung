"""PyInstaller-Entry-Point. Siehe build.md."""

import multiprocessing
import sys

from rechnung.cli import main

if __name__ == "__main__":
    # Pflicht fuer den Onefile-Build: die Zeitgrenze je Rechnung (guard.py)
    # startet Arbeitsprozesse per "spawn".
    multiprocessing.freeze_support()
    sys.exit(main())
