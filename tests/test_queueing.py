"""Effizienz-Baustein 5: Warteschlange nach Aufwand (queueing.py) --
schnelle Faelle (E-Rechnung, Textebene + bekannte Vorlage) zuerst, langsame
(OCR/KI) danach, Modell-Freigabe nach dem KI-Stapel."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from rechnung import queueing, semantic
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import scan_folder
from rechnung.store import Store

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402
from rechnung import pdf as pdfmod  # noqa: E402


def _with_secured_template(store: Store, path: Path) -> None:
    from rechnung import learn

    doc = pdfmod.load(path)
    store.save_supplier(supplier_from_doc(doc, "muster", "Muster Handels GmbH"))
    tpl, _ = learn.learn_template(doc, "muster", {
        "rechnungsnummer": "RE-2026-00001", "rechnungsdatum": "2026-02-14",
        "netto": 1000.0, "ust_betrag": 190.0, "ust_satz": 19.0, "brutto": 1190.0})
    tpl.confirmations = 3
    store.save_template(tpl)


# ---- _classify ----------------------------------------------------------
def test_classify_image_is_slow(tmp_path: Path):
    store = Store(tmp_path / "data")
    img = tmp_path / "scan.jpg"
    img.write_bytes(b"fake")
    assert queueing._classify(img, store) == queueing.LANGSAM


def test_classify_unknown_supplier_is_slow(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf")
    assert queueing._classify(inv, store) == queueing.LANGSAM


def test_classify_known_supplier_with_secured_template_is_fast(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf", rechnungsnr="RE-2026-00001")
    _with_secured_template(store, inv)
    assert queueing._classify(inv, store) == queueing.SCHNELL


def test_classify_known_supplier_without_template_is_slow(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf")
    store.save_supplier(supplier_from_doc(pdfmod.load(inv), "muster", "Muster"))
    assert queueing._classify(inv, store) == queueing.LANGSAM


def test_classify_stale_template_is_slow(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv = make_invoice(tmp_path / "a.pdf", rechnungsnr="RE-2026-00001")
    _with_secured_template(store, inv)
    tpl = store.template("muster__standard")
    tpl.stale = True
    store.save_template(tpl)
    assert queueing._classify(inv, store) == queueing.LANGSAM


def test_classify_broken_pdf_is_slow(tmp_path: Path):
    store = Store(tmp_path / "data")
    p = tmp_path / "kaputt.pdf"
    p.write_bytes(b"not a pdf")
    assert queueing._classify(p, store) == queueing.LANGSAM


# ---- _classify_safe: eigene kurze Zeitgrenze -----------------------------
def test_classify_safe_times_out_to_slow(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "data")
    p = tmp_path / "a.pdf"
    p.write_bytes(b"irrelevant")

    def hangs(path, s):
        time.sleep(1.0)
        return queueing.SCHNELL

    monkeypatch.setattr(queueing, "_classify", hangs)
    assert queueing._classify_safe(p, store, timeout=0.05) == queueing.LANGSAM


def test_classify_safe_returns_quick_result(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "data")
    p = tmp_path / "a.pdf"
    p.write_bytes(b"irrelevant")
    monkeypatch.setattr(queueing, "_classify", lambda path, s: queueing.SCHNELL)
    assert queueing._classify_safe(p, store, timeout=1.0) == queueing.SCHNELL


# ---- split_by_effort -------------------------------------------------------
def test_split_by_effort_separates_and_orders_by_age(tmp_path: Path):
    store = Store(tmp_path / "data")
    inv_a = make_invoice(tmp_path / "a.pdf", rechnungsnr="RE-2026-00001")
    _with_secured_template(store, inv_a)
    import os

    # Andere USt-IdNr/IBAN -- sonst waeren slow1/slow2 "derselbe" Lieferant
    # wie inv_a (make_invoice() verwendet sonst immer dieselben Stammdaten).
    slow1 = make_invoice(tmp_path / "slow1.pdf", rechnungsnr="RE-SLOW-1",
                         lieferant="Fremdfirma Eins AG", strasse="Fremdweg 1", ort="99999 Nirgendwo",
                         domain="fremdfirma-eins.de", ustid="DE999999991", iban="DE02120300000000202051")
    time.sleep(0.01)
    slow2 = make_invoice(tmp_path / "slow2.pdf", rechnungsnr="RE-SLOW-2",
                         lieferant="Fremdfirma Zwei AG", strasse="Fremdweg 2", ort="99998 Nirgendwo",
                         domain="fremdfirma-zwei.de", ustid="DE999999992", iban="DE02500105170137075030")
    time.sleep(0.01)
    fast2 = make_invoice(tmp_path / "fast2.pdf", rechnungsnr="RE-2026-00002")
    # fast2 ist aelter gemacht als inv_a, damit die Alters-Reihenfolge
    # innerhalb der schnellen Schlange geprueft werden kann
    old_time = time.time() - 100
    os.utime(fast2, (old_time, old_time))
    _with_secured_template(store, fast2)

    fast, slow = queueing.split_by_effort([inv_a, slow1, slow2, fast2], store)
    assert [p.name for p in fast] == ["fast2.pdf", "a.pdf"]
    assert [p.name for p in slow] == ["slow1.pdf", "slow2.pdf"]


# ---- scan_folder: schnell vor langsam, Modell-Freigabe --------------------
def test_scan_folder_processes_fast_before_slow(tmp_path: Path):
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    fast_inv = make_invoice(samples / "fast.pdf", rechnungsnr="RE-2026-00001")
    _with_secured_template(store, fast_inv)
    # Andere USt-IdNr/IBAN -- kein Lieferant aus der Bibliothek passt.
    make_invoice(samples / "slow.pdf", rechnungsnr="RE-SLOW",
                lieferant="Fremdfirma Eins AG", strasse="Fremdweg 1", ort="99999 Nirgendwo",
                domain="fremdfirma-eins.de", ustid="DE999999991", iban="DE02120300000000202051")

    order: list[str] = []

    class _OrderGuard:
        def process(self, path, use_llm=True):
            from rechnung.pipeline import process as _process
            order.append(Path(path).name)
            return _process(path, store, use_llm=use_llm)

        def close(self):
            pass

    results = scan_folder(samples, store, guard=_OrderGuard())
    assert order == ["fast.pdf", "slow.pdf"]
    assert len(results) == 2


def test_scan_folder_releases_model_only_when_slow_batch_ran(tmp_path: Path, monkeypatch):
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    fast_inv = make_invoice(samples / "fast.pdf", rechnungsnr="RE-2026-00001")
    _with_secured_template(store, fast_inv)

    calls = []
    monkeypatch.setattr(semantic, "release_model", lambda: calls.append("released"))

    class _StubGuard:
        def process(self, path, use_llm=True):
            from rechnung.pipeline import process as _process
            return _process(path, store, use_llm=use_llm)

        def close(self):
            pass

    scan_folder(samples, store, guard=_StubGuard())
    assert calls == []     # nur schnelle Rechnungen -- kein KI-Stapel, keine Freigabe

    # Andere USt-IdNr/IBAN -- sonst waere das "derselbe" (bekannte) Lieferant.
    make_invoice(samples / "slow.pdf", rechnungsnr="RE-SLOW",
                lieferant="Fremdfirma Eins AG", strasse="Fremdweg 1", ort="99999 Nirgendwo",
                domain="fremdfirma-eins.de", ustid="DE999999991", iban="DE02120300000000202051")
    scan_folder(samples, store, guard=_StubGuard())
    assert calls == ["released"]


def test_release_model_noop_when_llm_disabled(monkeypatch):
    from rechnung.config import LlmConfig

    monkeypatch.setattr(semantic, "_llm_cfg", lambda: LlmConfig(enabled=False))
    semantic.release_model()   # darf nicht werfen, auch ohne Netzwerk


def test_release_model_sends_keep_alive_zero(monkeypatch):
    from rechnung.config import LlmConfig

    seen = {}

    def fake_urlopen(req, timeout=None):
        import json
        seen["body"] = json.loads(req.data.decode("utf-8"))

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        return _Resp()

    monkeypatch.setattr(semantic, "_llm_cfg",
                        lambda: LlmConfig(enabled=True, backend="ollama", model="m"))
    monkeypatch.setattr(semantic.urllib.request, "urlopen", fake_urlopen)
    semantic.release_model()
    assert seen["body"] == {"model": "m", "keep_alive": 0}


def test_release_model_ignores_connection_errors(monkeypatch):
    from rechnung.config import LlmConfig

    def boom(req, timeout=None):
        raise OSError("nicht erreichbar")

    monkeypatch.setattr(semantic, "_llm_cfg",
                        lambda: LlmConfig(enabled=True, backend="ollama", model="m"))
    monkeypatch.setattr(semantic.urllib.request, "urlopen", boom)
    semantic.release_model()   # darf nicht werfen
