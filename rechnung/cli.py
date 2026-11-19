"""Kommandozeile: fetch / scan / messen / testsatz / vorlernen / suppliers / templates / journal / schatten / modus / push / web / app / demo."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from .store import Store


def _cmd_link_mails(args) -> int:
    """Mails ohne Rechnungsanhang zaehlen (linkmails.py) -- nur lesend,
    kein Link wird geoeffnet; Ausgabe nur Zahlen und Link-Domains."""
    from . import linkmails

    b = linkmails.bilanz(Store(args.data))
    if args.json:
        print(json.dumps(b.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(linkmails.bericht(b, min_mails=args.min_mails))
    return 0


def _cmd_link_abruf(args) -> int:
    """Rechnungen hinter Links holen (linkabruf.py). Schalter
    RECHNUNG_LINK_ABRUF=aus (Standard): nur zeigen, was geholt wuerde --
    kein Link wird geoeffnet."""
    from . import linkabruf

    store = Store(args.data)
    ziel = Path(args.ziel or args.samples)
    r = linkabruf.lauf(store, ziel, max_je_lauf=args.max)
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0
    an = r["modus"] == linkabruf.MODUS_AN
    print(f"Link-Abruf: {'AN' if an else 'AUS (nur Probelauf, kein Link wird geoeffnet)'}")
    print(f"Mails ohne Rechnungsanhang: {r['mails']}")
    for grund, n in sorted(r["uebersprungen"].items(), key=lambda kv: -kv[1]):
        print(f"  nicht geholt: {grund}: {n}")
    print(f"ausgewaehlt: {r['kandidaten']}" if an else f"wuerde geholt: {r['kandidaten']}")
    for k in r["liste"]:
        print(f"  {k['lieferant']}  ({k['domain']})  {k['datei']}")
    if an:
        print(f"geholt: {r['geholt']}, schon im Eingang: {r['schon_da']}")
        for grund, n in r["fehler"].items():
            print(f"  Fehler: {grund}: {n}")
    else:
        print("Einschalten erst nach Freigabe durch die IT (RECHNUNG_LINK_ABRUF=an, siehe "
              "IT-FREIGABE-AENDERUNG-LINKABRUF.md).")
    return 0


def _cmd_link_freigabe(args) -> int:
    """Link-Domains je Lieferant freigeben/entziehen/anzeigen."""
    from . import linkabruf

    store = Store(args.data)
    if args.lieferant and args.domain:
        try:
            linkabruf.freigeben(store, args.lieferant, args.domain, entfernen=args.entfernen)
        except ValueError as e:
            print(f"Fehler: {e}")
            return 2
    elif args.lieferant or args.domain:
        print("Bitte --lieferant UND --domain angeben.")
        return 2
    alle = linkabruf.freigaben(store)
    if not alle:
        print("Keine Link-Freigaben.")
    for lid, domains in sorted(alle.items()):
        s = store.supplier(lid)
        print(f"{lid}  {s.name if s else '(Lieferant fehlt)'}: {', '.join(domains)}")
    return 0


def _cmd_lernmaterial(args) -> int:
    """Lernmaterial gezielt aus den Postfaechern holen (lernmaterial.py).
    Gibt nur Anzahlen aus, keine Absender oder Inhalte."""
    import os

    from . import lernmaterial

    # abgeholt.json (ingest) liegt im selben Datenordner wie --data
    os.environ["RECHNUNG_DATA"] = str(Path(args.data))
    store = Store(args.data)
    ziel = Path(args.ziel or args.samples)
    ziel.mkdir(parents=True, exist_ok=True)
    r = lernmaterial.lauf(store, ziel, neu_scannen=args.neu_scannen, je80=args.je80,
                          je95=args.je95, nur_anzeigen=args.nur_anzeigen)
    if args.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0
    print(f"Mails mit Anhang (ohne eigene Hotels): {r['mails_mit_anhang']} von {r['absender']} Absendern")
    print(f"80-%-Gruppe: {r['absender_80']} Absender (je {args.je80}), "
          f"80-95-%-Gruppe: {r['absender_95'] - r['absender_80']} Absender (je {args.je95})")
    print(f"schon geholt (angerechnet): {r['schon_geholt']}")
    print(f"neu ausgewaehlt: {r['ausgewaehlt']} (80 %: {r['holen_80']}, 80-95 %: {r['holen_95']})")
    if args.nur_anzeigen:
        print("Nur angezeigt -- nichts geholt (ohne --nur-anzeigen wird geholt).")
    elif r['ausgewaehlt']:
        print(f"geholt: {r.get('geholt', 0)} Mails, {r.get('dateien', 0)} Dateien im Eingang ({ziel}), "
              f"{r.get('mensch', 0)} an Menschliche Bearbeitung, {r.get('fehler', 0)} Fehler"
              + (f", {r['nicht_eingerichtet']} aus nicht eingerichteten Postfaechern"
                 if r.get('nicht_eingerichtet') else ""))
    else:
        print("Nichts Neues zu holen -- die Auswahl ist schon im Eingang.")
    return 0


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


def _cmd_vorbereiten(args) -> int:
    """Rechnungen im Eingang vorab auslesen (Texterkennung + KI), damit der
    Mensch spaeter ohne Wartezeit nur noch prueft und sendet. Auch ohne offenes
    Programmfenster; nichts wird bestaetigt, gesendet oder gelernt."""
    from .vorbereitung import vorbereiten_lauf

    store = Store(args.data)
    b = vorbereiten_lauf(store, args.samples, abholen=not args.ohne_abholen,
                         zeitgrenze=args.zeit, erneut=args.erneut, max_dateien=args.max,
                         melde=(lambda t: None) if args.json else print,
                         leerlauf=not args.ohne_leerlauf)
    if args.json:
        print(json.dumps(b, indent=2, ensure_ascii=False))
        return 0
    if b["uebersprungen_laeuft"]:
        return 0
    print(f"Vorbereitet: {b['bereit']} neu, {b['schon_fertig']} waren schon fertig, "
          f"{len(b['fehler'])} Fehler, {b['belegt']} woanders in Arbeit (von {b['dateien']} Dateien).")
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
    res = push_queue(store.root, trockenlauf_aufheben=True)
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
    vorlagen = store.all_templates()
    for t in vorlagen:
        print(f"{t.id:24s} supplier={t.supplier_id:16s} variant={t.variant:10s} "
              f"confirmations={t.confirmations} secured={t.secured}"
              + (" VERALTET" if t.stale else ""))
        for f in t.fields:
            kind = "anchor" if f.anchor else ("abs" if f.abs_frac else "-")
            print(f"   {f.name:16s} {f.value_type:8s} {kind}")
        if t.line_items and t.line_items.columns:
            print("   Positionstabelle: ja ("
                  + ", ".join(c.name for c in t.line_items.columns)
                  + (", mehrseitig" if t.line_items.folgeseiten else "") + ")")
        else:
            print("   Positionstabelle: nein")
    # Zusammenfassung (Aaron 16.11.2026: "hat das Programm aus der manuellen
    # Tabellenerstellung schon was gelernt?") -- nur Zahlen, keine Inhalte.
    aktiv = [t for t in vorlagen if not t.stale]
    mit = sum(1 for t in aktiv if t.line_items and t.line_items.columns)
    print()
    print(f"Vorlagen (aktiv): {len(aktiv)}, davon mit Positionstabelle: {mit}")
    print(f"Spaltenwoerterbuch: {len(store.spaltenwoerter())} Kopfwoerter")
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


def _cmd_nachlernen(args) -> int:
    """Vorlagen aus bereits bestaetigten Rechnungen neu lernen (nachlernen.py);
    ohne --ausfuehren nur anzeigen."""
    from . import nachlernen

    try:
        b = nachlernen.laufen(Store(args.data), ausfuehren=args.ausfuehren,
                              operator=args.operator or "", lieferant=args.lieferant)
    except ValueError as e:
        print(f"Fehler: {e}")
        return 2
    if args.json:
        print(json.dumps(b, indent=2, ensure_ascii=False))
        return 0
    if not b["rechnungen"] and not b["uebersprungen"]:
        print("Noch keine bestaetigten Rechnungen.")
        return 0
    print(nachlernen.text(b))
    return 0


def _cmd_testsatz(args) -> int:
    """Testsatz aus bestaetigten Rechnungen im Trockenlauf pruefen
    (testsatz.py): je Feld richtig/falsch/fehlt, Positionen, Zeit, Vergleich
    mit dem letzten Lauf."""
    from . import testsatz

    store = Store(args.data)
    try:
        b = testsatz.pruefen(store, speichern_=not args.kein_bericht,
                             lieferant=args.lieferant, variante=args.variante,
                             nur_ohne_ki=args.ohne_ki, blind=args.blind,
                             max_faelle=args.max)
    except (ValueError, NotImplementedError) as e:
        print(f"Fehler: {e}")
        return 2
    if args.ausgabe:
        Path(args.ausgabe).write_text(json.dumps(b, indent=2, ensure_ascii=False), "utf-8")
    if args.json:
        print(json.dumps(b, indent=2, ensure_ascii=False))
        return 0
    if not b["anzahl"] and not b["uebersprungen"]:
        print("Noch keine bestaetigten Rechnungen im Testsatz.")
        return 0
    print(testsatz.text(b))
    return 0


def _cmd_fehlerstatistik(args) -> int:
    """Je Lieferant x Feld: Anteil korrigierter Rechnungen, Trend 4 Wochen,
    lohnendste Verbesserungen (eventlog.fehlerstatistik)."""
    from .eventlog import fehlerstatistik

    data = fehlerstatistik(Store(args.data))
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    if not data["zeilen"]:
        print("Noch keine Korrekturen.")
        return 0
    if data["lohnend"]:
        print("Lohnendste Verbesserungen:")
        for r in data["lohnend"]:
            print(f"  {r['text']}")
        print()
    pct = lambda v: "-" if v is None else f"{100 * v:.0f} %"  # noqa: E731
    print(f"{'Lieferant':28s} {'Feld':18s} {'korr.':>9s} {'Anteil':>7s} "
          f"{'4 Wochen':>8s} {'davor':>7s}  Trend")
    for r in data["zeilen"]:
        who = r["lieferant_name"] or r["lieferant"]
        print(f"{who:28.28s} {r['feld_label']:18.18s} {r['korrigiert']:4d}/{r['rechnungen']:<4d} "
              f"{pct(r['anteil']):>7s} {pct(r['anteil_4_wochen']):>8s} {pct(r['anteil_davor']):>7s}"
              f"  {r['trend'] or ''}")
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
    rows = modus.lieferanten_fortschritt(store)
    frei = sum(1 for r in rows if r["frei"])
    bereit = sum(1 for r in rows if r["bereit"] and not r["frei"])
    if frei or bereit:
        print(f"  Lieferanten ohne Klick: {frei}, bereit zur Freigabe: {bereit} "
              f"(Einzelheiten: rechnung autonomie)")
    return 0


def _cmd_autonomie(args) -> int:
    """Autonomie je Lieferant anzeigen, freigeben, entziehen (modus.py)."""
    from . import modus

    store = Store(args.data)
    aendern = args.freigeben or args.entziehen or args.alle_bereiten
    if aendern and not args.operator:
        print("Fehler: Freigeben/Entziehen nur mit --operator NAME (wird im Audit-Log vermerkt).")
        return 2
    meldungen, fehler = [], 0
    ids = list(args.freigeben or [])
    if args.alle_bereiten:
        ids += [r["lieferant"] for r in modus.lieferanten_fortschritt(store)
                if r["bereit"] and not r["frei"] and r["lieferant"] not in ids]
    for sid in ids:
        try:
            modus.lieferant_freigeben(store, sid, args.operator, args.grund or "")
            meldungen.append(f"freigegeben: {sid}")
        except ValueError as e:
            meldungen.append(f"Fehler: {e}")
            fehler += 1
    for sid in args.entziehen or []:
        ok = modus.lieferant_entziehen(store, sid, args.operator, args.grund or "von Hand")
        meldungen.append(f"entzogen: {sid}" if ok else f"war nicht freigegeben: {sid}")
    rows = modus.lieferanten_fortschritt(store)
    if args.json:
        print(json.dumps({"meldungen": meldungen, "lieferanten": rows}, indent=2,
                         ensure_ascii=False))
        return 2 if fehler else 0
    for m in meldungen:
        print(m)
    if modus.is_autonomous(store):
        print("Hinweis: der Modus 'autonom' gilt programmweit -- alle Lieferanten laufen "
              "ohne Klick.")
    print(modus.lieferanten_text(rows))
    return 2 if fehler else 0


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
    for r in (bericht.get("fehlerstatistik") or {}).get("lohnend", [])[:5]:
        print(f"  lohnend: {r['text']}")
    ll = bericht.get("leerlauf") or {}
    if (lauf := ll.get("blindtest_laufend")):
        print(f"  Blindtest im Leerlauf: {lauf['erledigt']} erledigt, {lauf['offen']} offen")
    if (bt := ll.get("blindtest")):
        q, qv = bt.get("trefferquote"), bt.get("trefferquote_vorher")
        print(f"  Letzter Blindtest ({(bt.get('erstellt') or '')[:10]}, {bt.get('anzahl')} Rechnungen): "
              f"Trefferquote {q if q is not None else '-'} %"
              + (f" (vorher {qv} %)" if qv is not None else "")
              + f", komplett richtig {bt.get('komplett_quote')} %, ohne KI-Deckel "
              f"{bt.get('sicher_ohne_ki_deckel')} durch / {bt.get('davon_fehlerhaft')} fehlerhaft, "
              f"mit Gegenpruefung {bt.get('sicher_mit_gegenpruefung')} / "
              f"{bt.get('gegenpruefung_fehlerhaft')}")
        for s in bt.get("schlechter") or []:
            print(f"    schlechter: {s}")
    for name, h in bericht["gesundheit"].items():
        mark = {True: "ok", False: "FAIL", None: "-"}[h["ok"]]
        print(f"  [{mark:4s}] {name}: {h['detail']}")
    return 0


def _cmd_web(args) -> int:
    import uvicorn

    from .web.server import create_app

    from . import instanz

    store = Store(args.data)
    try:
        sperre = instanz.Sperre(store.root, port=args.port).nehmen()
    except instanz.LaeuftBereits as e:
        print(f"FEHLER: {e}")
        return 3
    try:
        app = create_app(store, Path(args.samples))
        print(f"Training-UI:  http://127.0.0.1:{args.port}")
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    finally:
        sperre.freigeben()
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

    lm = sub.add_parser("lernmaterial",
                        help="Lernmaterial gezielt aus den Postfaechern holen (80/15-Regel: je 3 "
                             "Rechnungen der Lieferanten mit 80 %% der Mails, je 1 bis 95 %%)")
    lm.add_argument("--ziel", help="Eingangsordner (Standard: --samples)")
    lm.add_argument("--neu-scannen", action="store_true",
                    help="Postfaecher neu durchsehen (sonst gespeicherter Scan, falls juenger als 7 Tage)")
    lm.add_argument("--je80", type=int, default=3, help="Rechnungen je Lieferant der 80-%%-Gruppe")
    lm.add_argument("--je95", type=int, default=1, help="Rechnungen je Lieferant der 80-95-%%-Gruppe")
    lm.add_argument("--nur-anzeigen", action="store_true", help="nur Auswahl zeigen, nichts holen")
    lm.add_argument("--json", action="store_true")
    lm.set_defaults(func=_cmd_lernmaterial)

    lk = sub.add_parser("link-mails",
                        help="Mails ohne Rechnungsanhang zaehlen: Download-Link, Portal, kein "
                             "Link (nur lesend, oeffnet keinen Link)")
    lk.add_argument("--min-mails", type=int, default=2,
                    help="Link-Domains erst ab so vielen Mails einzeln zeigen")
    lk.add_argument("--json", action="store_true")
    lk.set_defaults(func=_cmd_link_mails)

    la = sub.add_parser("link-abruf",
                        help="Rechnungen hinter Links holen -- solange RECHNUNG_LINK_ABRUF nicht "
                             "'an' ist, nur Probelauf (kein Link wird geoeffnet)")
    la.add_argument("--ziel", help="Eingangsordner (Standard: --samples)")
    la.add_argument("--max", type=int, default=20, help="hoechstens so viele Abrufe je Lauf")
    la.add_argument("--json", action="store_true")
    la.set_defaults(func=_cmd_link_abruf)

    lf = sub.add_parser("link-freigabe",
                        help="Link-Domain fuer einen Lieferanten freigeben (ohne Angaben: anzeigen)")
    lf.add_argument("--lieferant", help="Lieferanten-ID (siehe 'rechnung suppliers')")
    lf.add_argument("--domain", help="z.B. rechnung.lieferant.de")
    lf.add_argument("--entfernen", action="store_true", help="Freigabe entziehen")
    lf.set_defaults(func=_cmd_link_freigabe)

    vb = sub.add_parser("vorbereiten",
                        help="Eingang vorab auslesen (Postfach abholen, falls eingerichtet) -- "
                             "auch ohne offenes Programmfenster, fuer die Aufgabenplanung")
    vb.add_argument("--ohne-abholen", action="store_true", help="Postfach nicht abholen")
    vb.add_argument("--zeit", type=float, default=600.0,
                    help="Zeitgrenze je Datei in Sekunden (Standard 600)")
    vb.add_argument("--max", type=int, default=0, help="hoechstens N Dateien pro Lauf (0 = alle)")
    vb.add_argument("--erneut", action="store_true", help="auch schon vorbereitete Dateien neu auslesen")
    vb.add_argument("--ohne-leerlauf", action="store_true",
                    help="danach keine Leerlauf-Arbeit (Neu-Lesen nach Lernstand, Blindtest)")
    vb.add_argument("--json", action="store_true")
    vb.set_defaults(func=_cmd_vorbereiten)

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

    nl = sub.add_parser("nachlernen",
                        help="Vorlagen aus bereits bestaetigten Rechnungen neu lernen "
                             "(ohne --ausfuehren nur anzeigen)")
    nl.add_argument("--ausfuehren", action="store_true", help="gelernte Vorlagen speichern")
    nl.add_argument("--operator", help="wer nachlernt (Pflicht bei --ausfuehren, Audit)")
    nl.add_argument("--lieferant", help="nur dieser Lieferant (ID oder Teil des Namens)")
    nl.add_argument("--json", action="store_true")
    nl.set_defaults(func=_cmd_nachlernen)

    ts = sub.add_parser("testsatz",
                        help="Bestaetigte Rechnungen im Trockenlauf neu lesen und je Feld "
                             "richtig/falsch/fehlt messen (Goldstandard)")
    ts.add_argument("--lieferant", help="nur dieser Lieferant (ID oder Teil des Namens)")
    ts.add_argument("--variante", default="A",
                    help="Extraktionsvariante A (ein Rundum-Prompt) | B (Positionen als "
                         "eigener Block) | C (vorgesehen, noch nicht gebaut)")
    ts.add_argument("--ohne-ki", action="store_true",
                    help="nur Testfaelle, die ohne KI auskommen (Vorlage/E-Rechnung); "
                         "mit --blind: alle Testfaelle, aber ohne KI lesen")
    ts.add_argument("--blind", action="store_true",
                    help="Blindtest: jede Rechnung so lesen, als waere ihr Lieferant neu "
                         "(eigene Vorlagen/Gedaechtnis ausgeblendet) -- wie gut liest das "
                         "Programm unbekannte Lieferanten, und waere es ohne Mensch "
                         "fehlerfrei gewesen?")
    ts.add_argument("--max", type=int, default=0,
                    help="hoechstens N Rechnungen, reihum ueber die Lieferanten (0 = alle)")
    ts.add_argument("--kein-bericht", action="store_true",
                    help="Bericht nicht unter <data>/berichte speichern")
    ts.add_argument("--ausgabe", help="Bericht zusaetzlich als JSON in diese Datei schreiben")
    ts.add_argument("--json", action="store_true")
    ts.set_defaults(func=_cmd_testsatz)

    fs = sub.add_parser("fehlerstatistik",
                        help="Welche Felder werden bei welchen Lieferanten wie oft korrigiert "
                             "(Anteil, Trend 4 Wochen, lohnendste Verbesserung)")
    fs.add_argument("--json", action="store_true")
    fs.set_defaults(func=_cmd_fehlerstatistik)

    mo = sub.add_parser("modus",
                        help="Betriebsmodus (testphase/autonom) anzeigen oder umschalten")
    mo.add_argument("modus", nargs="?", choices=["testphase", "autonom"],
                    help="neuer Modus (ohne Angabe: nur anzeigen)")
    mo.add_argument("--operator", help="wer schaltet um (Pflicht beim Umschalten, Audit)")
    mo.add_argument("--grund", help="optionaler Vermerk fuer das Audit-Log")
    mo.add_argument("--json", action="store_true")
    mo.set_defaults(func=_cmd_modus)

    au = sub.add_parser("autonomie",
                        help="Autonomie je Lieferant: anzeigen, bewaehrte Lieferanten ohne "
                             "Klick freigeben, Freigabe entziehen")
    au.add_argument("--freigeben", action="append", metavar="ID",
                    help="diesen (bereiten) Lieferanten freigeben, mehrfach moeglich")
    au.add_argument("--alle-bereiten", action="store_true",
                    help="alle Lieferanten freigeben, die den Richtwert erreicht haben")
    au.add_argument("--entziehen", action="append", metavar="ID",
                    help="Freigabe zuruecknehmen, mehrfach moeglich")
    au.add_argument("--operator", help="wer gibt frei/entzieht (Pflicht, Audit)")
    au.add_argument("--grund", help="optionaler Vermerk fuer das Audit-Log")
    au.add_argument("--json", action="store_true")
    au.set_defaults(func=_cmd_autonomie)

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
