<#
.SYNOPSIS
  Baut "rechnung" direkt auf diesem Windows-Rechner: Python pruefen, venv
  anlegen, Abhaengigkeiten installieren, Tests laufen lassen, rechnung.exe
  bauen und einen fertigen Programmordner dist\Rechnung zusammenstellen.
  Optional: Tesseract (Texterkennung, deutsch) und Ollama (lokales KI-Modell,
  CPU-tauglich) installieren und pruefen.

.DESCRIPTION
  Schritt-fuer-Schritt-Anleitung: INSTALL-WINDOWS.md.
  Aufruf (PowerShell im Projektordner):

    powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
    powershell -ExecutionPolicy Bypass -File .\build-windows.ps1 -MitTesseract -MitOllama

  Ein Protokoll jedes Laufs steht in build-windows.log.

.PARAMETER MitTesseract
  Tesseract-OCR installieren (falls noch nicht da), deutsches Sprachpaket
  sicherstellen und mit einem Testbild pruefen. Braucht Adminrechte.

.PARAMETER MitOllama
  Ollama installieren (falls noch nicht da), das Modell -OllamaModell laden und
  mit einer Testanfrage pruefen.

.PARAMETER OllamaModell
  Kleines, CPU-taugliches Modell. Standard: qwen2.5:1.5b-instruct (ca. 1 GB).

.PARAMETER PythonInstallieren
  Python 3.12 per winget installieren, falls kein passendes Python gefunden wird.

.PARAMETER OhneTests
  Tests ueberspringen (nicht empfohlen).

.PARAMETER TestsIgnorieren
  Bei fehlgeschlagenen Tests trotzdem weiterbauen (nur nach Ruecksprache).

.PARAMETER OhneExe
  Nur venv + Abhaengigkeiten + Tests, keine rechnung.exe bauen.

.PARAMETER NurPruefen
  Nichts installieren oder bauen, nur den Stand pruefen und anzeigen.
#>

[CmdletBinding()]
param(
    [switch]$MitTesseract,
    [switch]$MitOllama,
    [string]$OllamaModell = "qwen2.5:1.5b-instruct",
    [switch]$PythonInstallieren,
    [switch]$OhneTests,
    [switch]$TestsIgnorieren,
    [switch]$OhneExe,
    [switch]$NurPruefen
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest ist sonst sehr langsam

# Verzeichnis dieses Skripts = Projektordner
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$LogFile = Join-Path $Root "build-windows.log"
try { Start-Transcript -Path $LogFile -Force | Out-Null } catch { }

$script:Warnungen = New-Object System.Collections.Generic.List[string]

# ---------------------------------------------------------------------------
# Ausgabe-Helfer
# ---------------------------------------------------------------------------
function Schritt([string]$text) {
    Write-Host ""
    Write-Host "==> $text" -ForegroundColor Cyan
}
function Ok([string]$text)      { Write-Host "    [ok] $text" -ForegroundColor Green }
function Hinweis([string]$text) { Write-Host "    [!]  $text" -ForegroundColor Yellow; $script:Warnungen.Add($text) }
function Info([string]$text)    { Write-Host "         $text" }

function Abbruch([string]$text, [string]$abhilfe = "") {
    Write-Host ""
    Write-Host "FEHLER: $text" -ForegroundColor Red
    if ($abhilfe) {
        Write-Host ""
        Write-Host "Was tun?" -ForegroundColor Yellow
        foreach ($zeile in $abhilfe -split "`n") { Write-Host "  $zeile" }
    }
    Write-Host ""
    Write-Host "Protokoll: $LogFile"
    try { Stop-Transcript | Out-Null } catch { }
    exit 1
}

# Externes Programm aufrufen und den Rueckgabewert liefern. Fehlerausgaben
# (stderr) duerfen hier KEIN Abbruch sein -- pip schreibt z.B. Hinweise
# dorthin. Entscheidend ist nur der Rueckgabewert.
function Aufruf([string]$exe, [string[]]$argumente) {
    $alt = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # Out-Host: Ausgabe nur anzeigen, sonst wuerde sie Teil des Rueckgabewerts
        & $exe @argumente | Out-Host
        return $LASTEXITCODE
    } catch {
        Write-Host "    $($_.Exception.Message)"
        return 9999
    } finally {
        $ErrorActionPreference = $alt
    }
}

