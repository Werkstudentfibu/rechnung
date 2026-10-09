<#
.SYNOPSIS
  Texterkennung (Tesseract, deutsch) OHNE Adminrechte einrichten.

.DESCRIPTION
  Fuer Firmenlaptops ohne echte Adminrechte (09.10.2026): holt micromamba
  (eine einzelne exe) und damit Tesseract von conda-forge in einen eigenen
  Ordner <Basis>\tesseract -- keine Installation, kein Eintrag in Windows.
  Das deutsche Sprachpaket liegt im Programm (windows\tessdata). Danach wird
  in <Basis>\rechnung.env die Texterkennung eingeschaltet.

  Aufruf (normale PowerShell, KEIN Admin noetig):
    powershell -ExecutionPolicy Bypass -File C:\Rechnung\app\windows\texterkennung-einrichten.ps1

  Rueckgaengig: Ordner C:\Rechnung\tesseract loeschen und in rechnung.env
  RECHNUNG_OCR_ENABLED=0 setzen.
#>
[CmdletBinding()]
param([string]$Basis = "")

$ErrorActionPreference = "Stop"
if (-not $Basis) { $Basis = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path }
$Ziel   = Join-Path $Basis "tesseract"
$Mamba  = Join-Path $Basis ".mamba"
$Exe    = Join-Path $Mamba "micromamba.exe"
$Tess   = Join-Path $Ziel "Library\bin\tesseract.exe"
$Daten  = Join-Path $Ziel "Library\share\tessdata"
$EnvDat = Join-Path $Basis "rechnung.env"
$Log    = Join-Path $Basis "texterkennung.log"
try { Start-Transcript -Path $Log -Force | Out-Null } catch { }

function Schritt([string]$t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }
function Ok([string]$t)      { Write-Host "    [ok] $t" -ForegroundColor Green }
function Abbruch([string]$t) {
    Write-Host "    [FEHLER] $t" -ForegroundColor Red
    Write-Host "Protokoll: $Log  (an den Entwickler schicken)"
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Schritt "1/4 micromamba holen"
if (-not (Test-Path $Exe)) {
    New-Item -ItemType Directory -Force -Path $Mamba | Out-Null
    $url = "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64"
    try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $Exe }
    catch { Abbruch "Download micromamba fehlgeschlagen ($url): $($_.Exception.Message)" }
}
Ok $Exe

Schritt "2/4 Tesseract von conda-forge nach $Ziel (dauert ein paar Minuten)"
if (-not (Test-Path $Tess)) {
    & $Exe create -y -p $Ziel -r $Mamba -c conda-forge --override-channels tesseract
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Tess)) { Abbruch "micromamba create fehlgeschlagen (Exit $LASTEXITCODE)" }
}
Ok $Tess

Schritt "3/4 Deutsches Sprachpaket"
New-Item -ItemType Directory -Force -Path $Daten | Out-Null
Copy-Item -Force (Join-Path $PSScriptRoot "tessdata\deu.traineddata") $Daten
$env:TESSDATA_PREFIX = $Daten
$langs = (& $Tess --list-langs 2>&1) -join " "
if ($langs -notmatch "\bdeu\b") { Abbruch "Tesseract findet 'deu' nicht: $langs" }
Ok "Sprachen: $langs"

Schritt "4/4 Texterkennung in rechnung.env einschalten"
if (Test-Path $EnvDat) {
    # Byte-genau (Latin-1 = 1:1) lesen/schreiben: Passwoerter mit Umlauten
    # o.ae. in rechnung.env duerfen nicht veraendert werden.
    $enc = [Text.Encoding]::GetEncoding(28591)
    $zeilen = @([IO.File]::ReadAllLines($EnvDat, $enc) | Where-Object { $_ -notmatch '^\s*RECHNUNG_OCR_ENABLED\s*=' })
    $zeilen += "RECHNUNG_OCR_ENABLED=1"
    [IO.File]::WriteAllLines($EnvDat, [string[]]$zeilen, $enc)
    Ok "RECHNUNG_OCR_ENABLED=1 in $EnvDat"
} else {
    Ok "keine rechnung.env -- Texterkennung ist im Programm ohnehin Standard"
}

Write-Host ""
Write-Host "Fertig. Programm 'Rechnung' neu starten." -ForegroundColor Green
try { Stop-Transcript | Out-Null } catch { }
exit 0
