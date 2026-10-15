# Auftrag: Nicht-Rechnungen in eigenen Ordner aussortieren (Entscheidung Aaron 14.10.2026)

Repo `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app`. Worktree existiert schon: `/tmp/rw-keine` auf Branch `emmy/keine-rechnung` (Basis origin/release 70208f2), Venv-Symlink liegt (nie committen). Haupt-Checkout nicht anfassen. Commit je Baustein (deutsch, Grund + "Entscheidung Aaron 14.10.2026", Zeile `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`). **Nicht pushen, nie auf release.**

Lies zuerst README.md, SPEC-v1.md, `git log -10`, dann `rechnung/ingest.py` (analyse_mail, _classify_attachment, MAIL_*/ANHANG_*-Gruende, human_items), `rechnung/belegart.py` (BELEGARTEN, ANLAGEN, aus_titel, anlage_aus_titel), `rechnung/human.py` (Ordner "Menschliche Bearbeitung", Begleitdatei .grund.json, erledigt()), `rechnung/anlagen.py`, `rechnung/pipeline.py` (Stellen, an denen ein Ergebnis an human.py geht), `rechnung/eventlog.py`, den Tagesbericht und `rechnung/web/server.py`/`static/index.html`.

## Aarons Vorgabe
"Dokumente, die keine Rechnungen sind, werden in einen Ordner aussortiert." Heute landen sie gemischt mit echten Problemrechnungen in "Menschliche Bearbeitung" -- dort sollen nur noch Rechnungen liegen, an denen ein Mensch etwas tun muss.

## Grundsaetze (wichtiger als jeder Sonderfall)
- Nur woertliche, nachvollziehbare Regeln, KEINE KI-Entscheidung.
- Eine echte Rechnung darf NIE im Aussortier-Ordner landen. Im Zweifel bleibt alles wie heute ("Menschliche Bearbeitung"). Fehlalarm-Freiheit vor Abdeckung.
- Nie loeschen, nie still: Datei + Begleitdatei `<datei>.grund.json` (Grund, erkannte Beschriftung, Absender/Betreff bei Mails, Zeitpunkt), Ereignis im Ereignisprotokoll, Zaehler im Tagesbericht ("N Dokumente als Nicht-Rechnung aussortiert", mit Liste). Geht nicht an JobRouter.
- Schalter `RECHNUNG_KEINE_RECHNUNG_ORDNER=0` -> exakt altes Verhalten. Ordner `RECHNUNG_KEINE_RECHNUNG`, Standard `<data>/keine-rechnung/` (normaler Dateiordner wie menschlich/, im Explorer sichtbar). Windows-Pfade denken.

## 1. Was aussortiert wird (Postfach)
- Mail OHNE jeden Anhang und OHNE Rechnungs-Hinweis im Text (heutiger Grund MAIL_OHNE_ANHANG, sofern es nicht "Rechnung im Mailtext" oder "nur Link im Mailtext" ist) -> Mail als .eml in keine-rechnung/.
- Mail, deren Anhaenge ALLE eindeutig als Nicht-Beleg beschriftet sind (belegart.ANLAGEN: AGB, Lieferschein, Auftragsbestaetigung, Datenblatt, ... -- ggf. Liste um Angebot, Newsletter, Preisliste, Katalog, Werbung, Kontoauszug, Vertrag erweitern, nur woertlich und mit Tests) und KEIN Beleg-Anhang -> Anhaenge + .eml dorthin.
- BLEIBT bei "Menschliche Bearbeitung" wie heute: Rechnung im Mailtext, nur Link, verschluesselte Mail/PDF/ZIP, defekte ZIPs, "Anhang nicht zuzuordnen" (unbeschriftet = koennte Rechnung sein; heute schon normal verarbeitet), Mahnung/Gutschrift/Storno usw. (sind Belegarten).
- Anlagen, die neben einem echten Beleg in derselben Mail stecken, bleiben wie heute als Anlage am Beleg (Baustein 8) -- nicht aussortieren.

## 2. Was aussortiert wird (Pipeline, z. B. Scan im Eingangsordner)
Ein verarbeitetes Dokument geht nur dann nach keine-rechnung/, wenn ALLES zutrifft: Titelbereich Seite 1 traegt eine bekannte Nicht-Beleg-Beschriftung (anlage_aus_titel), KEINE Belegart im Titel, keine E-Rechnung (XML), kein QR-Zahlteil, und es wurde weder eine Rechnungsnummer hinter einem Rechnungs-Label noch ein Bruttobetrag hinter einem Summen-Label gefunden. Sonst normaler Weg. Tests mit synthetischen Dokumenten (synth.py erweitern): Lieferschein, Auftragsbestaetigung, Angebot -> aussortiert; Rechnung mit "Lieferschein-Nr." im Text, Rechnung + Lieferschein in einem PDF, Rechnung ohne Titel -> NICHT aussortiert.

## 3. Bedienung
- Web-UI: eigener Bereich/Reiter "Keine Rechnung" (Liste: Datei, Grund, Absender, Datum; Vorschau wie bei menschlich).
- Knopf "Doch eine Rechnung" -> Datei zurueck in den Eingang, wird normal verarbeitet, Ereignis protokolliert (das ist die Rueckmeldung, dass die Regel falsch lag -- im Tagesbericht/Qualitaet als "Fehlsortierung" zaehlen).
- Knopf "Keine Rechnung" in der Pruefmaske einer offenen Rechnung (Mensch sortiert von Hand aus, mit Bestaetigungsdialog) -> verlaesst menschlich/, landet in keine-rechnung/ mit Grund "von Hand aussortiert (<Bearbeiter>)".
- Optional "Erledigt/archivieren" je Eintrag (nach keine-rechnung/erledigt/), nie loeschen.

## Qualitaet
- Suite gruen (`.venv/bin/python -m pytest -q`), auch mit `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1 RECHNUNG_IMAP_USER=x`. Positive UND negative Tests je Regel. `.ps1`/`.cmd` nicht anfassen ausser noetig (dann CRLF).
- UI per Playwright pruefen (`/tmp/pwv/bin/python`, `/usr/bin/google-chrome`), keine Konsolenfehler, Screenshots nach /tmp/uidemo-keine/ per ATTACH.
- README + SPEC-v1.md: Abschnitt "Nicht-Rechnungen aussortieren 14.10.2026" mit Schaltern.

## Abschlussbericht (kurz, deutsch)
Was landet jetzt wo (Tabelle Grund -> Ordner vorher/nachher), Schalter, Tests, bekannte Grenzen/Fehlsortier-Risiken, `git log --oneline`.
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