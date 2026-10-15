HINWEIS NACHTLAUF 15./16.10.: Dieser Auftrag kann eine FORTSETZUNG sein (ein früherer Lauf kann am Abo-Limit abgebrochen sein).
Zuerst `git log --oneline -15` und `git status` im Worktree ansehen: committete Bausteine NICHT neu bauen; uncommittete Änderungen
sichten, fertigstellen, testen, committen. Committe nach JEDEM fertigen Baustein sofort (damit ein Limit-Abbruch nichts verliert).
Läuft unbeaufsichtigt -- keine Rückfragen, selbst entscheiden und im Bericht begründen.

# Auftrag: Menschliches Rechnungs-Wissen als feste Regeln (8 Bausteine, Entscheidung Aaron 13.10.2026)

Repo `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app`. Eigener Worktree:
`git worktree add /tmp/rw-regeln -b emmy/hausregeln emmy/datenlernen` (falls emmy/datenlernen fehlt: von
emmy/tabelle-bedienung). Haupt-Checkout nicht anfassen. Venv: Symlink
`/tmp/rw-regeln/.venv -> /home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app/.venv`
(nie committen). Commit je Baustein (deutsch, Grund + "Entscheidung Aaron 13.10.2026",
Zeile `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`). **Nicht pushen, nie auf `release`.**

Kontext: Rechnungseingang einer Hotelgruppe (Althoff, DE + CH). Läuft auf einem schwachen Firmenlaptop
(Ollama qwen2.5:1.5b, Tesseract). Lies zuerst `README.md`, `SPEC-v1.md`, `git log -15`, dann
`rechnung/summen.py`, `rechnung/validate.py`, `rechnung/rechnen.py`, `rechnung/belegart.py`,
`rechnung/dubletten.py`, `rechnung/plausi.py`, `rechnung/pflicht.py`, `rechnung/billing.py`,
`rechnung/semantic.py` (Prompt + Beleg-Pflicht), `rechnung/tabelle.py`.

Grundsätze (Aaron):
- Feste, nachvollziehbare Regeln (Text/Zahlen), **keine KI-Entscheidung**. Die KI darf nur Werte liefern,
  die Regeln prüfen/ordnen.
- Nie still verwerfen oder still durchwinken: entweder Hinweis (gelb) oder Prüfung rot mit Grund, sichtbar
  in der Prüfmaske und im JobRouter-Datensatz (`warnings`/`hints`). Rot = Rechnung bleibt beim Menschen.
