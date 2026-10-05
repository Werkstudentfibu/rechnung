# Onefile-Build (Weitergabe zwischen Büros)

Ziel: **eine** ausführbare Datei, die ohne Python-Installation und ohne
Admin-Rechte startet.

## Windows-Laptop: build-windows.ps1 (empfohlen)

Gebaut wird **direkt auf dem Windows-Laptop** (PyInstaller cross-kompiliert
nicht; kein GitHub/CI). Das Skript `build-windows.ps1` erledigt alles in
einem Lauf und bricht bei jedem Problem mit einer klaren Meldung samt „Was
tun?“ ab; Protokoll in `build-windows.log`. Schritt-für-Schritt-Anleitung
für Nicht-Entwickler: **`INSTALL-WINDOWS.md`**.

```
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1 [-MitTesseract] [-MitOllama]
```

1. Python ≥ 3.11 suchen (`py -3.12/-3.13/-3.11/-3`, dann `python`; der
   Microsoft-Store-Platzhalter zählt nicht), sonst Hinweis bzw. mit
   `-PythonInstallieren` per `winget`.
2. `.venv` anlegen (eine mitkopierte Linux-venv ohne `Scripts\python.exe`
   wird ersetzt), `pip install -e ".[dev]" pyinstaller`, danach
   `pip install -e ".[qr]"` (QR-Zahlteil Swiss-QR-Rechnung; schlägt das
   fehl, nur ein Hinweis).
3. `pytest -q` – Abbruch bei roten Tests (`-TestsIgnorieren` nur nach
   Rücksprache, `-OhneTests` zum Überspringen).
4. `python -m PyInstaller --noconfirm --clean rechnung.spec` → `dist\rechnung.exe`,
   Starttest (`rechnung.exe modus` in einem leeren Temp-Datenordner).
