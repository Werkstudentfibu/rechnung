"""Baustein 2: Messen "PDF lesen ohne XML" gegen den ZUGFeRD-Korpus.

Ruft die normale Pipeline (``pipeline.process`` ueber den vorhandenen
Trockenlauf-Mechanismus, ``testsatz.TARGET``/``guard.Guard``) auf allen
Testfaellen aus ``tools/korpus.py`` auf -- aber mit abgeschaltetem Stufe-0-
Lesen des eingebetteten XML (``einvoice.IGNORE_ENV``), damit gemessen wird,
wie gut reines PDF-Lesen (Text/Tabelle/KI) waere, wie bei einem Scan/
Ausdruck ohne E-Rechnung.

Zwei Pfade je Testfall: mit KI und ohne KI (``use_llm``). Nur PDF-Dateien
werden gemessen (reine XRechnung-``.xml`` ohne PDF hat keinen Text zum
Lesen -- die zaehlen fuer Baustein 1/Stufe-0-Pruefung, nicht hier).

Trefferquote wird zusaetzlich je Szenario ausgewiesen (eine Datei je
Dubletten-Gruppe, erste nach Dateiname) -- siehe ``tools/korpus.py``, Aarons
Hinweis vom 13.10.2026 gegen das Ueberwiegen mehrfach vorhandener Beispiele.

Bericht: ``~/korpus/berichte/korpus_<datum>_<zeit>.{json,md}``.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rechnung import einvoice, lernbericht, testsatz  # noqa: E402
from rechnung.guard import Guard  # noqa: E402

TESTSATZ = Path.home() / "korpus" / "testsatz_zugferd.json"
BERICHTE = Path.home() / "korpus" / "berichte"
TMP_STORE = Path.home() / "korpus" / "_messstore"

# Der generische Pipeline-Feldvergleich deckt den Lieferantennamen nicht ab:
# er kommt nur aus einem Store-Treffer (bekannter Lieferant), nie als eigenes
# gelesenes Feld -- fuer fremde Korpus-Lieferanten waere das immer "fehlt",
# ohne etwas ueber die Lesequalitaet auszusagen.
_AUSSCHLUSS = {"verkaeufer_name"}


def _laden() -> dict:
    if not TESTSATZ.exists():
        sys.exit(f"Kein Testsatz gefunden ({TESTSATZ}) -- zuerst "
                 f"`.venv/bin/python tools/korpus.py` laufen lassen.")
    return json.loads(TESTSATZ.read_text(encoding="utf-8"))


def _fall_fuer_vergleich(f: dict) -> dict:
    felder = {k: v for k, v in f["felder"].items() if k not in _AUSSCHLUSS}
    return {**f, "felder": felder}


def _je_szenario(faelle: list[dict]) -> list[dict]:
    """Eine Datei je Szenario (erste nach Dateiname) -- gegen das Gewicht
    mehrfach vorhandener Format-/Profilvarianten derselben Rechnung."""
    out: dict[str, dict] = {}
    for f in sorted(faelle, key=lambda f: f["datei"]):
        out.setdefault(f["szenario_id"], f)
    return list(out.values())


def _lauf(root: str, faelle: list[dict], use_llm: bool) -> dict:
    os.environ[einvoice.IGNORE_ENV] = "1"
    TMP_STORE.mkdir(parents=True, exist_ok=True)
    guard = Guard(TMP_STORE, target=testsatz.TARGET, extra={"variante": "A"}, protokoll=False)
    out = []
    t0 = time.monotonic()
    try:
        for f in faelle:
            pfad = str(Path(root) / f["datei"])
            res = guard.process(pfad, use_llm=use_llm)
            v = testsatz.vergleiche(_fall_fuer_vergleich(f), res)
            out.append({"sha256": f["sha256"], "datei": f["datei"],
                       "szenario_id": f["szenario_id"], "dauer_s": res.duration_s,
                       "abgebrochen": res.aborted, **v})
    finally:
        guard.close()
        os.environ.pop(einvoice.IGNORE_ENV, None)
    felder, pos, quote = testsatz._summe(out)
    return {"pfad": "mit KI" if use_llm else "ohne KI", "anzahl": len(out),
           "trefferquote": quote, "felder": felder, "positionen": pos,
           "abgebrochen": sum(1 for f in out if f["abgebrochen"]),
           "dauer_gesamt_s": round(time.monotonic() - t0, 2), "faelle": out}


def _fehlerarten(lauf: dict, n: int = 20) -> list[tuple[str, int]]:
    c: Counter[str] = Counter()
    for f in lauf["faelle"]:
        for feld, v in f["felder"].items():
            if v["ergebnis"] == testsatz.FALSCH:
                c[f"{feld}: {lernbericht.fehlerart(v['soll'], v['ist'])}"] += 1
    return c.most_common(n)


def _markdown(ergebnis: dict) -> str:
    lines = ["# Korpus-Bericht: PDF lesen ohne eingebettetes XML", "",
            f"Erstellt: {ergebnis['erstellt']}",
            f"Testfaelle (PDF): {ergebnis['anzahl_dateien']} in "
            f"{ergebnis['anzahl_szenarien']} Szenarien", ""]
    for key in ("je_datei", "je_szenario"):
        lines.append(f"## {'Je Datei' if key == 'je_datei' else 'Je Szenario (Dubletten zusammengefasst)'}")
        lines.append("")
        for lauf in ergebnis[key]:
            lines.append(f"### {lauf['pfad']} ({lauf['anzahl']} Rechnungen, "
                        f"Trefferquote {lauf['trefferquote']:.1f} %)"
                        if lauf['trefferquote'] is not None else f"### {lauf['pfad']}")
            lines.append("")
            lines.append("| Feld | richtig | falsch | fehlt | Quote |")
            lines.append("|---|---|---|---|---|")
            for feld, r in lauf["felder"].items():
                q = f"{r['quote']:.1f} %" if r["quote"] is not None else "-"
                lines.append(f"| {feld} | {r[testsatz.RICHTIG]} | {r[testsatz.FALSCH]} | "
                            f"{r[testsatz.FEHLT]} | {q} |")
            pos = lauf["positionen"]
            if pos.get("soll"):
                q = f"{pos['quote']:.1f} %" if pos["quote"] is not None else "-"
                lines.append(f"\nPositionen: {pos['korrekt']} von {pos['soll']} Zeilen korrekt "
                            f"({pos['gefunden']} gefunden, Quote {q})")
            lines.append(f"\nZeit gesamt: {lauf['dauer_gesamt_s']:.1f} s, "
                        f"abgebrochen: {lauf['abgebrochen']}")
            lines.append("")
    lines.append("## 20 haeufigste Fehlerarten (mit KI, je Datei)")
    lines.append("")
    lines.append("| Feld: Fehlerart | Anzahl |")
    lines.append("|---|---|")
    for art, n in ergebnis["fehlerarten_mit_ki"]:
        lines.append(f"| {art} | {n} |")
    lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    d = _laden()
    root = d["root"]
    alle = [f for f in d["faelle"] if f["datei"].lower().endswith(".pdf")]
    je_datei_faelle = alle
    je_szenario_faelle = _je_szenario(alle)

    ergebnis = {
        "art": "korpus_bericht", "erstellt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "quelle": d["quelle"], "lizenz": d["lizenz"],
        "anzahl_dateien": len(je_datei_faelle), "anzahl_szenarien": len(je_szenario_faelle),
        "je_datei": [_lauf(root, je_datei_faelle, use_llm=True),
                    _lauf(root, je_datei_faelle, use_llm=False)],
        "je_szenario": [_lauf(root, je_szenario_faelle, use_llm=True),
                       _lauf(root, je_szenario_faelle, use_llm=False)],
    }
    ergebnis["fehlerarten_mit_ki"] = _fehlerarten(ergebnis["je_datei"][0])

    BERICHTE.mkdir(parents=True, exist_ok=True)
    stempel = time.strftime("%Y-%m-%d_%H%M%S")
    (BERICHTE / f"korpus_{stempel}.json").write_text(
        json.dumps(ergebnis, indent=2, ensure_ascii=False), encoding="utf-8")
    (BERICHTE / f"korpus_{stempel}.md").write_text(_markdown(ergebnis), encoding="utf-8")
    print(f"Bericht: {BERICHTE / f'korpus_{stempel}.md'}")
    for lauf in ergebnis["je_datei"]:
        q = lauf["trefferquote"]
        print(f"je Datei, {lauf['pfad']}: {lauf['anzahl']} Rechnungen, "
             f"Trefferquote {q:.1f} %" if q is not None else f"{lauf['pfad']}: -")


if __name__ == "__main__":
    main()
