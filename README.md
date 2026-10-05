# Rechnung – Template-basierte Rechnungsextraktion (v1)

Liest Rechnungsdaten aus PDF-Rechnungen mit **Textlayer** (kein OCR, kein ML in v1).
Pro Lieferant wird das Layout **einmalig angelernt** (1–3 Rechnungen manuell), danach
laufen weitere Rechnungen desselben Lieferanten automatisch durch.

## Installation (Entwicklung)

```
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Optional, für Swiss-QR-Rechnungen (QR-Zahlteil lesen, siehe „Postfach“
unten): `.venv/bin/pip install -e ".[qr]"` (`zxing-cpp`). Fehlt das Paket,
degradiert `swissqr.py` sauber auf „nicht geprüft“ -- der Rest der
Rechnung läuft normal weiter.

## Nutzung

```
rechnung demo                       # Selbsttest mit synthetischen Rechnungen
rechnung --samples ./samples app    # Training-UI als eigenstaendiges Fenster (Endnutzer)
rechnung --samples ./samples web    # Training-UI im Browser (Entwicklung/Linux)
rechnung --samples ./samples scan   # alle PDFs verarbeiten (Konsole)
rechnung suppliers                  # bekannte Lieferanten
rechnung templates                  # gespeicherte Templates
rechnung schatten                   # Schattenbetrieb: haette das Programm allein richtig gelegen?
rechnung fehlerbericht              # haeufigste wiederkehrende Fehler/Korrekturen
rechnung menschlich                 # Ordner "Menschliche Bearbeitung": was wartet und warum
rechnung modus                      # Betriebsmodus + Fortschritt Testphase -> Autonomie
rechnung modus autonom --operator NAME   # ausdruecklich umschalten (Audit-Eintrag)
rechnung nacht                      # Nachtlauf: JobRouter, Layoutwechsel, Berichte, Gesundheit
rechnung imap-test                  # IONOS: Anmeldung/Ordner/ungelesen pruefen, ohne abzuholen
rechnung jobrouter-test              # JobRouter: Erreichbarkeit/Anmeldung pruefen, ohne anzulegen
rechnung messen <ordner> [--json]    # Stufenzeiten je Rechnung messen (Trockenlauf, nichts wird gespeichert)
rechnung vorlernen <ordner>          # Altbestand gruppieren, Vorlagen-Vorschlaege anlegen
rechnung vorlernen                   # Vorlernen-Gruppen auflisten
rechnung vorlernen --bestaetigen GRUPPE --operator NAME   # Vorlage einer Gruppe aktivieren
```

`app` und `web` sind dieselbe Oberfläche (gleicher FastAPI-Code) – `app`
zeigt sie über `pywebview` in einem eigenständigen Fenster statt in einem
Browser-Tab. Für den produktiven Einsatz auf dem Firmen-Laptop ist `app`
gedacht.

Daten (Lieferanten, Templates, Log) liegen unter `./data/` bzw. `$RECHNUNG_DATA`.
Bestätigte Rechnungen landen als `data/out/<name>.json` **und** `.csv`.

Weitergabe als eine `.exe` ohne Python-Installation: siehe `build.md`.
**Installation auf dem Windows-Firmenlaptop** (Build direkt dort, CPU,
optional Tesseract + Ollama mit kleinem Modell): `build-windows.ps1`, Anleitung
für Nicht-Entwickler in `INSTALL-WINDOWS.md`.
Vollständige v1-Abgrenzung (für die IT-Abnahme): siehe `SPEC-v1.md`.
Entwurf der aktualisierten IT-Freigabe (Laptop, OCR, Ollama, IONOS,
JobRouter, Ordner „Menschliche Bearbeitung“, Testphase/autonom, späterer
Server): `IT-FREIGABE-ENTWURF-v2.md` – `IT-FREIGABE.md` bleibt bis zur
Abstimmung unverändert.

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
| 4 – Entscheidung | Unsicheres → Ordner „Menschliche Bearbeitung“. Sicheres: in der **Testphase** (Standard) Klick „Extraktion bestätigen“, im Modus **autonom** ohne Klick an JobRouter (dort gibt ein Mensch frei). Der Schattenbetrieb misst, ob das Programm allein richtig entschieden hätte | – |

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
als Datengrundlage für den Fortschritt Testphase → Autonomie (unten) – an
der Bestätigungspflicht ändert sie nichts.

## Bestätigungspflicht (Testphase)

Im Betriebsmodus **Testphase** (Standard) gilt: egal wie sicher eine Rechnung
erkannt wird (auch `auto` bei einem längst gesicherten Template), **jede**
Rechnung braucht einen expliziten Klick auf "Extraktion bestätigen" im
Training-UI, bevor sie archiviert, geloggt oder an JobRouter weitergereicht
wird. Automatisch ist nur die Vorbefüllung/Kategorie-Vorschläge – der Mensch
drückt immer noch den Knopf und kann davor jederzeit korrigieren.

## Betriebsmodus: Testphase → Autonomie

Programmweit, bewusst schlank – jede Rechnung wird in JobRouter ohnehin noch
einmal von einem Menschen freigegeben.

- **`testphase`** (Standard): Bestätigungspflicht wie oben, der
  Schattenbetrieb misst. Kopfzeile im Training-UI, `rechnung modus` und der
  Nachtbericht (`betrieb`) zeigen den Fortschritt gegen einen Richtwert:
  standardmäßig **50 sichere Rechnungen in Folge ohne Korrektur** von
  **mindestens 10 verschiedenen Lieferanten**, kein Mindestzeitraum
  (`RECHNUNG_TESTPHASE_IN_FOLGE`, `RECHNUNG_TESTPHASE_LIEFERANTEN`). Eine
  Fehlentscheidung (hätte durchgelassen, Mensch/JobRouter hat korrigiert)
  setzt die Folge zurück; eine unsichere Rechnung, die zum Menschen ging,
  zählt nicht, unterbricht aber auch nicht.
- **`autonom`**: alles, was **nicht unsicher** ist, läuft ohne Klick durch
  `finalize` in die JobRouter-Warteschlange (`rechnung scan`, dann wie
  bisher `rechnung push`); im Audit und im JobRouter-Datensatz steht
  `freigabe: autonom`. Nur Unsicheres (siehe Ordner „Menschliche
  Bearbeitung“) geht zum Menschen.
- **Umschalten nur per Befehl**, nie automatisch – auch nicht beim
  Erreichen des Richtwerts: `rechnung modus autonom --operator NAME` bzw.
  `rechnung modus testphase --operator NAME` (Audit `betriebsmodus` mit
  altem/neuem Modus und Fortschritt; Hinweis, falls der Richtwert noch
  nicht erreicht ist).
- **Bekannt gewordene Fehler** (JobRouter-Rückmeldung) landen im
  Fehlerprotokoll/Fehlerbericht, im Schattenbetrieb (Fehlentscheidung) und
  lösen Neu-Lernen aus – auch für autonom übernommene Rechnungen.
- **Optional, Standard aus:** Stichprobe (`RECHNUNG_AUTONOM_STICHPROBE`,
  Prozent sicherer Rechnungen, die trotzdem zum Menschen gehen) und
  automatischer Rückfall in die Testphase bei einer JobRouter-Korrektur
  (`RECHNUNG_AUTONOM_RUECKFALL=1`).
- **IBAN-Abweichung** von den Stammdaten: `RECHNUNG_IBAN_ABWEICHUNG=mensch`
  (Standard: zurückgestellt → Ordner) oder `warnen` (geht durch, aber mit
  deutlichem Feld `warnung` „ACHTUNG IBAN weicht von den Stammdaten ab …“ im
  JobRouter-Datensatz; gezahlt wird weiterhin an die Stammdaten-IBAN).

Die bisherigen Prüfungen (Summen, IBAN-Prüfziffer, Datum, Dublette …)
bleiben unverändert – sie entscheiden nur darüber, ob eine Rechnung
„unsicher → Ordner“ ist.

## Status-Werte

| Status | Bedeutung |
|---|---|
| `auto` | gesichertes Template, Konfidenz + Prüfungen ok → hohe Zuversicht, braucht aber trotzdem Bestätigung |
| `review` | Template vorhanden, aber Konfidenz/Prüfung schwach → manuell ansehen |
| `no_template` | Lieferant bekannt, noch kein Template → anlernen |
| `zurückgestellt – menschlich bearbeiten` | kein eindeutiger Lieferant/keine eindeutige Rechnungsadresse aus der Bibliothek, IBAN weicht von den Stammdaten ab, OCR nicht verfügbar/fehlgeschlagen, Zeitgrenze überschritten → ein Mensch muss ran, Gründe stehen in `hold_reasons` |

`scan_no_textlayer` ist historisch und wird nicht mehr vergeben – Scans laufen
seit der Texterkennung (siehe unten) automatisch durch.

## Ordner „Menschliche Bearbeitung“

Das Programm rät nicht. Jede Rechnung, bei der es **nicht sicher** ist, legt
`rechnung scan` bzw. „Neu scannen“ im Training-UI als Kopie in einen eigenen
Ordner (`RECHNUNG_MENSCHLICH`, Standard `data/menschlich/`) – daneben eine
kleine Begleitdatei `<datei>.grund.json` mit den Gründen, den betroffenen
Feldern und den Vorschlagswerten samt Herkunft (`einvoice`/`template`/`llm`/
`library`, ggf. `via: ocr`).

Nicht sicher heißt: zurückgestellt (kein eindeutiger Lieferant/keine
eindeutige Rechnungsadresse, IBAN weicht von den Stammdaten ab, OCR nicht
verfügbar, Zeitgrenze, Verarbeitungsfehler), Pflichtfeld fehlt, Prüfung rot
(Summen, IBAN-Prüfziffer, Datum, Dublette …), OCR unsicher oder ein gelesener
Wert unter der Mindestkonfidenz 0,7 (z.B. ein reiner KI-Vorschlag).

Im Training-UI zeigt der Reiter **„Menschliche Bearbeitung“** die Liste mit
diesen Gründen (`GET /api/menschlich`, CLI `rechnung menschlich`). Klick auf
eine Zeile öffnet die Rechnung; nach Korrektur und **Bestätigung** verlässt
sie den Ordner (Begleitdatei mit Bearbeiter und korrigierten Feldern unter
`data/menschlich/erledigt/<sha>.json`), Stufe 1 lernt daraus die
Vorlage/Variante, und Ablage wie Erledigung stehen im Fehlerprotokoll
(`events.jsonl`, Fehlerbericht-Abschnitt „An Mensch gegeben“). Die Originale
im Eingangsordner bleiben unverändert.

## Texterkennung für Scans (OCR)

PDFs ohne Textebene sowie Bild-Anhänge (`.jpg`, `.jpeg`, `.png`, `.tif`,
`.tiff`) laufen automatisch durch **Tesseract** (Sprache `deu`, rein lokal,
kein Netzwerkzugriff) und danach normal durch dieselbe Pipeline wie jede
andere Rechnung. Das Original bleibt unverändert (Archiv/Hash/Korrekturen
hängen immer am Original); die erkannte Textebene liegt als Arbeitskopie
unter `data/ocr/<sha>.pdf`.

- **Tesseract ist eine Voraussetzung, keine Python-Abhängigkeit** – siehe
  `build.md` für die Installation. Ist es nicht installiert (oder fehlt das
  deutsche Sprachpaket `deu`), degradiert das Programm sauber: die Rechnung
  wird zurückgestellt (Grund „OCR nicht verfügbar“), nichts stürzt ab.
- Jede OCR-Rechnung bekommt eine Konfidenz (mittlere Wortkonfidenz). Liegt sie
  unter der Schwelle (`RECHNUNG_OCR_MIN_CONF`, Standard 80) oder steckt ein
  unsicher erkanntes Wort in einem gelesenen Feld, ist die Prüfung
  `ocr-konfidenz` rot bzw. das Feld fällt unter die Mindestkonfidenz – nie
  `auto`, nie „würde durchlassen“ im Schattenbetrieb.
- Felder, deren Wert aus der Texterkennung stammt, sind im Ergebnis zusätzlich
  mit `via: "ocr"` markiert (Stammdaten/manuelle Korrekturen nicht).
- Konfigurierbar über `RECHNUNG_TESSERACT`, `RECHNUNG_OCR_MIN_CONF`,
  `RECHNUNG_OCR_WORD_MIN_CONF` (siehe `rechnung.env.example`).

## Zeitgrenze pro Rechnung

Jede Rechnung bekommt für die komplette Verarbeitung (inkl. OCR und KI)
höchstens `RECHNUNG_TIMEOUT` Sekunden (Standard 120, `0` = keine Grenze).
Eine hängende Rechnung (z.B. ein in einem C-Parser feststeckendes PDF) wird
nach Ablauf hart abgebrochen und zurückgestellt – die Verarbeitung der
übrigen Warteschlange blockiert das nicht (`rechnung scan`/`app`/`web`
verarbeiten dafür jede Rechnung in einem eigenen Arbeitsprozess bzw.
Hintergrund-Thread, siehe `rechnung/guard.py`).

## Leichte Vorschlags-Hilfen (kein ML-Modell)

- **Kategorie je Position**: wird aus bisher bestätigten/korrigierten Positionen
  gelernt (Fuzzy-Textvergleich, keine Modell-Gewichte) und lieferantenübergreifend
  vorgeschlagen. Im Feld direkt korrigierbar – die Korrektur lernt sofort nach.
- **Ähnliche Lieferanten beim Anlernen**: bei neuem/unbekanntem Lieferanten schlägt
  das Training-UI Layouts mit ähnlichem Kopf-Fingerprint vor; "Vorlage übernehmen"
  kopiert deren Template als Startpunkt statt komplett neu anzulernen. Muss danach
  trotzdem erst wieder 3× bestätigt werden, bevor `auto` greift.

## Layoutwechsel erkennen

Findet eine **gesicherte** Vorlage bei einem Lieferanten zwei bestätigte
Rechnungen in Folge ein Pflichtfeld nicht oder geht eine Kernprüfung
(Beträge) nicht auf, markiert das Programm die Vorlage als `stale`
("veraltet") -- Audit `template_stale`. Die Vorlage wird **nicht gelöscht**,
aber nicht mehr angewendet: die nächsten Rechnungen laufen über die
KI-Stufe/Handeingabe, bis aus der nächsten Bestätigung automatisch eine neue
Vorlage/Variante gelernt ist (eigene Id `<lieferant>__<variante>__v2`, ...).

## Nachtlauf

`rechnung nacht [--json]`: holt JobRouter-Rückmeldungen und spielt sie als
Korrekturen ein, listet als veraltet markierte Vorlagen (§Layoutwechsel
erkennen) auf, schreibt Fehlerbericht + Tagesbericht (verarbeitet, an
Mensch, in den Ordner „Menschliche Bearbeitung“ gelegt, autonom übernommen,
Fehlerquote, Dauer-Verteilung, Rückstand) sowie Betriebsmodus und
Fortschritt Testphase → Autonomie (`betrieb`) als Datei unter
`data/berichte/<datum>_<zeit>.json` und führt Gesundheitschecks aus (IONOS
erreichbar, staut sich die JobRouter-Warteschlange, genug Plattenplatz).

Läuft exklusiv über eine Lock-Datei (`data/nacht.lock`, `rechnung/lock.py`):
die laufende Abholung (`rechnung fetch`) und jede Stapellauf-Verarbeitung
(`rechnung scan`, Training-UI) pausiert für die Dauer des Nachtlaufs --
eine Verzögerung dabei ist beabsichtigt und unkritisch. Ein zweiter
gleichzeitig gestarteter Nachtlauf bricht sofort mit einer Fehlermeldung ab,
statt parallel zu laufen.

**Zeitsteuerung** (Beispiel: jede Nacht um 2:30 Uhr):

Linux/macOS (`crontab -e`):
```
30 2 * * * cd /pfad/zum/programm && .venv/bin/rechnung --data ./data --samples ./samples nacht >> data/nacht.log 2>&1
```

Windows (Aufgabenplanung) über das mitgelieferte `nachtlauf.cmd` neben
`rechnung.exe` – es wechselt selbst in den Programmordner, liest
`rechnung.env` und schreibt nach `data\nacht.log` (mit
`MIT_VERARBEITUNG=1` vorher auch `fetch`, `scan`, `push`):
```
schtasks /Create /TN "Rechnung Nachtlauf" /TR "\"C:\Rechnung\nachtlauf.cmd\"" /SC DAILY /ST 02:30
```
Details: `INSTALL-WINDOWS.md`.

## Rückmeldung aus JobRouter

Die genaue JobRouter-API dafür ist noch unbekannt. `rechnung/jobrouter.py`
stellt deshalb eine Adapter-Schnittstelle (`FeedbackAdapter.fetch()`) bereit:
`FileFeedbackAdapter` (dateibasiert, `data/jobrouter_feedback/*.json`,
Standard ohne `RECHNUNG_JOBROUTER_ENABLED=1`) für Tests/Simulation,
`RestFeedbackAdapter` als Platzhalter für den scharfen Betrieb -- **noch
nicht gegen ein echtes JobRouter getestet**. `apply_feedback()` spielt eine
Rückmeldung mit korrigierten Feldern als Korrektur ein (Operator
`jobrouter`): `store.corrections`, Ereignisprotokoll, Schattenstatistik
(eine vorher "würde durchlassen" gewertete Rechnung kann so nachträglich
zur Fehlentscheidung werden) und stößt ggf. Neu-Lernen der Vorlage an.

## JobRouter-Übergabe für den echten Anschluss vorbereiten

Die echten Prozessdaten kommen erst vom JobRouter-Admin des Kunden. Bis
dahin ist alles Trockenlauf (`RECHNUNG_JOBROUTER_ENABLED` bleibt `0`) und
**nicht gegen ein echtes JobRouter getestet**:

- Prozessname/-version/Schritt/Benutzer/Rolle sind über
  `RECHNUNG_JOBROUTER_PROCESS`/`_VERSION`/`_STEP`/`_USER`/`_ROLE`
  konfigurierbar (siehe `rechnung.env.example`), nicht fest im Code.
- Die Feldzuordnung (unser Feldname → JobRouter-Feldname) kommt aus einer
  JSON-Datei (`RECHNUNG_JOBROUTER_MAPPING`, Standard
  `data/jobrouter-mapping.json`) statt fest im Code -- Beispiel siehe
  `jobrouter-mapping.example.json`. Ohne diese Datei (oder ohne Eintrag für
  ein Feld) bleibt der eigene Feldname unverändert.
- Das Original (PDF bzw. XML bei E-Rechnungen) geht als Anhang mit
  (`multipart/form-data`, wie es die JobRouter-REST-API für Incidents mit
  Dateien erwartet -- reine stdlib-Implementierung, kein neues Paket).
- `rechnung jobrouter-test` prüft nur Erreichbarkeit/Anmeldung (ein GET auf
  die Basis-URL mit dem konfigurierten Token), **ohne** eine Rechnung
  anzulegen. `rechnung imap-test` prüft analog Anmeldung + Ordnerliste +
  Anzahl ungelesener Mails am IONOS-Postfach, **ohne** etwas abzuholen oder
  als gelesen zu markieren.

### Checkliste: Was wir vom Kunden für den Anschluss brauchen

**IONOS-Postfach:**
- Host, Port (Standard `imap.ionos.de:993`, SSL)
- Benutzer (Postfach-Adresse) + Passwort bzw. App-Passwort
- Quellordner (Standard `INBOX`)
- Zielordner nach erfolgreicher Abholung (optional -- sonst bleibt die Mail
  im Quellordner, nur als gelesen markiert)

**JobRouter:**
- Basis-URL + Zugangsdaten (Bearer-Token oder Benutzer/Passwort -- je
  nachdem, was die JobRouter-Installation des Kunden verlangt)
- Prozessname, Prozessversion, Schritt, Benutzer/Rolle, unter denen die
  Rechnung angelegt werden soll
- Die JobRouter-Feldnamen für unsere Felder (`rechnungsnummer`,
  `rechnungsdatum`, `netto`, `ust_betrag`, `ust_satz`, `brutto`,
  `bestellnummer`, `iban`, `ustid`, `steuernummer`, `adresse`,
  `waehrung` (EUR/CHF, keine Umrechnung), `faelligkeit`, `skonto_prozent`,
  `skonto_frist`, `belegart` (Rechnung/Gutschrift/Rechnungskorrektur/
  Storno/Mahnung/Abschlags-/Schlussrechnung), `bezug_rechnungsnummer`
  (Verweis auf die Ursprungsrechnung bei Gutschrift/Storno/Mahnung),
  `zahlungsreferenz` (QRR/SCOR aus einer Schweizer QR-Rechnung), ...) für
  `jobrouter-mapping.json`. `attachments` (andere beschriftete
  Mail-Anhänge wie AGB/Lieferschein, siehe „Postfach“ unten) kommt als
  Liste `{dateiname, art}` mit, kein eigenes Mapping-Feld nötig.
- Der Rückmelde-Weg: wie/wo trägt ein Sachbearbeiter eine Korrektur oder
  Ablehnung in JobRouter ein, und wie liest unser Programm das zurück
  (REST-Endpunkt, Format) -- siehe "Rückmeldung aus JobRouter" oben

## Fehler-/Ereignisprotokoll + Fehlerbericht

Jede Verarbeitung (CLI `scan`, Bestätigen im Training-UI) hängt ein
strukturiertes Ereignis an `data/events.jsonl` an: Datei, sha256, Lieferant,
welche Stufen Werte beigetragen haben, Herkunft/Konfidenz je Feld, alle
Prüfungen mit Ergebnis/Grund, Dauer, Endstatus. Eine im Training-UI
vorgenommene Korrektur (Feld, alter Wert, neuer Wert) hängt sich als eigenes
Ereignis an. Rein additiv wie `audit.jsonl`/`shadow.jsonl`.

`rechnung fehlerbericht [--seit JJJJ-MM-TT] [--json]` bzw.
`GET /api/fehlerbericht?seit=...` werten das aus: häufigste wiederkehrende
rote Prüfungen und fehlende/unsichere Felder je Prüfung/Feld und Lieferant,
sowie häufige Korrekturen je Feld -- jeweils mit Beispiel-Rechnungen,
absteigend nach Häufigkeit sortiert. Zusätzlich `korrekturen_trend`: je
Lieferant Korrekturen pro Rechnung über die Zeit (letzte 10 vs. davor --
zeigt, ob eine Vorlage tatsächlich besser wird) und die am häufigsten
korrigierten Felder. Jede Korrektur trägt dafür Vorlage (`template_id`)
und Herkunft des Werts davor (`quelle_vorher`, z.B. `template-anchor`/
`llm`/`missing`) mit.

## Postfach: Abholung, Anhang-Auswahl, CHF/Schweiz (2026-10-05)

Ergänzungen rund um das IONOS-Postfach (`ingest.py`), siehe `SPEC-v1.md`
§7y für Details je Baustein:

- **Jede Mail führt zu einem Ergebnis** (BODY.PEEK[] statt RFC822): eine
  Mail ohne passenden Anhang wird nicht mehr still übersprungen, sondern
  geht mit Grund in den Ordner „Menschliche Bearbeitung“.
- **ZIP-Anhänge** werden entpackt (Grenzen gegen Zip-Bomben, kein
  Pfad-Traversal); **passwortgeschützte PDFs/ZIPs** gehen mit Grund an den
  Menschen.
- **Anhänge nach Beschriftung auswählen** (`belegart.py`, `anlagen.py`):
  nur Beleg-Anhänge (Rechnung/Gutschrift/Rechnungskorrektur/Storno/
  Mahnung/Abschlags-/Schlussrechnung) werden verarbeitet; andere
  beschriftete Anhänge derselben Mail (AGB, Lieferschein, ...) hängen als
  Anlage am Beleg (Archiv + JobRouter-Feld `attachments`). Kein erkannter
  Beleg oder ein nicht zuzuordnender Anhang -> Mensch, nie stillschweigend
  verworfen.
- **Sammel-PDFs** werden nur an sicheren Grenzen aufgeteilt (neue
  Rechnungsnummer + neuer Seitenzähler + neuer Summenblock); im Zweifel
  bleibt die Datei ganz und geht an den Menschen. **Lange Scans**: OCR
  liest zuerst Kopf/Fuß (120-s-Budget), der Rest folgt im Hintergrund.
- **Dubletten**: zusätzlich zu sha256/(Lieferant, Rechnungsnummer) auch
  normalisierte Rechnungsnummer, gleicher Lieferant+Betrag+Datum, und
  Mahnungen/Zahlungserinnerungen mit Verweis auf die bekannte Rechnung.
- **Fälligkeit/Skonto** (`zahlung.py`) und **Belegart/Vorzeichen**
  (`belegart.py`) werden auch aus reinen PDFs gelesen (Felder `faelligkeit`,
  `skonto_prozent`, `skonto_frist`, `belegart`, `bezug_rechnungsnummer`).
- **CHF/Schweizer Rechnungen**: Währung sicher aus dem Text erkennen
  (`waehrung.py`, Feld `waehrung`, **keine Umrechnung**), Schweizer
  Zahlenformat (`1'234.50`, `120.–`), Schweizer MWST-Sätze (8,1/2,6/3,8 %
  und die alten 7,7/2,5/3,7 %), Schweizer UID (`CHE-123.456.789`) wie eine
  USt-IdNr. gegen die Bibliothek abgeglichen. **Swiss QR-Rechnung**
  (`swissqr.py`): QR-Zahlteil lesen (optionale Abhängigkeit `zxing-cpp`,
  degradiert ohne sie sauber auf „nicht geprüft“), IBAN/Betrag gegen Text
  und Bibliothek geprüft (Abweichung -> Mensch wie beim Betrugsschutz),
  Zahlungsreferenz als Feld `zahlungsreferenz`. Andere Währungen als
  EUR/CHF gehen immer an den Menschen.

## Rechen-Prüfungen, Gegencheck, Selbstlernen von Tabelle/USt-Block

- **Rechen-Prüfungen** (`validate.py`): je Position `menge * einzelpreis`
  (abzüglich erkanntem Rabatt/Nachlass) = Zeilensumme (Prüfung
  `positionen-rechnung`); Positionssumme gegen **Netto** (Toleranz 0,02 +
  0,01 je Position), gegen **Brutto** nur mit erkennbarem
  Bruttopreis-Kennzeichen im Text (`positionen-summe`); je Steuersatz
  Positionssumme gegen die Zeile der USt-Aufstellung und USt-Betrag =
  netto × Satz (`steuersatz-positionen`). Jede rote Prüfung -> Mensch mit
  konkretem Grund; eine fehlende Positionstabelle ist kein Fehler.
- **Gegencheck in der Testphase** (`pipeline._gegencheck`): solange eine
  Vorlage unter `GEGENCHECK_MIN` Bestätigungen hat (Standard 5,
  `RECHNUNG_GEGENCHECK_MIN`), liest zusätzlich die KI unabhängig dieselbe
  Rechnung; weichen Vorlage und KI bei einem Pflichtfeld, einem Betrag
  oder der Positionssumme ab, geht die Rechnung mit beiden Werten
  nebeneinander an den Menschen. Reicht das 120-s-Budget nicht mehr, wird
  der Gegencheck übersprungen (Hinweis im Ereignisprotokoll). Aktiv in der
  Testphase; im autonomen Modus per Schalter `RECHNUNG_GEGENCHECK_AUTONOM`
  (Standard aus).
- **Positionstabelle und USt-Block selbst lernen** (`learn.py`,
  `learn_line_items`/`learn_taxblock`): aus bestätigten Positionen bzw.
  einer bestätigten USt-Aufstellung wird dieselbe Spec abgeleitet wie beim
  manuellen Anklicken -- nur gespeichert, wenn ein Probelauf die
  bestätigten Werte exakt reproduziert, sonst weggelassen statt geraten.
  Wird dasselbe Feld einer **gesicherten** Vorlage wiederholt in Folge
  korrigiert (Schwelle `RECHNUNG_STALE_FELD_SCHWELLE`, Standard 2), wird
  die Vorlage über den bestehenden Stale-/Versions-Mechanismus neu
  gelernt -- gesicherte Vorlagen werden nie still umgeschrieben.
- **Im UI: vorschlagen statt anklicken**: Vorschläge (KI/Vorlage/
  E-Rechnung) stehen vorausgefüllt in der Feldtabelle und sind im PDF
  umrahmt (mehrdeutige Werte bewusst ohne Rahmen). Positionstabelle und
  USt-Aufstellung sind direkt editierbar (Zelle, Zeile +/−, Spalte
  umbenennen) und werden mit „Positionen/USt-Aufstellung übernehmen“ als
  Korrektur gespeichert (`/api/tabelle`); beim Bestätigen lernt das
  Programm daraus die Tabelle für die nächste Rechnung. Die
  Rechen-Prüfungen laufen dabei live mit (rot/grün je Zeile,
  `/api/pruefen`).

## Härtung (v1.3)

Nach einer Risiko-Durchsicht ergänzt: mehrdeutige Lieferanten-/Anker-Treffer
werden erkannt statt geraten (siehe `SPEC-v1.md` §7d), ein "Kategorien"-Reiter
macht die gelernte Kategorie-Tabelle sichtbar/korrigierbar, und alle
Zustandsdateien werden atomar mit Backup geschrieben -- ein Absturz mitten im
Speichern zerstört keine Daten mehr.

## Effizienz: messen, gezielt lesen, Vorab-Lernen

Zielplattform ist ein Laptop/Server **ohne Grafikkarte** mit kleinem lokalem
Modell, ≤ 120 s je Rechnung, ~60 Rechnungen/Tag (Details SPEC §7x).

- **Messen:** `rechnung messen <ordner> [--json]` verarbeitet alle Dateien
  trocken (kein Speichern, keine Warteschlange, keine Ablage) und zeigt je
  Rechnung die Dauer der Stufen Laden/OCR/E-Rechnung/Lieferant/Vorlage/KI/
  Prüfungen sowie Median/P90/Max. Dieselbe Verteilung steht im
  Tagesbericht des Nachtlaufs (`dauer_je_stufe`).
- **KI liest gezielt:** Mit Vorlage fragt die KI nur fehlende Pflichtfelder
  (bzw. Felder der Vorlage) nach, mit Textausschnitt statt Volltext und
  passend kleinem Kontext; bei vollständiger Vorlage gar nicht. Antwortform
  per JSON-Schema (Ollama ≥ 0.5 / OpenAI `json_schema`, sonst Rückfall).
  Belegt wird immer gegen den vollen Text; fehlt ein Pflichtfeld wegen des
  Ausschnitts, wird einmal mit vollem Text nachgefasst – nie geraten.
- **OCR nur wo nötig:** nur Seiten ohne Textebene, parallel
  (`RECHNUNG_OCR_JOBS`, Standard Kerne−1; Laptop: 1–2).
- **Lieferant zuerst über USt-IdNr./IBAN:** gecachter Index der Bibliothek;
  zeigen Kennungen auf verschiedene Lieferanten → Mensch (Betrugsschutz).
- **Warteschlange nach Aufwand:** schnelle Fälle (E-Rechnung, Textebene +
  Vorlage) zuerst, OCR/KI dahinter; ältestes zuerst; Modell nach dem
  KI-Stapel freigegeben.
- **Vorab-Lernen aus Altbestand:** `rechnung vorlernen <ordner>` bzw. Reiter
  **„Vorlernen“** in der Oberfläche: alte Rechnungen werden nach Lieferant
  (Bibliothek) und Layout gruppiert; je Gruppe stehen alle Werte aller
  Rechnungen nebeneinander, fehlende/abweichende gelb markiert. Werte per
  Klick korrigieren, Rechnungen ausschließen, ggf. Lieferant zuordnen –
  **erst „Gruppe bestätigen“ macht die Vorlage aktiv** (startet mit 0
  Bestätigungen, Bestätigungspflicht für Live-Rechnungen bleibt).
  Vorgelernte Rechnungen gehen **nie** nach JobRouter und zählen nicht in
  der Schattenstatistik (Ablage `data/vorlernen/`, Audit
  `vorlernen_bestaetigt`).

## KI-Extraktion konfigurieren (optional)

Standardmäßig aus. Einschalten über `RECHNUNG_LLM_ENABLED=1` (siehe
`rechnung.env.example`). Backend wahlweise lokales **Ollama**
(`RECHNUNG_LLM_BACKEND=ollama`) oder ein **OpenAI-kompatibler Endpoint auf
eigener Hardware** wie vLLM oder llama.cpp-Server (`RECHNUNG_LLM_BACKEND=openai`,
`RECHNUNG_LLM_URL=http://<server>:8000/v1`). Modell über `RECHNUNG_LLM_MODEL`.
Es ist kein Cloud-Dienst eingebaut. KI-Werte sind in der Konfidenz gedeckelt
(nie allein `auto`) und werden verworfen, wenn sie nicht im PDF-Text stehen.

## Nicht in v1

OCR für Scans · IONOS-IMAP-Abholung · JobRouter-REST-Übergabe (alle drei
inzwischen nachgezogen, siehe `SPEC-v1.md` §7i–§7n).
