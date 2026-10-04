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
rechnung schatten                   # Schattenbetrieb: haette das Programm allein richtig gelegen?
```

`app` und `web` sind dieselbe Oberfläche (gleicher FastAPI-Code) – `app`
zeigt sie über `pywebview` in einem eigenständigen Fenster statt in einem
Browser-Tab. Für den produktiven Einsatz auf dem Firmen-Laptop ist `app`
gedacht.

Daten (Lieferanten, Templates, Log) liegen unter `./data/` bzw. `$RECHNUNG_DATA`.
Bestätigte Rechnungen landen als `data/out/<name>.json` **und** `.csv`.

Weitergabe als eine `.exe` ohne Python-Installation: siehe `build.md`.
Vollständige v1-Abgrenzung (für die IT-Abnahme): siehe `SPEC-v1.md`.

## Ablauf: Stufen 0–4

Jede Rechnung durchläuft dieselbe Kette; die erste Stufe, die greift, liefert
die Werte. Die Herkunft steht je Feld im Ergebnis (`origin`: `einvoice` /
`template` / `llm` / `library` / `manual`).

| Stufe | Was passiert | Quelle |
|---|---|---|
| 0 – E-Rechnung | XRechnung/ZUGFeRD/Factur-X: Werte direkt aus dem XML | `einvoice` |
| 1 – gelernte Vorlage | Template des Lieferanten (manuell angelernt **oder automatisch aus bestätigten Rechnungen gelernt**) | `template` |
| 2 – KI-Extraktion | ohne Template: lokales/firmeneigenes Sprachmodell liest die ganze Rechnung (optional, Standard aus); jeder Wert muss im PDF-Text belegt sein | `llm` |
| 3 – Prüfungen | Netto+USt=Brutto, IBAN-Prüfziffer, Datum, Dubletten, Stammdaten-Abgleich | – |
| 4 – Entscheidung | **immer der Mensch** (Klick „Extraktion bestätigen“). Parallel misst der Schattenbetrieb, ob das Programm allein richtig entschieden hätte | – |

Stammdaten (IBAN, USt-IdNr, Adresse) kommen in jeder Stufe aus dem
Lieferanten-Datensatz (`library`), manuelle Korrekturen (`manual`) gewinnen immer.

## Ablauf im Training-UI

1. **Neu scannen** – Liste aller PDFs im Sample-Ordner mit Status.
2. Rechnung mit Status `unknown_supplier` / `no_template` öffnen. Ist die
   KI-Extraktion eingeschaltet, sind die Felder bereits vorbefüllt (Quelle
   `llm`); Vorschläge, die nicht im PDF stehen, werden verworfen und unter
   „Verworfene KI-Vorschläge“ angezeigt.
3. Lieferant anlegen bzw. Lieferanten-ID + Name eintragen.
4. Werte prüfen und ggf. korrigieren (Klick in die Spalte „aktuell“) –
   **oder** wie bisher pro Feld anlernen: Zeile wählen → im PDF **das Label
   mit Shift anklicken** (Anker) → **den Wert anklicken**, dann **Template speichern**.
5. **Extraktion bestätigen.** Ist der Lieferant bekannt, lernt das Programm
   dabei die Vorlage automatisch: es sucht die bestätigten Werte im PDF,
   nimmt das nächstgelegene eindeutige Label als Anker und speichert das
   Template nur, wenn es die bestätigten Werte exakt reproduziert
   (Audit `template_learned`). Mehrdeutige Werte ohne klares Label werden
   weggelassen statt geraten.
6. Bei den nächsten Rechnungen desselben Lieferanten greift die Vorlage
   (Stufe 1); jede Bestätigung ergänzt/präzisiert sie. Nach 3 Bestätigungen
   gilt das Template als *gesichert* → Status `auto`. Gesicherte Templates
   werden nur noch ergänzt, nie automatisch umgeschrieben.

## Schattenbetrieb

Für jede bestätigte Rechnung hält das Programm fest, ob es sie **selbst
durchgelassen hätte** (alle Pflichtfelder da, keine Prüfung rot, bei neuem
Lieferanten nicht nur aus der KI) – entschieden auf dem Stand *vor* manuellen
Korrekturen – und vergleicht das mit dem, was der Mensch bestätigt bzw.
korrigiert hat (auch ein an dieser Rechnung nachtrainierter Anker zählt als
Korrektur). `rechnung schatten` bzw. `GET /api/shadow` zeigt je Lieferant:
Rechnungen · würde durchlassen · davon ohne Korrektur · Fehlentscheidungen
(hätte durchgelassen, Mensch musste korrigieren). Das ist **reine Messung**
als Datengrundlage für die langfristige Perspektive unten – an der
Bestätigungspflicht ändert sie nichts.

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

## KI-Extraktion konfigurieren (optional)

Standardmäßig aus. Einschalten über `RECHNUNG_LLM_ENABLED=1` (siehe
`rechnung.env.example`). Backend wahlweise lokales **Ollama**
(`RECHNUNG_LLM_BACKEND=ollama`) oder ein **OpenAI-kompatibler Endpoint auf
eigener Hardware** wie vLLM oder llama.cpp-Server (`RECHNUNG_LLM_BACKEND=openai`,
`RECHNUNG_LLM_URL=http://<server>:8000/v1`). Modell über `RECHNUNG_LLM_MODEL`.
Es ist kein Cloud-Dienst eingebaut. KI-Werte sind in der Konfidenz gedeckelt
(nie allein `auto`) und werden verworfen, wenn sie nicht im PDF-Text stehen.

## Nicht in v1

OCR für Scans · IONOS-IMAP-Abholung · JobRouter-REST-Übergabe (beide inzwischen
in v1.1 nachgezogen). Siehe `SPEC-v1.md` §7.
