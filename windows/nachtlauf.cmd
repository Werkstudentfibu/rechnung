@echo off
rem ---------------------------------------------------------------------------
rem  Nachtlauf fuer die Windows-Aufgabenplanung (siehe INSTALL-WINDOWS.md).
rem  Liegt neben rechnung.exe. Schreibt alles nach data\nacht.log.
rem
rem  Aufgabe einrichten (einmalig, Eingabeaufforderung):
rem    schtasks /Create /TN "Rechnung Nachtlauf" /TR "\"C:\Rechnung\nachtlauf.cmd\"" /SC DAILY /ST 02:30
rem
rem  MIT_VERARBEITUNG=1 (z.B. in rechnung.env): vor dem Nachtlauf zusaetzlich
rem  IONOS abholen (fetch), verarbeiten (scan) und an JobRouter uebergeben (push).
rem  Im Modus "autonom" laufen sichere Rechnungen dabei ohne Klick durch;
rem  unsichere landen im Ordner "Menschliche Bearbeitung".
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
call :env
call :exe || exit /b 1
set "LOG=%RECHNUNG_DATA%\nacht.log"
echo ==== %date% %time% ==== >> "%LOG%"
if "%MIT_VERARBEITUNG%"=="1" (
  "%RECHNUNG_EXE%" --data "%RECHNUNG_DATA%" --samples "%RECHNUNG_EINGANG%" fetch >> "%LOG%" 2>&1
  "%RECHNUNG_EXE%" --data "%RECHNUNG_DATA%" --samples "%RECHNUNG_EINGANG%" scan >> "%LOG%" 2>&1
  "%RECHNUNG_EXE%" --data "%RECHNUNG_DATA%" --samples "%RECHNUNG_EINGANG%" push >> "%LOG%" 2>&1
)
"%RECHNUNG_EXE%" --data "%RECHNUNG_DATA%" --samples "%RECHNUNG_EINGANG%" nacht >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo ==== Ende, Code %RC% ==== >> "%LOG%"
exit /b %RC%

:env
if exist "%~dp0rechnung.env" (
  for /f "usebackq eol=# tokens=* delims=" %%L in ("%~dp0rechnung.env") do set "%%L"
)
if not defined RECHNUNG_DATA set "RECHNUNG_DATA=%~dp0data"
if not defined RECHNUNG_EINGANG set "RECHNUNG_EINGANG=%~dp0eingang"
if not defined RECHNUNG_INBOX set "RECHNUNG_INBOX=%RECHNUNG_EINGANG%"
if not exist "%RECHNUNG_DATA%" mkdir "%RECHNUNG_DATA%"
if not exist "%RECHNUNG_EINGANG%" mkdir "%RECHNUNG_EINGANG%"
exit /b 0

:exe
set "RECHNUNG_EXE=%~dp0rechnung.exe"
if exist "%RECHNUNG_EXE%" exit /b 0
set "RECHNUNG_EXE=%~dp0..\.venv\Scripts\rechnung.exe"
if exist "%RECHNUNG_EXE%" exit /b 0
set "RECHNUNG_EXE=%~dp0.venv\Scripts\rechnung.exe"
if exist "%RECHNUNG_EXE%" exit /b 0
echo FEHLER: rechnung.exe nicht gefunden (erwartet in %~dp0). >> "%~dp0nacht-fehler.log"
exit /b 1
