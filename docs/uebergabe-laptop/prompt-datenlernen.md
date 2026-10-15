# Auftrag: Aus der Masse bestätigter Rechnungen lernen (3 Bausteine, Entscheidung Aaron 12.10.2026)

Repo `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app`. Eigener Worktree:
`git worktree add /tmp/rw-daten -b emmy/datenlernen emmy/tabelle-bedienung` (Vorgänger: Tabelle lernen, Rahmen,
Zeilenprüfung, Rangfolge des Wissens). Haupt-Checkout nicht anfassen. Venv:
`/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app/.venv` (Symlink, vor Commit entfernen).
Commit je Baustein. **Nicht pushen, nie auf `release`.**

Lies zuerst `git log -10` + Diffs der Vorgänger-Branches (emmy/kleines-modell, emmy/tabelle-bedienung), dann
`rechnung/semantic.py` (Prompts, Varianten, Cache, Beleg-Pflicht), `rechnung/testsatz.py` (Soll-Werte bestätigter
Rechnungen unter data/testsatz), `rechnung/eventlog.py` (Korrekturen inkl. Programmwert vorher, Herkunft je Feld),
`rechnung/lieferantenprofil*`/Plausibilität (Lieferanten-Gedächtnis), `rechnung/pipeline.py` (CONF_MIN, Status),
`rechnung/shadow.py`, `rechnung/modus.py` (Testphase→autonom), `rechnung/suggest.py` (ähnliche Lieferanten).

Grundsätze: alles lokal; kein Raten; Korrektur des Menschen gewinnt; Wissen desselben Lieferanten vor übertragenem
(Rangfolge des Vorgängers wiederverwenden, nicht neu erfinden); Laptop ohne GPU, ~8 GB RAM, qwen2.5:1.5b.

## 1. Eigene Beispiele für die KI (Retrieval-Few-Shot)
- Wird die KI zu einer Rechnung gefragt (alle Varianten/Blöcke), sucht das Programm unter den BESTÄTIGTEN Rechnungen
  (data/testsatz bzw. Archiv) die ähnlichste: erst derselbe Lieferant (neueste zuerst), sonst Layout-Familie /
  ähnlicher Kopf- bzw. Tabellenaufbau (vorhandene Ähnlichkeit aus suggest.py/tabelle.py nutzen). Davon ein kurzer
  Ausschnitt + die bestätigten Soll-Werte als EIN Beispiel in den Prompt ("Beispiel: Text … → Antwort {…}").
- Ausschnitt knapp halten (Laptop-Tempo): nur der für den Block relevante Teil, Gesamtprompt so, dass `num_ctx`
  höchstens eine Stufe steigt. Kein Beispiel, wenn nichts ähnlich genug (Schwelle) — dann wie bisher.
- Beispiele dürfen nie Werte "durchreichen": Beleg-Pflicht im aktuellen Text bleibt; ein Wert, der nur im Beispiel
  steht, wird verworfen (Test dafür!).
- Schalter `RECHNUNG_KI_BEISPIELE=1` (Standard an, wenn ≥ 1 bestätigte Rechnung). Messen mit dem Testsatz-Werkzeug
  (leave-one-out: Rechnung X wird mit Beispielen aus den ANDEREN bestätigten Rechnungen gelesen): mit vs. ohne Beispiel,
  je Feld + Positionen + Zeit. Synthetische Lieferanten mit je ≥ 3 Rechnungen erzeugen (synth.py, Text + Scan), falls
  keine echten vorhanden.

## 2. Selbstkalibrierte Ampel
- Aus Ereignisprotokoll + Testsatz je (Quelle × Feld), wo genug Daten auch je (Lieferant × Quelle × Feld), die
  tatsächliche Trefferquote zählen: Wert vom Programm vorgeschlagen → vom Menschen unverändert bestätigt (richtig) oder
  korrigiert (falsch). Quellen wie im Programm: vorlage/library/einvoice/qr/summen/kopf/tabelle/llm/errechnet …
- Ampel/Konfidenz daraus ableiten: grün nur, wenn die untere Grenze eines Konfidenzintervalls (z. B. Wilson 95 %) über
  einer Schwelle liegt (Standard 0,95, `RECHNUNG_AMPEL_GRUEN`); mit wenig Daten bleibt der bisherige feste Wert
  (CONF_MIN) maßgeblich. Nie besser als die feste Logik bei < 10 Beobachtungen.
- Kalibrierung zentral (eine Funktion), periodisch neu berechnet (beim Start + nach jeder Bestätigung inkrementell),
  gespeichert in data/kalibrierung.json. UI: Reiter "Qualität" Tabelle Quelle×Feld (n, Trefferquote, Ampel), im
  Tooltip eines Feldes "Quelle X: 48/50 richtig".
- Verzahnung Testphase→autonom (modus.py): Fortschrittsanzeige zusätzlich mit gemessener Trefferquote je Feld; KEIN
  automatisches Umschalten (bleibt Entscheidung des Menschen).

## 3. Lesefehler-Gedächtnis
- Aus Korrekturen (Programmwert vorher → Wert nachher) Zeichen-Verwechslungen lernen (Alignment je Zeichen): O↔0,
  l/I↔1, S↔5, B↔8, Z↔2, G↔6, Leerzeichen/Bindestrich verschluckt, Komma/Punkt; zählen je Quelle (OCR vs. Text-PDF vs.
  KI) und global; data/lesefehler.json.
- Anwenden: Passt ein Wert nicht zum Lieferantenmuster (Rechnungsnummer-Schema, IBAN-Prüfziffer, USt-IdNr-Format,
  Datum ungültig), Kandidaten durch die gelernten Verwechslungen erzeugen (max. 2 Ersetzungen); passt GENAU ein
  Kandidat zu Muster/Prüfziffer UND steht er (oder die Originalfolge an derselben Stelle) im Text, als gelber
  Vorschlag mit Begründung anbieten ("12O2608998 → 1202608998? (O statt 0, häufig bei Scans)"), Übernahme per Klick.
  Nie still ersetzen. Prüfziffern-Felder (IBAN) dürfen bei eindeutigem Treffer orange "sehr wahrscheinlich" zeigen.

## Qualität
- Tests je Baustein inkl. Grenzfälle (keine Daten, widersprüchliche Daten, Beispiel-Wert nicht im Text → verworfen).
- Suite grün, auch mit `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1 RECHNUNG_IMAP_USER=x`. `.ps1`/`.cmd` nicht
  anfassen. Deutsche Kommentare "Aaron 12.10.2026". UI per Playwright prüfen (`/tmp/pwv/bin/python`,
  `/usr/bin/google-chrome`), keine Konsolenfehler, Screenshots nach /tmp/uidemo-daten/ per `ATTACH:`.
- Messung lokal mit Ollama qwen2.5:1.5b auf CPU (options num_gpu=0, num_thread=4; Tesseract /tmp/mm/tess/bin/tesseract,
  TESSDATA_PREFIX=/tmp/mm/tess/share/tessdata). Nur Gemessenes berichten.

## Abschlussbericht
Deutsch, kurz: je Baustein Dateien, Messwerte (mit/ohne Beispiel, Kalibrierung, Lesefehler-Treffer), Commits,
offene Punkte, was erst mit echten bestätigten Rechnungen aussagekräftig wird.
