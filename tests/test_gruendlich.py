"""Modus "gruendlich" (Aaron 05.11.2026): solange das Programm lernt, geht
Genauigkeit vor Tempo. Standard AN (``RECHNUNG_GRUENDLICH=0`` schaltet ab);
ausdrueckliche Einzelwerte in rechnung.env gehen immer vor."""

from __future__ import annotations

import pytest

from rechnung import config, ocr, pipeline, semantic
from rechnung.template import FieldResult

from tests.test_gegencheck import _KI_PASSEND, _fake, _setup  # noqa: F401 (Hilfen)


@pytest.fixture
def gruendlich(monkeypatch):
    monkeypatch.setenv("RECHNUNG_GRUENDLICH", "1")
    for k in ("RECHNUNG_TIMEOUT", "RECHNUNG_LLM_TIMEOUT", "RECHNUNG_LLM_CTX",
              "RECHNUNG_LLM_LINE_ITEMS", "RECHNUNG_GRUENDLICH_SEITEN"):
        monkeypatch.delenv(k, raising=False)


def test_standard_ohne_variable_ist_gruendlich(monkeypatch):
    monkeypatch.delenv("RECHNUNG_GRUENDLICH", raising=False)
    assert config.gruendlich() is True
    monkeypatch.setenv("RECHNUNG_GRUENDLICH", "0")
    assert config.gruendlich() is False


def test_gruendlich_werte_und_ausdrueckliche_einstellung_geht_vor(gruendlich, monkeypatch):
    assert config.process_timeout() == 360
    cfg = config.llm()
    assert (cfg.timeout, cfg.num_ctx, cfg.line_items) == (120, 6144, True)

    monkeypatch.setenv("RECHNUNG_TIMEOUT", "120")
    monkeypatch.setenv("RECHNUNG_LLM_TIMEOUT", "30")
    monkeypatch.setenv("RECHNUNG_LLM_CTX", "2048")
    monkeypatch.setenv("RECHNUNG_LLM_LINE_ITEMS", "0")
    cfg = config.llm()
    assert config.process_timeout() == 120
    # ausdruecklich gesetzt -> gruendlich ueberschreibt NICHTS (die Standardwerte
    # der Klasse werden beim Import aus der Umgebung gelesen)
    basis = config.LlmConfig()
    assert (cfg.timeout, cfg.num_ctx, cfg.line_items) == (basis.timeout, basis.num_ctx, basis.line_items)


def test_aus_ist_wie_frueher(monkeypatch):
    monkeypatch.setenv("RECHNUNG_GRUENDLICH", "0")
    monkeypatch.delenv("RECHNUNG_TIMEOUT", raising=False)
    assert config.process_timeout() == 120
    assert config.llm() == config.LlmConfig()


def test_scans_voll_erkannt_nur_mit_genug_zeit(gruendlich, monkeypatch):
    assert ocr.partial_pages(8) is None                      # gruendlich: alle 8 Seiten
    assert ocr.partial_pages(30) == [0, 1, 28, 29]           # zu lang: Kopf/Fuss wie frueher
    monkeypatch.setenv("RECHNUNG_TIMEOUT", "120")            # Zeitgrenze fest auf 120 s
    assert ocr.partial_pages(8) == [0, 1, 6, 7]              # sonst riss die volle OCR die Grenze
    monkeypatch.setenv("RECHNUNG_GRUENDLICH", "0")
    monkeypatch.delenv("RECHNUNG_TIMEOUT")
    assert ocr.partial_pages(8) == [0, 1, 6, 7]


def test_ocr_sprache_nur_mit_installiertem_englisch(gruendlich, monkeypatch):
    monkeypatch.setattr(ocr, "tesseract_cmd", lambda: "tesseract")
    monkeypatch.setattr(ocr, "_installierte_sprachen", lambda cmd: frozenset({"deu", "eng", "osd"}))
    assert ocr.sprache() == "deu+eng"
    monkeypatch.setattr(ocr, "_installierte_sprachen", lambda cmd: frozenset({"deu"}))
    assert ocr.sprache() == "deu"                            # nie eine fehlende Sprache anfordern
    monkeypatch.setattr(ocr, "_installierte_sprachen", lambda cmd: frozenset({"deu", "eng"}))
    monkeypatch.setenv("RECHNUNG_GRUENDLICH", "0")
    assert ocr.sprache() == "deu"


def test_ki_bekommt_ganzen_text_statt_ausschnitt(gruendlich, monkeypatch):
    text = " ".join(f"wort{i}" for i in range(900))           # ~6000 Zeichen? knapp darunter
    text = text[:5500]
    assert semantic._targeted_excerpt(text, ["rechnungsnummer"]) == text
    monkeypatch.setenv("RECHNUNG_GRUENDLICH", "0")
    assert semantic._targeted_excerpt(text, ["rechnungsnummer"]) != text


def test_gegencheck_laeuft_immer_in_der_lernphase(tmp_path, monkeypatch, gruendlich):
    store, inv = _setup(tmp_path, confirmations=12)          # weit ueber GEGENCHECK_MIN
    aufrufe = []

    def fake(text, names, hints, rejected=None):
        aufrufe.append(list(names))
        return {n: FieldResult(n, v, str(v), 0.6, "llm") for n, v in _KI_PASSEND.items()
                if n in names}

    monkeypatch.setattr(pipeline.semantic, "extract", fake)
    res = pipeline.process(inv, store)
    assert aufrufe, "gruendlich: KI-Gegencheck auch bei eingespielter Vorlage"
    assert res.gegencheck_abweichungen == {}

    monkeypatch.setenv("RECHNUNG_GRUENDLICH", "0")
    aufrufe.clear()
    pipeline.process(inv, store)
    assert not aufrufe


def test_abweichung_vorlage_ki_macht_feld_unsicher_und_ist_im_ui_sichtbar(tmp_path, monkeypatch, gruendlich):
    store, inv = _setup(tmp_path, confirmations=12)
    abweichend = dict(_KI_PASSEND, netto=999.0)

    def fake(text, names, hints, rejected=None):
        # Gegencheck fragt OHNE Hinweise; die Luecken-Fuellung der Anzeige mit
        return {n: FieldResult(n, v, str(v), 0.6, "llm") for n, v in abweichend.items()
                if n in names} if not hints else {}

    monkeypatch.setattr(pipeline.semantic, "extract", fake)

    res = pipeline.process(inv, store)

    assert res.status == pipeline.DEFERRED                   # nie "fertig" ohne den Menschen
    assert res.gegencheck_abweichungen["netto"]["ki"] == 999.0
    assert res.fields["netto"].confidence <= 0.5
    assert res.fields["rechnungsnummer"].confidence > 0.5    # nur das abweichende Feld

    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod
    client = TestClient(server_mod.create_app(store, inv.parent))
    d = client.get("/api/invoice", params={"file": "a.pdf"}).json()
    assert d["ki_abweichungen"]["netto"]["ki"] == 999.0
    assert d["current"]["netto"]["confidence"] <= 0.5


def test_systemstatus_zeigt_gruendlich(tmp_path, gruendlich):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.store import Store
    from rechnung.web import server as server_mod
    samples = tmp_path / "s"
    samples.mkdir()
    client = TestClient(server_mod.create_app(Store(tmp_path / "d"), samples))
    sysd = client.get("/api/postfach-bericht").json()["system"]
    assert sysd["gruendlich"].startswith("AN")
    assert "Zeitgrenze je Rechnung 360" in sysd["gruendlich_verarbeitung"]
