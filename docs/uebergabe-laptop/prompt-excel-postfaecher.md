# Auftrag: Echte Stammdaten aus Excel + ~50 IONOS-Postfächer (Entscheidung Aaron 15.10.2026)

Repo `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app`. Worktree existiert: `/tmp/rw-excel` auf Branch `emmy/excel-postfaecher` (Basis origin/release inkl. "Keine Rechnung"). Venv-Symlink liegt (nie committen). Commit je Baustein, deutsch, "Entscheidung Aaron 15.10.2026", Zeile `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. **Nicht pushen, nie auf release.** Läuft nachts unbeaufsichtigt: keine Rückfragen, selbst entscheiden, im Bericht begründen. Nach jedem Baustein sofort committen.

## Lage
Aaron hat eine **Excel-Liste** (xlsx) mit: allen Kreditoren, unterteilt in Deutschland und Schweiz; allen Mandanten (~50); den Zugangsdaten der IONOS-Postfächer — **jeder Mandant hat ein eigenes Postfach**. Das Layout der Liste kennen wir NICHT (Spaltennamen, ein Blatt oder mehrere Blätter, DE/CH als eigene Blätter oder als Spalte).
**Die echten Daten bleiben ausschließlich auf dem Firmenlaptop (Windows, schwach, ~8 GB RAM).** Hier gibt es keine echten Daten — alles mit synthetischen Excel-Dateien testen (verschiedene plausible Layouts erzeugen, auch unordentliche).

## Aarons Grundsatz (wichtiger als alles andere)
"Wenn das Programm irgendwo nicht sicher ist, soll es nicht raten, sondern mir Bescheid geben, dann kann ich ihm das Richtige sagen, damit die Daten nicht falsch sind."
→ Jede Unsicherheit beim Import wird als offene Frage angezeigt und erst nach Aarons Antwort übernommen. Nichts wird still übernommen, still verworfen oder still „repariert“.

## Lies zuerst
README.md, SPEC-v1.md, `rechnung/importers.py` (bestehender Abbyy-CSV-Import Vendors/BusinessUnits — Felder dort sind das Zielmodell, wiederverwenden), `rechnung/identify.py` (Supplier), `rechnung/billing.py` (BillingAddress = Mandant/Geschäftsbereich), `rechnung/store.py`, `rechnung/config.py` (ImapConfig), `rechnung/ingest.py` (fetch), `rechnung/abholung.py` (Abholer), `rechnung/web/server.py` + `static/index.html`, Startklar-Check.

## 1. Excel-Import mit Spalten-Zuordnung (Web-UI, Reiter "Stammdaten")
- xlsx lesen: prüfen, ob `openpyxl` schon im venv/requirements ist; sonst nur stdlib (xlsx = ZIP + XML, Lesen von sharedStrings/sheet*.xml ist machbar) — keine neue Pflicht-Abhängigkeit, die auf dem Laptop extra installiert werden müsste, außer sie ist schon im Windows-Paket enthalten (prüfen, begründen).
- Datei hochladen → Programm zeigt je Blatt die erkannten Spalten und SCHLÄGT eine Zuordnung vor (Name, Straße, PLZ, Ort, Land, USt-IdNr/UID, Steuernummer, IBAN, BIC, Kreditorennummer, Mandant/Gesellschaft, E-Mail-Adresse, Passwort, Server …) anhand der Spaltenüberschrift (Wortliste DE/EN, erweiterbar). Der Mensch bestätigt oder ändert die Zuordnung je Spalte; unbekannte Spalten = "ignorieren" nur auf ausdrückliche Wahl. Zuordnung pro Datei-Layout merken (nächster Import mit gleichen Überschriften schlägt sie direkt vor).
- Art je Blatt/Zeile: Kreditor DE / Kreditor CH / Mandant / Postfach. Ist das nicht eindeutig (z. B. kein Land, Blattname unklar) → fragen.
- **Vorschau vor dem Übernehmen**: Tabelle aller Zeilen mit Status grün (eindeutig) / gelb (Frage offen) / rot (Fehler). Übernommen wird erst per Knopf, und nur grüne + von Aaron beantwortete Zeilen.
- Gelb/Fragen u. a.: IBAN-Prüfziffer falsch; UID/USt-IdNr-Format falsch (DE: DE+9 Ziffern; CH: CHE-123.456.789 MWST/TVA/IVA); Land passt nicht zu IBAN/UID (DE-Kreditor mit CH-IBAN); Dublette (gleicher Name/UID/IBAN zweimal, oder schon in der Bibliothek mit ANDEREN Werten → Frage "alt oder neu?", nie still überschreiben); Pflichtfeld leer; Mandant-Verweis eines Kreditors nicht auffindbar; PLZ-Format (DE 5-stellig, CH 4-stellig). Jede Frage mit Grund und Antwortmöglichkeiten ("so übernehmen" / "korrigieren auf …" / "Zeile weglassen").
- Bestehende Lieferanten mit Vorlagen/gelerntem Wissen dürfen beim Import nicht verloren gehen (zusammenführen, nicht ersetzen).
- Audit: wer hat wann was importiert/beantwortet.

## 2. Mehrere Postfächer (~50, je Mandant eines)
- ImapConfig von einem auf viele Postfächer erweitern: Liste mit {Mandant, E-Mail/Benutzer, Server (Standard imap.ionos.de:993), Ordner, Erledigt-Ordner}. Die alte Einzelkonfiguration über RECHNUNG_IMAP_* bleibt als "Postfach 1" voll funktionsfähig (bestehende Tests grün).
- **Passwörter**: nie im Klartext in data/ oder Logs. Auf Windows über DPAPI (CryptProtectData per ctypes, stdlib, an den Windows-Benutzer gebunden), auf Linux für Tests ein klar markierter Fallback (z. B. Datei 0600 mit Warnung). Im UI nur "gesetzt/nicht gesetzt", Passwort nie wieder anzeigen. Nach dem Excel-Import Hinweis an Aaron: "Die Passwörter stehen noch im Klartext in der Excel-Datei — Datei löschen oder Passwortspalte leeren."
- Abholung: alle Postfächer reihum (nacheinander, nicht parallel — schwacher Laptop), ein kaputtes Postfach (Login falsch, Server weg) blockiert die anderen nicht, sondern erscheint als rote Zeile im Startklar-Check und im Tagesbericht.
- **Mandant aus dem Postfach**: Jede abgeholte Rechnung trägt den Mandanten ihres Postfachs. Das ist ein starker Hinweis für den Geschäftsbereich — aber: Erkennt die Rechnung selbst (Rechnungsadresse) eindeutig einen ANDEREN Mandanten → nicht raten, Rechnung zurückstellen mit Grund "Postfach Mandant A, Rechnungsadresse Mandant B — bitte entscheiden". Stimmen beide überein → grün. Keine Rechnungsadresse erkannt → Postfach-Mandant als gelber Vorschlag, nicht automatisch.
- Startklar-Check: je Postfach Verbindung + Login + Ordner prüfen (Knopf "alle testen"), Ergebnis als Tabelle.
- Tagesbericht: abgeholte Mails je Postfach.

## Qualität
- Suite grün (`.venv/bin/python -m pytest -q`), auch mit `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1 RECHNUNG_IMAP_USER=x`. Tests für: mehrere Excel-Layouts, jede Frage-Art, keine stille Übernahme, Dubletten/Konflikt mit Bestand, Passwort nie im Klartext gespeichert/geloggt, ein Postfach kaputt → andere laufen, Mandant-Widerspruch → zurückgestellt.
- Windows denken (Pfade, DPAPI, CRLF bei .cmd/.ps1). UI per Playwright (`/tmp/pwv/bin/python`, `/usr/bin/google-chrome`), keine Konsolenfehler, Screenshots nach /tmp/uidemo-excel/ per ATTACH.
- README + SPEC-v1.md: Abschnitt "Excel-Stammdaten und mehrere Postfächer 15.10.2026" mit Kurzanleitung für Aaron (Schritt für Schritt: Excel hochladen → Zuordnung prüfen → Fragen beantworten → übernehmen → Postfächer testen → Excel-Passwörter löschen).

## Abschlussbericht (kurz, deutsch, für Aaron)
Was er morgen auf dem Laptop konkret tun muss (Schritte), welche Fragen das Programm ihm stellen wird, Grenzen, `git log --oneline`.
