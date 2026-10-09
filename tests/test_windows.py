"""Laptop-Tauglichkeit unter Windows (SPEC §7w): Tesseract-Pfad, OCR an/aus,
Lock-Datei ohne PID-Signale, Konsole/CSV-Kodierung, Arbeitsprozess per
spawn, Windows-Skripte und PyInstaller-Spec. Laeuft hier unter Linux --
geprueft wird, was sich ohne Windows pruefen laesst."""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

import pytest

from rechnung import cli, guard, human, lock, ocr

ROOT = Path(__file__).resolve().parent.parent


# ---- Tesseract-Pfad ----------------------------------------------------------
def test_tesseract_env_wins(monkeypatch):
    monkeypatch.setenv("RECHNUNG_TESSERACT", r"D:\Tools\tesseract.exe")
    assert ocr.tesseract_cmd() == r"D:\Tools\tesseract.exe"


def test_tesseract_windows_standard_path_fallback(monkeypatch, tmp_path: Path):
    exe = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"")
    monkeypatch.delenv("RECHNUNG_TESSERACT", raising=False)
    monkeypatch.setattr(ocr.shutil, "which", lambda _n: None)
    monkeypatch.setattr(ocr, "_on_windows", lambda: True)
    # eine echte portable Einrichtung (<Basis>\tesseract) haette Vorrang
    monkeypatch.setattr(ocr, "portable_tesseract", lambda: tmp_path / "fehlt.exe")
    monkeypatch.setenv("TESTPROGRAMS", str(tmp_path))
    monkeypatch.setattr(ocr, "_WINDOWS_CANDIDATES",
                        ("%NICHT_GESETZT%/Tesseract-OCR/tesseract.exe",
                         "%TESTPROGRAMS%/Tesseract-OCR/tesseract.exe"))
    assert ocr.tesseract_cmd() == str(exe)
    # Nicht unter Windows: kein Fallback
    monkeypatch.setattr(ocr, "_on_windows", lambda: False)
    assert ocr.tesseract_cmd() is None


def test_windows_fallback_lists_program_files():
    assert any("Program" in c and c.endswith(r"Tesseract-OCR\tesseract.exe")
               for c in ocr._WINDOWS_CANDIDATES)


def test_no_console_window_flag_on_windows(monkeypatch):
    seen = {}
    monkeypatch.setattr(ocr, "_on_windows", lambda: True)
    monkeypatch.setattr(ocr.subprocess, "run", lambda args, **kw: seen.update(kw))
    ocr._run(["tesseract", "--version"], 5)
    assert "creationflags" in seen and seen["capture_output"]


# ---- OCR an/aus --------------------------------------------------------------
def test_ocr_disabled_defers_scan_to_human(monkeypatch, tmp_path: Path):
    from PIL import Image

    from rechnung.pipeline import DEFERRED, process
    from rechnung.store import Store

    monkeypatch.setenv("RECHNUNG_OCR_ENABLED", "0")
    monkeypatch.setattr(ocr, "available", lambda: pytest.fail("darf nicht aufgerufen werden"))
    img = tmp_path / "scan.png"
    Image.new("RGB", (50, 50), "white").save(img)
    res = process(img, Store(tmp_path / "data"))
    assert res.status == DEFERRED and res.hold_reasons == [ocr.OCR_DISABLED]
    assert [g["art"] for g in human.gruende(res)] == ["ocr"]


def test_ocr_enabled_by_default(monkeypatch):
    monkeypatch.delenv("RECHNUNG_OCR_ENABLED", raising=False)
    assert ocr.enabled()


# ---- Lock-Datei ----------------------------------------------------------------
def test_stale_lock_is_ignored_and_replaced(tmp_path: Path, capsys):
    with lock.acquire(tmp_path):
        pass
    p = lock.lock_path(tmp_path)
    p.write_text("{}", "utf-8")                       # liegengeblieben (Absturz)
    old = time.time() - lock.STALE_S - 60
    os.utime(p, (old, old))
    assert not lock.is_locked(tmp_path)
    lock.wait_until_unlocked(tmp_path, max_wait=5, poll=0.01)   # kehrt sofort zurueck
    with lock.acquire(tmp_path):
        assert lock.is_locked(tmp_path)
    assert "liegengebliebene Lock-Datei" in capsys.readouterr().err
    assert not p.exists()


def test_fresh_lock_still_blocks(tmp_path: Path):
    with lock.acquire(tmp_path):
        with pytest.raises(RuntimeError):
            with lock.acquire(tmp_path):
                pass


def test_lock_module_does_not_signal_processes():
    src = (ROOT / "rechnung" / "lock.py").read_text("utf-8")
    assert "os.kill" not in src.replace("``os.kill(pid, 0)``", "")


