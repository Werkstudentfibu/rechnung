"""Messaufbau "Effizienz 10.10.2026": synthetischer Testsatz (Goldstandard)
+ Messung Zeit je Rechnung und Trefferquote je Feld, vorher/nachher.

    python tools/effizienz_messung.py aufbauen <ordner>
    python tools/effizienz_messung.py messen <ordner> --name vorher [--ohne-ki]

``aufbauen`` erzeugt Rechnungen (Textebene und Scans, 1-3 Seiten) von acht
Lieferanten, legt die Lieferanten an, haelt die Mail-Herkunft fest (teils als
Weiterleitung aus dem eigenen Outlook) und bestaetigt jede Rechnung ueber die
Oberflaechen-API mit den wahren Werten (KI aus) -- genau wie ein Mensch:
Vorlagen werden dabei gelernt, die Soll-Werte landen im Testsatz.

``messen`` kopiert die Daten frisch (ohne OCR-/KI-Zwischenspeicher, damit
jeder Lauf dieselbe Arbeit hat), liest jeden Testfall im Trockenlauf neu
(``testsatz.verarbeiten_trocken``, im selben Prozess) und zaehlt neben
Trefferquote und Zeiten auch die Modell- und Tesseract-Aufrufe -- die sind
unabhaengig von der Hardware (Messrechner hat eine Grafikkarte, der Laptop
nicht). Ergebnis: ``<ordner>/messung_<name>.json``.

Laeuft auch gegen einen aelteren Stand des Programms (``PYTHONPATH`` auf
dessen Arbeitskopie), nutzt nur Schnittstellen, die es dort schon gab.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
import sys
import time
from pathlib import Path

os.environ.setdefault("RECHNUNG_VORVERARBEITUNG", "0")
os.environ.setdefault("RECHNUNG_FELDER_AUS", "")

LIEFERANTEN = [
    # id, Name, USt-IdNr, IBAN, Domain, Art, Seiten, Layout, Anzahl, Absender
    ("alpha", "Alpha Werkzeug GmbH", "DE123456789", "DE89370400440532013000",
     "alpha-werkzeug.de", "text", 1, "a", 5, "direkt"),
    ("beta", "Beta Buerobedarf AG", "DE987654321", "DE02120300000000202051",
     "beta-buero.de", "text", 1, "b", 4, "weitergeleitet"),
    ("cetra", "Cetra Logistik KG", "DE811111111", "DE75512108001245126199",
     "cetra-logistik.de", "text", 1, "a", 4, "direkt"),
    ("delta", "Delta Druck GmbH", "DE822222222", "DE12500105170648489890",
     "delta-druck.de", "scan", 1, "a", 4, "direkt"),
    ("epsilon", "Epsilon Energie AG", "DE833333333", "DE91100000000123456789",
     "epsilon-energie.de", "scan", 3, "multi", 4, "weitergeleitet"),
    # Rechnungsnummer nur auf Seite 1 -> Vorlage vollstaendig (Frueh-Ausstieg)
    ("fokus", "Fokus Feinkost GmbH", "DE844444444", "DE27100777770209299700",
     "fokus-feinkost.de", "scan", 3, "multi1", 4, "direkt"),
    ("gamma", "Gamma Gastro Service GmbH", "DE855555555", "DE44500105175407324931",
     "gamma-gastro.de", "text", 1, "b", 3, "direkt"),
    ("hotel", "Hotelwaesche Huber KG", "DE866666666", "DE68210501700012345678",
     "huber-waesche.de", "scan", 1, "b", 3, "freemail"),
    # schlechter Scan (150 dpi, starkes Rauschen) -> unsichere Woerter
    ("iota", "Iota IT-Service GmbH", "DE877777777", "DE02700100800030876808",
     "iota-it.de", "schlecht", 1, "a", 4, "direkt"),
]


def _de(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _scan(src: Path, dst: Path, seed: int, schlecht: bool = False) -> None:
    """Text-PDF -> Bild-PDF wie aus einem Buero-Scanner: 200 dpi Graustufen,
    leichtes Rauschen/Unschaerfe, JPEG."""
    import io

    import pypdfium2 as pdfium
    from PIL import Image, ImageFilter

    rnd = random.Random(seed)
    pdf = pdfium.PdfDocument(str(src))
    pages = []
    for i in range(len(pdf)):
        dpi = 150 if schlecht else 200
        im = pdf[i].render(scale=dpi / 72).to_pil().convert("L")
        im = im.rotate(rnd.uniform(-0.6, 0.6), fillcolor=255, resample=Image.BICUBIC)
        im = im.filter(ImageFilter.GaussianBlur(rnd.uniform(0.6, 0.9) if schlecht
                                                else rnd.uniform(0.3, 0.7)))
        px = im.load()
        w, h = im.size
        for _ in range(w * h // (12 if schlecht else 60)):
            x, y = rnd.randrange(w), rnd.randrange(h)
            px[x, y] = max(0, min(255, px[x, y] + rnd.randint(-90, 90)))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=rnd.randint(25, 35) if schlecht else rnd.randint(55, 75))
        pages.append(Image.open(io.BytesIO(buf.getvalue())).convert("RGB"))
    pdf.close()
    pages[0].save(dst, "PDF", resolution=dpi, save_all=True, append_images=pages[1:])


def _rechnungen(base: Path) -> list[dict]:
    from rechnung.synth import make_invoice, make_multipage_invoice

    rnd = random.Random(1010)
    out = []
    roh = base / "roh"
    ein = base / "eingang"
    roh.mkdir(parents=True, exist_ok=True)
    ein.mkdir(parents=True, exist_ok=True)
    for sid, name, ustid, iban, dom, art, seiten, layout, n, absender in LIEFERANTEN:
        for k in range(1, n + 1):
            nr = f"{sid[:2].upper()}-2026-{100 + k * 7:05d}"
            datum = f"{rnd.randint(1, 28):02d}.{rnd.randint(1, 9):02d}.2026"
            src = roh / f"{sid}_{k}.pdf"
            if layout.startswith("multi"):
                soll = make_multipage_invoice(src, seiten=seiten, lieferant=name, ustid=ustid,
                                              iban=iban, rechnungsnr=nr, datum=datum,
                                              positionen_je_seite=rnd.randint(5, 9),
                                              nummer_jede_seite=layout == "multi")
                netto, ust, brutto = soll["netto"], soll["ust"], soll["brutto"]
            else:
                netto = round(rnd.uniform(80, 4000), 2)
                make_invoice(src, lieferant=name, ustid=ustid, iban=iban, domain=dom,
                             rechnungsnr=nr, datum=datum, netto=netto, layout=layout,
                             zahlungstext="Zahlbar innerhalb von 14 Tagen ohne Abzug.")
                ust = round(netto * 0.19, 2)
                brutto = round(netto + ust, 2)
            ziel = ein / f"{sid}_{k}.pdf"
            if art in ("scan", "schlecht"):
                _scan(src, ziel, seed=sum(map(ord, sid)) * 100 + k, schlecht=art == "schlecht")
            else:
                shutil.copy(src, ziel)
            out.append({"lieferant": sid, "datei": ziel.name, "art": art, "seiten": seiten,
                        "absender": absender, "domain": dom,
                        "werte": {"rechnungsnummer": nr, "rechnungsdatum": datum,
                                  "netto": _de(netto), "ust_betrag": _de(ust),
                                  "brutto": _de(brutto)}})
    return out


def _herkunft(data: Path, pdf: Path, r: dict) -> None:
    from rechnung import mailherkunft

    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    if r["absender"] == "freemail":
        rec = {"von": "huber.waesche@gmx.de", "domain": "gmx.de", "weitergeleitet_von": ""}
    elif r["absender"] == "weitergeleitet":
        rec = {"von": f"rechnung@{r['domain']}", "domain": r["domain"],
               "weitergeleitet_von": "Aaron <aaron@althoffhotels.com>"}
    else:
        rec = {"von": f"buchhaltung@{r['domain']}", "domain": r["domain"],
               "weitergeleitet_von": ""}
    rec.update({"betreff": f"Rechnung {r['werte']['rechnungsnummer']}", "message_id": "",
                "datum": "", "grund": ""})
    mailherkunft.merken(data, sha, rec)


def aufbauen(base: Path) -> None:
    from fastapi.testclient import TestClient

    from rechnung import ocr
    from rechnung.identify import Supplier
    from rechnung.store import Store
    from rechnung.testsatz import gleich
    from rechnung.web import server as server_mod

    os.environ["RECHNUNG_LLM_ENABLED"] = "0"
    assert ocr.available(), "Tesseract fehlt (RECHNUNG_TESSERACT setzen)"
    if base.exists():
        shutil.rmtree(base)
    data = base / "data"
    store = Store(data)
    for sid, name, ustid, iban, dom, art, *_ in LIEFERANTEN:
        store.save_supplier(Supplier(id=sid, name=name, ustids=[ustid], ibans=[iban],
                                     domains=[] if sid == "hotel" else [dom]))
    rechnungen = _rechnungen(base)
    client = TestClient(server_mod.create_app(store, base / "eingang"))
    for r in rechnungen:
        pdf = base / "eingang" / r["datei"]
        try:
            _herkunft(data, pdf, r)
        except ImportError:
            pass
        inv = client.get("/api/invoice", params={"file": r["datei"]}).json()
        cur = inv.get("current") or {}
        for feld, wahr in r["werte"].items():
            ist = (cur.get(feld) or {}).get("value")
            if not gleich(feld, wahr, ist):
                rr = client.post("/api/correction",
                                 json={"file": r["datei"], "field": feld, "value": wahr})
                assert rr.status_code == 200, rr.text
        body = {"file": r["datei"]}
        if inv.get("template_id"):
            body["template_id"] = inv["template_id"]
        rr = client.post("/api/confirm", json=body)
        assert rr.status_code == 200, (r["datei"], rr.text)
        print(f"bestaetigt {r['datei']:14s} Vorlage={inv.get('template_id')}", flush=True)
    (base / "rechnungen.json").write_text(json.dumps(rechnungen, indent=2, ensure_ascii=False))
    print("Vorlagen:", [(t.id, t.confirmations) for s in store.suppliers()
                        for t in store.templates_for(s.id)])


class _Zaehler:
    def __init__(self):
        self.ki = 0           # echte Modellaufrufe (kein Cache-Treffer)
        self.ki_modelle: dict[str, int] = {}
        self.tesseract = 0    # Tesseract-Aufrufe (Seiten + Nachlese-Ausschnitte)

    def einbauen(self):
        from rechnung import ocr, semantic

        z = self
        orig_ollama, orig_openai = semantic._call_ollama, semantic._call_openai

        def ollama(cfg, prompt, schema=None):
            z.ki += 1
            z.ki_modelle[cfg.model] = z.ki_modelle.get(cfg.model, 0) + 1
            return orig_ollama(cfg, prompt, schema)

        def openai(cfg, prompt, schema=None):
            z.ki += 1
            return orig_openai(cfg, prompt, schema)

        semantic._call_ollama, semantic._call_openai = ollama, openai
        orig_run = ocr._run

        def run(args, timeout, **kw):
            if args and "--list-langs" not in args:
                z.tesseract += 1
            return orig_run(args, timeout, **kw)

        ocr._run = run


def messen(base: Path, name: str, ohne_ki: bool = False) -> dict:
    from rechnung import human, testsatz
    from rechnung.guard import Guard
    from rechnung.store import Store
    from rechnung.timing import distribution

    arbeit = base / f"lauf_{name}"
    if arbeit.exists():
        shutil.rmtree(arbeit)
    shutil.copytree(base / "data", arbeit, ignore=shutil.ignore_patterns(
        "ocr", "ki-cache", "berichte"))
    store = Store(arbeit)
    z = _Zaehler()
    z.einbauen()
    faelle, _skip = testsatz.faelle(store)
    guard = Guard(arbeit, timeout=0, target=testsatz.TARGET, extra={"variante": "A"},
                  protokoll=False)
    out = []
    t0 = time.monotonic()
    for fall in sorted(faelle, key=lambda f: f.get("datei") or ""):
        ki0, ts0 = z.ki, z.tesseract
        res = guard.process(fall["_pfad"], use_llm=not ohne_ki)
        v = testsatz.vergleiche(fall, res)
        gruende = human.gruende(res)
        out.append({"an_mensch": [g["art"] for g in gruende],"datei": fall.get("datei"), "lieferant": fall.get("supplier_id"),
                    "dauer_s": res.duration_s, "stufen": res.stufenzeiten,
                    "ki_aufrufe": z.ki - ki0, "tesseract_aufrufe": z.tesseract - ts0,
                    "ocr": res.ocr, "status": res.status, "hinweise": res.hinweise,
                    "frueh": getattr(res, "frueh_ausstieg", None),
                    "ki_stufe2": getattr(res, "ki_stufe2", None),
                    "abgebrochen": res.aborted, **v})
        print(f"{fall.get('datei'):14s} {res.duration_s:6.2f}s KI {z.ki - ki0} "
              f"Tess {z.tesseract - ts0} "
              + " ".join(f"{k}={x['ergebnis'][0]}" for k, x in v["felder"].items()), flush=True)
    felder, pos, quote = testsatz._summe(out)
    stufen: dict[str, list[float]] = {}
    for f in out:
        for k, s in (f["stufen"] or {}).items():
            stufen.setdefault(k, []).append(s)
    bericht = {
        "name": name, "ohne_ki": ohne_ki, "anzahl": len(out),
        "env": {k: v for k, v in os.environ.items() if k.startswith("RECHNUNG_")},
        "trefferquote": quote, "felder": felder, "positionen": pos,
        "zeit_s": distribution([f["dauer_s"] for f in out]),
        "zeit_summe_s": round(sum(f["dauer_s"] for f in out), 2),
        "stufen_summe_s": {k: round(sum(v), 2) for k, v in stufen.items()},
        "ki_aufrufe": z.ki, "ki_modelle": z.ki_modelle, "tesseract_aufrufe": z.tesseract,
        "an_mensch": sum(1 for f in out if f["an_mensch"]),
        "ocr_unsicher": sum(1 for f in out if "ocr" in f["an_mensch"]),
        "wand_s": round(time.monotonic() - t0, 2), "faelle": out,
    }
    (base / f"messung_{name}.json").write_text(json.dumps(bericht, indent=2,
                                                          ensure_ascii=False, default=str))
    print(json.dumps({k: bericht[k] for k in ("name", "anzahl", "trefferquote", "zeit_s",
                                              "zeit_summe_s", "stufen_summe_s", "ki_aufrufe",
                                              "ki_modelle", "tesseract_aufrufe", "an_mensch",
                                              "ocr_unsicher")},
                     ensure_ascii=False))
    print({k: v["quote"] for k, v in felder.items()})
    return bericht


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("aufbauen")
    a.add_argument("ordner")
    m = sub.add_parser("messen")
    m.add_argument("ordner")
    m.add_argument("--name", required=True)
    m.add_argument("--ohne-ki", action="store_true")
    args = ap.parse_args(argv)
    if args.cmd == "aufbauen":
        aufbauen(Path(args.ordner))
    else:
        messen(Path(args.ordner), args.name, args.ohne_ki)
    return 0


if __name__ == "__main__":
    sys.exit(main())
