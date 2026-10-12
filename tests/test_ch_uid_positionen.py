"""Schweizer UID ohne Leerzeichen vor MWST; leere KI-Positionen sichtbar
(Befund Aaron 12.10.2026, Zuerich-Rechnung)."""

import json

from rechnung import semantic
from rechnung.textnorm import find_ustids

from test_semantic_full import GOOD, _mock


def test_che_uid_schreibweisen():
    for t in ("CHE-105.846.358 MWST", "CHE-105.846.358MWST", "CHE- 105.846.358", "UID:CHE-105.846.358TVA"):
        assert find_ustids(t) == {"CHE105846358"}, t
    assert find_ustids("CHE-105.846.3581") == set()


def test_keine_ki_positionen_wird_angezeigt(monkeypatch):
    text = "Rechnung RE-2026-00042 vom 14.02.2026 Netto 1.000,00 USt 19 % 190,00 Brutto 1.190,00"
    _mock(monkeypatch, dict(GOOD, positionen=[]), line_items=True)
    full = semantic.extract_full(text)
    assert full.rejected.get("positionen") == "KI hat keine Positionen geliefert"
