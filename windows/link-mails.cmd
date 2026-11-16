@echo off
rem ---------------------------------------------------------------------------
rem  Mails ohne Rechnungsanhang zaehlen (rechnung link-mails): wie viele haben
rem  einen direkten Download-Link, wie viele fuehren in ein Kundenportal mit
rem  Anmeldung, wie viele haben gar keinen Link. Nur lesend: es wird KEIN
rem  Link geoeffnet und nichts veraendert. Ausgabe nur Zahlen und Domains.
rem
rem  Aufruf:  link-mails.cmd C:\Rechnung
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
"%PY%" -m rechnung.cli --data "%RECHNUNG_DATA%" link-mails %2 %3
exit /b %errorlevel%
