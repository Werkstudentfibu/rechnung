"""Selbst-Update aus dem privaten Git-Repo (Zweig ``release``).

Fuer die Testphase auf dem Firmenlaptop: das Programm laeuft dort direkt aus
einem Git-Klon (``C:\\Rechnung\\app``) mit eigener venv statt als
``rechnung.exe``. Ein Update ist damit nur "neuen Stand holen, Tests, neu
starten" -- Sekunden statt eines kompletten exe-Neubaus.

Ablauf ``aktualisieren()``:

1. ``git fetch origin release`` -- kein Netz/kein Zugang -> Status
   ``offline``, es aendert sich nichts.
2. Gleicher Stand -> ``aktuell``.
3. Laeuft gerade der Nachtlauf (``lock.is_locked``) -> ``beschaeftigt``,
   nichts aendern (Code wuerde ihm unter den Fuessen wechseln).
4. ``git reset --hard origin/release``; hat sich ``pyproject.toml``
   geaendert, Abhaengigkeiten nachinstallieren.
5. Tests (``pytest -q -x``). Rot -> zurueck auf den alten Stand
   (``zurueckgerollt``), das laufende Programm bleibt wie es war.

``data\\`` und ``rechnung.env`` liegen ausserhalb des Klons und werden nie
angefasst. Jeder Versuch steht in ``<data>/update.log``.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from . import lock

BRANCH = "release"
# Exit-Code, mit dem das Programm "bitte neu starten" signalisiert; der
# Laptop-Starter (windows/laptop-start.cmd) startet dann sofort neu.
RESTART_EXIT_CODE = 42
GIT_TIMEOUT_S = 90
PIP_TIMEOUT_S = 900
TEST_TIMEOUT_S = 900


@dataclass
class Ergebnis:
    status: str  # aktuell | aktualisiert | zurueckgerollt | offline | beschaeftigt | kein_git | fehler
    alt: str = ""
    neu: str = ""
    meldung: str = ""

    @property
    def neustart_noetig(self) -> bool:
        return self.status == "aktualisiert"

    def dict(self) -> dict:
        return dict(asdict(self), neustart_noetig=self.neustart_noetig)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def ist_git_klon(root: Path | None = None) -> bool:
    return ((root or repo_root()) / ".git").exists()


def _run(cmd: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess:
    kwargs = {}
    if sys.platform == "win32":
        # kein zusaetzliches Konsolenfenster aus dem Programmfenster heraus
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, **kwargs)


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return _run(["git", *args], root, GIT_TIMEOUT_S)


def _rev(root: Path, ref: str) -> str:
    r = _git(root, "rev-parse", ref)
    return r.stdout.strip() if r.returncode == 0 else ""


def version(root: Path | None = None) -> dict:
    """Aktueller Stand fuer die Anzeige: Commit, Datum, Betreff."""
    root = root or repo_root()
    if not ist_git_klon(root):
        return {"git": False, "commit": "", "datum": "", "betreff": ""}
    r = _git(root, "log", "-1", "--format=%h%x1f%cd%x1f%s", "--date=format:%d.%m.%Y %H:%M")
    teile = (r.stdout.strip().split("\x1f") + ["", "", ""])[:3] if r.returncode == 0 else ["", "", ""]
    return {"git": True, "commit": teile[0], "datum": teile[1], "betreff": teile[2]}


def _pip_install(root: Path, python: str) -> subprocess.CompletedProcess:
    return _run([python, "-m", "pip", "install", "--disable-pip-version-check", "-q",
                 "-e", ".[dev]"], root, PIP_TIMEOUT_S)


def _tests(root: Path, python: str) -> subprocess.CompletedProcess:
    return _run([python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], root, TEST_TIMEOUT_S)


def _letzte_zeilen(text: str, n: int = 15) -> str:
    return "\n".join((text or "").strip().splitlines()[-n:])


def _log(data_root: Path | None, e: Ergebnis) -> None:
    if data_root is None:
        return
    try:
        p = Path(data_root) / "update.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {e.status}"
                    f"  {e.alt[:7]} -> {e.neu[:7]}  {e.meldung}\n")
    except OSError:
        pass


def aktualisieren(data_root: Path | None = None, root: Path | None = None,
                  tests: bool = True, python: str | None = None) -> Ergebnis:
    root = root or repo_root()
    python = python or sys.executable
    e = _aktualisieren(root, data_root, tests, python)
    _log(data_root, e)
    return e


def _aktualisieren(root: Path, data_root: Path | None, tests: bool, python: str) -> Ergebnis:
    if not ist_git_klon(root):
        return Ergebnis("kein_git", meldung="Programm laeuft nicht aus einem Git-Klon (z.B. als rechnung.exe).")

    try:
        f = _git(root, "fetch", "--quiet", "origin", BRANCH)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Ergebnis("offline", meldung=f"Update-Server nicht erreichbar: {exc}")
    if f.returncode != 0:
        return Ergebnis("offline", meldung="Update-Server nicht erreichbar: " + _letzte_zeilen(f.stderr, 3))

    alt = _rev(root, "HEAD")
    neu = _rev(root, f"origin/{BRANCH}")
    if not neu:
        return Ergebnis("fehler", alt=alt, meldung=f"Zweig {BRANCH} nicht gefunden.")
    if alt == neu:
        return Ergebnis("aktuell", alt=alt, neu=neu, meldung="Schon auf dem neuesten Stand.")

    if data_root is not None and lock.is_locked(data_root):
        return Ergebnis("beschaeftigt", alt=alt, neu=neu,
                        meldung="Nachtlauf laeuft gerade -- Update spaeter erneut versuchen.")

    deps_geaendert = _git(root, "diff", "--quiet", alt, neu, "--", "pyproject.toml").returncode != 0

    def zurueck(grund: str) -> Ergebnis:
        _git(root, "reset", "--hard", "--quiet", alt)
        if deps_geaendert:
            _pip_install(root, python)
        return Ergebnis("zurueckgerollt", alt=alt, neu=neu,
                        meldung=f"{grund} -- alter Stand bleibt aktiv.")

    r = _git(root, "reset", "--hard", "--quiet", neu)
    if r.returncode != 0:
        return zurueck("Neuer Stand liess sich nicht auspacken: " + _letzte_zeilen(r.stderr, 3))

    try:
        if deps_geaendert:
            p = _pip_install(root, python)
            if p.returncode != 0:
                return zurueck("Abhaengigkeiten liessen sich nicht installieren:\n"
                               + _letzte_zeilen(p.stdout + p.stderr))
        if tests:
            t = _tests(root, python)
            if t.returncode != 0:
                return zurueck("Tests fehlgeschlagen:\n" + _letzte_zeilen(t.stdout + t.stderr))
    except subprocess.TimeoutExpired as exc:
        return zurueck(f"Zeitueberschreitung: {exc}")

    betreff = version(root).get("betreff", "")
    return Ergebnis("aktualisiert", alt=alt, neu=neu,
                    meldung=f"Neuer Stand {neu[:7]} installiert: {betreff}")
