@echo off
rem ---------------------------------------------------------------------------
rem  Rechnung starten (Bedienoberflaeche als eigenes Fenster).
rem  Liegt neben rechnung.exe. Liest rechnung.env (falls vorhanden) ein.
rem  Daten:   <dieser Ordner>\data      Eingang: <dieser Ordner>\eingang
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
call :env
call :exe || exit /b 1
"%RECHNUNG_EXE%" --data "%RECHNUNG_DATA%" --samples "%RECHNUNG_EINGANG%" app
set "RC=%errorlevel%"
if not "%RC%"=="0" (
  echo.
  echo FEHLER: Rechnung wurde mit Fehlercode %RC% beendet.
  pause
)
exit /b %RC%

:env
rem Zeilen "NAME=wert" aus rechnung.env setzen; Zeilen mit # sind Kommentare.
if exist "%~dp0rechnung.env" (
  for /f "usebackq eol=# tokens=* delims=" %%L in ("%~dp0rechnung.env") do set "%%L"
)
if not defined RECHNUNG_DATA set "RECHNUNG_DATA=%~dp0data"
if not defined RECHNUNG_EINGANG set "RECHNUNG_EINGANG=%~dp0eingang"
rem IONOS-Abholung legt in denselben Eingangsordner ab, den scan liest
if not defined RECHNUNG_INBOX set "RECHNUNG_INBOX=%RECHNUNG_EINGANG%"
if not exist "%RECHNUNG_DATA%" mkdir "%RECHNUNG_DATA%"
if not exist "%RECHNUNG_EINGANG%" mkdir "%RECHNUNG_EINGANG%"
exit /b 0

:exe
rem rechnung.exe neben diesem Skript, sonst aus dem Quellcode-venv
set "RECHNUNG_EXE=%~dp0rechnung.exe"
if exist "%RECHNUNG_EXE%" exit /b 0
set "RECHNUNG_EXE=%~dp0..\.venv\Scripts\rechnung.exe"
if exist "%RECHNUNG_EXE%" exit /b 0
set "RECHNUNG_EXE=%~dp0.venv\Scripts\rechnung.exe"
if exist "%RECHNUNG_EXE%" exit /b 0
echo FEHLER: rechnung.exe nicht gefunden (erwartet in %~dp0).
echo Bitte zuerst build-windows.ps1 ausfuehren, siehe INSTALL-WINDOWS.md.
pause
exit /b 1
