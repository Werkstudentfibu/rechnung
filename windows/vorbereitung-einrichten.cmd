@echo off
rem ---------------------------------------------------------------------------
rem  EINMAL ausfuehren (Doppelklick): legt in der Windows-Aufgabenplanung zwei
rem  Aufgaben an, damit Rechnungen vorbereitet werden, auch wenn das Programm
rem  nicht offen ist:
rem
rem    "Rechnung Vorbereiten (Tag)"   Mo-Fr, 07:00-19:00, alle 30 Minuten
rem    "Rechnung Vorbereiten (Nacht)" taeglich ab 20:00, stuendlich bis 06:00
rem
rem  Beide rufen windows\vorbereiten.cmd auf. Ueberlappung ist unschaedlich
rem  (der zweite Aufruf beendet sich sofort). Aendern: Zeiten unten anpassen und
rem  erneut ausfuehren (/F ueberschreibt). Entfernen:
rem    schtasks /Delete /TN "Rechnung Vorbereiten (Tag)" /F
rem    schtasks /Delete /TN "Rechnung Vorbereiten (Nacht)" /F
rem  Sofort testen:
rem    schtasks /Run /TN "Rechnung Vorbereiten (Tag)"
rem ---------------------------------------------------------------------------
setlocal
set "CMD=%~dp0vorbereiten.cmd"
if not exist "%CMD%" (
  echo FEHLER: %CMD% nicht gefunden.
  pause
  exit /b 1
)
schtasks /Create /F /TN "Rechnung Vorbereiten (Tag)" /TR "\"%CMD%\"" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 07:00 /RI 30 /DU 12:00
schtasks /Create /F /TN "Rechnung Vorbereiten (Nacht)" /TR "\"%CMD%\"" /SC DAILY /ST 20:00 /RI 60 /DU 10:00
echo.
echo Fertig. Pruefen: schtasks /Query /TN "Rechnung Vorbereiten (Tag)"
pause
