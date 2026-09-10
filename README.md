# Rechnung – Template-basierte Rechnungsextraktion (v1)

Liest Rechnungsdaten aus PDF-Rechnungen mit **Textlayer** (kein OCR, kein ML in v1).
Pro Lieferant wird das Layout **einmalig angelernt** (1–3 Rechnungen manuell), danach
laufen weitere Rechnungen desselben Lieferanten automatisch durch.

## Installation (Entwicklung)

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Nutzung

```
rechnung demo                       # Selbsttest mit synthetischen Rechnungen
rechnung --samples ./samples web    # Training-UI auf http://127.0.0.1:8000
rechnung --samples ./samples scan   # alle PDFs verarbeiten (Konsole)
rechnung suppliers                  # bekannte Lieferanten
rechnung templates                  # gespeicherte Templates
```

Daten (Lieferanten, Templates, Log) liegen unter `./data/` bzw. `$RECHNUNG_DATA`.
Bestätigte Rechnungen landen als `data/out/<name>.json` **und** `.csv`.

Weitergabe als eine `.exe` ohne Python-Installation: siehe `build.md`.
Vollständige v1-Abgrenzung (für die IT-Abnahme): siehe `SPEC-v1.md`.

## Ablauf im Training-UI

1. **Neu scannen** – Liste aller PDFs im Sample-Ordner mit Status.
2. Rechnung mit Status `unknown_supplier` / `no_template` öffnen.
3. Lieferanten-ID + Name eintragen.
4. Pro Feld: Zeile in der Tabelle wählen → im PDF **das Label anklicken mit Shift**
   (Anker) → **den Wert anklicken** (ein oder mehrere Wörter).
5. **Template speichern** – zeigt sofort die Extraktion.
6. Bei den nächsten Rechnungen desselben Lieferanten **Extraktion bestätigen**.
   Nach 3 Bestätigungen gilt das Template als *gesichert* → Status `auto`.

## Status-Werte

| Status | Bedeutung |
|---|---|
| `auto` | gesichertes Template, Konfidenz + Prüfungen ok → kann weitergeleitet werden |
| `review` | Template vorhanden, aber Konfidenz/Prüfung schwach → manuell ansehen |
| `no_template` | Lieferant bekannt, noch kein Template → anlernen |
| `unknown_supplier` | Lieferant nicht erkannt → anlernen |
| `scan_no_textlayer` | kein Textlayer (Scan) → OCR nötig, v1 nicht unterstützt |

## Nicht in v1

OCR für Scans · generisches NER-Modell für Einmal-Lieferanten · IONOS-IMAP-Abholung ·
JobRouter-REST-Übergabe · Rechnungspositionen (Line-Items). Siehe `SPEC-v1.md` §7.
