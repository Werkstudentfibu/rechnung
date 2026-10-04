"""Kommandozeile: fetch / scan / suppliers / templates / journal / schatten / push / web / app / demo."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .store import Store


def _cmd_fetch(args) -> int:
    """PDF-Anhaenge aus dem IONOS-Postfach in den Inbox-Ordner holen."""
    from .config import imap, inbox_dir
    from .ingest import fetch

    cfg = imap()
    dest = Path(args.dest) if args.dest else inbox_dir()
    res = fetch(cfg, dest, dry_run=args.dry_run, limit=args.limit)
    if args.json:
        print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
        return 0 if not res.error else 1
    if res.error:
        print(f"Fehler: {res.error}")
        return 1
    print(f"{res.mails} Mail(s), {len(res.saved)} Anhang/Anhaenge -> {dest}"
          + ("  [Trockenlauf]" if res.dry_run else ""))
    for s in res.saved:
        print(f"  {s}")
    return 0


def _cmd_push(args) -> int:
    """Bestaetigte Rechnungen aus der Warteschlange an JobRouter uebergeben."""
    from .jobrouter import push_queue

    store = Store(args.data)
    res = push_queue(store.root)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0
    tag = " [Trockenlauf]" if res.get("dry_run") else ""
    print(f"{res['processed']} in Warteschlange, {res['sent']} gesendet{tag}")
    for r in res.get("results", []):
        mark = "ok" if r.get("sent") else ("dry" if r.get("dry_run") else
               ("skip" if r.get("skipped") else "FAIL"))
        print(f"  [{mark:4s}] {Path(str(r.get('file') or '')).name} {r.get('error') or r.get('skipped') or ''}")
    return 0


def _cmd_scan(args) -> int:
    from .pipeline import finalize, scan_folder

    store = Store(args.data)
    results = scan_folder(args.samples, store)
    fin_results = []
    if getattr(args, "finalize", False):
        fin_results = [finalize(r, store) for r in results if r.status in ("auto", "review")]
        if not args.json:
            skipped = sum(1 for f in fin_results if not f.get("finalized"))
            done = len(fin_results) - skipped
            print(f"finalize: {done} uebernommen, {skipped} uebersprungen "
                  f"(noch nicht im Training-UI bestaetigt)\n")
    if args.json:
        by_file = {r.file: r.to_dict() for r in results}
        if getattr(args, "finalize", False):
            finalized = [r for r in results if r.status in ("auto", "review")]
            for r, fin in zip(finalized, fin_results):
                by_file[r.file]["finalize"] = fin
        print(json.dumps(list(by_file.values()), indent=2, ensure_ascii=False))
        return 0
    if not results:
        print(f"Keine PDFs in {args.samples}")
        return 0
    for r in results:
        name = Path(r.file).name
        head = f"{name:40.40s} {r.status:18s}"
        if r.supplier_name:
            head += f" {r.supplier_name} (score {r.match_score})"
        print(head)
        for k, fr in r.fields.items():
            flag = "!" if fr.source == "missing" or fr.confidence < 0.7 else " "
            print(f"   {flag} {k:16s} {str(fr.value):24.24s} conf={fr.confidence}")
        for c in r.checks:
            mark = {True: "ok", False: "FAIL", None: "-"}[c.ok]
            print(f"   [{mark:4s}] {c.name} {c.detail}")
    return 0


def _cmd_suppliers(args) -> int:
    store = Store(args.data)
    for s in store.suppliers():
        tpls = store.templates_for(s.id)
        print(f"{s.id:20s} {s.name:30s} templates={len(tpls)} "
              f"ustid={s.ustids} iban={s.ibans}")
    return 0


def _cmd_import_vendors(args) -> int:
    from .importers import import_vendors_csv

    n = import_vendors_csv(args.file, Store(args.data))
    print(f"{n} Lieferanten importiert/aktualisiert.")
    return 0


def _cmd_import_business_units(args) -> int:
    from .importers import import_business_units_csv

    n = import_business_units_csv(args.file, Store(args.data))
    print(f"{n} Rechnungsadressen/Geschaeftsbereiche importiert/aktualisiert.")
    return 0


def _cmd_templates(args) -> int:
    store = Store(args.data)
    for t in store.all_templates():
        print(f"{t.id:24s} supplier={t.supplier_id:16s} variant={t.variant:10s} "
              f"confirmations={t.confirmations} secured={t.secured}")
        for f in t.fields:
            kind = "anchor" if f.anchor else ("abs" if f.abs_frac else "-")
            print(f"   {f.name:16s} {f.value_type:8s} {kind}")
    return 0


def _cmd_journal(args) -> int:
    from .pipeline import journal_rows, journal_totals

    store = Store(args.data)
    rows = journal_rows(store)
    if args.json:
        print(json.dumps({"rows": rows, "totals": journal_totals(rows)},
                         indent=2, ensure_ascii=False))
        return 0
    if not rows:
        print("Noch keine bestaetigten Rechnungen.")
        return 0
    cols = ["rechnungsnummer", "lieferant", "rechnungsdatum",
            "netto_gesamt", "ust_gesamt", "netto_19", "ust_19", "netto_7", "ust_7"]
    print(" | ".join(f"{c:>13.13s}" for c in cols))
    for r in rows:
        print(" | ".join(f"{str(r.get(c, '') if r.get(c) is not None else ''):>13.13s}"
                         for c in cols))
    tot = journal_totals(rows)
    print("-" * 100)
    print(" | ".join(f"{str(tot.get(c, '') if tot.get(c) is not None else ''):>13.13s}"
                     for c in cols))
    return 0


def _cmd_shadow(args) -> int:
    """Schattenbetrieb: haette das Programm allein richtig entschieden?"""
    from .shadow import stats

    data = stats(Store(args.data))
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    if not data["rows"]:
        print("Noch keine bestaetigten Rechnungen im Schattenbetrieb.")
        return 0
    cols = [("lieferant", "Lieferant", 34), ("rechnungen", "Rechnungen", 10),
            ("wuerde_durchlassen", "wuerde durchl.", 14),
            ("ohne_korrektur", "ohne Korrektur", 14),
            ("fehlentscheidungen", "Fehlentsch.", 11)]
    print(" | ".join(f"{h:>{w}.{w}s}" if k != "lieferant" else f"{h:<{w}.{w}s}"
                     for k, h, w in cols))
    print("-" * 95)
    for r in data["rows"] + [dict(data["totals"], lieferant="Summe")]:
        label = r["lieferant"] if not r.get("name") else f"{r['lieferant']} ({r['name']})"
        print(" | ".join(f"{label:<{w}.{w}s}" if k == "lieferant" else f"{r[k]:>{w}d}"
                         for k, _h, w in cols))
    print("\nFehlentscheidung = haette durchgelassen, Mensch hat aber korrigiert. "
          "Nur Messung -- jede Rechnung braucht weiterhin den Bestaetigungs-Klick.")
    return 0


def _cmd_web(args) -> int:
    import uvicorn

    from .web.server import create_app

    app = create_app(Store(args.data), Path(args.samples))
    print(f"Training-UI:  http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def _cmd_app(args) -> int:
    from .desktop import run as run_desktop

    run_desktop(Store(args.data), Path(args.samples), port=args.port)
    return 0


def _cmd_demo(args) -> int:
    """Erzeugt 3 synthetische Rechnungen, lernt ein Template, laeuft die Pipeline."""
    from .identify import supplier_from_doc
    from . import pdf as pdfmod
    from .pipeline import process
    from .synth import make_invoice
    from .template import FieldSpec, Template, Anchor

    tmp = Path(tempfile.mkdtemp(prefix="rechnung-demo-"))
    store = Store(tmp / "data")
    samples = tmp / "samples"
    samples.mkdir()

    # bewusst unterschiedliche Satz-Kombinationen je Rechnung (nicht ueberall
    # alle drei), damit die dynamischen Journal-Spalten an einem echten Fall
    # zeigen, dass sie sich pro Rechnung anpassen statt ein festes Set anzunehmen
    make_invoice(samples / "re1.pdf", rechnungsnr="RE-2026-00042", items=[
        {"beschreibung": "Buerostuhl ErgoLine", "menge": 4, "einzelpreis": 189.50,
         "mwst": 19, "waehrung": "EUR"},
        {"beschreibung": "Schreibtisch Alu 160x80", "menge": 2, "einzelpreis": 139.00,
         "mwst": 19, "waehrung": "EUR"},
    ])
    make_invoice(samples / "re2.pdf", rechnungsnr="RE-2026-00043", items=[
        {"beschreibung": "Notebook ProBook 14", "menge": 3, "einzelpreis": 799.00,
         "mwst": 19, "waehrung": "EUR"},
        {"beschreibung": "Fachbuch Buchhaltung kompakt", "menge": 10, "einzelpreis": 24.90,
         "mwst": 7, "waehrung": "EUR"},
    ])
    make_invoice(samples / "re3.pdf", rechnungsnr="RE-2026-00044", items=[
        {"beschreibung": "Fachzeitschrift Abo Q1", "menge": 1, "einzelpreis": 32.00,
         "mwst": 7, "waehrung": "EUR"},
        {"beschreibung": "Ausfuhrlieferung Schweiz (steuerfrei)", "menge": 1,
         "einzelpreis": 120.00, "mwst": 0, "waehrung": "EUR"},
    ])

    doc = pdfmod.load(samples / "re1.pdf")
    sup = supplier_from_doc(doc, "muster", "Muster Handels GmbH")
    store.save_supplier(sup)

    tpl = Template(id="muster__standard", supplier_id="muster", secured_at=1, confirmations=1)
    tpl.fields = [
        FieldSpec("rechnungsnummer", "number", True,
                  anchor=Anchor("rechnungsnummer", 0), rel=(60, -4, 240, 12)),
        FieldSpec("rechnungsdatum", "date", True,
                  anchor=Anchor("rechnungsdatum", 0), rel=(50, -4, 240, 12)),
        FieldSpec("netto", "amount", True,
                  anchor=Anchor("nettobetrag", 0), rel=(120, -4, 320, 12)),
        FieldSpec("ust_betrag", "amount", True,
                  anchor=Anchor("umsatzsteuer", 0), rel=(120, -4, 320, 12)),
        FieldSpec("brutto", "amount", True,
                  anchor=Anchor("gesamtbetrag", 0), rel=(120, -4, 320, 12)),
        FieldSpec("iban", "iban", False,
                  anchor=Anchor("iban", 0), rel=(20, -4, 360, 12)),
    ]
    store.save_template(tpl)

    for p in sorted(samples.glob("*.pdf")):
        r = process(p, store)
        print(f"\n{p.name}: status={r.status} conf={r.overall_confidence}")
        for k, fr in r.fields.items():
            print(f"  {k:16s} = {fr.value}  (conf {fr.confidence}, {fr.source})")
        for c in r.checks:
            print(f"  check {c.name}: {c.ok} {c.detail}")
    print(f"\nDemo-Daten: {tmp}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="rechnung", description=__doc__)
    p.add_argument("--data", default="./data", help="Datenverzeichnis (Templates/Lieferanten)")
    p.add_argument("--samples", default="./samples", help="Ordner mit Eingangs-PDFs")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scan", help="Alle PDFs im Sample-Ordner verarbeiten")
    s.add_argument("--json", action="store_true")
    s.add_argument("--finalize", action="store_true",
                   help="auto/review-Treffer in die Buecher uebernehmen + fuer JobRouter einreihen")
    s.set_defaults(func=_cmd_scan)

    f = sub.add_parser("fetch", help="PDF-Anhaenge aus dem IONOS-Postfach holen")
    f.add_argument("--dest", help="Zielordner (Standard: <data>/inbox)")
    f.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts speichern/markieren")
    f.add_argument("--limit", type=int, default=0, help="hoechstens N Mails")
    f.add_argument("--json", action="store_true")
    f.set_defaults(func=_cmd_fetch)

    pu = sub.add_parser("push", help="Warteschlange an JobRouter uebergeben")
    pu.add_argument("--json", action="store_true")
    pu.set_defaults(func=_cmd_push)

    sub.add_parser("suppliers", help="Bekannte Lieferanten").set_defaults(func=_cmd_suppliers)
    sub.add_parser("templates", help="Gespeicherte Templates").set_defaults(func=_cmd_templates)

    iv = sub.add_parser("import-vendors",
                        help="Abbyy 'Vendors'-Datensatz (CSV) als Lieferanten einspielen")
    iv.add_argument("file", help="Pfad zur CSV-Datei")
    iv.set_defaults(func=_cmd_import_vendors)

    ib = sub.add_parser("import-business-units",
                        help="Abbyy 'BusinessUnits'-Datensatz (CSV) als Rechnungsadressen einspielen")
    ib.add_argument("file", help="Pfad zur CSV-Datei")
    ib.set_defaults(func=_cmd_import_business_units)

    j = sub.add_parser("journal", help="Buchungsjournal (Netto/USt je Steuersatz)")
    j.add_argument("--json", action="store_true")
    j.set_defaults(func=_cmd_journal)

    sh = sub.add_parser("schatten",
                        help="Schattenbetrieb: wuerde-durchlassen vs. Korrekturen je Lieferant")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=_cmd_shadow)

    w = sub.add_parser("web", help="Lokale Training-UI im Browser starten (Entwicklung/Linux)")
    w.add_argument("--port", type=int, default=8000)
    w.set_defaults(func=_cmd_web)

    a = sub.add_parser("app", help="Training-UI als eigenstaendiges Fenster starten (kein Browser)")
    a.add_argument("--port", type=int, default=8000)
    a.set_defaults(func=_cmd_app)

    sub.add_parser("demo", help="Selbsttest mit synthetischen Rechnungen").set_defaults(func=_cmd_demo)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
