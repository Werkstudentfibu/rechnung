"""Kommandozeile: fetch / scan / messen / vorlernen / suppliers / templates / journal / schatten / modus / push / web / app / demo."""

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
    for h in res.human:
        print(f"  -> Menschliche Bearbeitung: {h}")
    return 0


def _cmd_imap_test(args) -> int:
    """Anmeldung + Ordnerliste + Anzahl ungelesener Mails pruefen, ohne
    etwas abzuholen oder zu markieren."""
    from .config import imap
    from .ingest import check_connection

    res = check_connection(imap())
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res["ok"] is not False else 1
    mark = {True: "ok", False: "FAIL", None: "-"}[res["ok"]]
    print(f"[{mark}] {res['detail']}")
    for f in res.get("folders", []):
        print(f"   {f}")
    return 0 if res["ok"] is not False else 1


def _cmd_jobrouter_test(args) -> int:
    """Erreichbarkeit/Anmeldung pruefen, ohne eine Rechnung anzulegen."""
    from .config import jobrouter as jr_cfg
    from .jobrouter import check_connection

    res = check_connection(jr_cfg())
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0 if res["ok"] is not False else 1
    mark = {True: "ok", False: "FAIL", None: "-"}[res["ok"]]
    print(f"[{mark}] {res['detail']}")
    return 0 if res["ok"] is not False else 1


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
        print(head + f"  [{r.duration_s:.1f} s]")
        akt = (r.verteilung or {}).get("aktion")
        if akt == "mensch":
            for g in r.verteilung.get("gruende") or []:
                print(f"   >> Ordner Menschliche Bearbeitung: {g['grund']}")
        elif akt == "autonom":
            fin = r.verteilung.get("finalize") or {}
            print("   >> autonom uebernommen" + (" + JobRouter-Warteschlange"
                                                  if fin.get("queued") else ""))
        for w in r.warnings:
            print(f"   !! {w}")
        for k, fr in r.fields.items():
            flag = "!" if fr.source == "missing" or fr.confidence < 0.7 else " "
            print(f"   {flag} {k:16s} {str(fr.value):24.24s} conf={fr.confidence}")
        for c in r.checks:
            mark = {True: "ok", False: "FAIL", None: "-"}[c.ok]
            print(f"   [{mark:4s}] {c.name} {c.detail}")
    return 0


def _cmd_messen(args) -> int:
    """Stufenzeiten messen (Trockenlauf, nichts wird gespeichert/eingereiht)."""
    from .messen import messen, verteilung

    store = Store(args.data)
    folder = args.ordner or args.samples
    results = messen(folder, store)
    if args.json:
        print(json.dumps({"dateien": [r.to_dict() for r in results],
                          "verteilung": verteilung(results)}, indent=2, ensure_ascii=False))
        return 0
    if not results:
        print(f"Keine Rechnungen in {folder}")
        return 0
    for r in results:
        stufen = " ".join(f"{k}={v:.2f}s" for k, v in (r.stufenzeiten or {}).items())
        print(f"{Path(r.file).name:40.40s} {r.seiten:3d} S. gesamt={r.duration_s:.2f}s  {stufen}")
    print("\nVerteilung je Stufe (Median/P90/Max, n=Anzahl):")
    for name, d in verteilung(results).items():
        if not d:
            continue
        print(f"  {name:12s} median={d['median']:.2f}s p90={d['p90']:.2f}s "
              f"max={d['max']:.2f}s (n={d['anzahl']})")
    return 0


