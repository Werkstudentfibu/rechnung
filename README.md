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
rechnung --samples ./samples app    # Training-UI als eigenstaendiges Fenster (Endnutzer)
rechnung --samples ./samples web    # Training-UI im Browser (Entwicklung/Linux)
rechnung --samples ./samples scan   # alle PDFs verarbeiten (Konsole)
rechnung suppliers                  # bekannte Lieferanten
rechnung templates                  # gespeicherte Templates
```

`app` und `web` sind dieselbe Oberfläche (gleicher FastAPI-Code) – `app`
zeigt sie über `pywebview` in einem eigenständigen Fenster statt in einem
Browser-Tab. Für den produktiven Einsatz auf dem Firmen-Laptop ist `app`
gedacht.

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

## Bestätigungspflicht

Egal wie sicher eine Rechnung erkannt wird (auch `auto` bei einem längst
gesicherten Template): **jede** Rechnung braucht einen expliziten Klick auf
"Extraktion bestätigen" im Training-UI, bevor sie archiviert, geloggt oder an
JobRouter weitergereicht wird. Automatisch ist nur die Vorbefüllung/Kategorie-
Vorschläge – der Mensch drückt immer noch den Knopf und kann davor jederzeit
korrigieren.

**Langfristige Perspektive:** Ziel ist, schrittweise auf eine echte
Automatisierung (Rechnungen laufen ohne Klick durch) hinzuarbeiten – aber
erst, wenn die Fehlerquote im laufenden Testbetrieb nachweislich niedrig
genug dafür ist. Das ist noch nicht der Fall und noch nicht umgesetzt; die
Bestätigungspflicht oben gilt uneingeschränkt weiter, bis das explizit
(und mit eigener IT-Freigabe) geändert wird.

## Status-Werte

| Status | Bedeutung |
|---|---|
| `auto` | gesichertes Template, Konfidenz + Prüfungen ok → hohe Zuversicht, braucht aber trotzdem Bestätigung |
| `review` | Template vorhanden, aber Konfidenz/Prüfung schwach → manuell ansehen |
| `no_template` | Lieferant bekannt, noch kein Template → anlernen |
| `unknown_supplier` | Lieferant nicht erkannt → anlernen |
| `scan_no_textlayer` | kein Textlayer (Scan) → OCR nötig, v1 nicht unterstützt |

## Leichte Vorschlags-Hilfen (kein ML-Modell)

- **Kategorie je Position**: wird aus bisher bestätigten/korrigierten Positionen
  gelernt (Fuzzy-Textvergleich, keine Modell-Gewichte) und lieferantenübergreifend
  vorgeschlagen. Im Feld direkt korrigierbar – die Korrektur lernt sofort nach.
- **Ähnliche Lieferanten beim Anlernen**: bei neuem/unbekanntem Lieferanten schlägt
  das Training-UI Layouts mit ähnlichem Kopf-Fingerprint vor; "Vorlage übernehmen"
  kopiert deren Template als Startpunkt statt komplett neu anzulernen. Muss danach
  trotzdem erst wieder 3× bestätigt werden, bevor `auto` greift.

## Härtung (v1.3)

Nach einer Risiko-Durchsicht ergänzt: mehrdeutige Lieferanten-/Anker-Treffer
werden erkannt statt geraten (siehe `SPEC-v1.md` §7d), ein "Kategorien"-Reiter
macht die gelernte Kategorie-Tabelle sichtbar/korrigierbar, und alle
Zustandsdateien werden atomar mit Backup geschrieben -- ein Absturz mitten im
Speichern zerstört keine Daten mehr.

## Nicht in v1

OCR für Scans · generisches NER-Modell für Einmal-Lieferanten · IONOS-IMAP-Abholung ·
JobRouter-REST-Übergabe · Rechnungspositionen (Line-Items). Siehe `SPEC-v1.md` §7.