# Wie Aufruf, aber die Ausgabe als Text zurueckgeben (stdout+stderr).
function AufrufText([string]$exe, [string[]]$argumente) {
    $alt = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & $exe @argumente 2>&1 | ForEach-Object { "$_" }
        return [pscustomobject]@{ Code = $LASTEXITCODE; Text = ($out -join "`n") }
    } catch {
        return [pscustomobject]@{ Code = 9999; Text = $_.Exception.Message }
    } finally {
        $ErrorActionPreference = $alt
    }
}

function IstAdmin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

function HatWinget { return [bool](Get-Command winget -ErrorAction SilentlyContinue) }

function PfadNeuLaden {
    # Nach einer Installation kennt diese Sitzung den neuen PATH noch nicht.
    $m = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $u = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$m;$u"
}

# ---------------------------------------------------------------------------
# 0. Grundpruefungen
# ---------------------------------------------------------------------------
Schritt "Projektordner pruefen: $Root"
foreach ($f in @("pyproject.toml", "run.py", "rechnung.spec", "rechnung\cli.py")) {
    if (-not (Test-Path (Join-Path $Root $f))) {
        Abbruch "Datei '$f' fehlt im Projektordner." `
            "Dieses Skript muss im Projektordner liegen (dort, wo pyproject.toml liegt).`nDen kompletten Projektordner erneut auf den Laptop kopieren."
    }
}
if ($Root -match "[^\x00-\x7F]") {
    Hinweis "Der Projektpfad enthaelt Sonderzeichen/Umlaute ($Root). Falls der Build scheitert: Projekt nach C:\rechnung-build kopieren."
}
Ok "Projektdateien vorhanden"
Info ("PowerShell " + $PSVersionTable.PSVersion.ToString() + ", Windows " + [Environment]::OSVersion.Version.ToString())
if (IstAdmin) { Ok "laeuft mit Adminrechten" } else { Info "laeuft ohne Adminrechte (fuer -MitTesseract werden sie gebraucht)" }

# ---------------------------------------------------------------------------
# 1. Python finden (>= 3.11, empfohlen 3.12)
# ---------------------------------------------------------------------------
Schritt "Python suchen (benoetigt 3.11 oder neuer, empfohlen 3.12)"

function PythonVersion([string]$exe, [string[]]$vorab) {
    $r = AufrufText $exe ($vorab + @("-c", "import sys; print('%d.%d' % sys.version_info[:2])"))
    if ($r.Code -ne 0) { return $null }
    $zeile = ($r.Text -split "`n" | Where-Object { $_ -match "^\d+\.\d+$" } | Select-Object -First 1)
    if (-not $zeile) { return $null }
    return [version]$zeile
}

function PythonFinden {
    $kandidaten = @()
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in @("-3.12", "-3.13", "-3.11", "-3")) { $kandidaten += ,@("py", @($v)) }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) { $kandidaten += ,@("python", @()) }
    foreach ($k in $kandidaten) {
        $ver = PythonVersion $k[0] $k[1]
        if ($ver -and $ver -ge [version]"3.11") {
            return [pscustomobject]@{ Exe = $k[0]; Vorab = [string[]]($k[1]); Version = $ver }
        }
    }
    return $null
}

