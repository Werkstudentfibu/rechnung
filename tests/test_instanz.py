"""Serverbetrieb "Einzelplatz per Remote" (Aaron 19.11.2026): nur EIN
Programmfenster je Datenordner -- ein zweiter Start meldet, wer es offen
hat, statt still auf einem anderen Port dieselben Daten zu beschreiben.
Dazu der Startklar-Check fuer WebView2 (Programmfenster auf Windows Server)."""

from __future__ import annotations

import json
import os
import sys
import time
import types

import pytest

from rechnung import instanz, startklar


def _fremde_sperre(root, pid=None, alter_s=0.0, **extra):
    p = root / instanz.DATEI
    p.write_text(json.dumps({"pid": pid if pid is not None else os.getpid() + 999999,
                             "benutzer": "kollege", "rechner": "SRV01",
                             "seit": "19.11.2026 08:00", "port": 8000, **extra}), "utf-8")
    if alter_s:
        t = time.time() - alter_s
        os.utime(p, (t, t))
    return p


def test_sperre_nehmen_und_freigeben(tmp_path):
    s = instanz.Sperre(tmp_path).nehmen()
    info = json.loads((tmp_path / instanz.DATEI).read_text("utf-8"))
    assert info["pid"] == os.getpid() and info["rechner"]
    s.freigeben()
    assert not (tmp_path / instanz.DATEI).exists()


def test_zweiter_start_wird_abgewiesen(tmp_path, monkeypatch):
    _fremde_sperre(tmp_path)
    monkeypatch.setattr(instanz, "_prozess_laeuft", lambda pid: True)
    with pytest.raises(instanz.LaeuftBereits) as e:
        instanz.Sperre(tmp_path).nehmen()
    text = str(e.value)
    assert "bereits geoeffnet" in text and "kollege" in text and "SRV01" in text
    # die fremde Sperre bleibt unangetastet
    assert json.loads((tmp_path / instanz.DATEI).read_text("utf-8"))["benutzer"] == "kollege"


def test_sperre_eines_beendeten_prozesses_wird_uebernommen(tmp_path, monkeypatch):
    _fremde_sperre(tmp_path)
    monkeypatch.setattr(instanz, "_prozess_laeuft", lambda pid: pid == os.getpid())
    s = instanz.Sperre(tmp_path).nehmen()
    assert json.loads((tmp_path / instanz.DATEI).read_text("utf-8"))["pid"] == os.getpid()
    s.freigeben()


def test_veraltete_sperre_wird_uebernommen(tmp_path, monkeypatch):
    """Prozess laeuft angeblich noch (z.B. fremder Benutzer, nicht pruefbar),
    die Sperre wurde aber laenger nicht erneuert -> haengt -> frei."""
    _fremde_sperre(tmp_path, alter_s=instanz.VERALTET_S + 5)
    monkeypatch.setattr(instanz, "_prozess_laeuft", lambda pid: True)
    instanz.Sperre(tmp_path).nehmen().freigeben()


def test_beendeter_prozess_wird_erkannt():
    """Echte Prozesspruefung (unter Windows OpenProcess, sonst os.kill 0)."""
    import subprocess

    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert instanz._prozess_laeuft(os.getpid()) is True
    assert instanz._prozess_laeuft(p.pid) is False
    assert instanz._prozess_laeuft(0) is False


def test_sperre_wird_laufend_erneuert(tmp_path, monkeypatch):
    monkeypatch.setattr(instanz, "ERNEUERN_S", 0.05)
    s = instanz.Sperre(tmp_path).nehmen()
    p = tmp_path / instanz.DATEI
    alt = time.time() - 1000
    os.utime(p, (alt, alt))
    time.sleep(0.3)
    assert time.time() - p.stat().st_mtime < 10
    s.freigeben()


def test_freigeben_vor_neustart(tmp_path):
    """/api/neustart beendet mit os._exit -- vorher gibt instanz.freigeben()
    die Sperre dieses Prozesses frei."""
    instanz.Sperre(tmp_path).nehmen()
    instanz.freigeben()
    assert not (tmp_path / instanz.DATEI).exists()
    instanz.freigeben()  # zweimal ist harmlos


def test_fremde_sperre_wird_beim_freigeben_nicht_geloescht(tmp_path):
    s = instanz.Sperre(tmp_path)
    _fremde_sperre(tmp_path)
    s.freigeben()
    assert (tmp_path / instanz.DATEI).exists()


def test_programmfenster_zweiter_start(tmp_path, monkeypatch):
    """``rechnung app``: zweiter Start endet mit Meldung, ohne Fenster;
    nach dem Schliessen des Fensters ist die Sperre frei."""
    from rechnung import desktop
    from rechnung.store import Store

    fenster = []
    fake_webview = types.SimpleNamespace(
        create_window=lambda *a, **k: fenster.append(a),
        start=lambda: None)
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    store = Store(tmp_path / "data")

    monkeypatch.setattr(instanz, "_prozess_laeuft", lambda pid: True)
    _fremde_sperre(store.root)
    with pytest.raises(SystemExit) as e:
        desktop.run(store, tmp_path / "eingang", port=0)
    assert "bereits geoeffnet" in str(e.value)
    assert fenster == []

    (store.root / instanz.DATEI).unlink()
    desktop.run(store, tmp_path / "eingang", port=0)
    assert len(fenster) == 1
    assert not (store.root / instanz.DATEI).exists()


def test_browser_variante_zweiter_start(tmp_path, monkeypatch, capsys):
    from rechnung.cli import main as cli_main

    (tmp_path / "data").mkdir()
    monkeypatch.setattr(instanz, "_prozess_laeuft", lambda pid: True)
    _fremde_sperre(tmp_path / "data")
    assert cli_main(["--data", str(tmp_path / "data"), "web"]) == 3
    assert "bereits geoeffnet" in capsys.readouterr().out


@pytest.mark.parametrize("werte,status", [
    ({("HKLM", 0): "120.0.1.0"}, "ok"),
    ({("HKCU", 2): "121.0.0.0"}, "ok"),
    ({("HKLM", 0): "0.0.0.0"}, "fehler"),
    ({}, "fehler"),
])
def test_webview2_check(werte, status):
    schluessel = startklar._WEBVIEW2_SCHLUESSEL

    def lesen(hive, pfad):
        i = schluessel.index((hive, pfad))
        return werte.get((hive, i), "")

    z = startklar.webview2(plattform="win32", lesen=lesen)
    assert z["status"] == status
    if status == "fehler":
        assert "rechnung web" in z["hilfe"]


def test_webview2_nur_unter_windows():
    assert startklar.webview2(plattform="linux")["status"] == "aus"
