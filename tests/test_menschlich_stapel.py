"""Stapel "Menschliche Bearbeitung" (Aaron 12.11.2026): nach Senden /
Keine Rechnung kommt die naechste Rechnung DIESES Stapels. Testet die reine
Funktion ``naechsteImStapel`` aus index.html mit Node (wie
test_lieferant_status_frontend.py); ohne Node wird uebersprungen."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HTML = Path(__file__).resolve().parent.parent / "rechnung" / "web" / "static" / "index.html"


def _js(aufrufe: str) -> list:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht installiert")
    html = HTML.read_text("utf-8")
    start = html.index("function naechsteImStapel")
    ende = html.index("function stapelStand")
    prog = html[start:ende] + "\n" + aufrufe
    out = subprocess.run([node, "-e", prog], capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    return json.loads(out)


def test_naechste_im_stapel_der_reihe_nach():
    r = _js("""
    const q = {dateien: ['a.pdf', 'b.pdf', 'c.pdf'], erledigt: []};
    const out = [naechsteImStapel(q, 'a.pdf'), naechsteImStapel(q, 'b.pdf'),
                 naechsteImStapel(q, 'c.pdf')];
    console.log(JSON.stringify(out));
    """)
    assert r == ["b.pdf", "c.pdf", None]


def test_uebersprungene_kommen_am_ende_dran():
    r = _js("""
    const q = {dateien: ['a.pdf', 'b.pdf', 'c.pdf'], erledigt: []};
    const out = [naechsteImStapel(q, 'b.pdf'), naechsteImStapel(q, 'c.pdf')];
    console.log(JSON.stringify(out));
    """)
    assert r == ["c.pdf", "a.pdf"]


def test_stapel_haken_im_html():
    """Senden, Bestaetigen und Keine Rechnung pruefen den Stapel; ein Klick in
    der Hauptliste beendet ihn."""
    html = HTML.read_text("utf-8")
    assert html.count("if (HUMAN_QUEUE) {") >= 3
    assert "if (row) HUMAN_QUEUE = null;" in html
    assert "HUMAN_QUEUE = { dateien: d.offen.map(x => x.datei), erledigt: [] };" in html
