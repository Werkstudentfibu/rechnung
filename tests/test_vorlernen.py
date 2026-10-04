"""Effizienz-Baustein 6: Vorab-Lernen aus Altbestand (vorlernen.py).

Synthetische Alt-Rechnungen (synth.py) zweier Lieferanten + ein unbekannter;
die KI-Volllesung ist gemockt und liefert die im PDF stehenden Werte -- das
Modell selbst ist hier nicht Gegenstand."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from rechnung import cli, semantic, vorlernen
from rechnung.identify import Supplier
from rechnung.pipeline import process
from rechnung.store import Store
from rechnung.template import FieldResult

reportlab = pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

ALPHA = dict(lieferant="Alpha Werkzeug GmbH", ustid="DE123456789",
             iban="DE89370400440532013000", domain="alpha-werkzeug.de")
BETA = dict(lieferant="Beta Buerobedarf AG", ustid="DE987654321",
            iban="DE02120300000000202051", domain="beta-buero.de")
UNBEKANNT = dict(lieferant="Gamma Unbekannt KG", ustid="DE111222333",
                 iban="DE75512108001245126199", domain="gamma.example")


def _fake_full(text: str) -> semantic.FullExtraction:
    """Stellvertreter fuer die KI-Volllesung: liest die synth-Werte aus dem
    Text (also belegt), wie ein gut funktionierendes Modell."""
    out = semantic.FullExtraction()
    m = re.search(r"(?:Rechnungsnummer:|Rechnung Nr\.)\s*(\S+)", text)
    if m:
        out.fields["rechnungsnummer"] = FieldResult("rechnungsnummer", m.group(1), m.group(1), 0.6, "llm")
    m = re.search(r"(?:Rechnungsdatum:|Datum)\s*(\d\d)\.(\d\d)\.(\d{4})", text)
    if m:
        d, mo, y = m.groups()
        out.fields["rechnungsdatum"] = FieldResult("rechnungsdatum", f"{y}-{mo}-{d}",
                                                   f"{d}.{mo}.{y}", 0.6, "llm")
    for name, label in (("netto", "Nettobetrag"), ("ust_betrag", "Umsatzsteuer"),
                        ("brutto", "Gesamtbetrag")):
        m = re.search(label + r"[^\n]*?(\d+\.\d\d) EUR", text)
        if m:
            out.fields[name] = FieldResult(name, float(m.group(1)), m.group(1), 0.6, "llm")
    return out


@pytest.fixture
def ki(monkeypatch):
    calls: list[str] = []

    def fake(text):
        calls.append(text)
        return _fake_full(text)

    monkeypatch.setattr(semantic, "extract_full", fake)
    return calls


@pytest.fixture
def store(tmp_path: Path) -> Store:
    st = Store(tmp_path / "data")
    for sid, s in (("alpha", ALPHA), ("beta", BETA)):
        st.save_supplier(Supplier(id=sid, name=s["lieferant"], ustids=[s["ustid"]],
                                  ibans=[s["iban"]]))
    return st


@pytest.fixture
def altbestand(tmp_path: Path) -> Path:
    d = tmp_path / "alt"
    d.mkdir()
    for i, netto in enumerate((1000.0, 250.0, 80.5), 1):
        make_invoice(d / f"alpha_{i}.pdf", rechnungsnr=f"AL-2025-{i:04d}",
                     datum=f"0{i}.03.2025", netto=netto, **ALPHA)
    for i, netto in enumerate((300.0, 90.0), 1):
        make_invoice(d / f"beta_{i}.pdf", rechnungsnr=f"B-77{i:03d}", datum=f"1{i}.04.2025",
                     netto=netto, layout="b", **BETA)
    make_invoice(d / "gamma_1.pdf", rechnungsnr="G-1", netto=10.0, **UNBEKANNT)
    return d


def _by_supplier(store) -> dict:
    return {g["supplier_id"] or "unbekannt": g for g in vorlernen.list_groups(store)}


def test_scan_groups_by_supplier_and_layout(store, altbestand, ki):
    rep = vorlernen.scan(altbestand, store)
    assert rep.dateien == 6 and rep.gruppen == 3 and not rep.fehler
    groups = _by_supplier(store)
    assert set(groups) == {"alpha", "beta", "unbekannt"}
    assert len(groups["alpha"]["dateien"]) == 3
    assert len(groups["beta"]["dateien"]) == 2
    assert not any(g["bestaetigt"] for g in groups.values())
    # Werte je Datei, Stammdaten aus der Bibliothek
    a1 = next(d for d in groups["alpha"]["dateien"] if d["datei"] == "alpha_1.pdf")
    assert a1["werte"]["rechnungsnummer"]["value"] == "AL-2025-0001"
    assert a1["werte"]["netto"]["value"] == 1000.0
    assert a1["werte"]["iban"]["source"] == "library"
    # Nur Vorschlag: noch keine Vorlage aktiv
    assert store.templates_for("alpha") == []


def test_scan_is_idempotent_and_keeps_corrections(store, altbestand, ki):
    vorlernen.scan(altbestand, store)
    g = _by_supplier(store)["alpha"]
    sha = g["dateien"][0]["sha256"]
    vorlernen.set_excluded(store, g["gruppe_id"], sha, True)
    vorlernen.scan(altbestand, store)
    g = _by_supplier(store)["alpha"]
    assert len(g["dateien"]) == 3
    assert next(d for d in g["dateien"] if d["sha256"] == sha)["ausgeschlossen"]


def test_confirm_activates_template_used_for_live_invoice(store, altbestand, ki, tmp_path):
    vorlernen.scan(altbestand, store)
    g = _by_supplier(store)["alpha"]
    out = vorlernen.confirm(store, g["gruppe_id"], "pruefer")
    assert {"rechnungsnummer", "rechnungsdatum", "netto", "brutto"} <= set(out["gelernt"])
    assert out["abweichungen"] == {}
    tpl = store.template(out["template_id"])
    assert tpl is not None and tpl.supplier_id == "alpha"
    # Bestaetigungspflicht bleibt: eine vorgelernte Vorlage ist nicht "gesichert"
    assert tpl.confirmations == 0 and not tpl.secured
    g = vorlernen.get_group(store, g["gruppe_id"])
    assert g["bestaetigt"] and g["bestaetigt_von"] == "pruefer"
    assert any(e["action"] == "vorlernen_bestaetigt" for e in store.audit_log())

    # Eine NEUE Live-Rechnung dieses Lieferanten wird ohne KI ueber die Vorlage gelesen
    neu = make_invoice(tmp_path / "live.pdf", rechnungsnr="AL-2026-0099", datum="02.10.2026",
                       netto=4321.0, **ALPHA)
    ki.clear()
    res = process(neu, store, use_llm=False)
    assert res.template_id == tpl.id
    assert res.fields["rechnungsnummer"].value == "AL-2026-0099"
    assert res.fields["netto"].value == 4321.0


def test_vorlernen_never_exports_or_counts_as_live(store, altbestand, ki):
    vorlernen.scan(altbestand, store)
    for g in vorlernen.list_groups(store):
        if g["supplier_id"]:
            vorlernen.confirm(store, g["gruppe_id"], "pruefer")
    root = store.root
    assert not list((root / "out").glob("*.json"))          # JobRouter-Warteschlange
    assert not (root / "shadow.jsonl").exists()               # Schattenstatistik
    assert not (root / "processed.jsonl").exists()            # Live-Verarbeitung
    assert not (root / "archive").exists() or not any((root / "archive").iterdir())
    assert all(not store.is_confirmed(d["sha256"])
               for g in vorlernen.list_groups(store) for d in g["dateien"])


def test_confirm_does_not_overwrite_existing_template(store, altbestand, ki):
    vorlernen.scan(altbestand, store)
    gid = _by_supplier(store)["alpha"]["gruppe_id"]
    first = vorlernen.confirm(store, gid, "a")
    tpl = store.template(first["template_id"])
    tpl.confirmations = 5
    store.save_template(tpl)
    again = vorlernen.confirm(store, gid, "b")
    assert again["template_id"] == first["template_id"]
    assert store.template(first["template_id"]).confirmations == 5
    assert set(again["kept"]) >= {"rechnungsnummer", "netto"}


def test_unknown_supplier_needs_assignment(store, altbestand, ki):
    vorlernen.scan(altbestand, store)
    g = _by_supplier(store)["unbekannt"]
    assert g["gruppe_id"].startswith("unbekannt__")
    with pytest.raises(ValueError, match="Lieferanten zuordnen"):
        vorlernen.confirm(store, g["gruppe_id"])
    store.save_supplier(Supplier(id="gamma", name=UNBEKANNT["lieferant"]))
    vorlernen.assign_supplier(store, g["gruppe_id"], "gamma", UNBEKANNT["lieferant"])
    out = vorlernen.confirm(store, g["gruppe_id"], "pruefer")
    assert store.template(out["template_id"]).supplier_id == "gamma"


def test_corrections_excludes_and_deviations(store, altbestand, ki):
    vorlernen.scan(altbestand, store)
    g = _by_supplier(store)["alpha"]
    gid = g["gruppe_id"]
    shas = [d["sha256"] for d in sorted(g["dateien"], key=lambda d: d["datei"])]
    assert vorlernen.deviations(g) == {}

    # Fehlender Wert in einer Datei -> markiert
    vorlernen.set_correction(store, gid, shas[0], "bestellnummer", "PO-1")
    g = vorlernen.get_group(store, gid)
    assert vorlernen.deviations(g)["bestellnummer"] == sorted(shas[1:])

    # Stabiles Feld weicht von der Mehrheit ab -> nur der Ausreisser markiert
    vorlernen.set_correction(store, gid, shas[2], "ust_satz", "7")
    for sha in shas[:2]:
        vorlernen.set_correction(store, gid, sha, "ust_satz", "19")
    g = vorlernen.get_group(store, gid)
    assert vorlernen.deviations(g)["ust_satz"] == [shas[2]]
    assert next(d for d in g["dateien"] if d["sha256"] == shas[2])["korrekturen"]["ust_satz"] == 7.0

    # Ausgeschlossene Datei zaehlt nicht mehr
    vorlernen.set_excluded(store, gid, shas[2], True)
    assert "ust_satz" not in vorlernen.deviations(vorlernen.get_group(store, gid))

    # Leerer Wert nimmt die Korrektur zurueck, Unlesbares wird abgelehnt
    vorlernen.set_correction(store, gid, shas[0], "bestellnummer", "")
    assert "bestellnummer" not in vorlernen.get_group(store, gid)["dateien"][0]["korrekturen"]
    with pytest.raises(ValueError):
        vorlernen.set_correction(store, gid, shas[0], "netto", "viel")


def test_known_template_read_without_ki(store, altbestand, ki):
    """Hat der Lieferant schon eine aktive Vorlage, liest das Vorlernen damit
    -- die KI wird fuer seine Rechnungen gar nicht erst gefragt."""
    vorlernen.scan(altbestand, store)
    vorlernen.confirm(store, _by_supplier(store)["alpha"]["gruppe_id"], "p")
    for p in store.root.glob("vorlernen/*.json"):
        p.unlink()
    ki.clear()
    vorlernen.scan(altbestand, store)
    assert not any("Alpha Werkzeug" in t for t in ki)
    a1 = next(d for d in _by_supplier(store)["alpha"]["dateien"] if d["datei"] == "alpha_1.pdf")
    assert a1["werte"]["netto"]["source"].startswith("template")


def test_scan_without_ki_counts_files_without_values(store, altbestand, monkeypatch):
    monkeypatch.setattr(semantic, "extract_full", lambda text: semantic.FullExtraction())
    rep = vorlernen.scan(altbestand, store)
    assert rep.dateien == 6 and rep.ohne_werte == 6


def test_broken_file_does_not_abort(store, altbestand, ki):
    (altbestand / "kaputt.pdf").write_bytes(b"kein pdf")
    rep = vorlernen.scan(altbestand, store)
    assert rep.dateien == 6
    assert [f["datei"] for f in rep.fehler] == ["kaputt.pdf"]


def test_cli_vorlernen(store, altbestand, ki, capsys):
    data = str(store.root)
    assert cli.main(["--data", data, "vorlernen", str(altbestand), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["gruppen"] == 3
    assert cli.main(["--data", data, "vorlernen"]) == 0
    assert "Vorschlag" in capsys.readouterr().out
    gid = _by_supplier(store)["beta"]["gruppe_id"]
    assert cli.main(["--data", data, "vorlernen", "--bestaetigen", gid]) == 2  # ohne --operator
    capsys.readouterr()
    assert cli.main(["--data", data, "vorlernen", "--bestaetigen", gid,
                     "--operator", "pruefer"]) == 0
    assert "aktiv" in capsys.readouterr().out


def test_web_vorlernen(store, altbestand, ki, tmp_path):
    pytest.importorskip("fastapi.testclient")
    from fastapi.testclient import TestClient

    from rechnung.web import server as server_mod

    samples = tmp_path / "eingang"
    samples.mkdir()
    client = TestClient(server_mod.create_app(store, samples))
    assert client.post("/api/vorlernen/scan", json={"ordner": str(tmp_path / "fehlt")}).status_code == 404
    r = client.post("/api/vorlernen/scan", json={"ordner": str(altbestand)})
    assert r.status_code == 200 and r.json()["gruppen"] == 3
    gruppen = {g["supplier_id"] or "unbekannt": g for g in client.get("/api/vorlernen").json()["gruppen"]}
    assert gruppen["alpha"]["markiert"] == {}
    gid, sha = gruppen["alpha"]["gruppe_id"], gruppen["alpha"]["dateien"][0]["sha256"]

    r = client.post(f"/api/vorlernen/{gid}/korrektur", json={"sha256": sha, "feld": "netto", "wert": "x"})
    assert r.status_code == 400
    r = client.post(f"/api/vorlernen/{gid}/ausschliessen", json={"sha256": sha})
    assert next(d for d in r.json()["dateien"] if d["sha256"] == sha)["ausgeschlossen"]

    unb = gruppen["unbekannt"]["gruppe_id"]
    assert client.post(f"/api/vorlernen/{unb}/bestaetigen").status_code == 409
    assert client.post(f"/api/vorlernen/{unb}/lieferant", json={"supplier_id": "nix"}).status_code == 404

    r = client.post(f"/api/vorlernen/{gid}/bestaetigen")
    assert r.status_code == 200 and store.template(r.json()["template_id"]) is not None
    assert client.delete(f"/api/vorlernen/{unb}").json() == {"ok": True}
    assert client.delete(f"/api/vorlernen/{unb}").status_code == 404
    # Live-Liste bleibt unberuehrt
    assert client.get("/api/scan").json()["results"] == []
