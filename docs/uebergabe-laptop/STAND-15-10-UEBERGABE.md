# Übergabe-Stand 15.10.2026 -- Weiterarbeit auf dem Firmenlaptop über Langdock

Diese Notiz ist eine Orientierungshilfe, kein Ersatz für `README.md`/`SPEC-v1.md` (die beiden bleiben die
eigentliche, laufend gepflegte Doku und enthalten mehr Detail). Grund für diesen Ordner: Aaron entwickelt ab
sofort direkt auf dem Firmenlaptop weiter (über Langdock/Claude Code, nicht mehr über den privaten Server),
weil die echten Daten (Kreditoren/Mandanten/IONOS-Zugangsdaten) ohnehin nur dort liegen dürfen. Die serverseitige
automatische Nachtkette (`rechnung-nacht-1510.timer`) wurde am 15.10. gestoppt, damit sich niemand mehr mit den
hier beschriebenen Branches in die Quere kommt.

## Wie weiterarbeiten
1. `git fetch origin`, dann den gewünschten Branch auschecken -- aktuell am aktivsten: `emmy/excel-postfaecher`
   (siehe unten).
2. Claude Code über Langdock statt privatem Abo laufen lassen: `~/.claude/settings.json` mit
   `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` auf Langdocks Anthropic-Endpunkt zeigen (Details hat Aaron schon
   von Emmy bekommen; siehe Langdock-Doku "Connect your tools > Claude Code").
3. README.md + SPEC-v1.md zuerst lesen (das tut jeder Worker-Auftrag hier auch als ersten Schritt).
4. Die `prompt-*.md`-Dateien in diesem Ordner sind die Original-Aufträge, mit denen die jeweiligen Branches
   gebaut wurden -- gute Referenz, wenn unklar ist, WARUM ein Branch etwas so macht wie es macht.

## Branch-Übersicht (Stand 15.10.2026, alle jetzt auf GitHub)

**Aktiv / in Arbeit:**
- `emmy/excel-postfaecher` -- Baustein 4/4 fertig: Web-API für Excel-Stammdaten-Import + Postfach-Verwaltung.
  Entscheidungen zu den echten Spaltenköpfen siehe `echte-spaltenkoepfe-15-10.md` (Werte dort anonymisiert,
  Strukturen/Entscheidungen echt). Basis: `origin/release` + "Keine Rechnung"-Ordner-Feature.
- `emmy/uebungskorpus` / `emmy/hausregeln` / `emmy/datenlernen` -- waren die nächsten 3 Schritte der (jetzt
  gestoppten) Nachtkette, alle von `emmy/excel-postfaecher` abwärts gestapelt. Jeweiliger Auftrag in
  `prompt-nacht-korpus.md`, `prompt-nacht-regeln.md`, `prompt-nacht-daten.md`.
- `emmy/keine-rechnung` -- "Keine Rechnung"-Ordner-Feature, Doku-Nacharbeit (README/SPEC).
- `emmy/review-1510` -- Review-Stand 15.10. des "Keine Rechnung"-Features (820 Tests grün).

**Review-/Kandidaten-Branches (möglicherweise schon in `release` enthalten -- vor Weiterarbeit `git log` gegen
`origin/release` prüfen):**
- `emmy/release-kandidat`, `emmy/review-1410`

**Einzelthemen, noch nicht gemerged (jeweils ein Auftrag/Befund, Commit-Nachricht beschreibt den Stand):**
- `emmy/abbyy-maske` -- Prüfmaske wie Abbyy FlexiCapture nachgebaut.
- `emmy/brutto-netto`, `emmy/felder-raus`, `emmy/rechte-seite`, `emmy/schweiz`, `emmy/tempo`,
  `emmy/kleines-modell`, `emmy/kopf-datum`, `emmy/startklar-positionen`, `emmy/tabelle-bedienung`,
  `emmy/qualitaet`, `emmy/lieferant-je-adresse`, `emmy/verstehen-positionsmodus`, `emmy/stammdaten` --
  jeweils einzelne Befunde/Bausteine von Aaron, siehe Commit-Message für Kontext und zugehörige `prompt-*.md`
  für den vollen Auftrag.

## Wichtigste Leitplanke (steht auch in jedem Auftrag, aber zur Erinnerung)
Aarons Grundsatz: "Wenn das Programm irgendwo nicht sicher ist, soll es nicht raten, sondern mir Bescheid geben,
dann kann ich ihm das Richtige sagen, damit die Daten nicht falsch sind." -- jede Unsicherheit beim Import/Verarbeiten
wird als offene Frage angezeigt, nie still übernommen/verworfen/"repariert".

## Was NICHT hierher kopiert wurde
- Rohe Worker-Logs und die Server-Orchestrierung (`kette-*.sh`, `spawn-claude-worker.sh`) -- das war
  Infrastruktur für die automatischen Nacht-Läufe auf dem Server, auf dem Laptop arbeitest du interaktiv,
  die brauchst du nicht.
- Die echte Excel-Datei mit Kreditoren/Mandanten/IONOS-Zugangsdaten -- die war nie auf dem Server und bleibt
  es auch nicht.

## Nachtrag 19:12 — Abschlussberichte ergänzt
Aaron wollte zusätzlich alles, was eventuell noch brauchbar ist. Dazu `abschlussberichte/` mit den 5
Worker-Berichten, die echten Inhalt hatten (reine Limit-Fehler-Logs ohne Inhalt blieben draußen): jeweils
der volle Abschlussbericht des Workers inkl. geänderter Dateien/Tests, als Referenz zu den gleichnamigen,
noch nicht gemergten Branches. Server-Orchestrierung (`kette-*.sh`, PIDs) bleibt draußen -- die ist
Linux/systemd-spezifisch und auf dem Laptop nicht lauffähig, nicht nur "nicht nötig".
