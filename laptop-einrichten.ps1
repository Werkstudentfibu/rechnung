<#
.SYNOPSIS
  Richtet "Rechnung" fuer die Testphase auf dem Firmenlaptop ein -- mit
  Selbst-Update aus dem privaten GitHub-Repo (Zweig release).

.DESCRIPTION
  Einmalig ausfuehren. Danach holt sich das Programm neue Staende selbst:
  beim Start, per Knopf "Update holen" im Programm und vor dem Nachtlauf.
  Anleitung: LAPTOP-UPDATE.md.

  Erwartet neben diesem Skript den Ordner "zugang" mit
    rechnung_deploy   (Lese-Schluessel nur fuer dieses eine Repo)
    known_hosts       (GitHub-Fingerabdruck, gegen untergeschobene Server)

  Aufbau danach:
    C:\Rechnung\app\            Programmcode (Git-Klon) + .venv
    C:\Rechnung\data\           alle Daten (wird von Updates nie angefasst)
    C:\Rechnung\eingang\        Rechnungen hier hineinlegen
    C:\Rechnung\rechnung.env    Einstellungen
    C:\Rechnung\zugang\         Lese-Schluessel
    C:\Rechnung\Rechnung starten.cmd, nachtlauf.cmd

  Aufruf (PowerShell als Administrator, im Ordner dieses Skripts):
    powershell -ExecutionPolicy Bypass -File .\laptop-einrichten.ps1 -MitTesseract -MitOllama -MitNachtlauf

.PARAMETER Ziel
  Basisordner. Standard: C:\Rechnung

.PARAMETER MitTesseract
  Texterkennung (Tesseract, deutsch) installieren -- weitergereicht an build-windows.ps1.

.PARAMETER MitOllama
  Lokales KI-Modell (Ollama) installieren -- weitergereicht an build-windows.ps1.

.PARAMETER MitNachtlauf
  Aufgabe "Rechnung Nachtlauf" taeglich 02:30 anlegen (inkl. Update davor).
#>

[CmdletBinding()]
param(
    [string]$Ziel = "C:\Rechnung",
    [switch]$MitTesseract,
    [switch]$MitOllama,
    [switch]$MitNachtlauf,
    [string]$Repo = "dollaaron0-del/rechnung"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Hier = Split-Path -Parent $MyInvocation.MyCommand.Path
New-Item -ItemType Directory -Force -Path $Ziel | Out-Null
$LogFile = Join-Path $Ziel "einrichten.log"
try { Start-Transcript -Path $LogFile -Force | Out-Null } catch { }

function Schritt([string]$t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }
function Ok([string]$t)      { Write-Host "    [ok] $t" -ForegroundColor Green }
function Hinweis([string]$t) { Write-Host "    [!]  $t" -ForegroundColor Yellow }
function Abbruch([string]$text, [string]$abhilfe = "") {
    Write-Host ""
    Write-Host "FEHLER: $text" -ForegroundColor Red
    if ($abhilfe) {
        Write-Host ""; Write-Host "Was tun?" -ForegroundColor Yellow
        foreach ($z in $abhilfe -split "`n") { Write-Host "  $z" }
    }
    Write-Host ""; Write-Host "Protokoll: $LogFile  (an den Entwickler schicken)"
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}
function Aufruf([string]$exe, [string[]]$argumente) {
    $alt = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & $exe @argumente | Out-Host; return $LASTEXITCODE }
    catch { Write-Host "    $($_.Exception.Message)"; return 9999 }
    finally { $ErrorActionPreference = $alt }
}
function PfadNeuLaden {
    $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $u = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$m;$u"
}

# ---------------------------------------------------------------------------
# 1. Git
# ---------------------------------------------------------------------------
Schritt "Git pruefen"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Abbruch "Git fehlt und winget ist nicht verfuegbar." `
            "Git fuer Windows von https://git-scm.com/download/win installieren (Standardoptionen),`ndanach dieses Skript erneut starten."
    }
    Write-Host "         Installiere Git per winget ..."
    $rc = Aufruf "winget" @("install", "-e", "--id", "Git.Git", "--accept-package-agreements", "--accept-source-agreements")
    if ($rc -ne 0) { Hinweis "winget meldete Code $rc" }
    PfadNeuLaden
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        $g = "C:\Program Files\Git\cmd"
        if (Test-Path (Join-Path $g "git.exe")) { $env:Path = "$env:Path;$g" }
    }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Abbruch "Git ist nach der Installation nicht auffindbar." "Neues PowerShell-Fenster oeffnen und das Skript erneut starten."
    }
}
Ok ("git " + ((& git --version) -replace "git version ", ""))