$Py = PythonFinden
if (-not $Py -and $PythonInstallieren -and -not $NurPruefen) {
    if (-not (HatWinget)) {
        Abbruch "Kein passendes Python gefunden und winget ist nicht verfuegbar." `
            "Python 3.12 von https://www.python.org/downloads/windows/ installieren.`nIm Installer unbedingt 'Add python.exe to PATH' ankreuzen.`nDanach dieses Skript erneut starten."
    }
    Info "Installiere Python 3.12 per winget ..."
    $rc = Aufruf "winget" @("install", "-e", "--id", "Python.Python.3.12", "--accept-package-agreements", "--accept-source-agreements")
    if ($rc -ne 0) { Hinweis "winget meldete Code $rc" }
    PfadNeuLaden
    $Py = PythonFinden
}
if (-not $Py) {
    Abbruch "Kein Python 3.11 oder neuer gefunden." `
        "Variante A: dieses Skript mit -PythonInstallieren starten (nutzt winget).`nVariante B: Python 3.12 von https://www.python.org/downloads/windows/ installieren,`n           dabei 'Add python.exe to PATH' ankreuzen.`nHinweis: Der Eintrag 'python' aus dem Microsoft Store zaehlt nicht -- er oeffnet nur den Store.`nDanach ein NEUES PowerShell-Fenster oeffnen und das Skript erneut starten."
}
Ok ("Python " + $Py.Version.ToString() + " (" + (($Py.Exe + " " + ($Py.Vorab -join " ")).Trim()) + ")")
if ($Py.Version -ge [version]"3.14") {
    Hinweis "Python $($Py.Version) ist sehr neu -- falls Pakete nicht installierbar sind, Python 3.12 verwenden."
}

# ---------------------------------------------------------------------------
# 2. Virtuelle Umgebung (.venv)
# ---------------------------------------------------------------------------
Schritt "Virtuelle Umgebung .venv"
$Venv = Join-Path $Root ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"

if ((Test-Path $Venv) -and -not (Test-Path $VenvPy)) {
    # z.B. von Linux mitkopiert (bin/ statt Scripts\) -- nur entfernen, wenn
    # es sicher eine virtuelle Umgebung ist.
    if (Test-Path (Join-Path $Venv "pyvenv.cfg")) {
        if ($NurPruefen) {
            Hinweis ".venv ist keine Windows-Umgebung (wird beim echten Lauf neu angelegt)"
        } else {
            Info ".venv stammt nicht von diesem Rechner (kein Scripts\python.exe) -- wird neu angelegt"
            Remove-Item -Recurse -Force $Venv
        }
    } else {
        Abbruch "Ordner .venv existiert, ist aber keine virtuelle Umgebung." `
            "Den Ordner .venv im Projektordner umbenennen oder loeschen und das Skript erneut starten."
    }
}
if (-not (Test-Path $VenvPy)) {
    if ($NurPruefen) {
        Hinweis ".venv fehlt noch"
    } else {
        $rc = Aufruf $Py.Exe ($Py.Vorab + @("-m", "venv", $Venv))
        if ($rc -ne 0 -or -not (Test-Path $VenvPy)) {
            Abbruch "Virtuelle Umgebung konnte nicht angelegt werden (Code $rc)." `
                "Python neu installieren (python.org, mit 'Add python.exe to PATH').`nVirenscanner kann das Anlegen blockieren -- ggf. IT fragen."
        }
        Ok ".venv angelegt"
    }
} else {
    Ok ".venv vorhanden"
}

# ---------------------------------------------------------------------------
# 3. Abhaengigkeiten
# ---------------------------------------------------------------------------
if (-not $NurPruefen) {
    Schritt "Abhaengigkeiten installieren (pip, kann einige Minuten dauern)"
    $rc = Aufruf $VenvPy @("-m", "pip", "install", "--upgrade", "pip")
    if ($rc -ne 0) { Hinweis "pip konnte nicht aktualisiert werden (Code $rc) -- es geht trotzdem weiter" }
    $rc = Aufruf $VenvPy @("-m", "pip", "install", "-e", ".[dev]", "pyinstaller")
    if ($rc -ne 0) {
        Abbruch "Installation der Abhaengigkeiten fehlgeschlagen (Code $rc)." `
            "Internetverbindung pruefen (pip laedt von pypi.org).`nIm Firmennetz mit Proxy vorher setzen, z.B.:`n  `$env:HTTPS_PROXY = 'http://proxy.firma.local:8080'`nDie genaue Fehlermeldung steht weiter oben bzw. in $LogFile."
    }
    Ok "Abhaengigkeiten installiert"
}

# ---------------------------------------------------------------------------
# 4. Tests
# ---------------------------------------------------------------------------
if ($OhneTests -or $NurPruefen) {
    if ($OhneTests) { Hinweis "Tests uebersprungen (-OhneTests)" }
} else {
    Schritt "Tests ausfuehren"
    $rc = Aufruf $VenvPy @("-m", "pytest", "-q")
    if ($rc -ne 0) {
        if ($TestsIgnorieren) {
            Hinweis "Tests fehlgeschlagen (Code $rc) -- wegen -TestsIgnorieren wird trotzdem gebaut"
        } else {
            Abbruch "Mindestens ein Test ist fehlgeschlagen (Code $rc)." `
                "Die Ausgabe oben zeigt, welcher Test. Datei build-windows.log an den Entwickler schicken.`nNur nach Ruecksprache: mit -TestsIgnorieren trotzdem bauen."
        }
    } else {
        Ok "alle Tests gruen"
    }
}

