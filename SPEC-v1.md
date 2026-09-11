# SPEC v1 – Template-basierte Rechnungsextraktion

Stand: 2026-09-10. Diese Spec beschreibt **v1** – bewusst klein gehalten, damit
das Werkzeug auf jedem Firmen-Laptop ohne Installation, ohne Admin-Rechte und
ohne Netzwerkzugriff läuft und von der IT abgenommen werden kann.

## 1. Zweck

Aus PDF-Rechnungen mit **Textlayer** die Kopf-Felder, optional die
Positionszeilen und die USt-Aufstellung je Steuersatz auslesen. Pro Lieferant
wird das Layout **einmalig manuell angelernt** (1–3 Rechnungen), danach laufen
weitere Rechnungen desselben Lieferanten automatisch. Kein OCR, kein ML-Modell
in v1.

Ziel ist die schrittweise Ablösung von FlexiCapture 12 für den Anteil der
Lieferanten mit stabilem Layout (Pareto: wenige Lieferanten = Großteil des
Volumens). Einmal-Lieferanten bleiben vorerst manuell.

## 2. Funktionsumfang v1

| Bereich | v1 |
|---|---|
| Eingabe | lokaler Ordner mit `*.pdf` |
| Lieferanten-Erkennung | USt-IdNr → IBAN → Absender-Domain → Firmenname → Kopf-Fingerprint (gewichteter Score, Schwelle 0.5) |
| Template-Schlüssel | `(supplier_id, variant)` – eine Lieferanten-ID kann mehrere Layout-Varianten haben |
| Anlernen | lokale Web-UI auf `127.0.0.1`: PDF-Seite als Bild, Wörter anklickbar; pro Feld optional ein **Anker** (Label) + der/die **Wert-Wörter** |
| Feldmodell | Anker + relative Region (`rel`) – robuster gegen kleine Layout-Verschiebungen als absolute Koordinaten; Fallback absolute Seitenanteile (`abs_frac`) |
| Anwenden | bekannter Lieferant + Template → Felder ziehen, Konfidenz je Feld |
| Template „gesichert" | nach 3 bestätigten Rechnungen (`secured_at`) |
| Felder | rechnungsnummer, rechnungsdatum, netto, ust_betrag, ust_satz, brutto, ustid, iban, bestellnummer, zahlungsziel |
| Validierung | netto + ust = brutto (±0,02); IBAN-Prüfziffer (ISO 7064); Datum plausibel (−10 J … +14 T); Dublettenprüfung je Lieferant + Rechnungsnummer |
| Status | `auto` / `review` / `no_template` / `unknown_supplier` / `scan_no_textlayer` |
| Ausgabe | JSON + CSV je Rechnung unter `./data/out/` |

## 3. Status-Logik

- `auto` – Template gesichert **und** alle Pflichtfelder gefunden **und**
  Mindestkonfidenz 0,7 **und** keine fehlgeschlagene Prüfung. Reines
  Konfidenz-Signal ("sieht sehr sicher aus") -- **keine** Freigabe fürs
  automatische Durchlaufen. Siehe §7b: Bestätigungspflicht.
- `review` – Template vorhanden, aber Konfidenz/Prüfung schwach → Mensch schaut drauf.
- `no_template` – Lieferant bekannt, noch kein Template → anlernen.
- `unknown_supplier` – Lieferant nicht erkannt → anlernen.
- `scan_no_textlayer` – kein Textlayer → OCR nötig, v1 nicht unterstützt.

## 4. Daten & Speicherorte

Alles liegt **lokal** neben dem Programm (bzw. unter `$RECHNUNG_DATA`):

```
data/
  suppliers.json          Lieferanten-Merkmale (USt-IdNr, IBAN, Domain, Fingerprint)
  templates/<id>.json     Feld-Definitionen
  processed.jsonl         Verarbeitungs-Log (Dublettenprüfung, Audit)
  out/<rechnung>.json|csv Extraktionsergebnis
```

Es werden **keine** Rechnungs-PDFs kopiert, nur der SHA-256-Hash im Log.

## 5. Sicherheits-/Abnahme-relevante Eigenschaften

- **Kein Netzwerkzugriff.** Die Web-UI bindet ausschließlich an `127.0.0.1`,
  keine Authentifizierung nötig (Einzelplatz). Kein ausgehender Traffic.
- **Keine Admin-Rechte, keine Installation.** Reines Nutzerkontext-Programm.
- **Keine Telemetrie, kein Auto-Update.**
- **Abhängigkeiten** (alle OSI-Lizenzen, siehe `pyproject.toml`):
  pdfplumber (MIT), pypdfium2 (BSD-3/Apache – PDFium), pillow (HPND/MIT-CMU),
  python-dateutil (Apache-2.0/BSD), fastapi (MIT), uvicorn (BSD-3), jinja2 (BSD-3),
  python-multipart (Apache-2.0). Dev-only: reportlab (BSD), pytest (MIT).
