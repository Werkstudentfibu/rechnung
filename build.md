# Onefile-Build (Weitergabe zwischen Büros)

Ziel: **eine** ausführbare Datei, die ohne Python-Installation und ohne
Admin-Rechte startet.

## Voraussetzung

Auf dem Build-Rechner (idealerweise Windows, wenn die Kollegen Windows nutzen –
PyInstaller cross-kompiliert nicht):

```
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]" pyinstaller
```

## Bauen

```
.venv\Scripts\pyinstaller --onefile --name rechnung ^
  --collect-all pdfplumber --collect-all pdfminer ^
  --collect-binaries pypdfium2 ^
  --collect-all webview --collect-all clr_loader ^
  --add-data "rechnung/web/static;rechnung/web/static" ^
  -c run.py
```

Linux/macOS: `:` statt `;` im `--add-data`, `^` durch `\` ersetzen.

Ergebnis: `dist/rechnung` bzw. `dist/rechnung.exe` (~40–60 MB).

## Starten (Endnutzer)

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

Entry-Point für PyInstaller (liegt im Projektwurzelverzeichnis):

```python
from rechnung.cli import main
import sys
sys.exit(main())
```

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

## Hinweise für die IT-Abnahme

- Kein Netzwerkzugriff; die Web-UI bindet nur an `127.0.0.1`.
- Keine Registry-Schreibzugriffe, kein Autostart, kein Dienst.
- SmartScreen/Defender meldet unsignierte Binaries – ggf. Code-Signing-Zertifikat
  der Firma verwenden: `signtool sign /fd SHA256 /a dist\rechnung.exe`.
- Tesseract (Apache-2.0) ist eine zusätzliche, separat zu installierende
  System-Abhängigkeit (kein Python-Paket, kein Netzwerkzugriff) – siehe oben.
