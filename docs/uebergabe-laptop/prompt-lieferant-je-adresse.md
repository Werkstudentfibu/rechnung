# Auftrag: Lieferantensuche je Rechnungsadresse (wie Abbyy)

Repo: `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app` (Python, FastAPI-UI in
`rechnung/web/static/index.html`). Venv: `.venv/bin/python`. Tests: `.venv/bin/python -m pytest -q -p no:cacheprovider`.

Arbeite auf einem neuen Branch `emmy/lieferant-je-adresse` (von `feature/selbstlernen`). Committe dort.
**Nicht pushen**, insbesondere nie auf `release` (der Firmenlaptop holt sich `release` automatisch).

## Hintergrund (Entscheidung des Auftraggebers Aaron, 09.10.2026)

In Abbyy FlexiCapture wird ein Lieferant nur unter der Rechnungsadresse (BusinessUnit) gesucht, der er
zugeordnet ist, nie übergreifend. So soll es auch hier sein: Liegt die Rechnungsadresse in Zürich, wird
nicht in Hamburger Lieferanten gesucht.

Abbyy-Doku Vendors-Datensatz: Spalte `Id` (Pflicht) und `BusinessUnitId` (optional). Ist `BusinessUnitId`
gefüllt, ist ein Eintrag erst durch die Kombination (BusinessUnitId, Id) eindeutig. Dieselbe Id kann also
unter verschiedenen BusinessUnits verschiedene Lieferanten bezeichnen. Der aktuelle Import
(`rechnung/importers.py`, `import_vendors_csv`) ignoriert `BusinessUnitId` und schreibt per Id. Das ist ein
Bug: Er überschreibt Lieferanten verschiedener Häuser.

## Anforderungen

1. **Datenmodell**: `Supplier` (`rechnung/identify.py`) bekommt
   - `addresses: list[str]` = IDs der Rechnungsadressen (`BillingAddress.id`), denen er zugeordnet ist.
     **Leer = gilt für alle Adressen** (rückwärtskompatibel, manuell angelegte Lieferanten bleiben gültig).
   - `external_id: str` = Lieferantennummer im Fremdsystem (Abbyy-`Id`).
   - to_dict/from_dict ergänzen, alte JSON-Daten ohne diese Felder müssen weiter laden.

2. **Import** (`import_vendors_csv`):
   - Mit `BusinessUnitId`: interne ID `f"{Id}@{BusinessUnitId}"`, `addresses=[BusinessUnitId]`,
     `external_id=Id`. Ohne `BusinessUnitId`: interne ID = `Id` wie bisher, `addresses=[]`, `external_id=Id`.
   - Upsert bleibt idempotent (zweimal dieselbe Datei = keine Duplikate).
   - CLI `import-vendors`: Hinweis ausgeben, wie viele Lieferanten auf eine BusinessUnitId verweisen, die
     (noch) nicht als Rechnungsadresse existiert (Empfehlung: erst BusinessUnits importieren).
   - Modul-Docstring aktualisieren.

3. **Erkennung je Adresse**: Erst die Rechnungsadresse bestimmen, dann nur unter den passenden Lieferanten
   suchen. Kandidaten = Lieferanten mit `addresses` leer ODER mit der erkannten Adress-ID darin.
   - Wenn Rechnungsadressen in der Bibliothek existieren, aber keine erkannt wurde: nur die globalen
     Lieferanten (addresses leer) durchsuchen. Nie stillschweigend die ganze Bibliothek. Der bestehende
     Rückstellgrund für die fehlende Adresse bleibt. Bei "kein Lieferant" soll der Grund erkennbar machen,
     dass nur je Adresse gesucht wurde (z. B. "… unter Rechnungsadresse X nicht gefunden" bzw.
     "Rechnungsadresse nicht erkannt – Lieferant wird nur je Adresse gesucht").
   - Keine Adressen in der Bibliothek: Verhalten wie bisher (alle Lieferanten).
   - Gilt überall, wo identifiziert wird: `pipeline.py` (`identify_with_reason`-Aufruf und E-Rechnung
     `identify_einvoice`; für E-Rechnungen die Adresse wie bisher bzw. aus dem Text bestimmen), `web/server.py`
     (alle `identify(`-Aufrufe), `vorlernen.py` (`_group_key`), sowie sonstige Fundstellen (`grep -rn identify`).
   - Den USt-IdNr/IBAN-Index (`Store.supplier_index`) entsprechend je Kandidatenmenge nutzen: entweder je
     Adress-ID cachen oder über Kandidaten neu aufbauen. Wichtig: Derselbe Lieferant (gleiche USt-IdNr) unter
     zwei Adressen darf NICHT zu "mehrdeutig" führen, solange die Suche auf eine Adresse beschränkt ist.
   - Saubere Hilfsfunktion, z. B. `kandidaten(suppliers, address_id, has_addresses)` in identify.py, statt
     die Filterlogik zu kopieren.

