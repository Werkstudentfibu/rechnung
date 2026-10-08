"""Selbst-Update aus dem Zweig ``release`` (rechnung/selbstupdate.py) --
mit echten Git-Repos im tmp-Ordner: ein "Server"-Repo als origin, ein Klon
als Laptop-Installation."""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from rechnung import lock, selbstupdate

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git fehlt")

PASSING = "def test_ok():\n    assert True\n"
FAILING = "def test_kaputt():\n    assert False\n"


def git(cwd, *args):
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def commit(repo, name, inhalt, msg):
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "tests" / name).write_text(inhalt)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    git(repo, "push", "-q", "origin", "HEAD:release")


@pytest.fixture
def repos(tmp_path):
    bare = tmp_path / "server.git"
    git(tmp_path, "init", "-q", "--bare", str(bare))
    dev = tmp_path / "dev"
    git(tmp_path, "clone", "-q", str(bare), str(dev))
    commit(dev, "test_a.py", PASSING, "erster Stand")
    laptop = tmp_path / "laptop"
    git(tmp_path, "clone", "-q", "-b", "release", str(bare), str(laptop))
    data = tmp_path / "data"
    data.mkdir()
    return dev, laptop, data


def test_aktuell_ohne_neuen_stand(repos):
    _, laptop, data = repos
    e = selbstupdate.aktualisieren(data, root=laptop, python=sys.executable)
    assert e.status == "aktuell" and not e.neustart_noetig


def test_neuer_stand_wird_installiert_und_protokolliert(repos):
    dev, laptop, data = repos
    commit(dev, "test_b.py", PASSING, "Fix Artikeltabelle")
    e = selbstupdate.aktualisieren(data, root=laptop, python=sys.executable)
    assert e.status == "aktualisiert", e.meldung
    assert e.neustart_noetig
    assert git(laptop, "rev-parse", "HEAD") == git(dev, "rev-parse", "HEAD")
    assert "Fix Artikeltabelle" in e.meldung
    assert "aktualisiert" in (data / "update.log").read_text(encoding="utf-8")


def test_rote_tests_rollen_zurueck(repos):
    dev, laptop, data = repos
    alt = git(laptop, "rev-parse", "HEAD")
    commit(dev, "test_c.py", FAILING, "kaputter Stand")
    e = selbstupdate.aktualisieren(data, root=laptop, python=sys.executable)
    assert e.status == "zurueckgerollt"
    assert "Tests fehlgeschlagen" in e.meldung
    assert git(laptop, "rev-parse", "HEAD") == alt
    assert not (laptop / "tests" / "test_c.py").exists()


def test_ohne_netz_bleibt_alles_wie_es_ist(repos):
    _, laptop, data = repos
    alt = git(laptop, "rev-parse", "HEAD")
    git(laptop, "remote", "set-url", "origin", str(laptop.parent / "gibt-es-nicht.git"))
    e = selbstupdate.aktualisieren(data, root=laptop, python=sys.executable)
    assert e.status == "offline"
    assert git(laptop, "rev-parse", "HEAD") == alt


def test_waehrend_nachtlauf_kein_update(repos):
    dev, laptop, data = repos
    alt = git(laptop, "rev-parse", "HEAD")
    commit(dev, "test_b.py", PASSING, "neu")
    lock.lock_path(data).write_text("{}")
    e = selbstupdate.aktualisieren(data, root=laptop, python=sys.executable)
    assert e.status == "beschaeftigt"
    assert git(laptop, "rev-parse", "HEAD") == alt


def test_kein_git_klon(tmp_path):
    e = selbstupdate.aktualisieren(tmp_path, root=tmp_path)
    assert e.status == "kein_git"


def test_version_zeigt_stand(repos):
    _, laptop, _ = repos
    v = selbstupdate.version(laptop)
    assert v["git"] and v["betreff"] == "erster Stand" and v["commit"]


def test_daten_und_env_bleiben_unberuehrt(repos):
    """rechnung.env/.venv im Klon sind ignoriert bzw. ungetrackt -- reset
    --hard darf sie nicht loeschen."""
    dev, laptop, data = repos
    (dev / ".gitignore").write_text(".venv/\nrechnung.env\n")
    git(dev, "add", "-A"); git(dev, "commit", "-q", "-m", "ignore"); git(dev, "push", "-q", "origin", "HEAD:release")
    selbstupdate.aktualisieren(data, root=laptop, python=sys.executable)
    (laptop / "rechnung.env").write_text("X=1\n")
    commit(dev, "test_b.py", PASSING, "neu")
    e = selbstupdate.aktualisieren(data, root=laptop, python=sys.executable)
    assert e.status == "aktualisiert"
    assert (laptop / "rechnung.env").read_text() == "X=1\n"
