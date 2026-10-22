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
6. Testsatz-Schutz (testsatz.py, Aaron 10.10.2026): gibt es mindestens 5
   bestaetigte Testfaelle, liest der NEUE Stand sie im Trockenlauf; sinkt
   die Gesamt-Trefferquote gegenueber dem letzten gespeicherten Lauf (gleicher
   Filter) um mehr als 2 Prozentpunkte -> zurueckgerollt mit Grund.
   ``RECHNUNG_UPDATE_TESTSATZ``: ``0`` = aus, Standard = nur Testfaelle ohne
   KI (Vorlage/E-Rechnung, KI auf dem Laptop ist langsam), ``voll`` = alle.

``data\\`` und ``rechnung.env`` liegen ausserhalb des Klons und werden nie
angefasst. Jeder Versuch steht in ``<data>/update.log``.
"""

from __future__ import annotations

import re
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
TESTSATZ_TIMEOUT_S = 1800
# ab so vielen Testfaellen wird der Testsatz-Schutz aktiv (weniger: zu
# zufaellig, ein einzelnes Feld waere schon mehrere Prozentpunkte)
TESTSATZ_MIN_FAELLE = 5
# erlaubter Rueckgang der Gesamt-Trefferquote in Prozentpunkten
TESTSATZ_MAX_RUECKGANG = 2.0


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


def _quelle(root: Path) -> str:
    """GitHub-Quelle ohne moegliche Zugangsdaten aus der Remote-URL."""
    r = _git(root, "remote", "get-url", "origin")
    raw = r.stdout.strip() if r.returncode == 0 else ""
    raw = re.sub(r"^(https?://)[^/@]+@", r"\1", raw)
    raw = raw.removeprefix("git@").replace(":", "/", 1).removesuffix(".git")
    return raw


def version(root: Path | None = None) -> dict:
    """Aktueller Stand und eindeutige Update-Quelle fuer die Anzeige."""
    root = root or repo_root()
    if not ist_git_klon(root):
        return {"git": False, "commit": "", "datum": "", "betreff": ""}
    r = _git(root, "log", "-1", "--format=%h%x1f%cd%x1f%s", "--date=format:%d.%m.%Y %H:%M")
    teile = (r.stdout.strip().split("\x1f") + ["", "", ""])[:3] if r.returncode == 0 else ["", "", ""]
    aktiv = _git(root, "branch", "--show-current")
    ziel = _rev(root, f"origin/{BRANCH}")
    return {"git": True, "commit": teile[0], "datum": teile[1], "betreff": teile[2],
            "update_branch": BRANCH,
            "aktiver_branch": aktiv.stdout.strip() if aktiv.returncode == 0 else "",
            "update_remote": _quelle(root), "update_ziel": ziel[:7]}


def _pip_install(root: Path, python: str) -> subprocess.CompletedProcess:
    return _run([python, "-m", "pip", "install", "--disable-pip-version-check", "-q",
                 "-e", ".[dev]"], root, PIP_TIMEOUT_S)


def _tests(root: Path, python: str) -> subprocess.CompletedProcess:
    return _run([python, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], root, TEST_TIMEOUT_S)


def _testsatz_modus() -> str:
    """"aus" | "ohne_ki" (Standard) | "voll" -- aus RECHNUNG_UPDATE_TESTSATZ."""
    import os

    v = os.environ.get("RECHNUNG_UPDATE_TESTSATZ", "").strip().lower()
    if v in ("0", "aus", "nein", "false", "off"):
        return "aus"
    return "voll" if v == "voll" else "ohne_ki"


def _testsatz(root: Path, data_root: Path, python: str, ohne_ki: bool) -> tuple[dict | None, str]:
    """Testsatz mit dem NEUEN Stand (eigener Prozess aus dem Klon) im
    Trockenlauf -> (Bericht, Fehlertext). Speichert selbst nichts -- erst
    nach bestandenem Vergleich wird der Lauf als neuer Bezug abgelegt."""
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "testsatz.json"
        cmd = [python, "-m", "rechnung.cli", "--data", str(data_root), "testsatz",
               "--kein-bericht", "--ausgabe", str(out)] + (["--ohne-ki"] if ohne_ki else [])
        r = _run(cmd, root, TESTSATZ_TIMEOUT_S)
        if r.returncode != 0 or not out.exists():
            return None, _letzte_zeilen(r.stdout + r.stderr)
        try:
            return json.loads(out.read_text("utf-8")), ""
        except (OSError, ValueError) as exc:
            return None, f"Bericht nicht lesbar: {exc}"


def _testsatz_schutz(root: Path, data_root: Path | None, python: str) -> str:
    """Leerer Text = in Ordnung (oder nicht anwendbar), sonst Grund fuers
    Zurueckrollen."""
    modus = _testsatz_modus()
    if modus == "aus" or data_root is None:
        return ""
    from . import testsatz

    ohne_ki = modus != "voll"
    if testsatz.anzahl(data_root, nur_ohne_ki=ohne_ki) < TESTSATZ_MIN_FAELLE:
        return ""
    neu, fehler = _testsatz(root, data_root, python, ohne_ki)
    if neu is None:
        return "Testsatz liess sich mit dem neuen Stand nicht ausfuehren:\n" + fehler
    alt = testsatz.letzter_bericht(data_root, neu.get("filter"))
    neu["vergleich"] = testsatz.vergleich(alt, neu)
    qa, qn = (alt or {}).get("trefferquote"), neu.get("trefferquote")
    if qa is not None and qn is not None and qa - qn > TESTSATZ_MAX_RUECKGANG:
        schlechter = "; ".join(neu["vergleich"]["schlechter"][:8])
        return (f"Testsatz schlechter: Trefferquote {qa:.1f} % -> {qn:.1f} % "
                f"(mehr als {TESTSATZ_MAX_RUECKGANG:g} Prozentpunkte)"
                + (f" -- schlechter bei: {schlechter}" if schlechter else ""))
    neu["selbstupdate"] = True
    testsatz.speichern(data_root, neu)
    return ""


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
    quelle = _quelle(root)
    if not neu:
        return Ergebnis("fehler", alt=alt, meldung=f"Zweig {BRANCH} nicht gefunden in {quelle or 'der eingestellten Quelle'}.")
    if alt == neu:
        return Ergebnis("aktuell", alt=alt, neu=neu,
                        meldung=f"Schon auf dem neuesten Stand von {quelle or 'origin'} ({BRANCH}).")

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
            grund = _testsatz_schutz(root, data_root, python)
            if grund:
                return zurueck(grund)
    except subprocess.TimeoutExpired as exc:
        return zurueck(f"Zeitueberschreitung: {exc}")

    betreff = version(root).get("betreff", "")
    return Ergebnis("aktualisiert", alt=alt, neu=neu,
                    meldung=f"Neuer Stand {neu[:7]} installiert: {betreff}")
