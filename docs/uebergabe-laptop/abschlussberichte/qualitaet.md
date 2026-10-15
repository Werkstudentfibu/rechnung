Die fünf Bausteine sind fertig, jeder als eigener Commit auf `emmy/qualitaet` (abgezweigt von `emmy/verstehen-positionsmodus`). Nicht gepusht, `release` unberührt. Beide Testläufe sind grün: normal **720 passed**, mit Laptop-Umgebung (`RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1 RECHNUNG_IMAP_USER=x`) ebenfalls **720 passed**. Den neuen Reiter „Qualität“ und die Prüfmaske habe ich im Browser durchgeklickt: Die Browser-Konsole bleibt fehlerfrei.

**Wichtig vorab:** Beim Bestätigen im UI wurde das Original-PDF bisher nicht ins Archiv kopiert. Das passierte nur über `finalize`. Ab jetzt archiviert das Bestätigen das Original. Rechnungen, die vor heute im UI bestätigt wurden, fehlen deshalb meist im Testsatz. Sie erscheinen dort als „übersprungen: Original nicht im Archiv“.

## Was gebaut wurde

**1. Testsatz aus bestätigten Rechnungen** (`a75970f`)
- Beim Bestätigen werden die Soll-Werte nach `data/testsatz/<sha>.json` geschrieben, das Original geht ins Archiv. Ältere Bestätigungen leite ich, soweit möglich, aus `processed.jsonl` her.
- `rechnung testsatz [--json] [--lieferant X] [--variante A|B|C] [--ohne-ki]`:
  - Trockenlauf ohne die gespeicherten Korrekturen. Es entstehen keine Entwürfe, kein Ereignis, kein Audit, kein Export.
  - Gemessen wird je Feld richtig/falsch/fehlt, dazu die Positionen und die Zeit je Rechnung (Median/P90).
  - Der Bericht landet unter `data/berichte/testsatz_<datum>_<zeit>.json`, mit „Schlechter geworden bei: …“ gegenüber dem letzten Lauf.
- Die Variante wird bis zur KI-Stelle durchgereicht. Nur A ist gebaut; B und C melden „noch nicht gebaut“, alles andere ist ein Fehler.
- Selbst-Update-Schutz:
  - Ab 5 Testfällen wird zurückgerollt, wenn die Trefferquote um mehr als 2 Prozentpunkte sinkt.
  - Standard ist „nur ohne KI“, `RECHNUNG_UPDATE_TESTSATZ=0` schaltet ab, `=voll` nimmt alle Fälle.
- Reiter „Qualität“ mit Knopf „Testsatz jetzt prüfen“; läuft im Hintergrund mit Fortschrittsanzeige.
- Dateien: `testsatz.py` (neu), `guard.py`, `pipeline.py`, `semantic.py`, `selbstupdate.py`, `cli.py`, `web/server.py`, `index.html`

**2. Lieferanten-Gedächtnis und Plausibilität** (`9eb627a`)
- Je Lieferant wird ein Profil aus den bestätigten Rechnungen abgeleitet, aktiv ab 3 Rechnungen.
- Rot (geht zum Menschen, auch im Schattenbetrieb):
  - Betrag mehr als 10× über dem Maximum oder unter einem Zehntel des Minimums
  - Rechnungsnummer passt nicht zum gelernten Muster
  - Fälligkeit vor dem Rechnungsdatum
  - Rechnungsdatum mehr als 1 Tag in der Zukunft, mit Hinweis auf möglichen Jahreszahl-Dreher (2062 statt 2026)
- Nur Hinweis:
  - Betrag mehr als 3× über dem Maximum bzw. unter einem Drittel des Minimums
  - Rechnungsnummer nicht größer als eine schon bestätigte, obwohl der Lieferant fortlaufend nummeriert
  - Steuersatz oder Währung, die der Lieferant sonst nie verwendet
  - Rechnungsdatum älter als 1 Jahr
- In der Prüfmaske steht das betroffene Feld rot mit Grund darunter, oben eine Box „Prüfungen rot“.
- Dateien: `plausi.py` (neu), `validate.py`, `human.py`

