# Auftrag: Fünf Qualitäts-Bausteine (Entscheidung Aaron, 10.10.2026: "alle 5 Punkte")

Repo: `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app` (Python, FastAPI-UI
`rechnung/web/static/index.html`, Abbyy-ähnliche Prüfmaske). Venv `.venv/bin/python`,
Tests `.venv/bin/python -m pytest -q -p no:cacheprovider`.

Ein Vorgänger hat auf Branch `emmy/verstehen-positionsmodus` gerade gebaut: `rechnung/summen.py` (Beträge aus dem
Kontext) und den Positionsmodus je Lieferant. Lies `git log -5` und dessen Diffs. Lege von dort den Branch
`emmy/qualitaet` an und committe je Baustein einzeln. **Nicht pushen, nie auf `release`.**

Vorhandenes zuerst lesen und **wiederverwenden statt neu bauen**: `rechnung/eventlog.py` (Ereignisprotokoll inkl.
`report()` mit Korrektur-Trend = Fehlerbericht), `rechnung/dubletten.py`, `rechnung/validate.py` (Checks),
`rechnung/shadow.py` (Schattenbetrieb/Statistik), `rechnung/learn.py`, `rechnung/store.py` (Archiv bestätigter
Rechnungen, Korrekturen je sha256), `rechnung/einvoice.py`, `rechnung/messen.py`, `rechnung/cli.py`.

Grundsätze des Projekts: kein Raten; Unsicheres → Mensch; Korrektur des Menschen gewinnt; alles lokal, nichts ins Netz;
deutsche Kommentare mit Begründung und Datum ("Aaron 10.10.2026"); README/SPEC-v1.md je Baustein kurz ergänzen.

## 1. Testsatz aus bestätigten Rechnungen (Goldstandard) — zuerst bauen
- Jede **bestätigte** Rechnung (Original-PDF/XML im Archiv + vom Menschen bestätigte Feldwerte inkl. Positionen und
  Steueraufstellung) ist ein Testfall. Prüfe, wo das Archiv/Bestätigungen schon liegen; ggf. beim Bestätigen
  zusätzlich `data/testsatz/<sha256>.json` (Soll-Werte) schreiben. Nichts doppelt speichern, was schon da ist.
- CLI `rechnung testsatz [--json] [--lieferant X] [--variante A|B|C]`: verarbeitet alle Testfälle im Trockenlauf
  (nichts speichern, keine Entwürfe, keine Exporte; Zeitgrenze wie konfiguriert) und misst je Feld: richtig / falsch /
  fehlt, plus Positionen (Zeilen gefunden/korrekt) und Zeit je Rechnung (Median/P90). Vergleich normalisiert
  (Beträge ±0,01, Datum ISO, Rechnungsnummer ohne Leerzeichen).
