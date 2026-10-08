@echo off
rem ---------------------------------------------------------------------------
rem  Laptop-Testphase: Nachtlauf mit vorherigem Selbst-Update.
rem  Aufruf ueber "C:\Rechnung\nachtlauf.cmd" (Aufgabenplanung, legt
rem  laptop-einrichten.ps1 an). Wie laptop-start.cmd wird diese Datei vorher
rem  nach %TEMP% kopiert, damit das Update sie nicht im Lauf ueberschreibt.
rem
rem  Erstes Argument: Basisordner (C:\Rechnung) mit app\, data\, rechnung.env
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
if not exist "%RECHNUNG_DATA%" mkdir "%RECHNUNG_DATA%"
set "PY=%BASIS%\app\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo FEHLER: %PY% fehlt. >> "%RECHNUNG_DATA%\nacht.log"
  exit /b 1
)
echo ==== %date% %time% Update ==== >> "%RECHNUNG_DATA%\nacht.log"
"%PY%" -m rechnung.cli --data "%RECHNUNG_DATA%" update >> "%RECHNUNG_DATA%\nacht.log" 2>&1
rem Der eigentliche Nachtlauf (findet die venv ueber app\.venv, Daten ueber
rem die oben gesetzten Variablen) -- erst nach dem Update aufgerufen.
call "%BASIS%\app\windows\nachtlauf.cmd"
exit /b %errorlevel%
