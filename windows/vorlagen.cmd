@echo off
rem ---------------------------------------------------------------------------
rem  Gelernte Vorlagen anzeigen (rechnung templates): je Vorlage die Felder
rem  und ob sie eine Positionstabelle hat, am Ende die Zusammenfassung
rem  "Vorlagen (aktiv): ..., davon mit Positionstabelle: ...". Nur lesend.
rem
rem  Aufruf:  vorlagen.cmd C:\Rechnung
rem ---------------------------------------------------------------------------
setlocal
set "BASIS=%~f1"
if "%~1"=="" for %%I in ("%~dp0..\..") do set "BASIS=%%~fI"
cd /d "%BASIS%"
if exist "%BASIS%\rechnung.env" (
  for /f "usebackq eol=# tokens=* delims=" %%L in ("%BASIS%\rechnung.env") do set "%%L"
)
if not defined RECHNUNG_DATA set "RECHNUNG_DATA=%BASIS%\data"
set "PY=%BASIS%\app\.venv\Scripts\python.exe"
"%PY%" -m rechnung.cli --data "%RECHNUNG_DATA%" templates
exit /b %errorlevel%
