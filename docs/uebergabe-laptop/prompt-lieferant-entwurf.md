# Auftrag: Unbekannte Lieferanten automatisch als Entwurf anlegen

Repo: `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app` (Python, FastAPI-UI in
`rechnung/web/static/index.html`). Venv: `.venv/bin/python`. Tests: `.venv/bin/python -m pytest -q -p no:cacheprovider`.

Du arbeitest auf dem Branch `emmy/lieferant-je-adresse`. Dort hat ein Vorgänger gerade die Lieferantensuche je
Rechnungsadresse gebaut (`Supplier.addresses`, `Supplier.external_id`, Kandidatenfilter in identify.py, Abbyy-Import
mit BusinessUnitId, UI-Übersteuerung der Adresse). Lies zuerst `git log -3` und den Diff des letzten Commits, um
den Stand zu kennen. Baue darauf auf, committe auf demselben Branch. **Nicht pushen**, nie auf `release`.

## Entscheidung des Auftraggebers (Aaron, 09.10.2026)

"Wenn der Lieferant noch nicht existiert, soll das Programm die Daten aus der Rechnung in den Lieferanten
eintragen, ihn aber als unbekannten Lieferanten markieren und auf eine menschliche Bestätigung warten."

Das ersetzt die bisherige Regel "Lieferanten werden nie automatisch angelegt" (Docstring in
`identify_with_reason`, README/SPEC) — dort entsprechend anpassen. Unverändert gilt: kein Raten, nichts läuft
automatisch durch, was ein Mensch nicht bestätigt hat.

## Anforderungen

1. **Status am Lieferanten**: `Supplier` bekommt `bestaetigt: bool` (Standard `True`, damit vorhandene und
   importierte Lieferanten bestätigt bleiben; alte JSON ohne Feld = bestätigt) und optional `entwurf_quelle`
   (Dateiname/sha256 der Rechnung, aus der er entstand) und `entwurf_am` (Zeitstempel).

2. **Automatischer Entwurf**: Wenn beim Verarbeiten einer Rechnung (pipeline.process, auch E-Rechnung) kein
   Lieferant gefunden wird, legt das Programm einen Lieferanten mit `bestaetigt=False` an, gefüllt aus der
   Rechnung: Name, Anschrift, USt-IdNr, IBAN, Domain, Währung — was eben verlässlich vorliegt (vorhandene
   Bausteine nutzen: `extract_identifiers`, `find_ibans`, KI-Lieferantenschlüssel in semantic.py `_SUPPLIER_KEYS`
   falls die KI aktiv ist, E-Rechnung: Verkäuferdaten). Nur belegte Werte (Beleg-Pflicht wie im Rest des
   Programms), nichts erfinden. Fehlt ein Name, einen lesbaren Platzhalter wie "Unbekannt (DE123…)" verwenden.
   - Zuordnung: `addresses=[erkannte Rechnungsadresse]`. Wurde keine Adresse erkannt, `addresses=[]` — aber ein
     unbestätigter Entwurf ohne Adresse darf bei der Suche **nicht** als "gilt für alle Adressen" behandelt werden
     (nur Rechnungen ohne erkannte Adresse dürfen ihn finden).
   - ID: stabiler Slug, z. B. aus dem Namen oder `neu-<ustid/iban>`, kollisionsfrei.
   - **Keine Duplikate**: Kommt die nächste Rechnung desselben unbekannten Lieferanten (gleiche USt-IdNr oder
     IBAN unter derselben Adresse), wird der vorhandene Entwurf gefunden statt ein zweiter angelegt.
   - Im Trockenlauf/Vorschau (`/api/invoice`-Anzeige ohne Verarbeitung, `messen`, `vorlernen`) **keine**
     Entwürfe anlegen — nur in der echten Verarbeitung (pipeline/guard bzw. Stapellauf). Prüfe, welche Aufrufer
     tatsächlich verarbeiten, und mach es über einen expliziten Parameter steuerbar statt über Seiteneffekte.

3. **Rechnung eines unbestätigten Lieferanten**: Wird ein Lieferant mit `bestaetigt=False` erkannt (frisch
   angelegt oder vorhandener Entwurf), läuft die Extraktion normal, die Rechnung bleibt aber immer bei
   "Menschliche Bearbeitung" mit dem Grund "Neuer Lieferant – wartet auf Bestätigung" (bzw. ähnlich klar).
   Sie zählt nie als "sicher" im Schattenbetrieb und geht nie automatisch an JobRouter. Auch im autonomen Modus.

4. **Bestätigen in der Oberfläche**:
   - Abschnitt "2 Lieferant" der Rechnungsansicht: bei unbestätigtem Lieferanten deutlich markieren
     ("Unbekannter Lieferant – bitte prüfen"), die eingetragenen Daten zeigen, editierbar (Name, ID, Adresse,
     USt-IdNr, IBAN, zugeordnete Rechnungsadressen), Knopf "Lieferant bestätigen" und Knopf "verwerfen".
     Die vorhandene Box "Neuer Lieferant erkannt" / "Als neuen Lieferanten anlegen" entsprechend ersetzen bzw.
     zusammenführen, damit es nur einen Weg gibt.
   - Reiter "Lieferanten": unbestätigte Lieferanten oben bzw. mit Badge "unbestätigt" und Filter; dort ebenfalls
     bestätigen/verwerfen.
   - API: z. B. `POST /api/suppliers/{id}/bestaetigen` und `.../verwerfen` (verwerfen löscht den Entwurf; nur für
     unbestätigte erlaubt). Audit: Ereignis ins vorhandene Ereignisprotokoll (`eventlog.py`) mit Operator.
   - Nach dem Bestätigen die Rechnung neu auswerten, damit der Rückstellgrund verschwindet.

5. **Sicherheit**: Ein unbestätigter Lieferant darf eine bestätigte Bibliothek nicht verdrängen: Treffer auf
   einen bestätigten Lieferanten haben Vorrang. Zeigen USt-IdNr/IBAN auf einen bestätigten und einen
   unbestätigten Lieferanten, ist das ein Widerspruch → Mensch, nicht still den Entwurf nehmen.

## Qualität

- Tests: Entwurf wird angelegt (Felder aus Rechnung, `bestaetigt=False`, Adresse zugeordnet), zweite Rechnung
  desselben Lieferanten = kein Duplikat, Rechnung eines Entwurfs bleibt beim Menschen (auch autonomer Modus) und
  geht nicht an JobRouter, Bestätigen macht ihn normal und die nächste Rechnung läuft regulär, Verwerfen löscht nur
  Entwürfe, Vorschau/vorlernen/messen legen nichts an, Entwurf ohne Adresse gilt nicht global, alte JSON lädt als
  bestätigt, Widerspruch bestätigt/unbestätigt → Mensch.
- Ganze Suite grün. Zusätzlich einmal mit gesetzter Laptop-Umgebung grün:
  `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=0 RECHNUNG_IMAP_USER=x .venv/bin/python -m pytest -q -p no:cacheprovider`
- `.ps1`/`.cmd` nicht anfassen (CRLF, `test_windows.py`). Deutsche Kommentare wie im Bestand.

## Abschlussbericht (letzte Ausgabe)

Kurz auf Deutsch: geänderte Dateien, Testergebnis beider Läufe (Zahlen), Commit-Hash, offene Punkte/Annahmen.
