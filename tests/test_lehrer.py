"""Anlernphase mit Claude als Lehrer (lehrer.py) -- Claude-API gemockt."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rechnung import human, lehrer
from rechnung import pdf as pdfmod
from rechnung.identify import supplier_from_doc
from rechnung.pipeline import finalize, process
from rechnung.store import Store

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402

RICHTIG = {"rechnungsnummer": "RE-2026-00042", "rechnungsdatum": "2026-02-14",
           "netto": "1000.00", "ust_betrag": "190.00", "ust_satz": "19", "brutto": "1190.00",
           "faelligkeit": "2026-02-28", "waehrung": "EUR"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RECHNUNG_CLAUDE_LEHRER", "1")
    monkeypatch.setenv("RECHNUNG_CLAUDE_STICHPROBE", "0")


def _store(tmp_path: Path, bestaetigt: bool = True):
    store = Store(tmp_path / "data")
    ordner = tmp_path / "in"
    ordner.mkdir()
    src = make_invoice(ordner / "a.pdf")
    sup = supplier_from_doc(pdfmod.load(src), "muster", "Muster Handels GmbH")
    sup.bestaetigt = bestaetigt
    store.save_supplier(sup)
    return store, ordner, src


def _claude(werte: dict, sicher: bool = True, calls: list | None = None):
    def post(body):
        if calls is not None:
            calls.append(body)
        felder = {k: {"wert": v, "sicher": sicher, "beleg": str(v)} for k, v in werte.items()}
        return {"content": [{"type": "text", "text": json.dumps({"felder": felder})}]}
    return post


def test_claude_bestaetigt_und_programm_lernt_vorlage(tmp_path):
    store, ordner, src = _store(tmp_path)
    calls = []
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG, calls=calls))
    assert r["ergebnis"] == lehrer.ERG_BESTAETIGT, r
    assert calls and calls[0]["messages"][0]["content"][0]["type"] == "document"
    assert r["gelernt"]["saved"]
    assert store.templates_for("muster")
    # nur Claude hat bestaetigt -> darf nicht in die Buecher / an JobRouter
    res = process(src, store)
    assert lehrer.angelernt(store, res.sha256) and not store.is_confirmed(res.sha256)
    out = finalize(res, store, to_jobrouter=True)
    assert out == {"finalized": False, "error": "not_confirmed", "status": res.status}
    assert not store.seen_sha(res.sha256)
    # Schattenbetrieb zaehlt wie bei einem Menschen, Operator claude
    assert store.shadow_log()[-1]["operator"] == "claude"


def test_naechste_rechnung_nutzt_gelernte_vorlage(tmp_path):
    store, ordner, src = _store(tmp_path)
    lehrer.anlernen_datei(store, src, post=_claude(RICHTIG))
    nxt = make_invoice(ordner / "b.pdf", rechnungsnr="RE-2026-00050", netto=420.0)
    res = process(nxt, store, use_llm=False)
    assert res.template_id and res.fields["netto"].value == 420.0


def test_claude_korrigiert_falschen_programmwert(tmp_path):
    store, ordner, src = _store(tmp_path)
    sha = pdfmod.load(src).sha256
    store.save_correction(sha, "netto", "999,00", "jemand")  # Programm "liegt falsch"
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG))
    assert r["ergebnis"] == lehrer.ERG_BESTAETIGT, r
    assert "netto" in r["korrigiert"]
    assert store.corrections(sha)["netto"] == "1000.00"
    assert any(a["action"] == "correct" and a["operator"] == "claude" for a in store.audit_log())


def test_erfundener_wert_geht_zum_menschen(tmp_path):
    store, ordner, src = _store(tmp_path)
    falsch = dict(RICHTIG, rechnungsnummer="RE-9999-ERFUNDEN")
    r = lehrer.anlernen_datei(store, src, post=_claude(falsch))
    assert r["ergebnis"] == lehrer.ERG_MENSCH
    assert "nicht im PDF gefunden" in r["gruende"][0]
    assert not lehrer.angelernt(store, pdfmod.load(src).sha256)
    assert human.offen(store)


def test_unsicherer_claude_geht_zum_menschen(tmp_path):
    store, ordner, src = _store(tmp_path)
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG, sicher=False))
    assert r["ergebnis"] == lehrer.ERG_MENSCH
    assert "unsicher" in r["gruende"][0]


def test_lieferanten_entwurf_wartet_und_wird_spaeter_aufgegriffen(tmp_path):
    store, ordner, src = _store(tmp_path, bestaetigt=False)
    calls = []
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG, calls=calls))
    assert r["ergebnis"] == lehrer.ERG_UEBERSPRUNGEN and r["wartet_auf_lieferant"]
    assert not calls  # Stammdaten entscheidet ein Mensch, Claude wird nicht gefragt
    # Mensch bestaetigt den Lieferanten -> naechste Runde nimmt die Rechnung
    sup = store.supplier("muster")
    sup.bestaetigt = True
    store.save_supplier(sup)
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG, calls=calls))
    assert r["ergebnis"] == lehrer.ERG_BESTAETIGT and calls


def test_alte_menscheintraege_wegen_lieferant_blockieren_nicht(tmp_path):
    """Eintraege aus der ersten Version (Lieferanten-Entwurf als 'mensch'
    protokolliert) duerfen die Rechnung nicht dauerhaft sperren."""
    store, ordner, src = _store(tmp_path)
    sha = pdfmod.load(src).sha256
    lehrer._protokoll(store, {"sha256": sha, "ergebnis": lehrer.ERG_MENSCH, "gruende": [
        "Lieferant ist nur ein Entwurf -- Stammdaten prueft ein Mensch"]})
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG))
    assert r["ergebnis"] == lehrer.ERG_BESTAETIGT


def test_wartende_zaehlen_nicht_zum_rundenlimit(tmp_path):
    store, ordner, src = _store(tmp_path, bestaetigt=False)
    make_invoice(ordner / "b.pdf", rechnungsnr="RE-2026-00050", netto=420.0)
    r = lehrer.anlernen(store, ordner, limit=1, post=_claude(RICHTIG))
    assert [x["ergebnis"] for x in r] == [lehrer.ERG_UEBERSPRUNGEN] * 2


def test_stichprobe_und_bilanz(tmp_path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_CLAUDE_STICHPROBE", "100")
    store, ordner, src = _store(tmp_path)
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG))
    assert r["ergebnis"] == lehrer.ERG_MENSCH and r.get("stichprobe")
    sha = r["sha256"]
    # Mensch kontrolliert: korrigiert brutto (Claude lag falsch) und bestaetigt
    store.save_correction(sha, "brutto", "1.190,01", "mensch")
    store.mark_confirmed(sha, "mensch")
    b = lehrer.bilanz(store)
    assert b["lehrer"]["stichprobe_kontrolliert"] == 1
    assert b["lehrer"]["claude_lag_falsch"] == 1 and b["lehrer"]["felder"] == {"brutto": 1}
    # vom Menschen bestaetigt -> darf jetzt regulaer weiter
    assert store.is_confirmed(sha)


def test_antwort_wird_gemerkt(tmp_path):
    store, ordner, src = _store(tmp_path)
    calls = []
    post = _claude(RICHTIG, calls=calls)
    lehrer.anlernen(store, ordner, post=post)
    lehrer.anlernen(store, ordner, post=post)  # zweiter Lauf: schon bestaetigt
    assert len(calls) == 1


def test_api_fehler_ohne_seiteneffekt(tmp_path):
    store, ordner, src = _store(tmp_path)

    def kaputt(body):
        raise OSError("keine Verbindung")
    r = lehrer.anlernen_datei(store, src, post=kaputt)
    assert r["ergebnis"] == lehrer.ERG_FEHLER
    assert not lehrer.angelernt(store, r["sha256"]) and not human.offen(store)


def test_aus_ist_standard(monkeypatch):
    monkeypatch.delenv("RECHNUNG_CLAUDE_LEHRER")
    assert not lehrer.an()


def test_kommandozeile_bilanz(tmp_path, capsys):
    store, ordner, src = _store(tmp_path)
    lehrer.anlernen_datei(store, src, post=_claude(RICHTIG))
    assert lehrer.main(["--data", str(store.root), "--bilanz"]) == 0
    assert "von Claude bestaetigt: 1" in capsys.readouterr().out


def test_dauerbetrieb_menschfall_blockiert_nicht_das_limit(tmp_path):
    store, ordner, src = _store(tmp_path)
    make_invoice(ordner / "b.pdf", rechnungsnr="RE-2026-00050", netto=420.0)
    calls = []
    unsicher = _claude(RICHTIG, sicher=False, calls=calls)
    r1 = lehrer.anlernen(store, ordner, limit=1, post=unsicher)
    assert [r["ergebnis"] for r in r1] == [lehrer.ERG_MENSCH]
    # naechste Runde: a.pdf liegt beim Menschen -> uebersprungen, b.pdf kommt dran
    r2 = lehrer.anlernen(store, ordner, limit=1, post=unsicher)
    assert [r["ergebnis"] for r in r2] == [lehrer.ERG_UEBERSPRUNGEN, lehrer.ERG_MENSCH]
    assert r2[1]["datei"] == "b.pdf"


def test_wiederholt_unlesbare_antwort_ruht(tmp_path):
    store, ordner, src = _store(tmp_path)

    def unsinn(body):
        return {"content": [{"type": "text", "text": "kein JSON"}]}
    for _ in range(lehrer.MAX_FEHLVERSUCHE):
        assert lehrer.anlernen_datei(store, src, post=unsinn)["ergebnis"] == lehrer.ERG_FEHLER
    assert lehrer.anlernen_datei(store, src, post=unsinn)["ergebnis"] == lehrer.ERG_UEBERSPRUNGEN


def test_limit_oder_netz_zaehlt_nicht_und_beendet_runde(tmp_path):
    store, ordner, src = _store(tmp_path)
    make_invoice(ordner / "b.pdf", rechnungsnr="RE-2026-00050", netto=420.0)
    calls = []

    def limit(body):
        calls.append(1)
        raise OSError("429 rate limit")
    for _ in range(lehrer.MAX_FEHLVERSUCHE + 2):
        r = lehrer.anlernen(store, ordner, post=limit)
        assert [x["ergebnis"] for x in r] == [lehrer.ERG_FEHLER]  # Runde bricht ab
    # a.pdf ruht NICHT, sobald Claude wieder da ist, geht es weiter
    r = lehrer.anlernen(store, ordner, post=_claude(RICHTIG))
    assert [x["ergebnis"] for x in r] == [lehrer.ERG_BESTAETIGT, lehrer.ERG_MENSCH]


def _cli_antwort(werte: dict, calls: list, is_error: bool = False):
    def aufruf(befehl, eingabe, cwd, env, timeout):
        calls.append({"befehl": befehl, "eingabe": eingabe, "cwd": cwd, "env": env})
        felder = {k: {"wert": v, "sicher": True} for k, v in werte.items()}
        out = json.dumps({"type": "result", "is_error": is_error,
                          "result": "```json\n" + json.dumps({"felder": felder}) + "\n```"})
        return (1 if is_error else 0), "Hinweiszeile\n" + out + "\n", ""
    return aufruf


def test_cli_weg_ueber_claude_code(tmp_path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_CLAUDE_BACKEND", "cli")
    monkeypatch.setenv("RECHNUNG_CLAUDE_CLI", "claude-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-darf-nicht-mit")
    monkeypatch.setattr(lehrer, "cli_befehl", lambda: "/usr/bin/claude")
    calls = []
    monkeypatch.setattr(lehrer, "_cli_aufruf", _cli_antwort(RICHTIG, calls))
    store, ordner, src = _store(tmp_path)
    assert lehrer.bereit() == ""
    r = lehrer.anlernen_datei(store, src)
    assert r["ergebnis"] == lehrer.ERG_BESTAETIGT, r
    assert r["weg"] == "cli"
    c = calls[0]
    assert c["befehl"][:2] == ["/usr/bin/claude", "-p"]
    assert "--allowedTools" in c["befehl"] and "Read" in c["befehl"]
    assert "--model" not in c["befehl"]          # Modell des Logins
    assert "a.pdf" in c["eingabe"] and "Antwortformat" in c["eingabe"]
    assert c["cwd"] == src.parent
    assert "ANTHROPIC_API_KEY" not in c["env"]   # sonst rechnet Claude Code per API ab


def test_cli_fehler_wird_gemeldet(tmp_path, monkeypatch):
    monkeypatch.setenv("RECHNUNG_CLAUDE_BACKEND", "cli")
    monkeypatch.setattr(lehrer, "cli_befehl", lambda: "/usr/bin/claude")
    monkeypatch.setattr(lehrer, "_cli_aufruf", _cli_antwort(RICHTIG, [], is_error=True))
    store, ordner, src = _store(tmp_path)
    r = lehrer.anlernen_datei(store, src)
    assert r["ergebnis"] == lehrer.ERG_FEHLER and "Claude Code" in r["gruende"][0]


def test_cli_ohne_claude_code(monkeypatch):
    monkeypatch.setenv("RECHNUNG_CLAUDE_BACKEND", "cli")
    monkeypatch.setattr(lehrer, "cli_befehl", lambda: None)
    assert "nicht gefunden" in lehrer.bereit()


def test_lokale_ki_bleibt_beim_anlernen_aus(tmp_path, monkeypatch):
    """Claude ist der Pruefer -- die lokale KI (Ollama) wuerde je Rechnung nur
    Minuten kosten. Jeder Pipeline-Lauf des Lehrers geht mit use_llm=False."""
    from rechnung import pipeline
    gesehen = []
    echt = pipeline.process

    def mitgeschnitten(path, store, use_llm=True, **kw):
        gesehen.append(use_llm)
        return echt(path, store, use_llm=use_llm, **kw)

    monkeypatch.setattr(pipeline, "process", mitgeschnitten)
    store, ordner, src = _store(tmp_path)
    r = lehrer.anlernen_datei(store, src, post=_claude(RICHTIG))
    assert r["ergebnis"] == lehrer.ERG_BESTAETIGT, r
    assert gesehen and all(u is False for u in gesehen), gesehen
    monkeypatch.setenv("RECHNUNG_CLAUDE_LOKALE_KI", "1")
    assert lehrer.lokale_ki()


def test_rechnung_im_menschordner_bleibt_erhalten(tmp_path):
    """Lehrer arbeitet direkt im Ordner "Menschliche Bearbeitung": eine von
    Claude bestaetigte Rechnung darf dort NICHT geloescht werden."""
    store, ordner, src = _store(tmp_path)
    res = process(src, store)
    human.ablegen(store, res, [{"art": "test", "grund": "Test", "felder": []}])
    kopie = human.datei_pfad(store, src.name)
    assert kopie is not None and kopie.exists()
    src.unlink()  # Original weg: die Arbeitskopie ist die einzige
    r = lehrer.anlernen_datei(store, kopie, post=_claude(RICHTIG))
    assert r["ergebnis"] == lehrer.ERG_BESTAETIGT, r
    assert kopie.exists()
    eintrag = [e for e in human.offen(store) if e["sha256"] == r["sha256"]][0]
    assert any("Claude" in h for h in eintrag["hinweise"]) and eintrag.get("claude_geprueft")
    # zweite Runde ueber den Ordner: schon bestaetigt, kein neuer Aufruf
    calls = []
    lehrer.anlernen(store, kopie.parent, post=_claude(RICHTIG, calls=calls))
    assert not calls


def test_zeitbudget_beendet_runde_zwischen_zwei_rechnungen(tmp_path):
    """Hintergrundrunde (windows/anlernen.cmd --minuten): nach Ablauf des
    Budgets wird keine weitere Rechnung angefangen, die laufende aber zu Ende
    gebracht -- kein Abschuss durch die Aufgabenplanung mitten in einer
    Rechnung."""
    store, ordner, src = _store(tmp_path)
    make_invoice(ordner / "b.pdf", rechnungsnr="RE-2026-00043")
    calls = []
    res = lehrer.anlernen(store, ordner, post=_claude(RICHTIG, calls=calls), zeitbudget_s=1e-9)
    assert len(res) == 1 and res[0]["datei"] == "a.pdf"
    assert len(calls) == 1
    # ohne Budget: beide
    res = lehrer.anlernen(store, ordner, post=_claude(RICHTIG, calls=calls))
    assert {r["datei"] for r in res} == {"a.pdf", "b.pdf"}


def test_kaputte_datei_bricht_runde_nicht_ab(tmp_path):
    """Proberunde 09.11.2026: eine Datei, die kein PDF ist, liess die ganze
    Runde mit einer Ausnahme sterben -- die restlichen Rechnungen kamen nie
    dran. Jetzt: Fehler protokollieren, weitermachen, nach 3 Versuchen ruhen."""
    store, ordner, src = _store(tmp_path)
    kaputt = ordner / "0_kaputt.pdf"          # sortiert VOR a.pdf
    kaputt.write_bytes(b"%PDF-1.4 das ist kein echtes PDF\n")
    calls = []
    res = lehrer.anlernen(store, ordner, post=_claude(RICHTIG, calls=calls))
    nach_datei = {r["datei"]: r["ergebnis"] for r in res}
    assert nach_datei["0_kaputt.pdf"] == lehrer.ERG_FEHLER
    assert nach_datei["a.pdf"] == lehrer.ERG_BESTAETIGT
    assert len(calls) == 1
    for _ in range(2):
        lehrer.anlernen_datei(store, kaputt)
    r = lehrer.anlernen_datei(store, kaputt)
    assert r["ergebnis"] == lehrer.ERG_UEBERSPRUNGEN
    assert "nicht lesbar" in r["gruende"][0]


def test_altes_rechnungsdatum_ist_fuer_den_lehrer_kein_grund(tmp_path):
    """Uebungsrechnungen sind oft Jahre alt (2024): die 2-Jahres-Grenze der
    lokalen KI darf Claudes belegtes Datum nicht als unsicher verwerfen."""
    store, ordner, src = _store(tmp_path)
    alt = make_invoice(ordner / "alt.pdf", datum="17.10.2024", rechnungsnr="RE-2024-0017")
    werte = dict(RICHTIG, rechnungsdatum="2024-10-17", rechnungsnummer="RE-2024-0017")
    r = lehrer.anlernen_datei(store, alt, post=_claude(werte))
    assert r["ergebnis"] == lehrer.ERG_BESTAETIGT, r


def test_waehrung_eur_ist_durch_eurozeichen_belegt():
    felder = {"waehrung": "text"}
    antwort = {"felder": {"waehrung": {"wert": "EUR", "sicher": True}}}
    ab = lehrer.abgleichen("Gesamtbetrag 1.190,00 \u20ac zahlbar bis", felder, {"waehrung": "EUR"}, antwort)
    assert "waehrung" not in ab.unsicher and ab.werte["waehrung"] == "EUR"
    ab = lehrer.abgleichen("Total CHF 1'190.00", felder, {"waehrung": "EUR"}, antwort)
    assert "waehrung" in ab.unsicher            # EUR steht nirgends
    ab = lehrer.abgleichen("Total CHF 1'190.00", felder, {"waehrung": "EUR"},
                           {"felder": {"waehrung": {"wert": "CHF", "sicher": True}}})
    assert ab.korrekturen.get("waehrung") == "CHF"
