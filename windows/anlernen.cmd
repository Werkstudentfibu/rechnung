@echo off
rem ---------------------------------------------------------------------------
rem  Anlernen mit Claude im Hintergrund (rechnung/lehrer.py, ANLERNEN-CLAUDE.md).
rem  Wird von der Aufgabe "Rechnung Anlernen" aufgerufen (anlernen-einrichten.ps1)
rem  -- tagsueber alle 10 Minuten eine Runde; laeuft eine Runde noch, faellt
rem  der naechste Start aus (MultipleInstances IgnoreNew), so arbeitet der
rem  Lehrer praktisch durch, solange etwas zu tun ist.
rem
rem  Erstes Argument: Basisordner (C:\Rechnung) mit app\, data\, rechnung.env
rem
rem  Tut nichts, solange RECHNUNG_CLAUDE_LEHRER nicht 1 ist oder gerade der
rem  Nachtlauf laeuft. Uebungsrechnungen: RECHNUNG_UEBUNG (Standard
rem  C:\Rechnung\uebung), je Runde hoechstens RECHNUNG_CLAUDE_LIMIT (Standard 40)
rem  und hoechstens RECHNUNG_CLAUDE_MINUTEN (Standard 50) lang -- danach endet
rem  die Runde zwischen zwei Rechnungen, die naechste macht weiter.
rem  Schon von Claude bestaetigte Rechnungen werden uebersprungen (kostenlos).
rem ---------------------------------------------------------------------------
setlocal
set "BASIS=%~f1"
if "%~1"=="" for %%I in ("%~dp0..\..") do set "BASIS=%%~fI"
cd /d "%BASIS%"
if exist "%BASIS%\rechnung.env" (
  for /f "usebackq eol=# tokens=* delims=" %%L in ("%BASIS%\rechnung.env") do set "%%L"
)
if not defined RECHNUNG_DATA set "RECHNUNG_DATA=%BASIS%\data"
if not defined RECHNUNG_UEBUNG set "RECHNUNG_UEBUNG=%BASIS%\uebung"
if not defined RECHNUNG_CLAUDE_LIMIT set "RECHNUNG_CLAUDE_LIMIT=40"
if not defined RECHNUNG_CLAUDE_MINUTEN set "RECHNUNG_CLAUDE_MINUTEN=50"
if not "%RECHNUNG_CLAUDE_LEHRER%"=="1" exit /b 0
if not exist "%RECHNUNG_DATA%" mkdir "%RECHNUNG_DATA%"
if not exist "%RECHNUNG_UEBUNG%" mkdir "%RECHNUNG_UEBUNG%"
set "LOG=%RECHNUNG_DATA%\anlernen.log"
if exist "%RECHNUNG_DATA%\nacht.lock" (
  echo ==== %date% %time% Nachtlauf aktiv, Runde ausgelassen ==== >> "%LOG%"
  exit /b 0
)
set "PY=%BASIS%\app\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo FEHLER: %PY% fehlt. >> "%LOG%"
  exit /b 1
)
echo ==== %date% %time% Anlernen (%RECHNUNG_UEBUNG%, max %RECHNUNG_CLAUDE_LIMIT%, max %RECHNUNG_CLAUDE_MINUTEN% min) ==== >> "%LOG%"
"%PY%" -m rechnung.lehrer --data "%RECHNUNG_DATA%" "%RECHNUNG_UEBUNG%" --limit %RECHNUNG_CLAUDE_LIMIT% --minuten %RECHNUNG_CLAUDE_MINUTEN% >> "%LOG%" 2>&1
exit /b %errorlevel%
