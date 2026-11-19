@echo off
rem ---------------------------------------------------------------------------
rem  Blindtest (rechnung testsatz --blind): die bestaetigten Rechnungen neu
rem  lesen, als waere jeder Lieferant neu -- ohne seine Vorlagen und sein
rem  Gedaechtnis. Zeigt, wie gut das Programm unbekannte Lieferanten liest
rem  und ob es ohne Mensch fehlerfrei gewesen waere. Nur messen: Rechnungen
rem  und Vorlagen bleiben unveraendert, der Bericht landet in data\berichte.
rem
rem  Aufruf:  blindtest.cmd C:\Rechnung
rem           blindtest.cmd C:\Rechnung --max 30      (Stichprobe, schneller)
rem           blindtest.cmd C:\Rechnung --ohne-ki     (nur Uebertragung ohne KI)
rem ---------------------------------------------------------------------------
setlocal
rem Ablageort merken, bevor "shift" auch %0 verschiebt
set "HIER=%~dp0"
set "BASIS="
set "ERST=%~1"
if defined ERST if not "%ERST:~0,2%"=="--" (
  set "BASIS=%~f1"
  shift
)
set "OPT="
:args
if "%~1"=="" goto weiter
set "OPT=%OPT% %1"
shift
goto args
:weiter
if not defined BASIS for %%I in ("%HIER%..\..") do set "BASIS=%%~fI"
cd /d "%BASIS%"
if exist "%BASIS%\rechnung.env" (
  for /f "usebackq eol=# tokens=* delims=" %%L in ("%BASIS%\rechnung.env") do set "%%L"
)
if not defined RECHNUNG_DATA set "RECHNUNG_DATA=%BASIS%\data"
set "PY=%BASIS%\app\.venv\Scripts\python.exe"
"%PY%" -m rechnung.cli --data "%RECHNUNG_DATA%" testsatz --blind%OPT%
exit /b %errorlevel%
