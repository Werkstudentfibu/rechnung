# Auftrag: Übungskorpus aus frei lizenzierten Rechnungen (Entscheidung Aaron 13.10.2026)

Repo `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app`. Eigener Worktree:
`git worktree add /tmp/rw-korpus -b emmy/uebungskorpus emmy/hausregeln` (falls nicht vorhanden: der
neueste von emmy/datenlernen, emmy/tabelle-bedienung). Venv-Symlink
`/tmp/rw-korpus/.venv -> .../app/.venv` (nie committen). Commit je Baustein, deutsch, Zeile
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. **Nicht pushen, nie auf `release`.**

Ziel: Bevor echte Rechnungen kommen, messen und verbessern, wie gut das Programm Rechnungen LIEST —
mit Rechnungen, deren richtige Werte maschinenlesbar mitgeliefert werden.

## Quellen (nur ausdrücklich frei lizenzierte Testdaten, nichts zusammensuchen)
1. ZUGFeRD-Korpus https://github.com/ZUGFeRD/corpus (Apache-2.0): PDFs (Factur-X/ZUGFeRD) mit
   eingebettetem XML. Klonen nach `/srv/korpus/zugferd` (bzw. `~/korpus/zugferd`, NICHT ins Repo).
   Lizenz und Herkunft je Datei in einer Manifest-Datei festhalten.
2. Schweizer QR-Rechnungs-Beispiele (SIX Implementation Guidelines / öffentliche Beispiel-PDFs), nur
   wenn Lizenz/Nutzungsbedingung eindeutig Testnutzung erlaubt — sonst weglassen und im Bericht nennen.
