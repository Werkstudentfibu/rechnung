@echo off
rem ---------------------------------------------------------------------------
rem  Laptop-Testphase: Rechnung aus dem Git-Klon starten, mit Selbst-Update.
rem  Aufruf ueber "C:\Rechnung\Rechnung starten.cmd" (legt laptop-einrichten.ps1
rem  an). Der Starter kopiert diese Datei vorher nach %TEMP%, weil ein Update
rem  sie sonst waehrend des Laufs ueberschreiben koennte.
rem
rem  Ablauf: Update holen (Zweig release, Tests) -> Programm starten.
rem  Beendet sich das Programm mit Code 42 ("Update holen" + Neustart im
rem  Fenster), startet es sofort neu mit dem neuen Stand.
rem
rem  Erstes Argument: Basisordner (C:\Rechnung) mit app\, data\, rechnung.env
rem ---------------------------------------------------------------------------
setlocal
set "BASIS=%~f1"
if "%~1"=="" for %%I in ("%~dp0..\..") do set "BASIS=%%~fI"
cd /d "%BASIS%"
call :env
set "PY=%BASIS%\app\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo FEHLER: %PY% fehlt. Bitte laptop-einrichten.ps1 erneut ausfuehren.
  pause
  exit /b 1
)

:start
echo Suche nach Updates ...
"%PY%" -m rechnung.cli --data "%RECHNUNG_DATA%" update
echo Starte Rechnung ...
"%PY%" -m rechnung.cli --data "%RECHNUNG_DATA%" --samples "%RECHNUNG_EINGANG%" app
set "RC=%errorlevel%"
if "%RC%"=="42" (
  echo Neustart mit neuem Stand ...
  goto start
)
if not "%RC%"=="0" (
  echo.
  echo FEHLER: Rechnung wurde mit Fehlercode %RC% beendet.
  pause
)
exit /b %RC%

:env
rem Zeilen "NAME=wert" aus rechnung.env setzen; Zeilen mit # sind Kommentare.
if exist "%BASIS%\rechnung.env" (
  for /f "usebackq eol=# tokens=* delims=" %%L in ("%BASIS%\rechnung.env") do set "%%L"
)
if not defined RECHNUNG_DATA set "RECHNUNG_DATA=%BASIS%\data"
if not defined RECHNUNG_EINGANG set "RECHNUNG_EINGANG=%BASIS%\eingang"
if not defined RECHNUNG_INBOX set "RECHNUNG_INBOX=%RECHNUNG_EINGANG%"
if not exist "%RECHNUNG_DATA%" mkdir "%RECHNUNG_DATA%"
if not exist "%RECHNUNG_EINGANG%" mkdir "%RECHNUNG_EINGANG%"
exit /b 0
