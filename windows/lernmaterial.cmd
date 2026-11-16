@echo off
rem ---------------------------------------------------------------------------
rem  Lernmaterial gezielt aus den Postfaechern holen (rechnung lernmaterial,
rem  80/15-Regel). Nur lesend: in den Postfaechern wird nichts markiert oder
rem  verschoben. Die Rechnungen landen im Eingang (RECHNUNG_EINGANG).
rem
rem  Aufruf:  lernmaterial.cmd C:\Rechnung [--nur-anzeigen] [--neu-scannen]
rem ---------------------------------------------------------------------------
setlocal
set "BASIS=%~f1"
if "%~1"=="" for %%I in ("%~dp0..\..") do set "BASIS=%%~fI"
cd /d "%BASIS%"
if exist "%BASIS%\rechnung.env" (
  for /f "usebackq eol=# tokens=* delims=" %%L in ("%BASIS%\rechnung.env") do set "%%L"
)
if not defined RECHNUNG_DATA set "RECHNUNG_DATA=%BASIS%\data"
if not defined RECHNUNG_EINGANG set "RECHNUNG_EINGANG=%BASIS%\eingang"
set "PY=%BASIS%\app\.venv\Scripts\python.exe"
"%PY%" -m rechnung.cli --data "%RECHNUNG_DATA%" --samples "%RECHNUNG_EINGANG%" lernmaterial %2 %3 %4
exit /b %errorlevel%
