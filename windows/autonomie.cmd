@echo off
rem ---------------------------------------------------------------------------
rem  Autonomie je Lieferant (rechnung autonomie): zeigt je Lieferant, wie
rem  viele Rechnungen in Folge sicher und richtig waren; bewaehrte Lieferanten
rem  koennen ausdruecklich freigegeben werden (laufen dann ohne Klick).
rem  Freigeben erst, wenn JobRouter angeschlossen ist.
rem
rem  Aufruf:  autonomie.cmd C:\Rechnung                                       (nur anzeigen)
rem           autonomie.cmd C:\Rechnung --alle-bereiten --operator NAME        (freigeben)
rem           autonomie.cmd C:\Rechnung --entziehen ID --operator NAME         (zuruecknehmen)
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
"%PY%" -m rechnung.cli --data "%RECHNUNG_DATA%" autonomie%OPT%
exit /b %errorlevel%