# ---------------------------------------------------------------------------
# 5. rechnung.exe bauen + Programmordner dist\Rechnung
# ---------------------------------------------------------------------------
$Dist = Join-Path $Root "dist"
$Exe = Join-Path $Dist "rechnung.exe"
$Ziel = Join-Path $Dist "Rechnung"

if (-not ($OhneExe -or $NurPruefen)) {
    Schritt "rechnung.exe bauen (PyInstaller, dauert einige Minuten)"
    if (Test-Path $Exe) { Remove-Item -Force $Exe }
    $rc = Aufruf $VenvPy @("-m", "PyInstaller", "--noconfirm", "--clean", "rechnung.spec")
    if ($rc -ne 0 -or -not (Test-Path $Exe)) {
        Abbruch "Bau der rechnung.exe fehlgeschlagen (Code $rc)." `
            "Haeufigste Ursache: der Virenscanner blockiert/entfernt die frisch gebaute Datei.`nDann den Projektordner beim Virenscanner ausnehmen lassen (IT) und erneut bauen.`nDetails: $LogFile"
    }
    Ok ("gebaut: $Exe (" + [math]::Round((Get-Item $Exe).Length / 1MB) + " MB)")

    Schritt "rechnung.exe kurz pruefen"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("rechnung-test-" + [guid]::NewGuid().ToString("N"))
    $r = AufrufText $Exe @("--data", $tmp, "--samples", $tmp, "modus")
    if ($r.Code -ne 0 -or $r.Text -notmatch "Modus") {
        Abbruch "rechnung.exe startet nicht richtig (Code $($r.Code))." `
            "Ausgabe:`n$($r.Text)`nVirenscanner pruefen (Datei in Quarantaene?). Details: $LogFile"
    }
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Ok "rechnung.exe startet ($($r.Text.Split("`n")[0]))"

    Schritt "Programmordner zusammenstellen: $Ziel"
    New-Item -ItemType Directory -Force -Path $Ziel | Out-Null
    Copy-Item -Force $Exe $Ziel
    Copy-Item -Force (Join-Path $Root "windows\start-rechnung.cmd") $Ziel
    Copy-Item -Force (Join-Path $Root "windows\nachtlauf.cmd") $Ziel
    Copy-Item -Force (Join-Path $Root "rechnung.env.example") $Ziel
    Copy-Item -Force (Join-Path $Root "jobrouter-mapping.example.json") $Ziel
    Copy-Item -Force (Join-Path $Root "INSTALL-WINDOWS.md") $Ziel
    # Bestehende Daten werden nie angefasst -- nur fehlende Ordner anlegen.
    foreach ($d in @("data", "eingang")) { New-Item -ItemType Directory -Force -Path (Join-Path $Ziel $d) | Out-Null }
    Ok "fertig: $Ziel"
}

# ---------------------------------------------------------------------------
# 6. Optional: Tesseract (Texterkennung, deutsch)
# ---------------------------------------------------------------------------
function TesseractFinden {
    $c = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    foreach ($p in @("$env:ProgramFiles\Tesseract-OCR\tesseract.exe",
                     "${env:ProgramFiles(x86)}\Tesseract-OCR\tesseract.exe",
                     "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe")) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return $null
}

function TesseractHatDeutsch([string]$tess) {
    $r = AufrufText $tess @("--list-langs")
    return ($r.Text -split "\s+") -contains "deu"
}

if ($MitTesseract) {
    Schritt "Tesseract (Texterkennung) pruefen/installieren"
    $tess = TesseractFinden
    if (-not $tess -and -not $NurPruefen) {
        if (-not (IstAdmin)) {
            Abbruch "Fuer die Tesseract-Installation werden Adminrechte gebraucht." `
                "PowerShell mit Rechtsklick 'Als Administrator ausfuehren' oeffnen und das Skript erneut starten."
        }
        if (HatWinget) {
            Info "Installiere Tesseract (UB Mannheim) per winget ..."
            $rc = Aufruf "winget" @("install", "-e", "--id", "UB-Mannheim.TesseractOCR", "--silent",
                                    "--accept-package-agreements", "--accept-source-agreements")
            if ($rc -ne 0) { Hinweis "winget meldete Code $rc" }
            PfadNeuLaden
            $tess = TesseractFinden
        }
        if (-not $tess) {
            Abbruch "Tesseract konnte nicht automatisch installiert werden." `
                "Installer von https://github.com/UB-Mannheim/tesseract/wiki herunterladen und ausfuehren`n(bei 'Additional language data' German/Deutsch ankreuzen). Danach erneut starten."
        }
    }
    if (-not $tess) {
        Hinweis "Tesseract nicht installiert"
    } else {
        Ok "Tesseract gefunden: $tess"
        $tessdata = Join-Path (Split-Path -Parent $tess) "tessdata"
        if (-not (TesseractHatDeutsch $tess)) {
            if ($NurPruefen) {
                Hinweis "deutsches Sprachpaket (deu) fehlt"
            } elseif (-not (IstAdmin)) {
                Abbruch "Das deutsche Sprachpaket (deu) fehlt; zum Nachinstallieren werden Adminrechte gebraucht." `
                    "PowerShell 'Als Administrator ausfuehren' und das Skript erneut mit -MitTesseract starten."
            } else {
                Info "Lade deutsches Sprachpaket nach $tessdata ..."
                try {
                    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/tesseract-ocr/tessdata/raw/main/deu.traineddata" `
                        -OutFile (Join-Path $tessdata "deu.traineddata")
                } catch {
                    Abbruch "Download des Sprachpakets fehlgeschlagen: $($_.Exception.Message)" `
                        "Datei deu.traineddata manuell von https://github.com/tesseract-ocr/tessdata herunterladen`nund nach $tessdata kopieren."
                }
            }
        }
        if (TesseractHatDeutsch $tess) {
            Ok "deutsches Sprachpaket (deu) vorhanden"
            # Funktionstest: Testbild mit Text erzeugen und erkennen lassen
            if (Test-Path $VenvPy) {
                $png = Join-Path ([IO.Path]::GetTempPath()) "rechnung-ocr-test.png"
                $code = "from PIL import Image, ImageDraw, ImageFont`n" +
                        "im = Image.new('RGB', (900, 160), 'white')`n" +
                        "try:`n    f = ImageFont.truetype('arial.ttf', 64)`n" +
                        "except OSError:`n    f = ImageFont.load_default()`n" +
                        "ImageDraw.Draw(im).text((20, 40), 'Rechnung 4711', fill='black', font=f)`n" +
                        "im.save(r'$png')"
                $null = AufrufText $VenvPy @("-c", $code)
                $r = AufrufText $tess @($png, "stdout", "-l", "deu")
                if ($r.Text -match "Rechnung" -and $r.Text -match "4711") {
                    Ok "Texterkennung funktioniert (Testbild erkannt)"
                } else {
                    Hinweis "Texterkennung lieferte nicht das Erwartete: '$($r.Text.Trim())'"
                }
                Remove-Item -Force $png -ErrorAction SilentlyContinue
            }
            if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
                Info "Hinweis: tesseract liegt nicht im PATH -- das Programm findet es trotzdem"
                Info "am Standardpfad. Sonst in rechnung.env: RECHNUNG_TESSERACT=$tess"
            }
        } elseif (-not $NurPruefen) {
            Abbruch "Das deutsche Sprachpaket (deu) wird von Tesseract nicht erkannt." `
                "Pruefen, ob $tessdata\deu.traineddata existiert. Ggf. Tesseract neu installieren."
        }
    }
}

# ---------------------------------------------------------------------------
# 7. Optional: Ollama + kleines Modell (CPU)
# ---------------------------------------------------------------------------
$OllamaUrl = "http://127.0.0.1:11434"

function OllamaFinden {
    $c = Get-Command ollama -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $p = "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe"
    if (Test-Path $p) { return $p }
    return $null
}

function OllamaLaeuft {
    try {
        Invoke-RestMethod -Uri "$OllamaUrl/api/version" -TimeoutSec 5 | Out-Null
        return $true
    } catch { return $false }
}

if ($MitOllama) {
    Schritt "Ollama (lokales KI-Modell) pruefen/installieren"
    $ollama = OllamaFinden
    if (-not $ollama -and -not $NurPruefen) {
        if (HatWinget) {
            Info "Installiere Ollama per winget ..."
            $rc = Aufruf "winget" @("install", "-e", "--id", "Ollama.Ollama",
                                    "--accept-package-agreements", "--accept-source-agreements")
            if ($rc -ne 0) { Hinweis "winget meldete Code $rc" }
            PfadNeuLaden
            $ollama = OllamaFinden
        }
        if (-not $ollama) {
            Info "Lade Ollama-Installer von ollama.com ..."
            $setup = Join-Path ([IO.Path]::GetTempPath()) "OllamaSetup.exe"
            try {
                Invoke-WebRequest -UseBasicParsing -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $setup
                Start-Process -FilePath $setup -ArgumentList "/VERYSILENT", "/NORESTART" -Wait
            } catch {
                Abbruch "Ollama konnte nicht heruntergeladen/installiert werden: $($_.Exception.Message)" `
                    "Installer manuell von https://ollama.com/download/windows herunterladen und ausfuehren.`nDanach das Skript erneut mit -MitOllama starten."
            }
            PfadNeuLaden
            $ollama = OllamaFinden
        }
        if (-not $ollama) {
            Abbruch "Ollama ist nach der Installation nicht auffindbar." `
                "Rechner neu anmelden (PATH) und das Skript erneut mit -MitOllama starten."
        }
    }
    if (-not $ollama) {
        Hinweis "Ollama nicht installiert"
    } else {
        Ok "Ollama gefunden: $ollama"
        if (-not (OllamaLaeuft)) {
            if ($NurPruefen) {
                Hinweis "Ollama-Dienst laeuft nicht"
            } else {
                Info "Starte Ollama im Hintergrund ..."
                Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
                $bis = (Get-Date).AddSeconds(60)
                while (-not (OllamaLaeuft) -and (Get-Date) -lt $bis) { Start-Sleep -Seconds 2 }
            }
        }
        if (OllamaLaeuft) {
            Ok "Ollama antwortet auf $OllamaUrl (nur lokal)"
            if (-not $NurPruefen) {
                Info "Lade Modell $OllamaModell (einmalig, ca. 1-2 GB) ..."
                $rc = Aufruf $ollama @("pull", $OllamaModell)
                if ($rc -ne 0) {
                    Abbruch "Modell $OllamaModell konnte nicht geladen werden (Code $rc)." `
                        "Internetverbindung/Proxy pruefen. Modellname korrekt? (siehe https://ollama.com/library)"
                }
                Ok "Modell $OllamaModell vorhanden"
            }
            Info "Testanfrage an das Modell (auf CPU kann das 10-120 Sekunden dauern) ..."
            $body = @{ model = $OllamaModell; prompt = "Antworte nur mit dem Wort OK."; stream = $false;
                       options = @{ num_ctx = 512; num_predict = 5 } } | ConvertTo-Json -Depth 4
            $t0 = Get-Date
            try {
                $antwort = Invoke-RestMethod -Uri "$OllamaUrl/api/generate" -Method Post -Body $body `
                    -ContentType "application/json" -TimeoutSec 600
                $sek = [math]::Round(((Get-Date) - $t0).TotalSeconds)
                Ok "Modell antwortet nach $sek s: '$($antwort.response.Trim())'"
                if ($sek -gt 60) {
                    Hinweis "Langsam ($sek s). In rechnung.env RECHNUNG_LLM_TIMEOUT/RECHNUNG_TIMEOUT hoch setzen oder KI ausgeschaltet lassen."
                }
                Info "Zum Einschalten in rechnung.env: RECHNUNG_LLM_ENABLED=1 und RECHNUNG_LLM_MODEL=$OllamaModell"
            } catch {
                Hinweis "Testanfrage fehlgeschlagen: $($_.Exception.Message)"
            }
        } else {
            Hinweis "Ollama antwortet nicht auf $OllamaUrl -- Ollama aus dem Startmenue starten und erneut pruefen."
        }
    }
}

# ---------------------------------------------------------------------------
# Zusammenfassung
# ---------------------------------------------------------------------------
Schritt "Fertig"
if ($script:Warnungen.Count -gt 0) {
    Write-Host "    Hinweise:" -ForegroundColor Yellow
    foreach ($w in $script:Warnungen) { Write-Host "     - $w" -ForegroundColor Yellow }
}
if (-not ($OhneExe -or $NurPruefen)) {
    Info "Programmordner: $Ziel"
    Info "Naechste Schritte (siehe INSTALL-WINDOWS.md):"
    Info "  1. Ordner $Ziel nach C:\Rechnung kopieren (beim ersten Mal)."
    Info "  2. rechnung.env.example dort als rechnung.env kopieren und anpassen."
    Info "  3. start-rechnung.cmd doppelklicken."
}
Info "Protokoll: $LogFile"
try { Stop-Transcript | Out-Null } catch { }
exit 0