def _cmd_vorlernen(args) -> int:
    """Altbestand vorlernen: Ordner lesen und gruppieren (Vorschlaege), die
    Gruppen auflisten oder eine Gruppe bestaetigen. Nichts davon geht nach
    JobRouter oder in die Schattenstatistik (vorlernen.py)."""
    from . import vorlernen

    store = Store(args.data)
    if args.bestaetigen:
        if not args.operator:
            print("Bestaetigen nur mit --operator NAME (Audit-Protokoll)")
            return 2
        try:
            out = vorlernen.confirm(store, args.bestaetigen, args.operator)
        except ValueError as e:
            print(f"Fehler: {e}")
            return 1
        if args.json:
            print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
            return 0
        print(f"Vorlage {out['template_id']} aktiv (gelernt: {', '.join(out['gelernt']) or '-'})")
        for fname, grund in out["skipped"].items():
            print(f"  nicht gelernt {fname}: {grund}")
        for fname, dateien in out["abweichungen"].items():
            for datei, w in dateien.items():
                print(f"  Abweichung {fname} in {datei}: Vorlage={w['gelernt']} "
                      f"erwartet={w['erwartet']}")
        return 0
    if args.ordner:
        rep = vorlernen.scan(args.ordner, store)
        if args.json:
            print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
            return 0
        print(f"{rep.dateien} Rechnung(en) in {rep.gruppen} Gruppe(n) vorgeschlagen, "
              f"{rep.e_rechnungen} E-Rechnung(en) uebersprungen (brauchen keine Vorlage)")
        if rep.ohne_werte:
            print(f"  {rep.ohne_werte} ohne gelesene Werte (KI aus/nicht erreichbar?) -- "
                  "Werte im Training-UI (Reiter Vorlernen) eintragen")
        for f in rep.fehler:
            print(f"  FEHLER {f['datei']}: {f['grund']}")
    groups = vorlernen.list_groups(store)
    if args.json and not args.ordner:
        print(json.dumps(groups, indent=2, ensure_ascii=False, default=str))
        return 0
    for g in groups:
        aktiv = sum(1 for d in g["dateien"] if not d.get("ausgeschlossen"))
        stand = f"bestaetigt -> {g['template_id']}" if g.get("bestaetigt") else "Vorschlag"
        print(f"{g['gruppe_id']:40.40s} {g.get('supplier_name') or '(Lieferant unbekannt)':30.30s} "
              f"{aktiv:3d} Rechnung(en)  {stand}")
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

    missing: list[str] = []
    n = import_vendors_csv(args.file, Store(args.data), missing)
    print(f"{n} Lieferanten importiert/aktualisiert.")
    if missing:
        bu_ids = ", ".join(sorted(set(missing)))
        print(f"Hinweis: {len(missing)} Lieferant(en) verweisen auf eine BusinessUnitId, "
              f"die (noch) nicht als Rechnungsadresse existiert ({bu_ids}) -- empfiehlt sich, "
              f"erst 'rechnung import-business-units' auszufuehren.")
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
              f"confirmations={t.confirmations} secured={t.secured}"
              + (" VERALTET" if t.stale else ""))
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


def _cmd_fehlerbericht(args) -> int:
    """Haeufigste wiederkehrende Fehler: rote Pruefungen, fehlende/unsichere
    Felder, Korrekturen -- je Pruefung/Feld und Lieferant, mit Beispielen."""
    from .eventlog import report

    data = report(Store(args.data), args.seit)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    if not (data["pruefungen"] or data["felder"] or data["korrekturen"] or data["menschlich"]):
        print("Keine Ereignisse" + (f" seit {args.seit}" if args.seit else "") + ".")
        return 0
    tag = f" seit {args.seit}" if args.seit else ""
    print(f"Fehlerbericht{tag}\n")

    def section(title: str, rows: list[dict], key: str) -> None:
        if not rows:
            return
        print(f"{title}:")
        for r in rows:
            who = r["lieferant_name"] or r["lieferant"]
            beispiele = ", ".join(b["datei"] for b in r["beispiele"][:3])
            print(f"  {r['anzahl']:4d}x  {r[key]:<24s} {who:<30.30s}  z.B. {beispiele}")
        print()

    section("Pruefungen (rot)", data["pruefungen"], "pruefung")
    section("Felder (fehlend/niedrige Konfidenz)", data["felder"], "feld")
    section("Korrekturen", data["korrekturen"], "feld")
    section("An Mensch gegeben (Ordner Menschliche Bearbeitung)", data["menschlich"], "art")
    return 0


def _cmd_modus(args) -> int:
    """Betriebsmodus anzeigen bzw. ausdruecklich umschalten (Audit)."""
    from . import modus

    store = Store(args.data)
    if args.modus:
        if not args.operator:
            print("Fehler: Umschalten nur mit --operator NAME (wird im Audit-Log vermerkt).")
            return 2
        before = modus.fortschritt(store)
        res = modus.set_mode(store, args.modus, args.operator, args.grund or "")
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        print(f"Betriebsmodus: {res['alt']} -> {res['modus']} (durch {res['operator']})")
        if args.modus == modus.AUTONOM and not before["erreicht"]:
            print("Hinweis: Richtwert der Testphase noch nicht erreicht -- "
                  + modus.text(before))
        return 0
    fp = modus.fortschritt(store)
    if args.json:
        print(json.dumps(fp, indent=2, ensure_ascii=False))
        return 0
    print(modus.text(fp))
    if fp.get("modus_seit"):
        print(f"  seit {fp['modus_seit']} (durch {fp['modus_operator']})")
    if fp["letzte_fehlentscheidung"]:
        lf = fp["letzte_fehlentscheidung"]
        print(f"  letzte Fehlentscheidung: {lf['datei']} ({lf['lieferant']}), "
              f"korrigiert: {', '.join(lf['korrigiert'])}")
    return 0