- **Quellcode** vollständig einsehbar, ~1,4k Zeilen, keine Obfuskation.
- **JobRouter/IONOS**: in v1 **nicht** angebunden – kein Zugriff auf Postfächer
  oder Firmen-APIs. Ausgabe ist eine Datei, die ein Mensch weiterreicht.

## 6. Verteilung

- Entwicklung: `python -m venv .venv && .venv/bin/pip install -e ".[dev]"`.
- Weitergabe zwischen Büros: **ein PyInstaller-Onefile-Build** (`build.md`),
  Windows-`.exe` bzw. Linux-Binary, ~40–60 MB, keine Runtime-Vorinstallation.
- Kein Versand per E-Mail-Anhang (Mailfilter) – über Firmen-Git oder freigegebenes
  Laufwerk.

## 7. Nicht in v1 (bewusst ausgeklammert)

1. **OCR für Scans** (Tesseract) – kommt als Fallback, sobald v1 im Feld steht.
2. **Generisches NER/ML-Modell** für Einmal-Lieferanten – der Teil, der „läuft
   auf jedem Laptop" gefährdet (Modellgröße, Laufzeit).
5. **Rechnungspositionen (Line-Items)**.
6. **Mehrbenutzer / Server-Betrieb / zentrale Template-Verwaltung**.

## 7a. In v1.1 nachgezogen

- **IONOS-IMAP-Abholung** – `rechnung fetch` holt PDF-Anhänge aus dem Postfach
  in `<data>/inbox`. Standardmäßig aus; scharf nur mit `RECHNUNG_IMAP_*`
  (siehe `rechnung.env`-Vorlage). Nur stdlib (`imaplib`/`email`), kein
  ausgehender Traffic ohne gesetzte Zugangsdaten.
- **JobRouter-REST-Übergabe** – `rechnung push` arbeitet die Warteschlange
  (`data/queue/`) ab: echter POST mit Retry/Backoff, `allow_status`-Filter.
  Ohne `RECHNUNG_JOBROUTER_ENABLED=1` reiner Trockenlauf (JSON in `out/`).
- **`rechnung scan --finalize`** – geprüfte Rechnungen in die Bücher übernehmen:
  Original-PDF unverändert nach `data/archive/<sha>.pdf` (GoBD), Audit-Trail
  (`audit.jsonl`, wer/wann/was), append-only Journal, JSON/CSV nach `out/`,
  Einreihung für JobRouter. Idempotent über `sha256` + Template.
- **Manueller Korrektur-Workflow** – pro Rechnung (`sha256`) übersteuerbare
  Feldwerte (`data/corrections/`), gewinnen in Pipeline und Web-UI.
- **Härtung Validierung** – Betrags-Plausibilität, Positionssummen-Abgleich,
  länderspezifische IBAN-Längenprüfung, Journal-Dedup über `sha256`.

## 7b. In v1.2 nachgezogen — leichte Vorschlags-Hilfen (weiterhin kein ML-Modell)

Zwei Assistenz-Bausteine, beide reine Textvergleiche (stdlib `difflib`,
Mengen-Jaccard auf dem ohnehin vorhandenen Kopf-Fingerprint) statt Modell
oder Gewichte — bleiben deterministisch, ohne neue Abhängigkeit, auditierbar:

- **Kategorie-Vorschlag je Position** (`rechnung/suggest.py`,
  `Store.category_examples`): aus bestätigten/korrigierten
  Positionstexten gelernte Nachschlagetabelle (`data/categories.json`),
  gilt lieferantenübergreifend. Reiner Vorschlag mit Konfidenz — der Mensch
  bestätigt/korrigiert im Training-UI, nie automatische Buchung.
- **Ähnliche-Lieferanten-Vorschlag beim Anlernen** (`similar_suppliers`
  in `suggest.py`, Endpunkt `/api/template/copy`): bei neuem/unbekanntem
  Lieferanten werden Layouts mit ähnlichem Kopf-Fingerprint vorgeschlagen;
  ein Klick kopiert deren Template als Startpunkt. Das kopierte Template
  startet mit `confirmations=0` und muss wie jedes andere erst an echten
  Rechnungen bestätigt werden, bevor es auf `auto` springen kann — keine
  Abkürzung bei der Vertrauens-Schwelle.

## 7c. Bestätigungspflicht (v1.2) -- gilt für JEDE Rechnung, immer

