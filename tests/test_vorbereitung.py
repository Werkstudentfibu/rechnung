"""Vorbereiten im Hintergrund, Pruefen im Rutsch (Aaron 17.10.2026): Zustand je
Rechnung (auch nach Neustart), Befehl ``rechnung vorbereiten`` mit Sperre,
Neu-Auswerten nach dem Lernen, Sprung zur naechsten bereiten Rechnung,
Lernwert-Reihenfolge. Nur synthetische Daten."""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from rechnung import cli, vorbereitung as vb
from rechnung.guard import Guard
from rechnung.store import Store
from rechnung.web.server import create_app

from test_jobrouter_feedback import _secured_setup  # noqa: F401

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

HEUTE = date(2026, 10, 17)


def _zwei(tmp_path: Path):
    """Zwei Rechnungen desselben (gesicherten) Lieferanten."""
    store, samples = _secured_setup(tmp_path)
    make_invoice(samples / "b.pdf", rechnungsnr="RE-2026-00002")
    return store, samples


def _guard(store):
    return Guard(store.root, timeout=120, mode="thread")


# ---- 1. Zustand je Rechnung ------------------------------------------------------
def test_zustand_wartet_dann_bereit_und_ueberlebt_neustart(tmp_path: Path):
    store, samples = _zwei(tmp_path)
    a = samples / "a.pdf"
    assert vb.zustaende(store.root, [a])["a.pdf"]["zustand"] == vb.WARTET
    assert vb.zustaende(store.root, [a], in_arbeit={"a.pdf"})["a.pdf"]["zustand"] == vb.IN_ARBEIT
    r = vb.vorbereite_datei(_guard(store), store, a)
    assert r["ergebnis"] == "bereit"
    # "Neustart": frischer Store, leerer Hash-Zwischenspeicher -> nur die Platte zaehlt
    vb._sha_cache.clear()
    z = vb.zustaende(Store(store.root).root, [a, samples / "b.pdf"])
    assert z["a.pdf"]["zustand"] == vb.BEREIT and z["b.pdf"]["zustand"] == vb.WARTET
    m = z["a.pdf"]["marker"]
    assert m["lieferant_id"] == "muster" and m["gesichert"] is True
    assert vb.zaehler(z) == {"bereit": 1, "in_vorbereitung": 0, "wartet": 1, "fehler": 0, "gesamt": 2}
    # Datei mit neuem Inhalt ist eine neue Rechnung -> wieder "wartet"
    a.write_bytes(a.read_bytes() + b"\n%geaendert")
    assert vb.zustaende(store.root, [a])["a.pdf"]["zustand"] == vb.WARTET


def test_zustand_fehler_und_ablauf(tmp_path: Path):
    store, samples = _zwei(tmp_path)
    a = samples / "a.pdf"
    vb.fehler_markieren(store.root, a, "Zeitgrenze 600 s ueberschritten")
    z = vb.zustaende(store.root, [a])["a.pdf"]
    assert z["zustand"] == vb.FEHLER and "Zeitgrenze" in z["fehler"]
    m = vb.marker(store.root, vb.datei_sha(a))
    m["zeit"] = "2020-01-01T00:00:00"          # alt -> darf nochmal versucht werden
    vb._schreibe(store.root, m["sha256"], m)
    assert vb.zustaende(store.root, [a])["a.pdf"]["zustand"] == vb.WARTET


def test_zeitueberschreitung_wird_fehler_nicht_bereit(tmp_path: Path):
    store, samples = _zwei(tmp_path)
    g = Guard(store.root, timeout=0.0001, mode="thread")
    g.timeout = 0.0001
    r = vb.vorbereite_datei(g, store, samples / "a.pdf")
    assert r["ergebnis"] == "fehler"
    assert vb.zustaende(store.root, [samples / "a.pdf"])["a.pdf"]["zustand"] == vb.FEHLER


# ---- 2. Befehl "rechnung vorbereiten" + Sperre ------------------------------------
def _cli(store, samples, *extra):
    return cli.main(["--data", str(store.root), "--samples", str(samples), "vorbereiten",
                     "--ohne-abholen", *extra])


