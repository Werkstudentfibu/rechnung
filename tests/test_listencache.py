"""Zwischenspeicher der Rechnungsliste (listencache.py, Befund Aaron 05.11.2026:
nach dem Neustart dauerte es Minuten, bis die Liste erschien): Treffer ohne
Neuberechnung, Invalidierung nach Korrektur/Vorlage/Lieferant, neue Dateien,
Hintergrund-Nachrechnen und Aufrufzahl bei vielen Rechnungen."""

import json
import time
from pathlib import Path

import pytest

from rechnung import pipeline
from rechnung.store import Store
from rechnung.textnorm import norm
from rechnung.web import server as server_mod

pytest.importorskip("reportlab")
pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def aufrufe(monkeypatch):
    """Zaehlt die Aufrufe der Verarbeitung (Guard loest ``pipeline.process`` erst beim Aufruf auf)."""
    namen: list[str] = []
    orig = pipeline.process

    def zaehlen(path, *a, **k):
        namen.append(Path(path).name)
        return orig(path, *a, **k)

    monkeypatch.setattr(pipeline, "process", zaehlen)
    return namen


def _rechnungen(ordner: Path, n: int, ab: int = 0) -> list[Path]:
    from rechnung.synth import make_invoice

    ordner.mkdir(exist_ok=True)
    return [make_invoice(ordner / f"r{i:03d}.pdf", rechnungsnr=f"RE-2026-{i:05d}", netto=100.0 + i)
            for i in range(ab, ab + n)]


def _app(tmp_path: Path, n: int = 3, budget: float | None = None):
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    _rechnungen(samples, n)
    app = server_mod.create_app(store, samples)
    if budget is not None:
        app.state.liste.budget_s = budget
    return TestClient(app), app, store, samples


def _nach(client, name):
    return next(r for r in client.get("/api/scan").json()["results"] if r["file"].endswith(name))


def test_zweites_laden_rechnet_nichts_neu(tmp_path, aufrufe):
    client, *_ = _app(tmp_path, 5)
    d = client.get("/api/scan").json()
    assert len(d["results"]) == 5 and len(aufrufe) == 5
    assert d["aktualisiert_im_hintergrund"] == 0
    aufrufe.clear()
    d2 = client.get("/api/scan").json()
    assert aufrufe == []
    assert [r["file"] for r in d2["results"]] == [r["file"] for r in d["results"]]
    assert [r["status"] for r in d2["results"]] == [r["status"] for r in d["results"]]


def test_treffer_ueberlebt_neustart(tmp_path, aufrufe):
    client, app, store, samples = _app(tmp_path, 4)
    erste = client.get("/api/scan").json()["results"]
    app.state.liste.speichern()
    assert (store.root / "liste-cache.json").is_file()
    aufrufe.clear()
    client2 = TestClient(server_mod.create_app(Store(store.root), samples))
    zweite = client2.get("/api/scan").json()["results"]
    assert aufrufe == []
    assert [r["supplier_id"] for r in zweite] == [r["supplier_id"] for r in erste]


def test_neustart_mit_neuem_programmstand_rechnet_neu(tmp_path, aufrufe, monkeypatch):
    client, app, store, samples = _app(tmp_path, 2)
    client.get("/api/scan")
    app.state.liste.speichern()
    aufrufe.clear()
    monkeypatch.setattr("rechnung.listencache.programmstand", lambda: "anderes-update")
    client2 = TestClient(server_mod.create_app(Store(store.root), samples))
    client2.get("/api/scan")
    assert len(aufrufe) == 2


def test_korrektur_rechnet_genau_diese_datei_sofort_neu(tmp_path, aufrufe):
    client, app, store, samples = _app(tmp_path, 3, budget=0)     # Budget 0: Rest nur im Hintergrund
    client.get("/api/scan")
    app.state.liste.warte_bis_leer()
    aufrufe.clear()
    sha = pipeline.process(samples / "r001.pdf", store, use_llm=False).sha256
    aufrufe.clear()
    vorher = _nach(client, "r001.pdf")
    assert aufrufe == []
    store.save_correction(sha, "rechnungsnummer", "KORRIGIERT-1")
    nachher = _nach(client, "r001.pdf")
    assert aufrufe == ["r001.pdf"]                        # nur diese, und sofort (Budget 0)
    assert nachher["status"] != "wird_gelesen" and vorher["sha256"] == nachher["sha256"]
    aufrufe.clear()
    _nach(client, "r001.pdf")
    assert aufrufe == []                                  # danach wieder aus dem Speicher


