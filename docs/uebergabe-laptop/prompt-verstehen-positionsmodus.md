# Auftrag: Beträge aus dem Kontext verstehen + Positionsmodus je Lieferant

Repo: `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app` (Python, FastAPI-UI in
`rechnung/web/static/index.html`). Venv `.venv/bin/python`, Tests `.venv/bin/python -m pytest -q -p no:cacheprovider`.

Lege den Branch `emmy/verstehen-positionsmodus` von `feature/selbstlernen` an (Stand 854fbc1) und committe dort.
**Nicht pushen, nie auf `release`** (der Firmenlaptop holt `release` automatisch).

Lies zuerst: `rechnung/rechnen.py` (Ergänzungsstufe, `_ergaenze_kopf`, schon mit Regel "Netto = Brutto bei Steuer > 0
verworfen"), `rechnung/identify.py` (`EXPECTED_RATES`, `Supplier`), `rechnung/pipeline.py` (`_finish_untemplated`,
`_llm_full`, Template-Pfad), `rechnung/positionen.py`, `rechnung/semantic.py` (`extract_full`, `_targeted_excerpt`,
`_LINE_KEYWORDS`), und in `index.html` `renderInvoice` (Abbyy-Prüfmaske) + `renderLiPreviewBox` (Spalten `LI_STD`).

## Teil A: Brutto/Netto/Steuer aus dem Kontext (ohne KI, deterministisch)

Problem (Aaron, 09.10.2026): Das kleine lokale Modell (qwen2.5:1.5b, CPU) liest Beträge, versteht aber nicht, welcher
Brutto/Netto ist, wenn es nicht ausdrücklich dasteht (z. B. "Total inkl. MWST"). Gesucht: eine **Rechenlogik**, die aus
dem Rechnungstext die Summenbeträge herleitet und Vorrang vor der KI hat.

Neues Modul `rechnung/summen.py`:
- Alle Geldbeträge im Text sammeln (vorhandene Parser in `textnorm.py` nutzen: `parse_amount`, deutsches und
  Schweizer Zahlenformat `1'234.50`), mit Position und Kontextwörtern davor/dahinter.
- Kandidaten-Tripel (netto, ust, brutto) und Paare suchen, die **rechnerisch aufgehen**: brutto = netto + ust
  (±0,01 bzw. Rundung je Steuerzeile) und ust/netto entspricht einem gültigen Satz der Währung (`EXPECTED_RATES`), auch
  Mischsätze als Summe mehrerer Steuerzeilen (Steuerblock je Satz: netto_i, ust_i).
- Bewertung der Kandidaten über Kontext: Schlüsselwörter (de/en/fr/it; z. B. "inkl.", "zu zahlen", "Total",
  "Gesamtbetrag", "Rechnungsbetrag", "Endbetrag", "zzgl.", "exkl.", "netto", "Zwischensumme", "MWST", "USt", "TVA",
  "IVA"), Lage (Endbetrag meist zuletzt/unten, größter plausibler Betrag), QR-Betrag falls vorhanden (stärkstes Signal).
- Ergebnis: bestes Tripel + Steuerzeilen je Satz, mit Konfidenz und Begründung (`raw`/Formel wie in rechnen.py, Quelle
  z. B. `"summen"`). Nur bei eindeutigem Sieger (Abstand zum Zweitbesten) etwas setzen, sonst nichts (kein Raten) und
  einen Hinweis.
- Einbau in `rechnen.rechne` bzw. davor in der Pipeline: Wenn die gelesenen Werte (KI/Vorlage) nicht aufgehen
  (netto+ust≠brutto, Satz ungültig, netto=brutto bei Steuer) oder fehlen, gewinnt das eindeutige Summen-Tripel. Eine
  **Korrektur des Menschen** gewinnt immer; QR-Zahlteil-Brutto bleibt maßgeblich. Vorlagen-Werte, die aufgehen, bleiben.
- Herkunft im UI sichtbar machen wie bei "errechnet" (gelbes "Vorschlag"-Feld ist ok).
- Tests mit echten Formaten: deutsche Rechnung mit "zzgl. 19 % MwSt", Schweizer "Total inkl. MWST 8.1%" mit
  `1'081.35`, Mischsätze 7/19, Rechnung mit Zwischensummen/Rabatt/Versand (darf nicht verwechselt werden), Rechnung
  ohne Steuer (0 %), mehrdeutiger Fall → nichts gesetzt.

## Teil B: Positionsmodus je Lieferant