5. Programmordner `dist\Rechnung\`: `rechnung.exe`, `start-rechnung.cmd`,
   `nachtlauf.cmd`, `rechnung.env.example`, `jobrouter-mapping.example.json`,
   `INSTALL-WINDOWS.md`, leere Ordner `data\` und `eingang\` (bestehende
   Daten werden nie angefasst).
6. `-MitTesseract` (Adminrechte): Tesseract per `winget`
   (`UB-Mannheim.TesseractOCR`) installieren, falls nicht gefunden; fehlt
   `deu`, `deu.traineddata` nach `tessdata\` laden; Testbild „Rechnung 4711“
   erkennen lassen.
7. `-MitOllama [-OllamaModell qwen2.5:1.5b-instruct]`: Ollama per `winget`
   bzw. `OllamaSetup.exe` installieren, Dienst auf `127.0.0.1:11434`
   abwarten/starten, Modell laden, Testanfrage mit Zeitmessung (Warnung
   ab 60 s).

`-NurPruefen` installiert/baut nichts und zeigt nur den Stand.

## PyInstaller-Spec (rechnung.spec)

Statt einer langen Kommandozeile steht die Build-Beschreibung in
`rechnung.spec`: alle Module des Pakets über `collect_submodules("rechnung")`
(neue Module wie `human.py`/`modus.py` und dynamisch geladene wie
`rechnung.pipeline` im Zeitgrenzen-Arbeitsprozess kommen automatisch mit),
`uvicorn`-Submodule, `collect_all` für pdfplumber, pdfminer, pypdfium2(_raw),
webview, clr_loader, pythonnet, zxingcpp (nicht installierte werden übersprungen),
`rechnung/web/static` als Daten. UPX aus (weniger Virenscanner-Fehlalarme),
Konsolenprogramm. Von Hand:

```
.venv\Scripts\python.exe -m pip install -e ".[dev]" pyinstaller
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean rechnung.spec
```

Linux/macOS identisch mit `.venv/bin/python`.

Ergebnis: `dist/rechnung` bzw. `dist/rechnung.exe` (~40–60 MB).

## Starten (Endnutzer)

Im Programmordner `start-rechnung.cmd` doppelklicken (liest `rechnung.env`
ein, Daten in `data\`, Eingang `eingang\`) – oder direkt:

```
rechnung.exe --samples "C:\Pfad\zu\Rechnungen" app
```

Öffnet ein eigenständiges Fenster (pywebview, Windows-Backend: Edge
WebView2 – auf Windows 10/11 durch Edge normalerweise bereits vorhanden,
keine separate Installation nötig). Kein Browser-Tab, kein sichtbarer
`127.0.0.1`-Link. Templates/Ergebnisse landen im Unterordner `data\` neben
der `.exe` (oder Umgebungsvariable `RECHNUNG_DATA` setzen).

Für die Entwicklung/unter Linux weiterhin `rechnung web` (Browser) nutzbar –
gleiche Oberfläche, nur ohne natives Fenster-Backend.

## run.py

Entry-Point für PyInstaller (liegt im Projektwurzelverzeichnis). Wichtig:
`multiprocessing.freeze_support()` – die Zeitgrenze je Rechnung (guard.py)
startet Arbeitsprozesse per „spawn“; ohne diesen Aufruf würde jeder
Arbeitsprozess der `.exe` das Programm erneut von vorn starten.

```python
import multiprocessing
import sys
from rechnung.cli import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
```

## Windows-Besonderheiten (SPEC §7w)

- Zeitgrenze: Arbeitsprozesse per `spawn` (wie unter Windows ohnehin
  einzig möglich), `freeze_support()` im Entry-Point.
- Tesseract: `RECHNUNG_TESSERACT` > `tesseract` im `PATH` >
  `%ProgramFiles%\Tesseract-OCR\tesseract.exe` (auch x86/`%LOCALAPPDATA%`);
  Aufruf ohne aufblitzendes Konsolenfenster; Ausgaben als UTF-8 gelesen.
  `RECHNUNG_OCR_ENABLED=0` schaltet die Texterkennung ganz ab.
- Lock-Datei des Nachtlaufs: `O_CREAT|O_EXCL` (atomar auch unter Windows),
  sofort geschlossen; liegengebliebene Sperren werden am Alter erkannt
  (`lock.STALE_S`, 6 h), nicht über Prozess-Signale.
- Konsole/Logdatei: nicht darstellbare Zeichen werden ersetzt statt
  abzustürzen (cp1252 bei `>> nacht.log`); CSV-Dateien mit BOM, damit Excel
  Umlaute richtig zeigt; Dateinamen werden mit `\` und `/` zerlegt.
- Startskripte `windows\start-rechnung.cmd` / `windows\nachtlauf.cmd`
  (ASCII, CRLF): wechseln in den eigenen Ordner, lesen `rechnung.env`
  (`for /f "eol=# ..."`), setzen `RECHNUNG_DATA`/`RECHNUNG_INBOX` auf
  `data\`/`eingang\`. Aufgabenplanung siehe `INSTALL-WINDOWS.md`.

## Texterkennung (OCR) – Tesseract installieren

Scans/Fotos ohne Textebene brauchen **Tesseract** mit dem deutschen
Sprachpaket -- eine eigenständige Installation, keine Python-Abhängigkeit
(siehe `rechnung/ocr.py`, `README.md`). Ohne Tesseract laufen alle anderen
Funktionen unverändert weiter; betroffene Rechnungen werden sauber mit dem
Grund „OCR nicht verfügbar" zurückgestellt.

- **Windows:** Installer von
  [UB-Mannheim/tesseract](https://github.com/UB-Mannheim/tesseract/wiki)
  (enthält `deu` standardmäßig). Pfad danach in `RECHNUNG_TESSERACT` eintragen,
  falls `tesseract.exe` nicht im `PATH` landet (Installer bietet das an).
- **Linux (Debian/Ubuntu):** `sudo apt install tesseract-ocr tesseract-ocr-deu`
- **macOS:** `brew install tesseract tesseract-lang`

Prüfen: `tesseract --list-langs` muss `deu` auflisten. Für den
PyInstaller-Build wird Tesseract **nicht** mitgebaut (eigener Prozessaufruf,
kein Python-Modul) -- es muss auf dem Zielrechner separat installiert sein,
und der Aufrufpfad entweder im `PATH` liegen oder über `RECHNUNG_TESSERACT`
gesetzt werden.

## Lokales KI-Modell (optional) – Ollama auf CPU

Standardmäßig aus. Auf dem Laptop ohne Grafikkarte nur ein kleines Modell
verwenden (Standard im Build-Skript `qwen2.5:1.5b-instruct`, ~1 GB; das
Programm-Standardmodell `qwen2.5:3b-instruct` ist auf CPU spürbar langsamer).
Ollama lauscht nur auf `127.0.0.1:11434`. Einschalten und Zeitgrenzen
anheben über das LAPTOP-PROFIL am Ende von `rechnung.env.example`
(`RECHNUNG_LLM_ENABLED=1`, `RECHNUNG_LLM_MODEL`, `RECHNUNG_LLM_CTX=2048`,
`RECHNUNG_LLM_TIMEOUT=120`, `RECHNUNG_TIMEOUT=300`). Installation/Prüfung:
`build-windows.ps1 -MitOllama`.

## Hinweise für die IT-Abnahme

- Kein Netzwerkzugriff; die Web-UI bindet nur an `127.0.0.1`.
- Keine Registry-Schreibzugriffe, kein Autostart, kein Dienst.
- SmartScreen/Defender meldet unsignierte Binaries – ggf. Code-Signing-Zertifikat
  der Firma verwenden: `signtool sign /fd SHA256 /a dist\rechnung.exe`.
- Tesseract (Apache-2.0) ist eine zusätzliche, separat zu installierende
  System-Abhängigkeit (kein Python-Paket, kein Netzwerkzugriff) – siehe oben.