- Ergebnis als Bericht + JSON in `data/berichte/testsatz_<datum>.json`; Vergleich mit dem letzten Lauf ("schlechter
  geworden bei: …").
- Ausbaubar für den geplanten Varianten-Vergleich A (eine große KI-Anfrage) / B (Blöcke) / C (Überblick + Blöcke):
  saubere Stelle vorsehen, an der die Extraktionsvariante gewählt wird. **Die Varianten B/C selbst baust du NICHT**,
  nur die Messbarkeit (Parameter wird durchgereicht, unbekannte Variante = Fehler).
- Selbst-Update-Schutz: In `rechnung/selbstupdate.py` (läuft vor jedem Laptop-Update Tests) optional nach den Tests
  den Testsatz laufen lassen, falls ≥ 5 Testfälle vorhanden: sinkt die Gesamt-Trefferquote gegenüber dem letzten
  gespeicherten Lauf um mehr als 2 Prozentpunkte → Update zurückrollen mit Grund. Abschaltbar
  (`RECHNUNG_UPDATE_TESTSATZ=0`). Laufzeit beachten (KI auf dem Laptop ist langsam): standardmäßig nur Testfälle, die
  ohne KI auskommen (Vorlage/E-Rechnung), KI-Fälle nur mit `RECHNUNG_UPDATE_TESTSATZ=voll`.
- UI: im Reiter "Journal" oder neuer Reiter "Qualität" die letzte Testsatz-Auswertung anzeigen + Knopf "Testsatz jetzt
  prüfen" (läuft im Hintergrund, Fortschritt anzeigen).

## 2. Lieferanten-Gedächtnis + Plausibilität (inkl. Datumsprüfung)
- Aus den bestätigten Rechnungen je Lieferant ein Profil ableiten (nicht von Hand pflegen): übliche Steuersätze,
  Währung, Betragsspanne (Brutto Median, min/max), Muster der Rechnungsnummer (Länge, Ziffern/Buchstaben-Schema,
  Präfix, fortlaufend steigend?), übliches Zahlungsziel. Erst ab 3 bestätigten Rechnungen aktiv.
- Neue Checks (in `validate.py`/eigenem Modul, im UI als rote Felder + Grund, wie die übrigen Prüfungen):
  - Betrag außerhalb der üblichen Spanne (z. B. > 3× Maximum oder < ⅓ Minimum) → Hinweis/rot.
  - Rechnungsnummer passt nicht zum gelernten Muster (häufiger OCR/KI-Lesefehler) → rot.
  - Rechnungsnummer kleiner/gleich einer schon bestätigten bei fortlaufendem Muster → Hinweis.
  - Steuersatz, den der Lieferant nie verwendet → Hinweis.
  - **Datum** (immer, auch ohne Profil): Rechnungsdatum nicht in der Zukunft (Toleranz 1 Tag), nicht älter als 1 Jahr
    (Hinweis, nicht rot), Fälligkeit nicht vor Rechnungsdatum, Jahreszahl-Dreher (2062 statt 2026) → rot.
- Diese Checks dürfen im Schattenbetrieb eine Rechnung zum Menschen schicken, wenn rot (wie andere rote Checks).

## 3. Pflichtangaben nach § 14 Abs. 4 UStG (Hinweis-Prüfung)
- Prüfen und als eigene Prüfgruppe anzeigen: vollständiger Name+Anschrift des Leistenden und des Empfängers,
  Steuernummer ODER USt-IdNr des Leistenden, Ausstellungsdatum, Rechnungsnummer, Menge/Art der Lieferung bzw.
  Umfang/Art der Leistung (Positionen oder Bezeichnung vorhanden), Zeitpunkt/Zeitraum der Leistung (Leistungsdatum
  oder Hinweis "Leistungsdatum = Rechnungsdatum"), Entgelt nach Steuersätzen aufgeschlüsselt, Steuersatz + Steuerbetrag
  bzw. Hinweis auf Steuerbefreiung/Reverse Charge.
- Kleinbetragsrechnung (≤ 250 € brutto, § 33 UStDV) mit reduzierten Pflichten berücksichtigen. Schweizer Rechnungen
  (CHF/CH) nach MWSTG Art. 26 (UID mit "MWST", Leistungsdatum, Satz) statt UStG — kurz, nicht perfekt.
- Nur **Hinweis-Prüfung** (gelb), kein Rückstellgrund, solange Aaron nichts anderes entscheidet; textuell begründet
  ("fehlt: Leistungsdatum"). Erkennung per Text/Feldern, ohne neue KI-Aufrufe. Neues Feld `leistungsdatum`
  (date, optional) falls nötig — dann in STANDARD_FIELDS + Abbyy-Maske unter "Sonstige Daten" ergänzen.

## 4. E-Rechnungen bevorzugen (Übersicht + Anschreiben)
- Auswertung: welche Lieferanten (bestätigt) schicken PDF/Scan statt E-Rechnung (ZUGFeRD/XRechnung), sortiert nach
  Anzahl Rechnungen und Korrekturaufwand (aus eventlog). CLI `rechnung e-rechnung-kandidaten` + Anzeige im
  Qualitäts-Reiter.
- Vorlage eines kurzen, sachlichen Anschreibens an Lieferanten ("Bitte senden Sie Rechnungen künftig als E-Rechnung
  im Format ZUGFeRD/XRechnung an <Adresse>"), als Textdatei/Abschnitt in README, mit Platzhaltern. Nichts versenden.
- Rechtlicher Hintergrund im Text nur knapp und korrekt: Empfangspflicht für E-Rechnungen im inländischen B2B seit
  1.1.2025; Übergangsfristen für den Versand bis Ende 2026 bzw. 2027 (Vorjahresumsatz ≤ 800.000 €). Keine weiteren
  Rechtsaussagen erfinden.

## 5. Fehlerstatistik je Lieferant und Feld
- Auf `eventlog.report()` aufbauen: Tabelle "welche Felder werden bei welchen Lieferanten wie oft korrigiert"
  (Anteil korrigierter Rechnungen je Lieferant×Feld, Trend letzte 4 Wochen), plus Top-Liste "lohnendste
  Verbesserung" (z. B. "Lieferant X: Rechnungsnummer in 6 von 8 Rechnungen korrigiert → Vorlage anlernen").
- Anzeige im Qualitäts-Reiter; CLI-Ausgabe für den Nachtlauf-Bericht falls dort sinnvoll.

## Qualität
- Je Baustein Tests (inkl. Grenzfälle). Ganze Suite grün, zusätzlich mit Laptop-Umgebung grün:
  `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1 RECHNUNG_IMAP_USER=x .venv/bin/python -m pytest -q -p no:cacheprovider`
- `.ps1`/`.cmd` nicht anfassen (CRLF, `test_windows.py`).
- UI: keine Fehler in der Browser-Konsole; neue Reiter im bestehenden Stil (Tannengrün #1c7550 Kopfzeile).

## Abschlussbericht (letzte Ausgabe)
Deutsch, kurz: je Baustein was gebaut wurde (Dateien), beide Testläufe (Zahlen), Commit-Hashes, Annahmen/offene
Punkte, was nur mit echten Daten geprüft werden kann.
