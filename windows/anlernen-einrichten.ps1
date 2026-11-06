<#
  Legt die Windows-Aufgabe "Rechnung Anlernen" an (ANLERNEN-CLAUDE.md):
  Montag bis Freitag von 08:00 bis 17:00 alle 30 Minuten eine kleine
  Anlern-Runde mit Claude (windows\anlernen.cmd), ohne sichtbares Fenster.
  Laeuft nur, wenn der Benutzer angemeldet ist -- keine Adminrechte noetig.

  Aufruf (PowerShell):
    powershell -ExecutionPolicy Bypass -File C:\Rechnung\app\windows\anlernen-einrichten.ps1
  Entfernen:
    powershell -ExecutionPolicy Bypass -File C:\Rechnung\app\windows\anlernen-einrichten.ps1 -Entfernen
#>
param(
    [string]$Ziel = "C:\Rechnung",
    [string]$Von = "08:00",
    [int]$Stunden = 9,
    [int]$AlleMinuten = 30,
    [switch]$Entfernen
)
$ErrorActionPreference = "Stop"
$Name = "Rechnung Anlernen"

if ($Entfernen) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Aufgabe '$Name' entfernt." -ForegroundColor Green
    exit 0
}

$Cmd = Join-Path $Ziel "app\windows\anlernen.cmd"
if (-not (Test-Path $Cmd)) {
    Write-Host "FEHLER: $Cmd fehlt -- erst 'Update holen'." -ForegroundColor Red
    exit 1
}
New-Item -ItemType Directory -Force -Path (Join-Path $Ziel "uebung") | Out-Null

# Vorher nach %TEMP% kopieren (wie Start/Nachtlauf), damit ein Update die
# laufende Datei nicht ueberschreibt.
$Befehl = "Copy-Item -LiteralPath '$Cmd' -Destination (Join-Path `$env:TEMP 'rechnung-anlernen.cmd') -Force; " +
          "& cmd.exe /c (Join-Path `$env:TEMP 'rechnung-anlernen.cmd') '$Ziel'"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"$Befehl`"" `
    -WorkingDirectory $Ziel

$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $Von
$Wiederholung = New-ScheduledTaskTrigger -Once -At $Von `
    -RepetitionInterval (New-TimeSpan -Minutes $AlleMinuten) `
    -RepetitionDuration (New-TimeSpan -Hours $Stunden)
$Trigger.Repetition = $Wiederholung.Repetition

$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 25)

Register-ScheduledTask -TaskName $Name -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Anlernen mit Claude (ANLERNEN-CLAUDE.md). Tut nichts ohne RECHNUNG_CLAUDE_LEHRER=1." `
    -Force | Out-Null

Write-Host "Aufgabe '$Name' angelegt: Mo-Fr ab $Von, $Stunden Stunden lang alle $AlleMinuten Minuten." -ForegroundColor Green
Write-Host "Uebungsrechnungen ablegen in: $(Join-Path $Ziel 'uebung')"
Write-Host "Protokoll: $(Join-Path $Ziel 'data\anlernen.log')"
Write-Host "Gleich testen: Start-ScheduledTask -TaskName '$Name'"
exit 0
