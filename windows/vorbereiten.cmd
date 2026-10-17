@echo off
rem ---------------------------------------------------------------------------
rem  Rechnungen vorab auslesen -- auch ohne offenes Programmfenster (Aaron
rem  17.10.2026): Postfach abholen (falls eingerichtet), dann jede Rechnung im
rem  Eingang nacheinander komplett auslesen (Texterkennung + KI). Nichts wird
rem  bestaetigt, gesendet oder gelernt; wer spaeter prueft, wartet nicht mehr.
rem
rem  Gedacht fuer die Aufgabenplanung (einmal einrichten mit
rem  vorbereitung-einrichten.cmd) oder per Doppelklick. Laeuft mit niedriger
rem  Prioritaet. Ein zweiter Aufruf waehrend ein Lauf arbeitet beendet sich
rem  sofort; ein gleichzeitig offenes Programmfenster rechnet dieselbe Datei
rem  nie doppelt (Sperre je Datei). Protokoll: data\vorbereiten.log
rem ---------------------------------------------------------------------------
setlocal
for %%I in ("%~dp0..\..") do set "BASIS=%%~fI"
cd /d "%BASIS%"
if exist "%BASIS%\rechnung.env" (
  for /f "usebackq eol=# tokens=* delims=" %%L in ("%BASIS%\rechnung.env") do set "%%L"
)
if not defined RECHNUNG_DATA set "RECHNUNG_DATA=%BASIS%\data"
if not defined RECHNUNG_EINGANG set "RECHNUNG_EINGANG=%BASIS%\eingang"
if not exist "%RECHNUNG_DATA%" mkdir "%RECHNUNG_DATA%"
if not exist "%RECHNUNG_EINGANG%" mkdir "%RECHNUNG_EINGANG%"
set "PY=%BASIS%\app\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo FEHLER: %PY% fehlt. >> "%RECHNUNG_DATA%\vorbereiten.log"
  exit /b 1
)
echo ==== %date% %time% ==== >> "%RECHNUNG_DATA%\vorbereiten.log"
start "" /b /low /wait "%PY%" -m rechnung.cli --data "%RECHNUNG_DATA%" --samples "%RECHNUNG_EINGANG%" vorbereiten >> "%RECHNUNG_DATA%\vorbereiten.log" 2>&1
exit /b %errorlevel%