def test_cli_vorbereitet_alles_einmal(tmp_path: Path, capsys):
    store, samples = _zwei(tmp_path)
    assert _cli(store, samples) == 0
    assert "2 neu" in capsys.readouterr().out
    z = vb.zustaende(store.root, sorted(samples.glob("*.pdf")))
    assert {v["zustand"] for v in z.values()} == {vb.BEREIT}
    assert not any(store.is_confirmed(m["marker"]["sha256"]) for m in z.values())   # nichts bestaetigt
    assert _cli(store, samples, "--json") == 0
    b = json.loads(capsys.readouterr().out)
    assert b["bereit"] == 0 and b["schon_fertig"] == 2
    assert not list(store.root.glob("vorbereitet/*.lock"))                          # Sperren frei


def test_cli_ueberspringt_datei_die_das_fenster_gerade_rechnet(tmp_path: Path, capsys):
    store, samples = _zwei(tmp_path)
    with vb.datei_sperre(store.root, "a.pdf") as frei:
        assert frei
        with vb.datei_sperre(store.root, "a.pdf") as zweiter:
            assert not zweiter                       # nie doppelt
        assert _cli(store, samples, "--json") == 0
    b = json.loads(capsys.readouterr().out)
    assert b["belegt"] == 1 and b["bereit"] == 1
    z = vb.zustaende(store.root, sorted(samples.glob("*.pdf")))
    assert z["a.pdf"]["zustand"] == vb.WARTET and z["b.pdf"]["zustand"] == vb.BEREIT


def test_zweiter_lauf_bricht_sofort_ab(tmp_path: Path, capsys):
    store, samples = _zwei(tmp_path)
    with vb.datei_sperre(store.root, vb.LAUF_SPERRE) as frei:
        assert frei
        assert _cli(store, samples, "--json") == 0
    b = json.loads(capsys.readouterr().out)
    assert b["uebersprungen_laeuft"] is True and b["bereit"] == 0
    assert not vb.ist_bereit(store.root, samples / "a.pdf")


def test_liegengebliebene_sperre_wird_uebernommen(tmp_path: Path):
    import os
    store, _ = _zwei(tmp_path)
    with vb.datei_sperre(store.root, "x.pdf") as frei:
        assert frei
        lock = next(vb.ordner(store.root).glob("*.lock"))
        alt = lock.stat().st_mtime - 7200
        os.utime(lock, (alt, alt))
        with vb.datei_sperre(store.root, "x.pdf") as neu:
            assert neu                               # Absturz-Rest, nicht ewig blockiert


def test_cli_holt_postfach_ab_wenn_eingerichtet(tmp_path: Path, monkeypatch, capsys):
    store, samples = _zwei(tmp_path)

    class Res:
        saved = ["neu.pdf"]
        error = ""
        mails = 1
        human: list = []

    class Ab:
        def nur_abholen(self):
            return Res()

    monkeypatch.setattr("rechnung.abholung.aus_konfiguration", lambda *a, **k: Ab())
    assert cli.main(["--data", str(store.root), "--samples", str(samples), "vorbereiten"]) == 0
    assert "Postfach: 1 neue" in capsys.readouterr().out


# ---- 3. Server: Liste, Neustart, Neu-Auswerten nach dem Lernen ----------------------
@pytest.fixture
def mit_hintergrund(monkeypatch):
    monkeypatch.setenv("RECHNUNG_VORVERARBEITUNG", "1")


def test_liste_zeigt_zustand_zaehler_und_neustart_ohne_neurechnen(tmp_path, mit_hintergrund):
    store, samples = _zwei(tmp_path)
    app = create_app(store, samples)
    app.state.vorverarbeiter.warte_bis_leer(60)
    c = TestClient(app)
    d = c.get("/api/scan").json()
    assert d["vorbereitung"]["bereit"] == 2 and d["vorbereitung"]["aktiv"] is True
    assert {r["vorbereitung"]["zustand"] for r in d["results"]} == {"bereit"}
    assert d["vorbereitung"]["gesichert"]["prozent"] == 100
    assert c.get("/api/vorbereitung").json()["dateien"] == {"a.pdf": "bereit", "b.pdf": "bereit"}
    # Neustart: Merkzettel sind da -> nichts wird neu eingereiht
    app2 = create_app(store, samples)
    app2.state.vorverarbeiter.warte_bis_leer(60)
    assert app2.state.vorverarbeiter.status["fertig"] == 0
    assert TestClient(app2).get("/api/vorbereitung").json()["bereit"] == 2


