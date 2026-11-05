"""Befund Aaron 05.11.2026: "legt immer noch keine Positionsdaten-Tabelle an".

Ursache: Sobald ein Lieferant eine Vorlage hat, die KEINE Positionstabelle
kennt (beim Lernen war die Tabelle ``nicht reproduzierbar`` -- Normalfall bei
der ersten Rechnung), lief fuer alle weiteren Rechnungen dieses Lieferanten
gar keine Tabellenerkennung mehr: ``process`` und die Bearbeitungsansicht
nutzten nur ``tpl.line_items``. Ohne Vorlage fand dieselbe Erkennung die
Positionen problemlos."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rechnung import learn, pipeline
from rechnung import pdf as pdfmod
from rechnung.identify import Supplier
from rechnung.store import Store

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def _store_mit_vorlage_ohne_tabelle(tmp_path):
    st = Store(tmp_path / "data")
    st.save_supplier(Supplier(id="mh", name="Muster Handels GmbH", ustids=["DE123456789"]))
    p1 = make_invoice(tmp_path / "1.pdf", rechnungsnr="RE-1")
    sha = pipeline.process(p1, st, use_llm=False).sha256
    for f, v in dict(rechnungsnummer="RE-1", rechnungsdatum="14.02.2026", netto="1000,00",
                     ust_betrag="190,00", brutto="1190,00").items():
        st.save_correction(sha, f, v, "t")
    r1 = pipeline.process(p1, st, use_llm=False)
    st.mark_confirmed(r1.sha256, "t")
    learn.learn_from_confirmed(st, r1, pdfmod.load(p1), "t")
    tpls = st.templates_for("mh")
    assert tpls, "Vorlage muss gelernt worden sein"
    for t in tpls:      # Vorlage kennt keine Positionstabelle
        t.line_items = None
        st.save_template(t)
    return st


def test_vorlage_ohne_tabelle_findet_positionen_trotzdem(tmp_path):
    st = _store_mit_vorlage_ohne_tabelle(tmp_path)
    p2 = make_invoice(tmp_path / "2.pdf", rechnungsnr="RE-2", netto=500.0)

    res = pipeline.process(p2, st, use_llm=False)

    assert res.template_id == "mh__standard"
    assert res.line_items, "Tabellenerkennung muss auch mit Vorlage ohne Positionsspec laufen"
    assert round(sum(it["gesamt_netto"] for it in res.line_items), 2) == 500.0


def test_manuell_geleerte_positionen_bleiben_leer(tmp_path):
    """Hat der Mensch die Positionen von Hand ersetzt, gewinnt das."""
    st = _store_mit_vorlage_ohne_tabelle(tmp_path)
    p2 = make_invoice(tmp_path / "2.pdf", rechnungsnr="RE-2", netto=500.0)
    sha = pipeline.process(p2, st, use_llm=False).sha256
    st.save_correction(sha, pipeline.POSITIONEN_KEY,
                       [{"beschreibung": "von Hand", "menge": 1, "gesamt_netto": 500.0}], "t")

    res = pipeline.process(p2, st, use_llm=False)

    assert [it["beschreibung"] for it in res.line_items] == ["von Hand"]


def test_bearbeitungsansicht_zeigt_positionen_bei_vorlage_ohne_tabelle(tmp_path):
    from rechnung.web import server as server_mod

    st = _store_mit_vorlage_ohne_tabelle(tmp_path)
    samples = tmp_path / "eingang"
    samples.mkdir()
    make_invoice(samples / "b.pdf", rechnungsnr="RE-2", netto=500.0)
    client = TestClient(server_mod.create_app(st, samples))

    d = client.get("/api/invoice", params={"file": "b.pdf"}).json()

    assert d["line_items"], "Bearbeitungsansicht muss die erkannte Tabelle zeigen"


def test_diagnose_nennt_grund_bei_leerer_tabelle(tmp_path, monkeypatch):
    """Ohne erkennbare Tabelle und mit KI aus steht der Grund in der Antwort."""
    from rechnung import config
    from rechnung.synth import make_plain_doc
    from rechnung.web import server as server_mod

    monkeypatch.setattr(config, "llm", lambda: config.LlmConfig(enabled=False))
    st = Store(tmp_path / "data")
    samples = tmp_path / "eingang"
    samples.mkdir()
    make_plain_doc(samples / "c.pdf", titel="Brief", zeilen=["Hallo", "Kein Tabellenkopf hier"])
    client = TestClient(server_mod.create_app(st, samples))

    d = client.get("/api/invoice", params={"file": "c.pdf"}).json()

    assert not d["line_items"]
    assert "keine Tabelle erkannt" in d["positionen_grund"]
    assert "KI ausgeschaltet" in d["positionen_grund"]


def test_keine_diagnose_wenn_positionen_da(tmp_path):
    from rechnung.web import server as server_mod

    st = Store(tmp_path / "data")
    samples = tmp_path / "eingang"
    samples.mkdir()
    make_invoice(samples / "a.pdf", netto=500.0)
    client = TestClient(server_mod.create_app(st, samples))

    d = client.get("/api/invoice", params={"file": "a.pdf"}).json()

    assert d["line_items"] and d["positionen_grund"] == ""
