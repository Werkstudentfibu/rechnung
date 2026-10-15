# Nachtrag zu "Nicht-Rechnungen aussortieren" (Entscheidung Aaron 14.10.2026, 18:14)

Worktree /tmp/rw-keine, Branch emmy/keine-rechnung -- dort weiterarbeiten, auf deinen eigenen Commits aufbauen. Gleiche Regeln (Commit deutsch, Co-Authored-By-Zeile, nicht pushen, nie release).

Aaron: "Das reicht im Programm, das wird menschlich bearbeitet, aber man soll sie loeschen koennen."
- Kein Verschieben im IONOS-Postfach (nichts am IMAP-Verhalten aendern).
- Im Bereich "Keine Rechnung" je Eintrag ein Knopf "Loeschen" (plus Mehrfachauswahl "Ausgewaehlte loeschen") mit Bestaetigungsdialog. Loeschen entfernt Datei + Begleitdatei endgueltig aus keine-rechnung/ (das ersetzt die fruehere Vorgabe "nie loeschen" -- gilt NUR fuer diesen Ordner, nirgends sonst).
- Protokoll bleibt: Ereignis "nicht-rechnung-geloescht" mit Dateiname, sha256, Grund, Bearbeiter, Zeitpunkt im Ereignisprotokoll/Audit (ohne Inhalt). Im Tagesbericht mitzaehlen.
- Sicherheitsnetz: Loeschen nur fuer Dateien, die tatsaechlich in keine-rechnung/ liegen (Pfad pruefen, kein Path-Traversal ueber die API, Test dafuer). Eine Datei mit derselben sha256 im Archiv/als verarbeitete Rechnung wird nicht beruehrt.
- Falls schon gebaut: "Erledigt/archivieren" darf bleiben, Loeschen ist die Hauptaktion.
- Tests (inkl. Traversal, Bestaetigung, Protokoll), Suite gruen auch mit RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1 RECHNUNG_IMAP_USER=x, Playwright-Screenshot vom Loesch-Dialog nach /tmp/uidemo-keine/ per ATTACH. README/SPEC-Abschnitt ergaenzen.
Abschlussbericht kurz, deutsch.