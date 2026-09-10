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
  Mindestkonfidenz 0,7 **und** keine fehlgeschlagene Prüfung. Nur dieser Status
  ist für automatische Weiterleitung freigegeben.
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
3. **IONOS-IMAP-Abholung** – v1 arbeitet auf einem Ordner.
4. **JobRouter-REST-Übergabe** – Stub liegt in `rechnung/jobrouter.py`, ist aber
   nicht verdrahtet.
5. **Rechnungspositionen (Line-Items)**.
6. **Mehrbenutzer / Server-Betrieb / zentrale Template-Verwaltung**.

## 8. Offene Punkte für v1.1

- Template-Export/-Import zwischen Kollegen (eine `.zip` mit `data/`).
- CSV-Sammelexport über alle Rechnungen eines Laufs.
- Konfigurierbare Konfidenz-Schwelle je Feld statt global 0,7.
- Umgang mit mehrseitigen Rechnungen mit Seitenumbruch in der Betragstabelle.
