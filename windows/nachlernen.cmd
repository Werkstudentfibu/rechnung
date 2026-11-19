@echo off
rem ---------------------------------------------------------------------------
rem  Nachlernen (rechnung nachlernen): die Vorlagen aus den bereits
rem  bestaetigten Rechnungen mit dem heutigen Lernstand neu lernen. Ohne
rem  --ausfuehren wird nur angezeigt, was sich aendern wuerde.
rem
rem  Aufruf:  nachlernen.cmd C:\Rechnung                                  (nur anzeigen)
rem           nachlernen.cmd C:\Rechnung --ausfuehren --operator NAME     (speichern)
rem           nachlernen.cmd C:\Rechnung --lieferant ID                   (nur ein Lieferant)
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
"%PY%" -m rechnung.cli --data "%RECHNUNG_DATA%" nachlernen%OPT%
exit /b %errorlevel%