4. **UI-Übersteuerung**: Im Rechnungs-Abschnitt "1 Rechnungsadresse" gibt es das Select `#variant`
   (Funktion `zeigeAdresse()`). Wählt der Mensch dort eine andere Adresse, soll die Rechnung mit dieser Adresse
   neu ausgewertet werden (Lieferantensuche unter dieser Adresse). Dafür `/api/invoice` um einen optionalen
   Parameter `address` erweitern, der die erkannte Adresse übersteuert. Im Frontend beim Ändern neu laden
   (`api('/api/invoice?file=...&address=...')` → `applyInvoiceData`). Die Wahl muss beim anschließenden
   Speichern/Bestätigen (Template = Lieferant + Variante) konsistent mitgehen; vorhandene Logik nicht brechen.

5. **Lieferanten-Reiter** (UI, `loadSuppliers` / Formular `sup-form` in index.html, API `/api/suppliers`,
   `SupplierPost` in server.py): zugeordnete Rechnungsadressen anzeigen (Spalte in der Liste) und im Formular
   per Checkboxen der vorhandenen Rechnungsadressen bearbeitbar machen; leer = "alle Adressen". `external_id`
   als Feld "Lieferantennummer" anzeigen/bearbeiten.

6. **Export**: In `jobrouter.payload_from_result` die `external_id` des Lieferanten als Feld
   `lieferantennummer` mitgeben, falls vorhanden (der Result enthält `supplier_id`; hole die externe ID über
   den Store oder lege sie beim Verarbeiten in den Result). Keine anderen Export-Änderungen.

## Qualität

- Tests für: Import mit/ohne BusinessUnitId (inkl. gleiche Id unter zwei BUs = zwei Lieferanten, Idempotenz),
  Kandidatenfilter, Pipeline erkennt Lieferanten nur unter passender Adresse (Zürich-Rechnung findet
  Hamburger Lieferanten NICHT, auch bei gleicher USt-IdNr nicht), gleiche USt-IdNr unter zwei Adressen ist
  nicht mehrdeutig, Adresse nicht erkannt → nur globale Lieferanten, UI-Übersteuerung über `address`-Parameter,
  alte Lieferanten-JSON ohne neue Felder lädt.
- Ganze Suite grün. Zusätzlich einmal mit gesetzter Laptop-Umgebung grün:
  `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=0 RECHNUNG_IMAP_USER=x .venv/bin/python -m pytest -q -p no:cacheprovider`
- `.ps1`/`.cmd` nicht anfassen (müssen CRLF bleiben, Test `test_windows.py`).
- Stil: deutsche Kommentare/Docstrings wie im Bestand, Begründung in Kommentaren kurz.
- Bestehende Entscheidungen respektieren: kein Raten, Unsicheres → Mensch; Lieferanten-Identität nur per
  Abgleich mit der Bibliothek.

## Abschlussbericht (letzte Ausgabe)

Kurz auf Deutsch: was geändert wurde (Dateien), Testergebnis beider Läufe (Zahlen), Commit-Hash, offene
Punkte/Annahmen. Keine Vermutungen als Fakten.
