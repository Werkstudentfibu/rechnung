"""Vorrang fuer den Klick (Befund Aaron 13.11.2026: 194 s beim Anklicken):
Hintergrundarbeit wirklich niedrig, Texterkennung je Datei nur einmal, eine
Datei, auf die der Mensch wartet, wird hochgestuft."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from rechnung import ocr, prioritaet
from rechnung.guard import Guard


def test_niedrig_nur_im_hintergrund_und_nicht_wenn_eilig():
    assert prioritaet.niedrig_fuer("a.pdf", hintergrund=False) is False
    assert prioritaet.niedrig_fuer("a.pdf", hintergrund=True) is True
    with prioritaet.eilig("a.pdf"):
        assert prioritaet.niedrig_fuer("a.pdf", hintergrund=True) is False
        assert prioritaet.niedrig_fuer("b.pdf", hintergrund=True) is True
        with prioritaet.eilig("a.pdf"):          # zwei Klicks gleichzeitig
            pass
        assert prioritaet.ist_eilig("a.pdf")
    assert not prioritaet.ist_eilig("a.pdf")


def test_unterprozess_flags():
    assert prioritaet.subprocess_flags(False) == {}
    flags = prioritaet.subprocess_flags(True)
    if sys.platform == "win32":
        assert flags["creationflags"] & prioritaet.BELOW_NORMAL
    else:
        assert callable(flags["preexec_fn"])


def _melde_hintergrund(path, store, **kw):
    return SimpleNamespace(hintergrund=prioritaet.ist_hintergrund())


def test_hintergrund_guard_setzt_den_verarbeitungs_thread(tmp_path: Path):
    ziel = f"{__name__}:_melde_hintergrund"
    hg = Guard(tmp_path, timeout=10, mode="thread", target=ziel, niedrig=True)
    vorn = Guard(tmp_path, timeout=10, mode="thread", target=ziel)
    assert hg.process(tmp_path / "x.pdf").hintergrund is True
    assert vorn.process(tmp_path / "x.pdf").hintergrund is False


def test_texterkennung_je_datei_nur_einmal_gleichzeitig(tmp_path: Path, monkeypatch):
    datei = tmp_path / "scan.pdf"
    datei.write_bytes(b"%PDF-1.4 test")
    gleichzeitig, hoechst = [0], [0]
    lock = threading.Lock()

    def langsam(path, cache_dir, timeout=None, full=False, sha=None):
        with lock:
            gleichzeitig[0] += 1
            hoechst[0] = max(hoechst[0], gleichzeitig[0])
        time.sleep(0.3)
        with lock:
            gleichzeitig[0] -= 1
        return ocr.OcrResult(sha)

    monkeypatch.setattr(ocr, "_ensure_text", langsam)
    ts = [threading.Thread(target=ocr.ensure_text, args=(datei, tmp_path / "ocr")) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert hoechst[0] == 1


def test_tesseract_bekommt_niedrige_prioritaet(monkeypatch):
    aufrufe = []
    monkeypatch.setattr(ocr.subprocess, "run", lambda args, **kw: aufrufe.append(kw))
    ocr._run(["tesseract", "--version"], 5, niedrig=True)
    ocr._run(["tesseract", "--version"], 5)
    if sys.platform == "win32":
        assert aufrufe[0]["creationflags"] & prioritaet.BELOW_NORMAL
        assert not aufrufe[1]["creationflags"] & prioritaet.BELOW_NORMAL
    else:
        assert "preexec_fn" in aufrufe[0] and "preexec_fn" not in aufrufe[1]