# ---------------------------------------------------------------------------
# 2. Lese-Schluessel
# ---------------------------------------------------------------------------
Schritt "Zugang einrichten"
$Zugang = Join-Path $Ziel "zugang"
$Key = Join-Path $Zugang "rechnung_deploy"
$Known = Join-Path $Zugang "known_hosts"
$QuelleZugang = Join-Path $Hier "zugang"
if ((Test-Path (Join-Path $QuelleZugang "rechnung_deploy")) -and ($QuelleZugang -ne $Zugang)) {
    New-Item -ItemType Directory -Force -Path $Zugang | Out-Null
    # Zweiter Lauf: der Schluessel vom ersten Lauf ist nur noch lesbar (icacls
    # unten) -- Copy-Item -Force scheitert daran. Erst wieder freigeben.
    if (Test-Path $Key) { $null = & icacls $Key /reset 2>&1 }
    Copy-Item -Force (Join-Path $QuelleZugang "rechnung_deploy") $Key
    Copy-Item -Force (Join-Path $QuelleZugang "known_hosts") $Known
}
if (-not (Test-Path $Key) -or -not (Test-Path $Known)) {
    Abbruch "Lese-Schluessel fehlt ($Key)." "Den Ordner 'zugang' aus dem Einrichtungspaket neben dieses Skript legen."
}
# Schluessel nur fuer den eigenen Benutzer lesbar (ssh verweigert sonst evtl.)
$null = & icacls $Key /inheritance:r /grant:r "$($env:USERNAME):(R)" 2>&1
Ok "Schluessel liegt in $Zugang"

$KeyFwd = $Key -replace "\\", "/"
$KnownFwd = $Known -replace "\\", "/"
# Port 443 statt 22: geht auch durch Firmen-Firewalls, die nur Web-Verkehr erlauben
$SshCmd = "ssh -i `"$KeyFwd`" -o IdentitiesOnly=yes -o UserKnownHostsFile=`"$KnownFwd`" -o StrictHostKeyChecking=yes"
$Url = "ssh://git@ssh.github.com:443/$Repo.git"