def test_bereite_stehen_oben(tmp_path, monkeypatch):
    store, samples = _zwei(tmp_path)
    # nur b.pdf ist vorbereitet; a.pdf (alphabetisch zuerst) wartet
    vb.vorbereite_datei(_guard(store), store, samples / "b.pdf")
    c = TestClient(create_app(store, samples))
    d = c.get("/api/scan").json()
    assert [Path(r["file"]).name for r in d["results"]] == ["b.pdf", "a.pdf"]
    assert d["vorbereitung"]["aktiv"] is False and d["vorbereitung"]["bereit"] == 1
    d = c.get("/api/scan?bereite_zuerst=false&sortierung=eingang").json()
    assert [Path(r["file"]).name for r in d["results"]] == ["a.pdf", "b.pdf"]


def test_senden_wertet_vorbereitete_rechnungen_des_lieferanten_neu_aus(tmp_path, mit_hintergrund):
    store, samples = _zwei(tmp_path)
    app = create_app(store, samples)
    v = app.state.vorverarbeiter
    v.warte_bis_leer(60)
    c = TestClient(app)
    c.get("/api/scan")
    sha_b = vb.datei_sha(samples / "b.pdf")
    assert "neu_ausgewertet" not in vb.marker(store.root, sha_b)
    d = c.get("/api/invoice?file=a.pdf").json()
    r = c.post("/api/senden", json={"file": "a.pdf", "template_id": d["template_id"]})
    assert r.status_code == 200, r.text
    assert v.warte_bis_leer(60)
    m = vb.marker(store.root, sha_b)
    assert m["ok"] and m.get("neu_ausgewertet")
    # nichts bestaetigt, nichts gesendet, b liegt weiter im Eingang
    assert (samples / "b.pdf").exists() and not store.is_confirmed(sha_b)
    assert vb.zustaende(store.root, [samples / "b.pdf"])["b.pdf"]["zustand"] == vb.BEREIT


def test_neu_auswerten_fasst_nur_vorbereitete_an(tmp_path: Path):
    store, samples = _zwei(tmp_path)
    g = _guard(store)
    assert vb.neu_auswerten(g, store, samples / "a.pdf") is False       # nicht vorbereitet
    vb.vorbereite_datei(g, store, samples / "a.pdf")
    assert vb.neu_auswerten(g, store, samples / "a.pdf") is True
    assert vb.marker(store.root, vb.datei_sha(samples / "a.pdf"))["neu_ausgewertet"]


def test_neu_auswerten_uebernimmt_gelerntes(tmp_path: Path):
    """Lieferant ohne Vorlage -> Vorlage entsteht -> der Merkzettel der anderen
    Rechnung zeigt sie nach dem Neu-Auswerten."""
    store, samples = _zwei(tmp_path)
    tpl = store.templates_for("muster")[0]
    store.delete_template(tpl.id)
    g = _guard(store)
    vb.vorbereite_datei(g, store, samples / "b.pdf")
    sha = vb.datei_sha(samples / "b.pdf")
    assert vb.marker(store.root, sha)["vorlage"] is False
    store.save_template(tpl)                                    # "gelernt"
    assert vb.neu_auswerten(g, store, samples / "b.pdf")
    m = vb.marker(store.root, sha)
    assert m["vorlage"] is True and m["gesichert"] is True


def test_qualitaet_zeigt_lernfortschritt(tmp_path, mit_hintergrund):
    store, samples = _zwei(tmp_path)
    app = create_app(store, samples)
    app.state.vorverarbeiter.warte_bis_leer(60)
    c = TestClient(app)
    c.get("/api/scan")
    q = c.get("/api/qualitaet").json()
    assert q["gesichert_anteil"] == {"wartend": 2, "gesichert": 2, "prozent": 100}
    (z,) = [r for r in q["lernfortschritt"] if r["lieferant"] == "muster"]
    assert z["gesichert"] is True and z["wartend"] == 2


# ---- 4. Lernwert-Reihenfolge -------------------------------------------------------
def E(name, idx, lieferant=None, ohne_vorlage=False, gesichert=False, unsicher=(), faellig="",
      zustand="bereit"):
    return {"file": f"/x/{name}", "name": name, "index": idx, "zustand": zustand,
            "lieferant": lieferant, "lieferant_name": lieferant or "", "ohne_vorlage": ohne_vorlage,
            "gesichert": gesichert, "unsicher": list(unsicher), "faellig": faellig}


