"""PyInstaller-Entry-Point. Siehe build.md."""

import sys

from rechnung.cli import main

if __name__ == "__main__":
    sys.exit(main())