def _cmd_menschlich(args) -> int:
    """Ordner "Menschliche Bearbeitung": offene Rechnungen mit Gruenden."""
    from .human import erledigt_sha, human_dir, offen

    store = Store(args.data)
    if getattr(args, "erledigt", None):
        rec = erledigt_sha(store, args.erledigt, args.operator or "", args.notiz or "")
        if rec is None:
            print(f"Kein offener Eintrag mit sha256 {args.erledigt}")
            return 1
        print(f"erledigt: {rec['datei']}")
        return 0
    rows = offen(store)
    if args.json:
        print(json.dumps({"ordner": str(human_dir(store.root)), "offen": rows},
                         indent=2, ensure_ascii=False, default=str))
        return 0
    print(f"Ordner: {human_dir(store.root)}")
    if not rows:
        print("Nichts offen.")
        return 0
    for r in rows:
        who = r.get("lieferant_name") or r.get("lieferant") or "(Lieferant unbekannt)"
        print(f"{r['datei']:40.40s} {who:30.30s} seit {(r.get('abgelegt') or '')[:16]}")
        for g in r.get("gruende") or []:
            print(f"   >> {g['grund']}")
    return 0


def _cmd_nacht(args) -> int:
    """Nachtlauf: JobRouter-Rueckmeldungen holen, Layoutwechsel pruefen,
    Fehlerbericht + Tagesbericht schreiben, Gesundheitschecks. Pausiert die
    laufende Abholung/Verarbeitung waehrend der Dauer (Lock-Datei)."""
    from .nightly import run_nightly

    store = Store(args.data)
    try:
        bericht = run_nightly(store, args.samples)
    except RuntimeError as e:
        print(f"Fehler: {e}")
        return 1
    if args.json:
        print(json.dumps(bericht, indent=2, ensure_ascii=False))
        return 0
    tb = bericht["tagesbericht"]
    print(f"Nachtlauf abgeschlossen ({bericht['dauer_s']:.1f} s) -> {bericht['datei']}")
    print(f"  heute verarbeitet: {tb['verarbeitet']}, an Mensch: {tb['an_mensch']}, "
          f"Fehlerquote: {tb['fehlerquote']}, Rueckstand: {tb['rueckstand']}")
    print(f"  in Ordner Menschliche Bearbeitung gelegt: {tb['menschlich_abgelegt']}, "
          f"autonom uebernommen: {tb['autonom_uebernommen']}")
    for name, d in tb.get("dauer_je_stufe", {}).items():
        if d:
            print(f"  Stufe {name:12s} median={d['median']:.2f}s p90={d['p90']:.2f}s "
                  f"max={d['max']:.2f}s (n={d['anzahl']})")
    print(f"  JobRouter-Rueckmeldungen abgeholt: {bericht['jobrouter_feedback']['abgeholt']}")
    print(f"  {bericht['betrieb']['text']}")
    print(f"  Ordner Menschliche Bearbeitung: {bericht['betrieb']['menschlich_offen']} offen")
    print(f"  veraltete Vorlagen: {len(bericht['layoutwechsel']['veraltete_vorlagen'])}")
    for name, h in bericht["gesundheit"].items():
        mark = {True: "ok", False: "FAIL", None: "-"}[h["ok"]]
        print(f"  [{mark:4s}] {name}: {h['detail']}")
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


