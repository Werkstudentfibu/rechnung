# Installation auf dem Windows-Laptop – Schritt für Schritt

Diese Anleitung ist für jemanden ohne Programmiererfahrung gedacht. Alles
passiert direkt auf dem Laptop; es wird nichts ins Internet hochgeladen.
Gebraucht werden: Adminrechte, eine Internetverbindung (nur für die
Installation) und etwa 30–60 Minuten.

Wenn etwas schiefgeht: Das Skript schreibt alles in die Datei
`build-windows.log` im Projektordner – diese Datei an den Entwickler schicken.

---

## Schritt 1 – Projektordner auf den Laptop kopieren

1. Den Projektordner (der Ordner, in dem `build-windows.ps1` und
   `pyproject.toml` liegen) z.B. per USB-Stick oder als ZIP auf den Laptop
   kopieren.
2. Nach `C:\rechnung-build` legen (kurzer Pfad ohne Umlaute und Leerzeichen
   vermeidet Probleme).
3. Falls darin ein Ordner `.venv` mitkopiert wurde: egal, das Skript legt ihn
   bei Bedarf neu an.

## Schritt 2 – Python installieren (falls noch nicht vorhanden)

Das Skript prüft das selbst und sagt Bescheid, wenn Python fehlt. Dann:

- **Variante A (einfach):** in Schritt 3 zusätzlich `-PythonInstallieren`
  anhängen – das installiert Python 3.12 über `winget`.
- **Variante B:** Python 3.12 von <https://www.python.org/downloads/windows/>
  herunterladen und installieren. **Wichtig:** im ersten Fenster des
  Installers unten das Häkchen **„Add python.exe to PATH“** setzen.

Danach ein **neues** PowerShell-Fenster öffnen.

## Schritt 3 – Bauen

1. Startmenü → „PowerShell“ eintippen → **Rechtsklick → „Als Administrator
   ausführen“** (Adminrechte braucht nur die Tesseract-Installation, schaden
   aber nicht).
2. In den Projektordner wechseln:

   ```
   cd C:\rechnung-build
   ```

3. Bauen – mit Texterkennung und lokalem KI-Modell:

   ```
   powershell -ExecutionPolicy Bypass -File .\build-windows.ps1 -MitTesseract -MitOllama
   ```

   Nur das Programm, ohne Texterkennung/KI:

   ```
   powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
   ```

   `-ExecutionPolicy Bypass` gilt nur für diesen einen Aufruf und ändert
   keine Einstellung am Rechner.

Das Skript macht nacheinander (jeweils mit `[ok]` oder einer klaren
Fehlermeldung samt „Was tun?“):

| Schritt | Was passiert |
|---|---|
| Python prüfen | Version 3.11 oder neuer vorhanden? |
| `.venv` anlegen | eigene, abgeschlossene Python-Umgebung im Projektordner |
| Abhängigkeiten | Programmbibliotheken aus dem Internet laden (pypi.org) |
| Tests | rund 500 automatische Prüfungen – alle müssen grün sein |
| `rechnung.exe` bauen | eine einzelne Programmdatei, danach Starttest |
| Programmordner | `dist\Rechnung` mit Programm, Startdateien, Vorlage `rechnung.env.example` |
| Tesseract (optional) | Installation, deutsches Sprachpaket, Testbild erkennen |
| Ollama (optional) | Installation, kleines Modell `qwen2.5:1.5b-instruct` (~1 GB) laden, Testanfrage mit Zeitmessung |

Zusätzlich (kein eigener Schalter, direkt nach den Abhängigkeiten):
`zxing-cpp` zum Lesen des QR-Zahlteils einer Schweizer QR-Rechnung.
Schlägt diese Installation fehl, erscheint nur ein Hinweis -- das Programm
liest Swiss-QR-Rechnungen dann ohne diese zusätzliche Prüfung weiter, kein
Abbruch.

Dauer: 10–30 Minuten, je nach Internet und Laptop.

**Häufige Meldungen:**

- *Installation der Abhängigkeiten fehlgeschlagen* – meist Firmen-Proxy.
  Vor dem Aufruf eintippen (Adresse von der IT):
  `$env:HTTPS_PROXY = "http://proxy.firma.local:8080"`
- *Bau der rechnung.exe fehlgeschlagen / startet nicht* – meist hat der
  Virenscanner die frisch gebaute Datei blockiert. IT bitten, den Ordner
  `C:\rechnung-build\dist` auszunehmen, dann erneut bauen.
- *Mindestens ein Test ist fehlgeschlagen* – `build-windows.log` an den
  Entwickler schicken. Nur nach Rücksprache mit `-TestsIgnorieren` bauen.
- *Modell antwortet sehr langsam* – auf einem Laptop ohne Grafikkarte normal.
  Dann die KI ausgeschaltet lassen (Standard) oder die Zeitgrenzen im
  Laptop-Profil von `rechnung.env` erhöhen (Schritt 5).

Nur prüfen, ohne etwas zu installieren: `-NurPruefen` anhängen.

## Schritt 4 – Programm an seinen Platz kopieren

1. Den Ordner `C:\rechnung-build\dist\Rechnung` nach **`C:\Rechnung`**
   kopieren.
2. Darin liegen:
   - `rechnung.exe` – das Programm
   - `start-rechnung.cmd` – startet die Bedienoberfläche
   - `nachtlauf.cmd` – für die nächtliche Aufgabe (Schritt 7)
   - `rechnung.env.example` – Vorlage für die Einstellungen
   - `data\` – hier speichert das Programm alles (Lieferanten, Vorlagen,
     Protokolle, Archiv, Ordner `menschlich\`)
   - `eingang\` – hier die zu verarbeitenden Rechnungen hineinlegen

**Bei späteren Updates** nur `rechnung.exe` (und ggf. die beiden `.cmd`)
ersetzen – `data\` und `rechnung.env` bleiben, wie sie sind.

## Schritt 5 – Einstellungen

1. In `C:\Rechnung` die Datei `rechnung.env.example` kopieren und die Kopie
   in **`rechnung.env`** umbenennen.
2. Mit dem Editor öffnen (Rechtsklick → Öffnen mit → Editor).
3. Für den Laptop ganz unten das **LAPTOP-PROFIL** nutzen: bei den
   gewünschten Zeilen das `# ` am Anfang entfernen.
