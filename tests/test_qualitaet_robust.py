"""Fehler in den Qualitaets-Hinweisen kippen nie die Verarbeitung."""

from pathlib import Path

import pytest

from rechnung import pipeline, plausi
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def test_kaputte_plausi_kippt_verarbeitung_nicht(tmp_path: Path, monkeypatch):
    def kaputt(*a, **k):
        raise ValueError("unerwartet")
    monkeypatch.setattr(plausi, "hinweise", kaputt)
    res = pipeline.process(make_invoice(tmp_path / "a.pdf"), Store(tmp_path / "data"))
    assert res.sha256 and any("Qualitaetshinweise nicht verfuegbar" in h for h in res.hinweise)