3. Englisch/USA: FATURA-Datensatz (Zenodo, ca. 10.000 Rechnungsbilder mit Annotationen) — NUR wenn die
   Lizenz (erwartet CC BY 4.0) auf der Quellseite eindeutig bestätigt ist; Lizenz im Manifest festhalten.
   Für Messung/Grundwissen englischer Beschriftungen (Invoice #, Amount Due, Balance Due, Sales Tax).
Keine Daten aus anderen Quellen, keine echten Firmenrechnungen.
Hinweis (Aaron 13.10.2026): Korpus ist fast nur DE (+7 FR), viele Fälle doppelt in mehreren
Formatversionen -> Dubletten-Szenarien zusammenfassen, Messung je Szenario statt je Datei gewichten.

## Baustein 0: US-/englische Eigenheiten als feste Regeln (auch ohne FATURA)
- Datumsformat: MM/DD/YYYY vs. DD.MM.YYYY. Bei Schrägstrich-Datum und englischer Rechnung (Sprache,
  „Invoice“, USD, US-Adresse) US-Lesart; mehrdeutige Fälle (beide Teile ≤ 12) -> gelber Hinweis mit
  beiden Lesarten, nie still raten. Plausibilität (Fälligkeit nach Rechnungsdatum, nicht Zukunft) nutzen.
- Zahlenformat 1,234.56 vs. 1.234,56 sicher unterscheiden (vorhandene Logik prüfen, Tests ergänzen).
- Beschriftungen: Invoice #/No./Number, Invoice Date, Due Date, Amount Due, Balance Due, Total,
  Subtotal, Sales Tax, Tax; „Sales Tax“ ist keine USt — keine USt-IdNr erwarten, Pflichtangaben-Prüfung
  (§14 UStG) bei Auslandsrechnung nur als Hinweis.

## PRIORITÄT (Aaron 13.10.2026)
Wichtigste Zielgruppe: DEUTSCHE und SCHWEIZER Rechnungen, ausdrücklich auch MEHRSEITIGE. USA nur
nebenbei (5–10/Woche): Baustein 0 knapp halten, FATURA optional. Frei lizenzierte mehrseitige DE/CH-
Rechnungen mit Soll-Werten gibt es kaum -> Baustein 1b ist der Schwerpunkt.

## Baustein 1b: Übungsgenerator für mehrseitige DE/CH-Rechnungen (Schwerpunkt)
`rechnung/synth.py` (bzw. neues `tools/uebungsgenerator.py`) so erweitern, dass mit festem Seed
reproduzierbar ~300 Rechnungen entstehen, Soll-Werte als JSON daneben:
- 1–12 Seiten, 5–80 Positionen; Tabellenkopf je Seite wiederholt ODER nur auf Seite 1; „Übertrag“/
  „Zwischensumme“ je Seite; Position über Seitenwechsel; „Seite x von y“; Summenblock auf letzter Seite.
- Mehrzeilige Artikelbeschreibungen, Artikelnummern-Spalte, Rabatt-/Skontozeilen, Pfand/Leergut,
  gemischte Sätze 7/19 % (DE), Gutschrift/Rechnungskorrektur, Abschlag mit Abzug (Zahlbetrag ≠ Brutto).
- Schweiz: CHF, 8,1/2,6/3,8 %, Apostroph-Tausender (1'234.50), UID CHE-xxx.xxx.xxx MWST (gültige
  Prüfziffer), QR-Zahlteil auf der letzten Seite (Swiss-QR-Code mit korrekten Daten, vorhandenes
  swissqr-Modul nutzen).
- Layout-Vielfalt: Spaltenreihenfolge, Beschriftungs-Synonyme (aus Baustein 3b), Schriftgrößen, Lage
  von Kopf-/Summenblock, Hotel als Empfänger (Lieferadresse ≠ Rechnungsadresse in einem Teil der Fälle).
- Scan-Simulation: gerendert 200 dpi, leichte Drehung (±1,5°), Rauschen, gelegentlich Stempel
  („Eingegangen“ + Datum) — für OCR-Pfad.
Messung IMMER getrennt ausweisen: (a) Generator-Rechnungen, (b) echte ZUGFeRD-Rechnungen. Verbesserungen
dürfen (b) nicht verschlechtern (Überanpassung an den eigenen Generator vermeiden). Generator-Hälfte A
zum Verbessern, Hälfte B + ZUGFeRD nur zum Prüfen.

## Baustein 1: Übungssatz mit Soll-Werten
`tools/korpus.py`: je PDF das eingebettete XML (CII) lesen (vorhandenes `rechnung/einvoice.py`
nutzen) -> Soll-Werte (Nr, Datum, Fälligkeit, Netto, USt, Brutto, Währung, USt-IdNr, IBAN,
Lieferantenname, Positionen mit Beschreibung/Menge/Einzelpreis/Netto/Satz). Kaputte/absichtlich
fehlerhafte Testdateien (Korpus enthält solche) erkennen und ausschließen.

## Baustein 2: Messen „PDF lesen ohne XML“
Pipeline so aufrufen, dass das eingebettete XML IGNORIERT wird (Stufe E-Rechnung aus, reines PDF-Lesen
wie bei einem Scan/Ausdruck: Text + ggf. KI + Tabellenerkennung). Optional zusätzlich als gerendertes
Bild (Scan-Simulation, 200 dpi) mit Tesseract, falls verfügbar. Vergleich je Feld mit
`testsatz.gleich`/`normalisiert`. Bericht `tools/korpus_bericht.py`: Trefferquote je Feld, je Pfad
(Text/Scan, mit/ohne KI), Zeit je Rechnung, die 20 häufigsten Fehlerarten (lernbericht.fehlerart).
Ergebnis als Markdown + JSON nach `~/korpus/berichte/`.

## Baustein 3: Grundwissen ableiten (nur aus diesen frei lizenzierten Daten)
- Spaltenwörterbuch (Tabellenköpfe -> Spalte) für das lieferantenübergreifende Wörterbuch aus
  emmy/tabelle-bedienung: als ausgeliefertes Grundwissen `rechnung/daten/grundwissen_spalten.json`,
  niedrigste Priorität (eigene Bestätigungen gewinnen immer).
- Beispiele für die KI (Retrieval aus emmy/datenlernen): kleiner, kuratierter Satz
  `rechnung/daten/grundwissen_beispiele.json` (Text-Ausschnitt + richtige Werte), nur genutzt, wenn noch
  keine eigenen bestätigten Beispiele ähnlich genug sind.
- Lizenz-Hinweis (Apache-2.0, Quelle) in README.

## Baustein 3b: Lage- und Beschriftungswissen (Idee Aaron 13.10.2026)
Die Soll-Werte aus dem XML wie eine Bestätigung durch den Menschen behandeln (nur im Übungslauf, nichts
in Aarons Daten): mit dem vorhandenen Lern-Code (`learn.py`, „wo steht der bestätigte Wert?“) je
Korpus-Rechnung automatisch finden, WO jeder Wert steht und WELCHE Beschriftung davor/darüber steht.
Daraus lieferantenübergreifend ableiten (als Grundwissen, niedrigste Priorität):
- Beschriftungs-Wörterbuch je Feld („Rechnungs-Nr.“, „Beleg-Nr.“, „Invoice No.“, „Rechnungsdatum“,
  „Datum“, „Gesamtbetrag“, „zu zahlen“, „Endbetrag“ …) mit Häufigkeit; Gegenbeispiele (Labels, hinter
  denen der Wert NIE stand, z. B. „Kunden-Nr.“) als Ausschlussliste — passt zu Hausregel 4/5.
- Typische Lage je Feld (Seitenbereich: Kopf rechts/links, Fußbereich, Summenblock unten rechts) als
  schwache Vorgabe, wenn weder Vorlage noch Beschriftung greifen.
- Nutzung: bei einem NEUEN Lieferanten (keine Vorlage) zuerst Beschriftungs-Wörterbuch + Lage, dann KI
  — und die KI bekommt die Kandidaten als Hinweis. Ergebnis immer Vorschlag mit Herkunft „Grundwissen“.

## Baustein 3c: Tabellen üben
Positionen aus dem XML sind die Soll-Tabelle. Die automatische Tabellenerkennung (`tabelle.py` aus
emmy/tabelle-bedienung) an allen Korpus-Rechnungen laufen lassen und gegen das Soll messen: Kopfzeile
gefunden? Spaltengrenzen richtig? Mehrzeilige Beschreibungen zusammengeführt? Seitenumbruch/Übertrag?
Zwischensummen/Rabattzeilen nicht als Position? Daraus allgemeine Verbesserungen ableiten (Kopf-Synonyme
ins Spaltenwörterbuch, robustere Spaltenerkennung). Trefferquote Positionen (Anzahl, Beschreibung
vollständig, Menge, Einzelpreis, Netto, Satz) vorher/nachher, getrennt nach Verbesserungs-/Prüfhälfte.

## Baustein 4: Gezielt verbessern
Die 3–5 häufigsten Fehlerarten aus Baustein 2 beheben (Regeln, Prompt, Tabellenerkennung) — nur
allgemeine Verbesserungen, kein Auswendiglernen einzelner Korpus-Rechnungen. Vorher/nachher messen.
Hälfte des Korpus zum Verbessern, andere Hälfte nur zum Prüfen (gegen Überanpassung), feste Aufteilung.

## Qualität
Alle Tests grün (`.venv/bin/python -m pytest -q`), Korpus-Messung NICHT in der normalen Testsuite (zu
langsam, Daten nicht im Repo) — als eigenes Werkzeug. Keine neuen Pflicht-Abhängigkeiten.

## Abschlussbericht (letzte Ausgabe)
Anzahl verwertbarer Rechnungen, Trefferquote je Feld vorher/nachher (Verbesserungs- und Prüfhälfte
getrennt), was verbessert wurde, Grenzen (z. B. Korpus-Layouts ≠ Hotel-Lieferanten). `git log --oneline`.