def test_geoeffnete_datei_wird_nach_aenderung_sofort_neu_gerechnet(tmp_path, aufrufe):
    client, app, store, samples = _app(tmp_path, 3, budget=0)
    client.get("/api/scan")
    app.state.liste.warte_bis_leer()
    r = client.get("/api/invoice", params={"file": "r002.pdf"})
    assert r.status_code == 200
    # Lieferant aendert sich (z.B. Stammdaten gespeichert): alle anderen nur im Hintergrund
    sup = store.suppliers()[0]
    sup.name = sup.name + " AG"
    store.save_supplier(sup)
    aufrufe.clear()
    d = client.get("/api/scan").json()
    assert "r002.pdf" in aufrufe[:1]                      # die geoeffnete sofort ...
    assert d["aktualisiert_im_hintergrund"] >= 1          # ... der Rest kommt nach
    assert app.state.liste.warte_bis_leer()
    assert sorted(set(aufrufe)) == ["r000.pdf", "r001.pdf", "r002.pdf"]
    aufrufe.clear()
    assert client.get("/api/scan").json()["aktualisiert_im_hintergrund"] == 0
    assert aufrufe == []


def _wort(woerter, text):
    ziel = norm(text)
    return next(w["i"] for w in woerter if ziel in norm(w["text"]))


def test_neue_vorlage_macht_andere_rechnungen_desselben_lieferanten_veraltet(tmp_path, aufrufe):
    client, app, store, samples = _app(tmp_path, 3, budget=0)
    client.get("/api/scan")
    app.state.liste.warte_bis_leer()
    sid = next(r for r in client.get("/api/scan").json()["results"])["supplier_id"]
    w = client.get("/api/invoice", params={"file": "r000.pdf"}).json()["pages"][0]["words"]
    body = {"file": "r000.pdf", "supplier_id": sid, "supplier_name": "Muster", "variant": "standard",
            "fields": [{"name": "rechnungsnummer", "value_type": "number", "required": True,
                        "anchor_word": _wort(w, "rechnungsnummer"), "value_words": [_wort(w, "RE-2026-00000")],
                        "page": 0}],
            "line_items": None}
    assert client.post("/api/template", json=body).status_code == 200
    aufrufe.clear()
    d = client.get("/api/scan").json()
    assert d["aktualisiert_im_hintergrund"] >= 1
    assert app.state.liste.warte_bis_leer()
    assert "r001.pdf" in aufrufe and "r002.pdf" in aufrufe
    aufrufe.clear()
    d = client.get("/api/scan").json()
    assert aufrufe == [] and d["aktualisiert_im_hintergrund"] == 0
    assert all(r["template_id"] for r in d["results"])


def test_fremder_lieferant_aendert_nichts_an_anderen_eintraegen(tmp_path, aufrufe):
    from rechnung.identify import Supplier

    client, app, store, samples = _app(tmp_path, 2)
    client.get("/api/scan")
    aufrufe.clear()
    store.save_supplier(Supplier(id="ganz-anderer", name="Ganz Anderer GmbH"))
    client.get("/api/scan")
    assert aufrufe == []


def test_neue_datei_erscheint_sofort_bei_kleinem_zugang(tmp_path, aufrufe):
    client, app, store, samples = _app(tmp_path, 2)
    client.get("/api/scan")
    aufrufe.clear()
    _rechnungen(samples, 1, ab=50)
    d = client.get("/api/scan").json()
    assert [r["file"].rsplit("/", 1)[-1] for r in d["results"]].count("r050.pdf") == 1
    assert aufrufe == ["r050.pdf"]
    assert next(r for r in d["results"] if r["file"].endswith("r050.pdf"))["status"] != "wird_gelesen"


