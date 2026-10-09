"""Tempo-Bausteine (Befund Aaron 09.10.2026: Scan > 120 s auf dem Laptop)."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from rechnung import semantic
from rechnung.abholung import Abholer
from rechnung.vorverarbeitung import Vorverarbeiter


def test_kontext_reicht_fuer_typischen_rechnungsprompt():
    # 2.830 Zeichen Rechnungsauszug = real 1.077 Token (gemessen mit qwen2.5) --
    # 1024 schnitt den Anfang ab
    prompt = "x" * 2830
    assert semantic._dynamic_num_ctx(prompt, cap=4096) >= 2048


def test_ki_antwort_wird_auf_platte_gemerkt(tmp_path, monkeypatch):
    aufrufe = []

    def fake(cfg, prompt, schema=None):
        aufrufe.append(prompt)
        return '{"rechnungsnummer": "R-1"}'

    monkeypatch.setattr(semantic, "_call_ollama", fake)
    semantic.set_cache_dir(tmp_path / "ki-cache")
    cfg = semantic._llm_cfg()
    assert semantic._call(cfg, "prompt A") == '{"rechnungsnummer": "R-1"}'
    semantic._resp_cache.clear()          # wie nach einem Neustart
    assert semantic._call(cfg, "prompt A") == '{"rechnungsnummer": "R-1"}'
    assert len(aufrufe) == 1
    semantic._call(cfg, "prompt B")
    assert len(aufrufe) == 2


def _warte(bedingung, s=5.0):
    ende = time.monotonic() + s
    while time.monotonic() < ende:
        if bedingung():
            return True
        time.sleep(0.02)
    return False


def test_vorverarbeiter_liest_jede_datei_einmal(tmp_path):
    a = tmp_path / "a.pdf"; a.write_bytes(b"1")
    b = tmp_path / "b.pdf"; b.write_bytes(b"2")
    gelesen = []
    v = Vorverarbeiter(lambda p: gelesen.append(p.name))
    v.einreihen([a, b, a])
    assert _warte(lambda: v.status["fertig"] == 2)
    v.einreihen([a, b])                    # unveraendert -> nicht nochmal
    time.sleep(0.2)
    assert sorted(gelesen) == ["a.pdf", "b.pdf"]
    b.write_bytes(b"geaendert")            # geaenderte Datei -> erneut
    v.einreihen([b])
    assert _warte(lambda: gelesen.count("b.pdf") == 2)


def test_anzeige_wartet_auf_laufende_vorverarbeitung(tmp_path):
    a = tmp_path / "a.pdf"; a.write_bytes(b"1")
    start, frei = threading.Event(), threading.Event()

    def langsam(p):
        start.set()
        frei.wait(5)

    v = Vorverarbeiter(langsam)
    v.einreihen([a])
    assert start.wait(5)
    sperre = v.sperre(a)
    assert not sperre.acquire(blocking=False)   # Anzeige muesste warten
    frei.set()
    assert sperre.acquire(timeout=5)
    sperre.release()


def test_kaputte_datei_haelt_schlange_nicht_auf(tmp_path):
    a = tmp_path / "a.pdf"; a.write_bytes(b"1")
    b = tmp_path / "b.pdf"; b.write_bytes(b"2")
    gelesen = []

    def f(p):
        if p.name == "a.pdf":
            raise ValueError("kaputt")
        gelesen.append(p.name)

    v = Vorverarbeiter(f)
    v.einreihen([a, b])
    assert _warte(lambda: gelesen == ["b.pdf"])
    assert "kaputt" in v.status["fehler"]


def test_abholer_meldet_neue_dateien(tmp_path):
    class Res:
        error = ""
        mails = 1
        saved = [str(tmp_path / "neu.pdf"), "(dry) x.pdf <- a@b"]

    gemeldet = []
    ab = Abholer(tmp_path, 5, fetch=lambda dest: Res(), bei_neuen=gemeldet.extend)
    ab.einmal()
    assert gemeldet == [tmp_path / "neu.pdf"]