def _namen(liste):
    return [e["name"] for e in liste]


def test_lernwert_a_neue_lieferanten_je_einer_meiste_zuerst():
    e = [E("y1", 0, "Y", True), E("x1", 1, "X", True), E("x2", 2, "X", True),
         E("x3", 3, "X", True), E("s1", 4, "S", False, True)]
    o = vb.lernwert_sortieren(e, heute=HEUTE)
    assert _namen(o)[:2] == ["x1", "y1"]                      # X hat 3 wartende, Y nur 1
    assert o[0]["stufe"] == vb.STUFE_NEU and o[0]["grund"] == "neuer Lieferant, 2 weitere warten"
    assert o[1]["grund"] == "neuer Lieferant"
    rest = {x["name"]: x for x in o[2:]}
    assert rest["x2"]["stufe"] > vb.STUFE_UNGESICHERT and "erste Rechnung" in rest["x2"]["grund"]
    assert _namen(o)[-1] != "x1"
    assert rest["s1"]["grund"] == "nur kurz ansehen"


def test_lernwert_b_ungesicherte_vorlage_nach_a_und_vor_unsicheren():
    e = [E("u1", 0, "U", False, True, ["netto", "brutto", "iban"]),
         E("z1", 1, "Z", False, False), E("z2", 2, "Z", False, False),
         E("n1", 3, "N", True)]
    o = vb.lernwert_sortieren(e, heute=HEUTE)
    assert _namen(o)[:3] == ["n1", "z1", "u1"]
    assert o[1]["stufe"] == vb.STUFE_UNGESICHERT and o[1]["grund"] == "Vorlage noch nicht gesichert, 1 weitere warten"
    assert o[2]["stufe"] == vb.STUFE_UNSICHER and o[2]["grund"] == "3 unsichere Felder"
    assert o[3]["name"] == "z2" and o[3]["grund"].startswith("erst die erste")


def test_lernwert_c_viele_unsichere_dann_d_rest():
    e = [E("ok", 0, "S", False, True), E("eins", 1, "S", False, True, ["netto"]),
         E("drei", 2, "S", False, True, ["a", "b", "c"])]
    o = vb.lernwert_sortieren(e, heute=HEUTE)
    assert _namen(o) == ["drei", "eins", "ok"]
    assert o[0]["grund"] == "3 unsichere Felder" and o[1]["grund"] == "1 unsicheres Feld"
    assert o[2]["stufe"] == vb.STUFE_REST and o[2]["grund"] == "nur kurz ansehen"


def test_lernwert_faelligkeit_geht_vor():
    iso = lambda n: (HEUTE + timedelta(days=n)).isoformat()      # noqa: E731
    e = [E("neu", 0, "N", True), E("spaet", 1, "S", False, True, faellig=iso(5)),
         E("bald", 2, "S", False, True, faellig=iso(2)),
         E("ueberfaellig", 3, "S", False, True, faellig=iso(-4))]
    o = vb.lernwert_sortieren(e, heute=HEUTE)
    assert _namen(o)[:3] == ["ueberfaellig", "bald", "neu"]
    assert o[0]["stufe"] == vb.STUFE_FAELLIG and o[0]["grund"] == "überfällig seit 13.10."
    assert o[1]["grund"] == "fällig am 19.10."
    assert o[3]["name"] == "spaet" and o[3]["stufe"] == vb.STUFE_REST    # 5 Tage: kein Vorrang


def test_faellige_rechnung_nimmt_dem_lieferanten_nicht_den_lernplatz():
    iso = (HEUTE + timedelta(days=1)).isoformat()
    e = [E("a", 0, "N", True, faellig=iso), E("b", 1, "N", True)]
    o = vb.lernwert_sortieren(e, heute=HEUTE)
    assert _namen(o) == ["a", "b"] and o[1]["stufe"] == vb.STUFE_NEU