`finalize()` (Archivierung, Audit-Log, Journal, JobRouter-Einreihung) verweigert
jede Rechnung, die nicht zuvor im Training-UI per "Extraktion bestätigen"
explizit bestätigt wurde (`Store.mark_confirmed`/`is_confirmed`,
`data/confirmations.jsonl`, append-only wie das Audit-Log). Das gilt unabhängig
vom Status -- auch eine als `auto` erkannte Rechnung eines längst gesicherten
Templates läuft nicht ohne diesen Klick durch.

Damit ist `auto` vs. `review` nur noch ein Konfidenz-Signal für die Anzeige
(grün/orange im UI), keine Automatisierungs-Freigabe: die Kategorisierung und
Feld-Vorbefüllung passieren automatisch (ab der 2. Rechnung eines Lieferanten),
aber jede einzelne Rechnung braucht weiterhin einen Menschen, der bestätigt
(und dabei jederzeit korrigieren kann) -- egal wie oft dieser Lieferant schon
bestätigt wurde. `rechnung scan --finalize` (CLI-Batchlauf) überspringt
unbestätigte Treffer entsprechend und meldet das.

## 7d. Härtung (v1.3) -- Reaktion auf eine Risiko-Durchsicht

- **Mehrdeutige Lieferanten-Erkennung** (`identify.py`): Bei zu knappem
  Abstand zwischen bestem und zweitbestem Fingerprint-Treffer (< 0,15) gibt
  `identify()` jetzt `None` zurück statt zu raten -- verhindert eine stille
  Verwechslung zwischen zwei Lieferanten mit ähnlichem Kopf-Layout (z.B.
  gleiche Buchhaltungssoftware). Ein exakter USt-IdNr./IBAN-Treffer gilt als
  eindeutig genug und überspringt diese Prüfung.
- **Mehrdeutige Anker** (`template.py`): Kommt ein Ankertext mehrfach auf der
  Seite vor, sinkt die Konfidenz von 0,92 auf 0,55 -- der beim Anlernen
  gespeicherte `occurrence`-Index ist dann nicht mehr garantiert stabil.
  Fällt damit unter `CONF_MIN` (0,7) und landet auf `review`, statt
  selbstbewusst falsch als `auto` durchzulaufen.
- **Kategorie-Verwaltung**: neuer Reiter "Kategorien" im Training-UI
  (`/api/categories`, GET/DELETE) -- macht die gelernte Nachschlagetabelle
  sichtbar und korrigierbar/löschbar, statt dass ein einzelner Tippfehler
  unsichtbar in künftige Vorschläge einfließt.
- **Datensicherheit** (`store.py`): alle Zustandsdateien (Lieferanten,
  Templates, Kategorien, Adressen, Korrekturen) werden atomar geschrieben
  (Temp-Datei + Rename) und heben vor jedem Überschreiben eine `.bak`-Kopie
  auf. Eine beschädigte Datei (Stromausfall, volle Platte mitten im
  Schreiben) lässt das Programm nicht mehr abstürzen: erst wird die `.bak`
  versucht, sonst wird die kaputte Datei zur Seite gelegt (nicht gelöscht)
  und mit leerem Stand weitergemacht. Append-only-Logs (`processed.jsonl`,
  `audit.jsonl`, `confirmations.jsonl`) überspringen einzelne beschädigte
  Zeilen statt komplett unlesbar zu werden.
- **Kategorie-Vergleichstext**: nur die erste Text-Spalte der Positionstabelle
  (typischerweise "Bezeichnung"), nicht mehr alle Text-Spalten zusammen --
  eine zusätzliche Freitext-Spalte hätte den Fuzzy-Vergleich verwässert.
- **Fuzzy-Schwelle konfigurierbar**: `RECHNUNG_CATEGORY_FUZZY_MIN`
  (Standard 0,72) -- reine Schätzung bisher, da noch keine echten Rechnungen
  zur Kalibrierung vorliegen; über ENV justierbar ohne Code-Änderung.
- **JobRouter-Warnung**: `_post` warnt auf stderr, wenn ein Bearer-Token über
  eine nicht-`https://`-URL geschickt würde.

**Bewusst nicht angefasst:** mehrseitige Positionstabellen mit
Seitenumbruch (§8) -- ohne echte Beispielrechnungen mit diesem Layout wäre
jede "Reparatur" ungetestete Spekulation. Bleibt offen bis reale Fälle
vorliegen.

## 8. Offene Punkte für v1.1

- Template-Export/-Import zwischen Kollegen (eine `.zip` mit `data/`).
- CSV-Sammelexport über alle Rechnungen eines Laufs.
- Konfigurierbare Konfidenz-Schwelle je Feld statt global 0,7.
- Umgang mit mehrseitigen Rechnungen mit Seitenumbruch in der Betragstabelle.