def test_neue_datei_ohne_budget_wird_gelesen_und_kommt_nach(tmp_path, aufrufe):
    client, app, store, samples = _app(tmp_path, 2, budget=0)
    client.get("/api/scan")
    app.state.liste.warte_bis_leer()
    aufrufe.clear()
    _rechnungen(samples, 1, ab=60)
    d = client.get("/api/scan").json()
    neu = next(r for r in d["results"] if r["file"].endswith("r060.pdf"))
    assert neu["status"] == "wird_gelesen" and d["status_labels"]["wird_gelesen"]
    assert d["aktualisiert_im_hintergrund"] >= 1
    assert app.state.liste.warte_bis_leer()
    neu = _nach(client, "r060.pdf")
    assert neu["status"] != "wird_gelesen" and neu["fields"]
    assert aufrufe == ["r060.pdf"]


def test_viele_rechnungen_liste_sofort_rest_im_hintergrund(tmp_path, aufrufe):
    client, app, store, samples = _app(tmp_path, 40, budget=0)
    t0 = time.monotonic()
    d = client.get("/api/scan").json()
    erste = time.monotonic() - t0
    assert len(d["results"]) == 40
    assert len(aufrufe) <= 1                          # nichts Blockierendes im Aufruf selbst
    assert d["aktualisiert_im_hintergrund"] >= 39
    assert erste < 5
    assert app.state.liste.warte_bis_leer(120)
    assert sorted(aufrufe) == [f"r{i:03d}.pdf" for i in range(40)]      # jede genau einmal
    aufrufe.clear()
    t0 = time.monotonic()
    d = client.get("/api/scan").json()
    assert aufrufe == [] and d["aktualisiert_im_hintergrund"] == 0
    assert all(r["status"] != "wird_gelesen" for r in d["results"])
    assert time.monotonic() - t0 < 2


def test_hintergrund_rechnet_nacheinander_nie_parallel(tmp_path, monkeypatch):
    import threading

    client, app, store, samples = _app(tmp_path, 8, budget=0)
    liste = app.state.liste
    aktiv, hoechst, lock = [0], [0], threading.Lock()
    orig = liste._rechne

    def beobachten(p):
        with lock:
            aktiv[0] += 1
            hoechst[0] = max(hoechst[0], aktiv[0])
        try:
            return orig(p)
        finally:
            with lock:
                aktiv[0] -= 1

    liste._rechne = beobachten
    client.get("/api/scan")
    client.get("/api/scan")
    assert liste.warte_bis_leer(60)
    assert hoechst[0] == 1


def test_hintergrund_wartet_auf_den_menschen(tmp_path):
    client, app, store, samples = _app(tmp_path, 3, budget=0)
    liste = app.state.liste
    frei = [False]
    liste._pause = lambda: not frei[0]
    client.get("/api/scan")
    time.sleep(0.6)
    assert liste.offen() >= 1 and not any(v["stand"] for v in liste._e.values())
    frei[0] = True
    assert liste.warte_bis_leer(30)
    assert len(liste._e) == 3


def test_bearbeitete_datei_verschwindet_aus_dem_speicher(tmp_path):
    client, app, store, samples = _app(tmp_path, 3)
    client.get("/api/scan")
    assert len(app.state.liste._e) == 3
    (samples / "r001.pdf").unlink()
    d = client.get("/api/scan").json()
    assert len(d["results"]) == 2 and len(app.state.liste._e) == 2


def test_gleiche_datei_unter_neuem_namen_nutzt_den_eintrag_nicht_falsch(tmp_path, aufrufe):
    client, app, store, samples = _app(tmp_path, 2)
    client.get("/api/scan")
    (samples / "r000.pdf").rename(samples / "umbenannt.pdf")
    d = client.get("/api/scan").json()
    assert {Path(r["file"]).name for r in d["results"]} == {"r001.pdf", "umbenannt.pdf"}


def test_kaputte_cache_datei_wird_ignoriert(tmp_path, aufrufe):
    client, app, store, samples = _app(tmp_path, 2)
    client.get("/api/scan")
    app.state.liste.speichern()
    (store.root / "liste-cache.json").write_text("{kaputt", "utf-8")
    aufrufe.clear()
    d = TestClient(server_mod.create_app(Store(store.root), samples)).get("/api/scan").json()
    assert len(d["results"]) == 2 and len(aufrufe) == 2


def test_cache_datei_ist_gueltiges_json_ohne_abgebrochene(tmp_path):
    client, app, store, samples = _app(tmp_path, 2)
    client.get("/api/scan")
    app.state.liste.speichern()
    d = json.loads((store.root / "liste-cache.json").read_text("utf-8"))
    assert d["format"] == 1 and len(d["eintraege"]) == 2