# ---- Zeitgrenze: Arbeitsprozess per spawn (wie unter Windows) ----------------
def test_guard_uses_spawn(monkeypatch, tmp_path: Path):
    seen = []
    real = guard.mp.get_context

    def spy(method=None):
        seen.append(method)
        return real(method)

    monkeypatch.setattr(guard.mp, "get_context", spy)
    g = guard.Guard(tmp_path, timeout=30)
    try:
        g._start()
    finally:
        g.close()
    assert seen == ["spawn"]


def test_run_py_calls_freeze_support():
    src = (ROOT / "run.py").read_text("utf-8")
    assert "multiprocessing.freeze_support()" in src


# ---- Kodierung -------------------------------------------------------------------
def test_console_output_never_crashes_on_cp1252(monkeypatch):
    buf = io.BytesIO()
    out = io.TextIOWrapper(buf, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    cli._console_safe()
    print("Ø 80 → Σ ✓")
    out.flush()
    assert buf.getvalue().startswith(b"\xd8 80 ?")


def test_csv_output_has_bom_for_excel(tmp_path: Path):
    from rechnung.store import Store

    p = Store(tmp_path / "d").write_output("x", {"lieferant": "Müller"})
    raw = p.with_suffix(".csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and "Müller".encode("utf-8") in raw


def test_windows_paths_in_results(tmp_path: Path):
    from rechnung import eventlog
    from rechnung.pipeline import Result
    from rechnung.store import Store

    store = Store(tmp_path / "d")
    rec = eventlog.record(store, Result(file=r"C:\Rechnung\eingang\re 1.pdf", sha256="x"))
    assert rec["file"] == "re 1.pdf"


# ---- Windows-Skripte + Build ------------------------------------------------------
@pytest.mark.parametrize("name", ["windows/start-rechnung.cmd", "windows/nachtlauf.cmd",
                                  "build-windows.ps1", "windows/laptop-start.cmd",
                                  "windows/laptop-nachtlauf.cmd", "laptop-einrichten.ps1"])
def test_windows_scripts_are_ascii_with_crlf(name):
    raw = (ROOT / name).read_bytes()
    raw.decode("ascii")      # Windows PowerShell 5.1/cmd: keine Umlaute ohne BOM
    assert b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n")


def test_cmd_scripts_load_env_skipping_comments():
    for name in ("start-rechnung.cmd", "nachtlauf.cmd"):
        src = (ROOT / "windows" / name).read_text("ascii")
        assert 'eol=#' in src and "rechnung.env" in src
        assert "cd /d \"%~dp0\"" in src


def test_env_example_is_loadable_by_cmd():
    """Jede Nicht-Kommentarzeile muss NAME=wert sein (for /f ... set)."""
    for line in (ROOT / "rechnung.env.example").read_text("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assert line == line.lstrip() and "=" in line, line
        assert line.split("=", 1)[0].isidentifier(), line


def test_build_script_copies_existing_files():
    src = (ROOT / "build-windows.ps1").read_text("ascii")
    for f in ("windows\\start-rechnung.cmd", "windows\\nachtlauf.cmd", "rechnung.env.example",
              "jobrouter-mapping.example.json", "INSTALL-WINDOWS.md", "rechnung.spec"):
        assert f in src, f
        assert (ROOT / f.replace("\\", "/")).exists(), f


def test_pyinstaller_spec_covers_package_and_static():
    src = (ROOT / "rechnung.spec").read_text("utf-8")
    assert 'collect_submodules("rechnung")' in src
    assert "rechnung/web/static" in src and '["run.py"]' in src
    compile(src, "rechnung.spec", "exec")


def test_laptop_start_restarts_on_update_exit_code():
    """Knopf "Update holen" beendet das Programm mit RESTART_EXIT_CODE --
    der Laptop-Starter muss genau darauf neu starten."""
    from rechnung import selbstupdate

    src = (ROOT / "windows" / "laptop-start.cmd").read_text("ascii")
    assert f'"%RC%"=="{selbstupdate.RESTART_EXIT_CODE}"' in src
    assert "goto start" in src
    assert " update" in src


def test_laptop_starter_run_from_temp_copy():
    """Die von laptop-einrichten.ps1 erzeugten Starter kopieren die Logik nach
    %TEMP%, damit ein Update die laufende .cmd nicht ueberschreibt."""
    src = (ROOT / "laptop-einrichten.ps1").read_text("ascii")
    for name in ("laptop-start.cmd", "laptop-nachtlauf.cmd"):
        assert f"app\\windows\\{name}`\" `\"%TEMP%" in src


def test_portable_tesseract_hat_vorrang(monkeypatch, tmp_path: Path):
    exe = tmp_path / "tesseract.exe"
    exe.write_bytes(b"")
    monkeypatch.delenv("RECHNUNG_TESSERACT", raising=False)
    monkeypatch.setattr(ocr.shutil, "which", lambda _n: None)
    monkeypatch.setattr(ocr, "_on_windows", lambda: True)
    monkeypatch.setattr(ocr, "portable_tesseract", lambda: exe)
    assert ocr.tesseract_cmd() == str(exe)