# ---------------------------------------------------------------------------
# 3. Programmcode holen
# ---------------------------------------------------------------------------
Schritt "Programmcode holen (Zweig release)"
$App = Join-Path $Ziel "app"
if (Test-Path (Join-Path $App ".git")) {
    $rc = Aufruf "git" @("-C", $App, "config", "core.sshCommand", $SshCmd)
    $rc = Aufruf "git" @("-C", $App, "fetch", "origin", "release")
    if ($rc -ne 0) { Abbruch "Verbindung zu GitHub fehlgeschlagen (Code $rc)." "Internet pruefen. Blockiert die Firma GitHub, bitte melden." }
    $rc = Aufruf "git" @("-C", $App, "reset", "--hard", "origin/release")
    Ok "vorhandener Klon auf neuesten Stand gebracht"
} else {
    if (Test-Path $App) { Abbruch "$App existiert, ist aber kein Git-Klon." "Ordner umbenennen und das Skript erneut starten." }
    $rc = Aufruf "git" @("-c", "core.sshCommand=$SshCmd", "clone", "-b", "release", $Url, $App)
    if ($rc -ne 0) {
        Abbruch "Programmcode konnte nicht geladen werden (Code $rc)." `
            "Internet pruefen. Meldung 'Permission denied' -> Schluessel falsch, an den Entwickler wenden.`nMeldung zu 'Host key' -> known_hosts veraltet, an den Entwickler wenden.`nZeitueberschreitung -> Firma blockiert die Verbindung, an den Entwickler wenden."
    }
    $rc = Aufruf "git" @("-C", $App, "config", "core.sshCommand", $SshCmd)
    Ok "Programmcode in $App"
}

# ---------------------------------------------------------------------------
# 4. Python, Abhaengigkeiten, Tests (+ optional Tesseract/Ollama)
# ---------------------------------------------------------------------------
Schritt "Python-Umgebung bauen (build-windows.ps1, ohne exe)"
$bargs = @("-ExecutionPolicy", "Bypass", "-File", (Join-Path $App "build-windows.ps1"), "-PythonInstallieren", "-OhneExe")
if ($MitTesseract) { $bargs += "-MitTesseract" }
if ($MitOllama) { $bargs += "-MitOllama" }
$rc = Aufruf "powershell" $bargs
if ($rc -ne 0) {
    Abbruch "build-windows.ps1 ist fehlgeschlagen (Code $rc)." "Die Meldung steht oben, Details in $App\build-windows.log."
}
Ok "Python-Umgebung fertig"

# ---------------------------------------------------------------------------
# 5. Ordner, Einstellungen, Starter
# ---------------------------------------------------------------------------
Schritt "Ordner und Starter anlegen"
foreach ($d in @("data", "eingang")) { New-Item -ItemType Directory -Force -Path (Join-Path $Ziel $d) | Out-Null }
$Env = Join-Path $Ziel "rechnung.env"
if (-not (Test-Path $Env)) {
    Copy-Item (Join-Path $App "rechnung.env.example") $Env
    Ok "rechnung.env aus der Vorlage angelegt"
} else {
    Ok "rechnung.env bleibt wie sie ist"
}

# Die Starter kopieren die eigentliche Logik vor dem Lauf nach %TEMP% --
# ein Update darf die laufende .cmd-Datei nicht ueberschreiben.
$Start = @(
    "@echo off",
    "rem Startet Rechnung mit Selbst-Update. Angelegt von laptop-einrichten.ps1.",
    "copy /y `"%~dp0app\windows\laptop-start.cmd`" `"%TEMP%\rechnung-laptop-start.cmd`" >nul",
    "call `"%TEMP%\rechnung-laptop-start.cmd`" `"%~dp0.`""
)
$Nacht = @(
    "@echo off",
    "rem Nachtlauf mit Update davor. Angelegt von laptop-einrichten.ps1.",
    "copy /y `"%~dp0app\windows\laptop-nachtlauf.cmd`" `"%TEMP%\rechnung-laptop-nachtlauf.cmd`" >nul",
    "call `"%TEMP%\rechnung-laptop-nachtlauf.cmd`" `"%~dp0.`""
)
$StartCmd = Join-Path $Ziel "Rechnung starten.cmd"
$NachtCmd = Join-Path $Ziel "nachtlauf.cmd"
Set-Content -Path $StartCmd -Value $Start -Encoding ASCII
Set-Content -Path $NachtCmd -Value $Nacht -Encoding ASCII
Ok "Starter: $StartCmd"

try {
    $sh = New-Object -ComObject WScript.Shell
    $lnk = $sh.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "Rechnung.lnk"))
    $lnk.TargetPath = $StartCmd
    $lnk.WorkingDirectory = $Ziel
    $lnk.Save()
    Ok "Verknuepfung 'Rechnung' auf dem Desktop"
} catch {
    Hinweis "Desktop-Verknuepfung nicht angelegt: $($_.Exception.Message)"
}

if ($MitNachtlauf) {
    $rc = Aufruf "schtasks" @("/Create", "/F", "/TN", "Rechnung Nachtlauf", "/TR", "`"$NachtCmd`"", "/SC", "DAILY", "/ST", "02:30")
    if ($rc -eq 0) { Ok "Aufgabe 'Rechnung Nachtlauf' taeglich 02:30" } else { Hinweis "Aufgabe nicht angelegt (Code $rc)" }
}

Write-Host ""
Write-Host "Fertig. Start: Desktop-Verknuepfung 'Rechnung' bzw. $StartCmd" -ForegroundColor Green
Write-Host "Updates kommen ab jetzt automatisch (beim Start, Knopf 'Update holen', vor dem Nachtlauf)."
try { Stop-Transcript | Out-Null } catch { }
exit 0
