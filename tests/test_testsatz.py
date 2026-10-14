"""Testsatz aus bestaetigten Rechnungen (rechnung/testsatz.py, Aaron 10.10.2026)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnung import pdf as pdfmod
from rechnung import testsatz
from rechnung.cli import main as cli_main
from rechnung.guard import Guard
from rechnung.identify import supplier_from_doc
from rechnung.store import Store

from conftest import SUPPLIERS, make_invoice

pytest.importorskip("fastapi.testclient")
from fastapi.testclient import TestClient  # noqa: E402

from rechnung.web import server as server_mod  # noqa: E402

WERTE = [
    ("AL-2026-0001", "05.01.2026", "1.000,00", "190,00", "1.190,00"),
    ("AL-2026-0002", "11.02.2026", "2.500,00", "475,00", "2.975,00"),
]


def _bestaetigen(tmp_path: Path, werte=WERTE) -> tuple[Store, TestClient, list[Path]]:
    """Rechnungen per UI eintippen + bestaetigen -> Vorlage gelernt,
    Soll-Werte im Testsatz."""
    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    files = []
    for no, datum, netto, ust, brutto in werte:
        files.append(make_invoice(samples / f"{no}.pdf", invoice_no=no, datum=datum, netto=netto,
                                  ust=ust, brutto=brutto, **SUPPLIERS["alpha"]))
    store.save_supplier(supplier_from_doc(pdfmod.load(files[0]), "alpha", "Alpha Werkzeug GmbH"))
    client = TestClient(server_mod.create_app(store, samples))
    for f, (no, datum, netto, ust, brutto) in zip(files, werte):
        inv = client.get("/api/invoice", params={"file": f.name}).json()
        if inv.get("template_id") is None:
            for k, v in {"rechnungsnummer": no, "rechnungsdatum": datum, "netto": netto,
                         "ust_betrag": ust, "brutto": brutto}.items():
                assert client.post("/api/correction",
                                   json={"file": f.name, "field": k, "value": v}).status_code == 200
            r = client.post("/api/confirm", json={"file": f.name})
        else:
            r = client.post("/api/confirm", json={"file": f.name,
                                                  "template_id": inv["template_id"]})
        assert r.status_code == 200, r.text
    return store, client, files


def _guard(store, variante="A"):
    return Guard(store.root, timeout=0, target=testsatz.TARGET, extra={"variante": variante},
                 protokoll=False)


def test_bestaetigen_legt_soll_werte_und_original_ab(tmp_path):
    store, _c, files = _bestaetigen(tmp_path)
    sha = pdfmod.load(files[0]).sha256
    rec = json.loads((store.root / "testsatz" / f"{sha}.json").read_text("utf-8"))
    assert rec["felder"]["rechnungsnummer"] == "AL-2026-0001"
    assert rec["supplier_id"] == "alpha"
    # Stammdaten aus der Bibliothek sind kein gelesener Wert -> nicht im Soll
    assert "iban" not in rec["felder"] and "ustid" not in rec["felder"]
    assert (store.root / "archive" / rec["original"]).exists()


def test_trockenlauf_misst_und_speichert_nichts(tmp_path):
    store, _c, _f = _bestaetigen(tmp_path)
    vorher = {p.name: p.stat().st_size for p in store.root.glob("*.jsonl")}
    b = testsatz.laufen(store, guard=_guard(store))
    assert b["anzahl"] == 2
    assert b["felder"]["rechnungsnummer"]["richtig"] == 2
    assert b["felder"]["brutto"]["richtig"] == 2
    assert b["felder"]["lieferant"]["richtig"] == 2
    assert b["felder"]["ust_aufstellung"]["richtig"] == 1
    # die erste Rechnung wurde mit USt bestaetigt, die gelernte Vorlage liest
    # den USt-Betrag (Zeile unter dem Satz) aber nicht -> ehrlich "fehlt"
    assert b["felder"]["ust_betrag"] == {"richtig": 0, "falsch": 0, "fehlt": 1, "quote": 0.0}
    assert 0 < b["trefferquote"] < 100
    assert b["zeit_s"]["anzahl"] == 2
    # nichts geschrieben: kein Ereignis, kein Audit, kein Journal, keine Bestaetigung
    assert {p.name: p.stat().st_size for p in store.root.glob("*.jsonl")} == vorher
    assert not (store.root / "berichte").exists()


def test_korrekturen_werden_im_trockenlauf_nicht_eingemischt(tmp_path):
    """Die erste Rechnung wurde komplett eingetippt (Korrekturen) -- ohne
    die Korrekturen liefert die Maschine nur, was die Vorlage liest. Wird der
    Sollwert verfaelscht, muss "falsch" herauskommen (nicht die Korrektur)."""
    store, _c, files = _bestaetigen(tmp_path)
    sha = pdfmod.load(files[1]).sha256
    p = store.root / "testsatz" / f"{sha}.json"
    rec = json.loads(p.read_text("utf-8"))
    rec["felder"]["brutto"] = 9999.0
    p.write_text(json.dumps(rec), "utf-8")
    b = testsatz.laufen(store, guard=_guard(store))
    fall = next(f for f in b["faelle"] if f["sha256"] == sha)
    assert fall["felder"]["brutto"]["ergebnis"] == testsatz.FALSCH
    assert b["felder"]["brutto"]["falsch"] == 1
    assert b["trefferquote"] < 100


def test_vergleich_mit_letztem_lauf(tmp_path):
    store, _c, files = _bestaetigen(tmp_path)
    erst = testsatz.pruefen(store, guard=_guard(store))
    assert Path(erst["datei"]).name.startswith("testsatz_")
    assert erst["vergleich"]["vorher"] is None
    sha = pdfmod.load(files[0]).sha256
    p = store.root / "testsatz" / f"{sha}.json"
    rec = json.loads(p.read_text("utf-8"))
    rec["felder"]["rechnungsnummer"] = "XX-1"
    p.write_text(json.dumps(rec), "utf-8")
    zweit = testsatz.pruefen(store, guard=_guard(store))
    v = zweit["vergleich"]
    assert v["vorher"] == erst["datei"]
    assert any(s.startswith("rechnungsnummer: 100.0 % -> 50.0 %") for s in v["schlechter"])
    assert any("rechnungsnummer" in s and "AL-2026-0001" in s for s in v["schlechter"])
    assert "Schlechter geworden bei" in testsatz.text(zweit)
    # anderer Filter wird nicht mit diesem Lauf verglichen
    assert testsatz.letzter_bericht(store.root, {"lieferant": "beta", "variante": "A",
                                                 "nur_ohne_ki": False}) is None


def test_filter_lieferant_und_ohne_ki(tmp_path):
    store, _c, _f = _bestaetigen(tmp_path)
    assert testsatz.laufen(store, lieferant="beta", guard=_guard(store))["anzahl"] == 0
    assert testsatz.laufen(store, lieferant="Alpha", guard=_guard(store))["anzahl"] == 2
    # erste Rechnung eingetippt, zweite ueber die daraus gelernte Vorlage --
    # inzwischen gibt es die Vorlage, beide kommen also ohne KI aus
    assert len(testsatz.faelle(store, nur_ohne_ki=True)[0]) == 2
    # Vorlage veraltet (Layoutwechsel) -> die eingetippte braucht die KI
    for t in store.all_templates():
        t.stale = True
        store.save_template(t)
    ohne_ki, _ = testsatz.faelle(store, nur_ohne_ki=True)
    assert [f["datei"] for f in ohne_ki] == ["AL-2026-0002.pdf"]


def test_unbekannte_variante_ist_fehler(tmp_path):
    store = Store(tmp_path / "data")
    with pytest.raises(ValueError):
        testsatz.laufen(store, variante="D")
    # C ist vorgesehen (Ueberblick + Bloecke), aber noch nicht gebaut --
    # B (Positionen als eigener Block, Baustein 3) ist es seit Aaron
    # 12.10.2026.
    with pytest.raises(NotImplementedError):
        testsatz.laufen(store, variante="C")
    assert cli_main(["--data", str(store.root), "testsatz", "--variante", "X"]) == 2
    from rechnung.pipeline import process
    with pytest.raises(ValueError):
        process(tmp_path / "egal.pdf", store, variante="Z")


def test_hergeleitet_aus_journal_und_archiv(tmp_path):
    """Vor dem 10.10.2026 bestaetigt: keine Soll-Datei, aber Journal +
    Bestaetigung + Original im Archiv -> Testfall mit den Kopf-Feldern."""
    store = Store(tmp_path / "data")
    src = make_invoice(tmp_path / "x.pdf", invoice_no="AL-1", datum="05.01.2026",
                       netto="100,00", ust="19,00", brutto="119,00", **SUPPLIERS["alpha"])
    sha = pdfmod.load(src).sha256
    store.archive_pdf(src, sha)
    store.mark_confirmed(sha)
    store.mark_processed({"file": "x.pdf", "sha256": sha, "supplier_id": "alpha",
                          "rechnungsnummer": "AL-1", "rechnungsdatum": "2026-01-05",
                          "netto": 100.0, "ust_betrag": 19.0, "brutto": 119.0})
    # unbestaetigt bzw. autonom: kein Goldstandard
    store.mark_processed({"file": "y.pdf", "sha256": "b" * 64, "rechnungsnummer": "1"})
    store.mark_processed({"file": "z.pdf", "sha256": "c" * 64, "rechnungsnummer": "2",
                          "freigabe": "autonom"})
    store.mark_confirmed("c" * 64)
    ok, skip = testsatz.faelle(store)
    assert [f["sha256"] for f in ok] == [sha]
    assert ok[0]["hergeleitet"] and ok[0]["felder"]["brutto"] == 119.0
    assert skip == []
    # ohne Original: uebersprungen mit Grund
    (store.root / "archive" / f"{sha}.pdf").unlink()
    ok, skip = testsatz.faelle(store)
    assert not ok and skip[0]["grund"] == "Original nicht im Archiv"


@pytest.mark.parametrize("feld,soll,ist,erwartet", [
    ("rechnungsnummer", "AL 2026 0001", "AL-2026-0001", False),
    ("rechnungsnummer", "AL 2026-0001", "AL2026-0001", True),
    ("rechnungsdatum", "05.01.2026", "2026-01-05", True),
    ("rechnungsdatum", "05.01.2026", "2026-05-01", False),
    ("brutto", 1190.0, "1.190,01", True),
    ("brutto", 1190.0, 1190.02, False),
    ("iban", "DE89 3704 0044 0532 0130 00", "DE89370400440532013000", True),
    ("waehrung", "eur", "EUR", True),
])
def test_normalisierter_vergleich(feld, soll, ist, erwartet):
    assert testsatz.gleich(feld, soll, ist) is erwartet


def test_positionen_unabhaengig_von_reihenfolge():
    soll = [{"menge": 2, "gesamt_netto": 10.0}, {"menge": 1, "gesamt_netto": 5.0}]
    ist = [{"menge": 1, "gesamt_netto": "5,00"}, {"menge": 3, "gesamt_netto": 10.0},
           {"gesamt_netto": 1.0}]
    assert testsatz._positionen_vergleich(soll, ist) == {"soll": 2, "gefunden": 3, "korrekt": 1}


def test_trockenlauf_im_arbeitsprozess(tmp_path):
    """Standardweg mit eigenem Arbeitsprozess (Zeitgrenze) und Variante als
    durchgereichtem Parameter."""
    store, _c, _f = _bestaetigen(tmp_path)
    b = testsatz.laufen(store)
    assert b["anzahl"] == 2 and b["abgebrochen"] == 0
    assert b["felder"]["rechnungsnummer"]["richtig"] == 2


def test_cli_und_api(tmp_path, capsys):
    store, client, _f = _bestaetigen(tmp_path)
    assert cli_main(["--data", str(store.root), "testsatz", "--ohne-ki"]) == 0
    out = capsys.readouterr().out
    assert "Testsatz (Variante A, nur ohne KI): 2 Rechnung(en)" in out
    st = client.get("/api/testsatz").json()
    assert st["faelle"] == 2 and st["letzter"]["filter"]["nur_ohne_ki"] is True
    assert client.post("/api/testsatz").json()["gestartet"]
    import time
    for _ in range(300):
        st = client.get("/api/testsatz").json()
        if not st["lauf"]["laeuft"]:
            break
        time.sleep(0.1)
    assert not st["lauf"]["fehler"], st["lauf"]["fehler"]
    assert st["letzter"]["anzahl"] == 2