4. Format: `NAME=wert` – ohne Leerzeichen um das `=`, ohne
   Anführungszeichen. Zeilen mit `#` am Anfang sind Kommentare.

Für den Anfang muss nichts geändert werden: ohne Einstellungen arbeitet das
Programm rein lokal, ohne Postfach, ohne JobRouter, ohne KI.

## Schritt 6 – Starten

`C:\Rechnung\start-rechnung.cmd` doppelklicken (gern eine Verknüpfung auf
den Desktop ziehen). Es öffnet sich ein eigenes Programmfenster; das
schwarze Konsolenfenster daneben bitte offen lassen, es zeigt
Fehlermeldungen.

- Rechnungen in `C:\Rechnung\eingang` legen, im Programm **„Neu scannen“**.
- Unsichere Rechnungen erscheinen im Reiter **„Menschliche Bearbeitung“**
  (und als Kopie im Ordner `C:\Rechnung\data\menschlich`).
- Oben rechts steht der **Betriebsmodus** (Testphase) und der Fortschritt.

## Schritt 7 – Nachtlauf einrichten (optional)

Einmalig in einer Eingabeaufforderung (Startmenü → `cmd`):

```
schtasks /Create /TN "Rechnung Nachtlauf" /TR "\"C:\Rechnung\nachtlauf.cmd\"" /SC DAILY /ST 02:30
```

- Läuft jede Nacht um 2:30 Uhr, solange der Laptop an ist und der Benutzer
  angemeldet ist (Windows-Standard; der Laptop darf gesperrt sein).
- Ergebnis und Fehler stehen in `C:\Rechnung\data\nacht.log`, der Bericht in
  `C:\Rechnung\data\berichte\`.
- Soll der Nachtlauf zusätzlich das Postfach abholen, verarbeiten und an
  JobRouter übergeben: in `rechnung.env` die Zeile `MIT_VERARBEITUNG=1`
  ergänzen (erst sinnvoll, wenn IONOS/JobRouter eingerichtet sind).
- Testen ohne zu warten: `schtasks /Run /TN "Rechnung Nachtlauf"`.
- Entfernen: `schtasks /Delete /TN "Rechnung Nachtlauf" /F`.

## Schritt 8 – Alte Rechnungen vorlernen (empfohlen, vor dem ersten Echtbetrieb)

Die alten Rechnungen (z.B. ~170 Stück) in einen Ordner legen, etwa
`C:\Rechnung\altbestand`. Dann entweder im Programm den Reiter
**„Vorlernen“** öffnen, den Ordner eintragen und „Ordner einlesen“ klicken –
oder in einer Eingabeaufforderung:

```
cd /d C:\Rechnung
rechnung.exe --data .\data vorlernen C:\Rechnung\altbestand
```

Das Programm bildet Gruppen je Lieferant und Layout. Im Reiter „Vorlernen“
stehen je Gruppe alle Rechnungen nebeneinander; **gelb** heißt: Wert fehlt
oder weicht ab. Werte per Klick korrigieren, unpassende Rechnungen
ausschließen, dann **„Gruppe bestätigen“** – erst dann ist die Vorlage aktiv.
Diese alten Rechnungen gehen **nicht** an JobRouter und zählen nicht für die
Testphase. Lieferanten müssen vorher in der Bibliothek stehen (Reiter
„Lieferanten“ bzw. Abbyy-Import); eine Gruppe ohne erkannten Lieferanten
lässt sich dort zuordnen.

## Schritt 9 – Geschwindigkeit messen

Wie lange braucht der Laptop je Rechnung und wofür? Ein paar typische
Rechnungen in einen Ordner legen und:

```
rechnung.exe --data .\data messen C:\Rechnung\messung
```

Es wird nichts gespeichert oder weitergegeben; angezeigt werden je Rechnung
die Zeiten für Laden, Texterkennung, Lieferant, Vorlage, KI und Prüfungen
sowie Median/P90/Max. Liegt eine Rechnung nahe an 120 s, in `rechnung.env`
z.B. `RECHNUNG_OCR_JOBS=1` bzw. ein kleineres Modell wählen – die
120-s-Grenze selbst bleibt; was nicht rechtzeitig fertig wird, landet in
„Menschliche Bearbeitung“.

## Schritt 10 – Testphase und Umschalten auf autonom

Das Programm startet immer in der **Testphase**: jede Rechnung muss im
Programm bestätigt werden. Wenn der Fortschritt oben rechts den Richtwert
erreicht (Standard: 50 sichere Rechnungen in Folge ohne Korrektur, 10
verschiedene Lieferanten), kann in den autonomen Modus geschaltet werden –
**nur von Hand**, in einer Eingabeaufforderung:

```
cd /d C:\Rechnung
rechnung.exe --data .\data modus autonom --operator "Vorname Nachname"
```

Zurück in die Testphase:

```
rechnung.exe --data .\data modus testphase --operator "Vorname Nachname"
```

Jedes Umschalten wird mit Namen und Zeitpunkt protokolliert. Im autonomen
Modus gehen sichere Rechnungen ohne Klick an JobRouter (dort gibt weiterhin
ein Mensch frei); unsichere landen wie bisher in „Menschliche Bearbeitung“.