Entscheidungen Aaron (10.10.2026), verbindlich:
1. **Verschiedene MwSt-Sätze dürfen NIE zusammengefasst werden** — immer eine Positionszeile je Steuersatz.
2. Der Modus ergibt sich aus dem Lieferanten und bleibt stabil. **Normalfall: alles einzeln.** Nur wenige Lieferanten
   haben eine Zusammenfassung.
3. Festgelegt wird er — wie in Abbyy die Entscheidung der bearbeitenden Person — **beim Prüfen einer Rechnung**
   (keine Stammdaten-Rechte nötig) und gilt danach für alle Rechnungen dieses Lieferanten (änderbar).

Modi (`Supplier.positionsmodus`, Standard `"einzeln"`; alte JSON ohne Feld = einzeln):
- `"einzeln"`: wie bisher (Vorlage/KI-Positionen).
- `"zusammenfassung"`: die Übersichtstabelle am Ende der Rechnung (Artikelart/Warengruppe, Kosten, MwSt) wird als
  Positionen genommen. Erkennung: Tabelle nach den Einzelpositionen, typischerweise auf der letzten Seite, mit wenigen
  Zeilen, deren Summe = Netto. Über eine **angelernte Positionstabelle** (vorhandener Mechanismus
  `learn_line_items`/`LineItemSpec`) ist das ohnehin abdeckbar — der Modus sorgt dafür, dass beim Anlernen/Lesen diese
  Tabelle gemeint ist und dass die KI (falls ohne Vorlage) gezielt nur den Schlussteil bekommt (letzte Seite(n), nicht
  10 Seiten Einzelposten — spart Laptop-Zeit).
- `"eine_position"` mit `Supplier.positionsbezeichnung` (z. B. "Strom"): Positionen werden **aus den Summen gebildet**,
  eine Zeile je Steuersatz aus der Steueraufstellung (Bezeichnung, Menge 1, Netto, MwSt-Satz, Währung). Bei einem Satz
  eine Zeile; bei mehreren Sätzen mehrere Zeilen ("Strom" mit 19 %, "Strom" mit 0 % …) — nie zusammenfassen. In diesem
  Modus liest die KI **keine** Einzelpositionen (Zeitersparnis bei 10+ Seiten).
- Positionen in den Modi 2/3 sind mit Herkunft markiert (z. B. `_herkunft`), damit Prüfungen (positionen-summe,
  steuersatz-positionen) korrekt laufen.

UI (Abbyy-Maske, Gruppe "Positionsdaten"):
- Auswahl "Positionen: einzeln / Zusammenfassung / eine Position je Steuersatz" + Feld "Bezeichnung" (nur bei
  eine_position). Änderung speichert am Lieferanten (API z. B. `POST /api/suppliers/{id}/positionsmodus`, Audit-Eintrag
  über `eventlog`/`store.audit` mit Operator) und lädt die Rechnung neu. Ohne bekannten Lieferanten: Auswahl gilt für
  diese Rechnung und wird beim Anlegen/Bestätigen des Lieferanten mitgespeichert.
- **Vorschlag** bei unbekanntem Lieferanten bzw. Modus noch nie gesetzt: wenn die Rechnung sehr lang ist (z. B. ≥ 5 Seiten)
  oder sehr viele Positionen hat, dezenter Hinweis "Langer Beleg – evtl. 'eine Position je Steuersatz' oder
  'Zusammenfassung'?". Nur Hinweis, nie automatisch umstellen (Normalfall bleibt einzeln).
- Lieferanten-Reiter: Modus + Bezeichnung anzeigen/bearbeiten.

Export: JobRouter-Payload bekommt die Positionen wie angezeigt (vorhandene `lineItems`-Logik), keine weitere Änderung.

## Qualität
- Ganze Suite grün, zusätzlich mit Laptop-Umgebung grün:
  `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1 RECHNUNG_IMAP_USER=x .venv/bin/python -m pytest -q -p no:cacheprovider`
- `.ps1`/`.cmd` nicht anfassen (CRLF, `test_windows.py`). Deutsche Kommentare wie im Bestand, Entscheidungen mit Datum
  ("Aaron 10.10.2026") im Kommentar. README/SPEC-v1.md um je einen kurzen Abschnitt ergänzen.
- Bestehendes Prinzip: kein Raten, Unsicheres → Mensch, Korrektur des Menschen gewinnt.

## Abschlussbericht (letzte Ausgabe)
Deutsch, kurz: geänderte Dateien, beide Testläufe (Zahlen), Commit-Hash(es), offene Punkte/Annahmen, was ohne echte
Beispielrechnungen nicht geprüft werden konnte.