**3. Pflichtangaben** (`aa184b0`)
- Eigene gelbe Gruppe nach § 14 Abs. 4 UStG, bei Kleinbeträgen bis 250 € nach § 33 UStDV, bei Schweizer Rechnungen nach Art. 26 MWSTG.
- Nur Hinweis mit Begründung („fehlt: Zeitpunkt/Zeitraum der Leistung“), nie ein Rückstellgrund.
- Neues Feld `leistungsdatum` unter „Sonstige Daten“. Es wird aus dem Text gefüllt, wenn ein eindeutiges Label dasteht, und nie bei der KI angefragt; der KI-Prompt bleibt unverändert.
- Datei: `pflicht.py` (neu)

**4. E-Rechnungen bevorzugen** (`9a19853`)
- `rechnung e-rechnung-kandidaten` und Anzeige im Reiter „Qualität“: Lieferanten, die PDF oder Scan statt E-Rechnung schicken, sortiert nach Anzahl und dann nach Korrekturen.
- Anschreiben-Vorlage `rechnung/web/static/anschreiben-e-rechnung.txt` mit Platzhaltern, im UI verlinkt und im README abgedruckt.
- `--anschreiben LIEFERANT_ID` füllt nur Bekanntes (Name, Anschrift aus den Stammdaten, Datum). Es wird nichts versendet.
- Datei: `e_rechnung.py` (neu)

**5. Fehlerstatistik je Lieferant und Feld** (`aaae91e`)
- Anteil der korrigierten Rechnungen je Lieferant und Feld, letzte 4 Wochen gegen davor, mit Trend.
- Top-Liste „lohnendste Verbesserung“ ab 2 korrigierten Rechnungen und mindestens 50 %, z. B. „…: rechnungsnummer in 6 von 8 Rechnungen korrigiert → Vorlage anlernen“.
- Abrufbar per CLI `rechnung fehlerstatistik`, im Reiter „Qualität“ und im Nachtlauf (Bericht plus die 5 wichtigsten Punkte in der Ausgabe).
- Datei: `eventlog.py`

README und SPEC (§7ac) sind je Baustein ergänzt.

## Bitte entscheiden
- **Datum in der Zukunft:** Die Toleranz ist jetzt 1 Tag statt bisher 14 Tage, wie vorgegeben. Vorausdatierte Rechnungen werden damit rot.
- **„Hinweis/rot“ beim Betrag:** Ich habe es so umgesetzt: ab 3× Hinweis, ab 10× rot. Gutschriften bewerte ich bei der Betragsspanne nicht.

## Weitere Annahmen und offene Punkte
- **Was gemessen wird:** Bibliotheksfelder (IBAN, USt-IdNr) zählen im Testsatz nicht als gelesen. Stattdessen gibt es ein Pseudo-Feld `lieferant` (richtiger Lieferant erkannt). Felder, die der Mensch leer bestätigt hat, werden nicht bewertet.
- **Bezugslauf und KI-Cache:**
  - Der Testsatz nutzt den KI-Antwort-Cache: Bei unverändertem Prompt liest das Modell nicht neu. Gemessen werden also Programmänderungen.
  - Der erste Update-Lauf mit mindestens 5 Fällen legt den Bezugswert fest. Ein schlechter Lauf wird nie zum neuen Bezug.
  - Dauert der Testsatz länger als 30 Minuten, wird das Update zurückgerollt.
- **Export:** `leistungsdatum` ist noch nicht in `jobrouter-mapping` eingetragen.
- **Pflichtangaben:** Die Prüfung ist bewusst vereinfacht. Anschriften erkenne ich z. B. über PLZ und Ort; sie kann irren. Bei E-Rechnungen wird das Lieferdatum aus dem XML nicht ausgewertet, dort steht „nicht prüfbar“.

## Nur mit echten Daten prüfbar
- Wie gut das Rechnungsnummern-Muster trifft und wie viele Fehlalarme es gibt (Lieferanten, die ihr Schema wechseln).
- Ob die Faktoren 3 und 10 für die Betragsspanne passen.
- Wie zuverlässig die Pflichtangaben an echten Layouts erkannt werden, besonders CH und Kleinbeträge.
- Laufzeit des Testsatzes auf dem Laptop, vor allem mit `=voll`.
- Ob die Einordnung „Scan“ passt (sie hängt an der OCR-Stufe im Ereignisprotokoll).

Die Screenshots stammen aus synthetischen Testrechnungen: der Reiter „Qualität“ und die Prüfmaske mit falsch gelesener Rechnungsnummer (O statt 0).

ATTACH: /tmp/uidemo/qualitaet.png
ATTACH: /tmp/uidemo/maske.png
