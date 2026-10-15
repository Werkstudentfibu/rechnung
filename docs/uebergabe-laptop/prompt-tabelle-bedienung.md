# Auftrag: Positionstabelle – automatisch lernen, Rahmen ziehen, Zeilenprüfung

Repo `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app`. Eigener Worktree:
`git worktree add /tmp/rw-tab -b emmy/tabelle-bedienung emmy/kleines-modell` (Vorgänger-Branch mit `rechnung/tabelle.py`
= Tabelle aus dem Layout ohne KI). Haupt-Checkout nicht anfassen (fremde WIP). Venv:
`/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app/.venv` (Symlink im Worktree, vor Commit entfernen).
Commit je Baustein. **Nicht pushen, nie auf `release`.**

Lies zuerst `git log -6` + Diffs des Vorgängers, dann `rechnung/learn.py` (learn_line_items, LineItemSpec),
`rechnung/template.py` (apply_line_items), `rechnung/positionen.py`, `rechnung/tabelle.py`, `rechnung/validate.py`
(positionen-rechnung/-summe/steuersatz-positionen), `rechnung/web/server.py` (/api/tabelle, /api/confirm, Senden,
Rahmen-Markieren für Kopffelder), `rechnung/web/static/index.html` (Abbyy-Maske, renderLiPreviewBox mit LI_STD-Spalten
Description/Quantity/Unit price/Total price netto/VAT percent/Currency, vorhandenes Rahmen-Ziehen für Felder, clickWord,
paint, renderLiPanel/„Positionstabelle anlernen").

Entscheidung Aaron (12.10.2026): Bausteine 1, 2, 4 + vernünftiges Rahmen-Ziehen für die Tabelle, ganze Tabelle UND
einzelne Zelle. **Die Artikelbeschreibung ist Pflicht** (vollständig, mehrzeilig zusammengeführt, keine Zahlenreste).

## 1. Einmal korrigieren = gelernt (ohne Extraschritt)
- Wird eine Rechnung mit vom Menschen korrigierten/bestätigten Positionen bestätigt bzw. gesendet, lernt das Programm
  die Tabellen-Spec des Lieferanten (je Lieferant + Variante) **automatisch** – Spaltenlage, Kopfzeile, Tabellenende,
  mehrzeilige Beschreibung – ohne „Positionstabelle anlernen“ bedienen zu müssen. Vorhandene Mechanik
  (learn_line_items: findet die korrigierten Zeilen im PDF wieder) nutzen/erweitern, nicht duplizieren.
- Probelauf vor dem Speichern: Die gelernte Spec muss auf genau dieser Rechnung die bestätigten Positionen
  reproduzieren (inkl. Beschreibung), sonst nicht speichern + Hinweis. Gesicherte Specs nie still überschreiben.
- Nächste Rechnung des Lieferanten: Positionen aus der Spec, ohne KI; Positionsmodus je Lieferant respektieren.
- Positionsmodus `eine_position`: nichts lernen.

## 2. Rahmen ziehen (UI) – ganze Tabelle und einzelne Zelle
- **Ganze Tabelle**: Knopf in „Positionsdaten“ „Tabelle im PDF markieren“ (auch angeboten, wenn keine Tabelle erkannt
  wurde). Mensch zieht in der Rechnung links einen Rahmen um die Tabelle (Kopfzeile bis letzte Position; bei
  mehrseitigen Tabellen je Seite ein Rahmen, „weitere Seite“). Programm erkennt darin Zeilen/Spalten (tabelle.py auf den
  Ausschnitt), ordnet Spalten per Kopfwort/Inhalt den LI_STD-Spalten zu, zeigt die Zuordnung als Spaltenauswahl über der
  Vorschau (Dropdown je erkannter Spalte: Description/Quantity/Unit price/Total price netto/VAT percent/Currency/
  ignorieren) und füllt die Positionen. Ändern der Zuordnung aktualisiert sofort.
- **Einzelne Zelle**: Klick in eine Tabellenzelle der Positionen wählt sie aus (wie ein Kopffeld); dann Rahmen um die
  gewünschten Wörter in der Rechnung ziehen bzw. Wörter anklicken → Text/Wert in diese Zelle. Gleiche Bedienung und
  Optik wie das vorhandene Rahmen-Markieren der Kopffelder (Code wiederverwenden). Danach automatisch zur nächsten
  leeren/roten Zelle der Zeile. Beschreibung: mehrere Zeilen im Rahmen werden zu einem Text zusammengeführt.
- Ein „Rahmen ziehen“ funktioniert auch auf Scans (OCR-Wörter) und mit Seitenzoom (fitPages/zoom beachten!).
- Markierte Rahmen bleiben im PDF sichtbar (Farbe je Spalte), Esc bricht ab. Kein Konflikt mit dem Kopffeld-Modus.
- Die so gefüllte Tabelle ist eine normale Korrektur (/api/tabelle) → fließt in Baustein 1.

## 3. Zeilen- und Spaltenzusammenhang nutzen (Hinweis Aaron 12.10.2026)
"Wenn das Programm einen Wert als Teil eines Artikels erkennt, stehen sehr wahrscheinlich links und rechts davon in
der gleichen Zeile die restlichen Informationen zu diesem Artikel."
- **Zeile**: Ist ein Wert sicher einer Position zugeordnet (z. B. Gesamtpreis, der in die Summe passt, oder eine vom
  Menschen markierte Zelle), die übrigen Spalten dieser Position auf derselben Zeile suchen (gleiche Grundlinie ±
  Toleranz, auch leicht schief bei Scans): links davon Beschreibung/Menge/Einzelpreis, rechts MwSt/Währung. Zuordnung
  zur Spalte über die x-Lage der Spaltenköpfe bzw. gelernten Spalten.
- **Spalte**: Umgekehrt stehen gleichartige Werte anderer Positionen senkrecht darunter/darüber in derselben Spalte
  (gleiche rechte Kante bei Zahlen, gleiche linke Kante bei Text) – ein gefundener Wert gibt die Spaltenlage für die
  ganze Tabelle vor.
- Beschreibung: beginnt links in der Zeile, kann in die nächste(n) Zeile(n) umbrechen, solange dort in den Zahlenspalten
  nichts steht.
- In tabelle.py als Erkennungsprinzip einbauen (falls der Vorgänger es nicht schon so macht) UND in der Bedienung
  nutzen: Markiert der Mensch per Rahmen EINE Zelle, schlägt das Programm die übrigen Zellen derselben Zeile vor
  (gelb, mit einem Klick übernehmbar) und bei einer Spaltenmarkierung die Werte der übrigen Zeilen in dieser Spalte.
  Vorschläge nur übernehmen, wenn die Zeilenprüfung (Baustein 4) aufgeht.

## 4. Jede Zeile prüfen
- Je Zeile: menge × einzelpreis ≈ gesamt (±0,01, Rabatt-/Gutschriftzeilen negativ erlaubt), Steuersatz ∈ Sätze der
  Steueraufschlüsselung, Beschreibung nicht leer und ohne Preis-/Mengenreste (z. B. endet nicht auf „2 320,00“),
  Währung = Rechnungswährung. Ergebnis je Zeile grün/rot mit kurzem Grund (Spalte „Prüfung“ gibt es schon) und rote
  Zelle markieren. Summe der Gesamtspalte vs. Netto bleibt als Gesamtprüfung.
- Rote Zeilen sind Rückstellgrund nur, wenn schon heute positionen-* rot zurückstellt (Verhalten nicht verschärfen).

## 5. Lieferantenübergreifend lernen – nur als Vorschlag (Frage Aaron 12.10.2026)
- **Spaltenwörterbuch**: Jede bestätigte Spaltenzuordnung (Kopfwort im PDF → LI_STD-Spalte, z. B. "Anz." → Quantity,
  "Bez." → Description, "E-Preis" → Unit price) wandert in ein globales Wörterbuch (`data/spaltenwoerter.json`, mit
  Zähler). tabelle.py nutzt es bei UNBEKANNTEN Lieferanten zusätzlich zu den fest eingebauten Schlüsselwörtern.
  Ein Wort zählt erst ab 2 Bestätigungen (bei ≥ 1 Lieferant) und nie gegen einen Widerspruch (anderes Ziel öfter).
- **Layout-Familie**: Viele Lieferanten nutzen dieselbe Rechnungssoftware → gleiches Tabellenlayout. Hat ein
  unbekannter Lieferant eine Tabelle mit sehr ähnlicher Kopfzeile/Spaltenlage wie ein gelernter Lieferant, die
  gelernte Spec als Startvorschlag anwenden (vorhandenen Mechanismus "ähnliche Lieferanten"/copyTemplate prüfen und
  wiederverwenden). Ergebnis nur übernehmen, wenn Zeilen- und Summenprüfung aufgehen; immer als Vorschlag gekennzeichnet
  ("Tabelle wie bei Lieferant X erkannt").
- **Rangfolge (Grundsatz Aaron 12.10.2026)**: Wissen über DENSELBEN Lieferanten wiegt stärker als übertragenes.
  Reihenfolge je Wert: Korrektur des Menschen > gelernte Spec dieses Lieferanten (neuere Rechnungen stärker, wegen
  Layoutwechseln) > Layout-Familie > globales Spaltenwörterbuch/Muster > fest eingebaute Schlüsselwörter > KI.
  Übertragenes Wissen kommt nur zum Zug, wo lieferanteneigenes fehlt oder nicht aufgeht. Rangfolge zentral an EINER
  Stelle umsetzen (keine verstreuten Sonderfälle) und im UI als Herkunft zeigen ("vom Lieferanten gelernt" /
  "wie bei Lieferant X" / "allgemein gelernt").
- Nie automatisch eine fremde Spec für einen Lieferanten SPEICHERN – gespeichert wird erst, wenn der Mensch die
  Positionen dieses Lieferanten bestätigt (Baustein 1).

## Messen + Qualität
- Die synthetischen Testrechnungen des Vorgängers (Text + Scan) wiederverwenden: Positionen inkl. **Beschreibung
  wörtlich korrekt** messen; automatisches Lernen: Rechnung 1 korrigieren+bestätigen → Rechnung 2/3 desselben
  Lieferanten ohne KI korrekt? Zahlen in den Bericht.
- UI per Playwright (`/tmp/pwv/bin/python`, Chrome `/usr/bin/google-chrome`) durchklicken: Rahmen um Tabelle, Spalten-
  zuordnung ändern, Zelle per Rahmen füllen (auch Scan + gezoomt), keine Konsolenfehler; Screenshots nach
  `/tmp/uidemo-tab/` und per `ATTACH:` melden.
- Suite grün, auch mit `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1 RECHNUNG_IMAP_USER=x`. `.ps1`/`.cmd` nicht
  anfassen. Deutsche Kommentare „Aaron 12.10.2026“.

## Abschlussbericht
Deutsch, kurz: was gebaut (Dateien), Messwerte, Commits, offene Punkte; nur Gemessenes.
