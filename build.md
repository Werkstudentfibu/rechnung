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
  --add-data "rechnung/web/static;rechnung/web/static" ^
  -c run.py
```

Linux/macOS: `:` statt `;` im `--add-data`, `^` durch `\` ersetzen.

Ergebnis: `dist/rechnung` bzw. `dist/rechnung.exe` (~40–60 MB).

## Starten (Endnutzer)

```
rechnung.exe --samples "C:\Pfad\zu\Rechnungen" web
```

Dann Browser auf `http://127.0.0.1:8000`. Templates/Ergebnisse landen im
Unterordner `data\` neben der `.exe` (oder Umgebungsvariable `RECHNUNG_DATA`
setzen).

## run.py

Entry-Point für PyInstaller (liegt im Projektwurzelverzeichnis):

```python
from rechnung.cli import main
import sys
sys.exit(main())
```

## Hinweise für die IT-Abnahme

- Kein Netzwerkzugriff; die Web-UI bindet nur an `127.0.0.1`.
- Keine Registry-Schreibzugriffe, kein Autostart, kein Dienst.
- SmartScreen/Defender meldet unsignierte Binaries – ggf. Code-Signing-Zertifikat
  der Firma verwenden: `signtool sign /fd SHA256 /a dist\rechnung.exe`.