- Keine echten Rechnungsdaten nötig/erlaubt: Tests mit synthetischen Rechnungen (`rechnung/synth.py`
  erweitern), DE- und CH-Schreibweisen (1.234,56 / 1'234.56).
- Jeder Baustein abschaltbar über `RECHNUNG_REGEL_<NAME>=0`. Bestehende Tests bleiben grün.

## 1. Pfand / Leergut
Positionen/Zeilen mit Pfand, Leergut, Kasten-/Flaschenpfand, Mehrweg, „Pfandrückgabe“, „Gutschrift Leergut“
erkennen. Pfand ist Teil der Summe (sonst geht Netto+USt≠Brutto), wird aber als eigene Positionsart
`pfand` markiert; Rückgabe = negative Position. Rechen-Prüfungen dürfen Pfand nicht als Fehler werten.
Steuersatz des Pfands folgt dem Getränk (DE: wie die Ware) — kein eigener Satz erzwingen, nur kennzeichnen.

## 2. Gemischte Steuersätze im Hotel (7 % / 19 %, CH 2,6 % / 8,1 %)
Plausibilität je Position: Lebensmittel (Fleisch, Gemüse, Obst, Milch, Brot …) typisch 7 %, Getränke
(alkoholisch und alkoholfrei außer Milch/Wasser je nach Rechtslage — nur Hinweis!), Non-Food, Dienstleistung
19 %. Kleine Wortliste als Datei (`rechnung/daten/steuersatz_woerter.json`), erweiterbar. Abweichung = gelber
Hinweis „Position X: 19 % bei Lebensmittel?“, nie rot. Schweiz analog (2,6 % Lebensmittel, 8,1 % Normal,
3,8 % Beherbergung).

## 3. Zahlbetrag ≠ Brutto
„abzüglich Anzahlung/Abschlag“, „bereits bezahlt“, „verrechnete Gutschrift“, „Restbetrag“, „Zahlbetrag“,
„zu zahlen“: Brutto (Rechnungssumme) und Zahlbetrag getrennt erfassen (neues Feld `zahlbetrag`, sichtbar
unter Beträge), Abzüge als Liste. Rechen-Prüfung: Brutto − Abzüge = Zahlbetrag. Kein Abzug → zahlbetrag =
brutto (nicht anzeigen). In JobRouter-Nutzlast mitgeben.

## 4. Nummern-Verwechslung
Rechnungsnummer vs. Kunden-, Bestell-, Auftrags-, Lieferschein-, Debitoren-, Steuer-, Vertragsnummer.
Regel: Ist der gelesene Wert der Rechnungsnummer im Text direkt hinter einem anderen Label („Kunden-Nr.“,
„Bestell-Nr.“, „Auftrag“, „Lieferschein“, „Kd.-Nr.“, „Ihre Bestellung“, „Steuer-Nr.“ …) belegt und nicht
hinter einem Rechnungs-Label → rot „sieht aus wie Kundennummer“ (mit Label im Grund). Zusätzlich die
anderen Nummern als eigene Felder erfassen, wo eindeutig beschriftet (`kundennummer`, `bestellnummer`
gibt es evtl. schon — prüfen, ausgeblendete Felder respektieren). KI-Prompt um klare Abgrenzung ergänzen.

## 5. Welches Datum ist das Rechnungsdatum?
Auf Rechnungen stehen Rechnungs-, Liefer-/Leistungs-, Bestell-, Druck-, Fälligkeits-, Buchungsdatum. Regel
nach Label-Nähe mit Rangfolge (Rechnungsdatum/Datum der Rechnung/Invoice date > „Datum“ im Rechnungskopf >
sonst nichts). Liegt der gelesene Wert hinter einem anderen Datums-Label → rot mit Grund. Leistungsdatum
getrennt (Feld existiert). Datum-Plausibilität aus emmy/kleines-modell weiterverwenden, nicht duplizieren.

## 6. „Kopie“, „Duplikat“, „Zweitschrift“, „Copy“, „Rechnungskopie“
Im Kopfbereich/als Stempel erkannt → Hinweis + verschärfte Dublettenprüfung (gleicher Lieferant + gleiche
Nummer ODER gleicher Betrag+Datum ±3 Tage → rot „wahrscheinlich Kopie einer bereits gesendeten Rechnung“).

## 7. Eingangsstempel / Handschrift auf Scans
Typische Stempeltexte („Eingegangen“, „Eingang“, „Erhalten“, „Geprüft“, „Sachlich richtig“, „Kostenstelle“,
Datum im Stempel, Kürzel) nicht als Rechnungsdatum/Betrag übernehmen. Wörter mit sehr niedriger
OCR-Konfidenz in Inseln außerhalb des Textflusses (Handschrift) aus der Feldsuche ausschließen, aber als
Hinweis „Handschrift/Stempel erkannt“ zeigen. Ein Kostenstellen-Stempel darf als Vorschlag für das Feld
`kostenstelle` dienen (gelb, nie automatisch).

## 8. Lieferadresse ≠ Rechnungsadresse
Hotelgruppe: Lieferung an Hotel A, Rechnung an Zentrale/Gesellschaft B. Geschäftsbereich weiter über die
Rechnungsadresse; zusätzlich „Lieferadresse/Lieferanschrift/Leistungsort/Lieferung an“ erkennen und gegen
die Geschäftsbereiche abgleichen. Passt die Lieferadresse zu einem ANDEREN Geschäftsbereich → gelber Hinweis
mit Vorschlag; keine Rechnungsadresse erkannt, aber Lieferadresse eindeutig → Vorschlag (nicht automatisch).

## Qualität
- Alle Tests grün (`.venv/bin/python -m pytest -q`), auch mit `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1`.
- Je Baustein Tests mit synthetischen Rechnungen, positive UND negative Fälle (keine Fehlalarme bei normalen
  Rechnungen — das ist wichtiger als jeden Sonderfall zu fangen).
- Windows denken (Pfade, CRLF bei .cmd/.ps1), keine neuen Pflicht-Abhängigkeiten.
- README + SPEC-v1.md: Abschnitt „Hausregeln 13.10.2026“ mit Schaltern.

## Abschlussbericht (letzte Ausgabe)
Je Baustein: Regel in einem Satz, Schalter, Testnamen, bekannte Grenzen/Fehlalarm-Risiken. `git log --oneline`.
