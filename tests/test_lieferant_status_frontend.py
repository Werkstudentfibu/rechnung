"""Teil B (Aarons zweiter Wunsch, 06.11.2026): klar sichtbare Anzeige, ob und
warum die Lieferanten-Bibliothek einen Treffer hatte, direkt beim
Lieferantenfeld in der Bearbeitungsansicht. Testet die reine Funktion
``liefBibliothekStatus`` aus index.html mit Node (wie
tests/test_vorbereitung.py es fuer ``naechsteBereite`` schon macht) --
keine DOM-Abhaengigkeit, daher ohne Browser pruefbar."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from rechnung import vorbereitung as vb


def _status(d: dict, hold_reasons: list[str]) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht installiert")
    html = (Path(vb.__file__).parent / "web" / "static" / "index.html").read_text("utf-8")
    start = html.index("function liefBibliothekStatus")
    ende = html.index("function renderInvoice")
    code = html[start:ende]
    prog = f"""
    {code}
    const r = liefBibliothekStatus({json.dumps(d)}, {json.dumps(hold_reasons)});
    console.log(JSON.stringify(r));
    """
    # encoding="utf-8" explizit: ohne das decodiert Windows subprocess-stdout
    # mit der Locale-Kodierung (cp1252) statt UTF-8 -- "bestätigt" wurde so
    # in der Windows-CI zu "best�tigt" (Befund 06.11.2026).
    out = subprocess.run([node, "-e", prog], capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    return json.loads(out)


def test_erkannt_und_bestaetigt_ist_gruen_mit_score():
    d = {"supplier": {"bestaetigt": True}, "match": {"score": 0.42}}
    r = _status(d, [])
    assert r["cls"] == "ok"
    assert "0.42" in r["text"]
    assert "Stammdaten" in r["detail"]


def test_erkannt_aber_unbestaetigter_entwurf_ist_gelb_und_eigener_text():
    """Unterscheidet sich vom Fall 'gar kein Treffer' (siehe Auftrag) --
    eigener Hinweistext, nicht nur eine Variante des roten Falls."""
    d = {"supplier": {"bestaetigt": False}, "match": {"score": 0.6}}
    r = _status(d, [])
    assert r["cls"] == "warn"
    assert "nicht bestätigt" in r["text"]
    assert "bestätigen" in r["detail"]


def test_kein_treffer_ist_rot_mit_tatsaechlichem_grund():
    d = {"supplier": None, "match": None}
    hold = ["Lieferant: kein Lieferant aus der Bibliothek passt unter Rechnungsadresse Beispiel Kunde AG"]
    r = _status(d, hold)
    assert r["cls"] == "bad"
    assert r["detail"] == "kein Lieferant aus der Bibliothek passt unter Rechnungsadresse Beispiel Kunde AG"


def test_kein_treffer_mehrdeutig_grund_wird_durchgereicht():
    d = {"supplier": None, "match": None}
    hold = ["Lieferant: mehrdeutig: a / b (Score 0.5 vs. 0.4)"]
    r = _status(d, hold)
    assert r["cls"] == "bad"
    assert "mehrdeutig" in r["detail"]


def test_kein_treffer_ohne_hold_reason_hat_generischen_fallback():
    """Falls ausnahmsweise kein 'Lieferant: ...'-Hold-Grund vorliegt (sollte
    laut pipeline.py nicht vorkommen, wenn kein Match da ist) -- kein Absturz,
    sondern ein generischer Grund."""
    r = _status({"supplier": None, "match": None}, [])
    assert r["cls"] == "bad"
    assert r["detail"]