def test_reihenfolge_nicht_bereite_unten_und_zaehlen_mit():
    e = [E("w1", 0, "X", True, zustand="wartet"), E("w2", 1, "X", True, zustand="vorbereitung"),
         E("b1", 2, "X", True), E("s", 3, "S", False, True)]
    o = vb.reihenfolge(e, heute=HEUTE)
    assert _namen(o) == ["b1", "s", "w1", "w2"]
    assert o[0]["grund"] == "neuer Lieferant, 2 weitere warten"      # wartende zaehlen mit
    assert o[2]["grund"] == "" and o[2]["stufe"] is None
    eingang = vb.reihenfolge(e, lernwert=False, bereite_zuerst=False, heute=HEUTE)
    assert _namen(eingang) == ["w1", "w2", "b1", "s"]
    nur_eingang = vb.reihenfolge(e, lernwert=False, heute=HEUTE)
    assert _namen(nur_eingang) == ["b1", "s", "w1", "w2"] and nur_eingang[0]["grund"] == ""


def test_gruppierung_ueber_internen_entwurf_nicht_ueber_dummy():
    def r(f, sid):
        return {"file": f, "supplier_id": sid, "supplier_name": "n", "template_id": None,
                "template_secured": False, "fields": {}, "checks": []}
    z = {"zustand": "bereit", "marker": None}
    e = [vb.eintrag(r("/x/1.pdf", "entwurf-abc"), z, 0), vb.eintrag(r("/x/2.pdf", "entwurf-abc"), z, 1),
         vb.eintrag(r("/x/3.pdf", "dummy-lieferant"), z, 2), vb.eintrag(r("/x/4.pdf", "dummy-lieferant"), z, 3),
         vb.eintrag(r("/x/5.pdf", None), z, 4)]
    assert [x["lieferant"] for x in e] == ["entwurf-abc", "entwurf-abc", None, None, None]
    o = vb.lernwert_sortieren(e, heute=HEUTE)
    assert o[0]["name"] == "1.pdf" and "1 weitere" in o[0]["grund"]
    assert [x["stufe"] for x in o if x["lieferant"] is None] == [vb.STUFE_REST] * 3     # nie als Gruppe


def test_gesichert_anteil():
    e = [E("a", 0, "S", False, True), E("b", 1, "S", False, True), E("c", 2, "N", True)]
    assert vb.gesichert_anteil(e) == {"wartend": 3, "gesichert": 2, "prozent": 67}
    assert vb.gesichert_anteil([])["prozent"] is None


# ---- 5. Oberflaeche: Sprung zur naechsten BEREITEN Rechnung ---------------------------
def _js_naechste(rows, alt, alte_liste, lernwert=True, aktiv=True):
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht installiert")
    html = (Path(vb.__file__).parent / "web" / "static" / "index.html").read_text("utf-8")
    start = html.index("function naechsteBereite")
    ende = html.index("function keineBereite")
    code = html[start:ende]
    prog = f"""
    const rows = {json.dumps([{"file": f, "zustand": z} for f, z in rows])}.map(r => ({{dataset: r}}));
    const document = {{ querySelectorAll: () => rows }};
    const SORT = {{ lernwert: {str(lernwert).lower()} }};
    const VB = {{ aktiv: {str(aktiv).lower()} }};
    {code}
    const r = naechsteBereite({json.dumps(alte_liste)}, {json.dumps(alt)});
    console.log(JSON.stringify(r ? r.dataset.file : null));
    """
    out = subprocess.run([node, "-e", prog], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def test_sprung_zur_naechsten_bereiten_nicht_zur_naechsten_beliebigen():
    alt = ["a", "b", "c", "d"]
    rows = [("c", "bereit"), ("b", "wartet"), ("d", "bereit")]          # a gesendet, Liste neu geladen
    assert _js_naechste(rows, "a", alt) == "c"                           # Lernwert: oberste bereite
    assert _js_naechste([("b", "wartet"), ("c", "vorbereitung")], "a", alt) is None   # nichts bereit
    assert _js_naechste([("b", "wartet")], "a", alt, aktiv=False) == "b"   # ohne Hintergrund: wie frueher
    # nach Eingang: die naechste bereite HINTER der gesendeten
    rows = [("a2", "bereit"), ("d", "bereit")]
    assert _js_naechste(rows, "c", ["a2", "b", "c", "d"], lernwert=False) == "d"
    assert _js_naechste([("a2", "bereit")], "c", ["a2", "b", "c", "d"], lernwert=False) == "a2"