def _cmd_update(args) -> int:
    """Neuen Stand vom Zweig ``release`` holen (selbstupdate.py). Exit-Code
    0 = nichts zu tun/offline, 42 = aktualisiert (Neustart noetig),
    1 = Fehler/zurueckgerollt."""
    from . import selbstupdate

    e = selbstupdate.aktualisieren(Path(args.data), tests=not args.ohne_tests)
    print(f"Update: {e.status} -- {e.meldung}")
    if e.status == "aktualisiert":
        return selbstupdate.RESTART_EXIT_CODE
    return 1 if e.status in ("zurueckgerollt", "fehler") else 0


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

    it = sub.add_parser("imap-test",
                        help="IONOS-Postfach: Anmeldung/Ordner/ungelesene Mails pruefen, ohne abzuholen")
    it.add_argument("--json", action="store_true")
    it.set_defaults(func=_cmd_imap_test)

    jt = sub.add_parser("jobrouter-test",
                        help="JobRouter: Erreichbarkeit/Anmeldung pruefen, ohne eine Rechnung anzulegen")
    jt.add_argument("--json", action="store_true")
    jt.set_defaults(func=_cmd_jobrouter_test)

    me = sub.add_parser("messen",
                        help="Stufenzeiten messen (Trockenlauf, nichts wird gespeichert/eingereiht)")
    me.add_argument("ordner", nargs="?", help="Ordner mit Rechnungen (Standard: --samples)")
    me.add_argument("--json", action="store_true")
    me.set_defaults(func=_cmd_messen)

    vl = sub.add_parser("vorlernen",
                        help="Altbestand vorlernen: Vorlagen-Vorschlaege je Lieferant/Layout "
                             "(ohne JobRouter/Schattenstatistik)")
    vl.add_argument("ordner", nargs="?", help="Ordner mit alten Rechnungen (ohne: Gruppen auflisten)")
    vl.add_argument("--bestaetigen", metavar="GRUPPE",
                    help="Vorlage aus dieser Gruppe lernen und aktivieren")
    vl.add_argument("--operator", help="wer bestaetigt (Pflicht bei --bestaetigen, Audit)")
    vl.add_argument("--json", action="store_true")
    vl.set_defaults(func=_cmd_vorlernen)

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

    fb = sub.add_parser("fehlerbericht",
                        help="Haeufigste wiederkehrende Fehler/Korrekturen je Pruefung/Feld und Lieferant")
    fb.add_argument("--seit", help="nur Ereignisse ab diesem Datum (YYYY-MM-DD)")
    fb.add_argument("--json", action="store_true")
    fb.set_defaults(func=_cmd_fehlerbericht)

    mo = sub.add_parser("modus",
                        help="Betriebsmodus (testphase/autonom) anzeigen oder umschalten")
    mo.add_argument("modus", nargs="?", choices=["testphase", "autonom"],
                    help="neuer Modus (ohne Angabe: nur anzeigen)")
    mo.add_argument("--operator", help="wer schaltet um (Pflicht beim Umschalten, Audit)")
    mo.add_argument("--grund", help="optionaler Vermerk fuer das Audit-Log")
    mo.add_argument("--json", action="store_true")
    mo.set_defaults(func=_cmd_modus)

    me = sub.add_parser("menschlich",
                        help="Ordner 'Menschliche Bearbeitung': offene Rechnungen mit Gruenden")
    me.add_argument("--json", action="store_true")
    me.add_argument("--erledigt", metavar="SHA256",
                    help="Eintrag ohne Rechnungs-Ergebnis (z.B. Mail ohne Anhang) abschliessen")
    me.add_argument("--notiz", default="", help="Vermerk zu --erledigt")
    me.add_argument("--operator", default="", help="Bearbeiter (Audit)")
    me.set_defaults(func=_cmd_menschlich)

    na = sub.add_parser("nacht",
                        help="Nachtlauf: JobRouter-Rueckmeldungen, Layoutwechsel, Berichte, Gesundheitschecks")
    na.add_argument("--json", action="store_true")
    na.set_defaults(func=_cmd_nacht)

    w = sub.add_parser("web", help="Lokale Training-UI im Browser starten (Entwicklung/Linux)")
    w.add_argument("--port", type=int, default=8000)
    w.set_defaults(func=_cmd_web)

    a = sub.add_parser("app", help="Training-UI als eigenstaendiges Fenster starten (kein Browser)")
    a.add_argument("--port", type=int, default=8000)
    a.set_defaults(func=_cmd_app)

    up = sub.add_parser("update", help="Neuen Programmstand vom Zweig release holen (Laptop-Testphase)")
    up.add_argument("--ohne-tests", action="store_true", help="Tests nach dem Update ueberspringen")
    up.set_defaults(func=_cmd_update)

    sub.add_parser("demo", help="Selbsttest mit synthetischen Rechnungen").set_defaults(func=_cmd_demo)

    args = p.parse_args(argv)
    _console_safe()
    return args.func(args)


def _console_safe() -> None:
    """Unter Windows ist die Ausgabe in eine Datei (z.B. ``>> nacht.log`` der
    geplanten Aufgabe) cp1252 -- Zeichen wie "→" oder "Ø" wuerden dort mit
    UnicodeEncodeError abbrechen. Nicht darstellbare Zeichen ersetzen statt
    abzustuerzen."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


if __name__ == "__main__":
    sys.exit(main())
