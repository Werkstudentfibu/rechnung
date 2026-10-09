@echo off
rem ---------------------------------------------------------------------------
rem  Laptop-Testphase: IONOS-Postfach jetzt abholen (statt erst im Nachtlauf).
rem  Doppelklick auf C:\Rechnung\app\windows\abholen.cmd -- holt die Anhaenge
rem  neuer Mails nach eingang\; danach im Programm die Liste neu laden.
rem  Nur Abholen, kein Verarbeiten und keine Uebergabe an JobRouter.
rem ---------------------------------------------------------------------------
setlocal
for %%I in ("%~dp0..\..") do set "BASIS=%%~fI"
cd /d "%BASIS%"
if exist "%BASIS%\rechnung.env" (
  for /f "usebackq eol=# tokens=* delims=" %%L in ("%BASIS%\rechnung.env") do set "%%L"
)
if not defined RECHNUNG_DATA set "RECHNUNG_DATA=%BASIS%\data"
if not defined RECHNUNG_EINGANG set "RECHNUNG_EINGANG=%BASIS%\eingang"
if not exist "%RECHNUNG_EINGANG%" mkdir "%RECHNUNG_EINGANG%"
set "PY=%BASIS%\app\.venv\Scripts\python.exe"
"%PY%" -m rechnung.cli --data "%RECHNUNG_DATA%" fetch --dest "%RECHNUNG_EINGANG%"
echo.
pause
