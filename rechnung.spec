# -*- mode: python ; coding: utf-8 -*-
# PyInstaller-Spec fuer rechnung.exe (eine Datei, kein Setup).
# Aufruf:  .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean rechnung.spec
# bzw. automatisch ueber build-windows.ps1 -- siehe build.md.
#
# Warum eine Spec statt einer langen Kommandozeile: alle Module des Pakets
# (auch die nur dynamisch geladenen, z.B. guard.py -> "rechnung.pipeline:process"
# per importlib im Arbeitsprozess) und die Daten-/Binaerdateien der
# Abhaengigkeiten stehen hier an einer Stelle und werden bei neuen Modulen
# automatisch mitgenommen (collect_submodules).

import importlib.util

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("rechnung/web/static", "rechnung/web/static")]
binaries = []
# Alle eigenen Module (human, modus, guard, ocr, nightly, ...) und uvicorn
# (laedt Protokoll-/Loop-Implementierungen dynamisch).
hiddenimports = collect_submodules("rechnung") + collect_submodules("uvicorn")

# pdfminer: CMap-Daten; pypdfium2(_raw): pdfium.dll; webview/clr_loader/
# pythonnet: Fenster ueber Edge WebView2. Fehlt ein Paket (z.B. pythonnet
# ausserhalb von Windows), wird es uebersprungen statt den Build abzubrechen.
for pkg in ("pdfplumber", "pdfminer", "pypdfium2", "pypdfium2_raw",
            "webview", "clr_loader", "pythonnet"):
    if importlib.util.find_spec(pkg) is None:
        print(f"[rechnung.spec] Paket {pkg} nicht installiert -- uebersprungen")
        continue
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="rechnung",
    debug=False,
    strip=False,
    # UPX aus: gepackte Binaries loesen bei Virenscannern haeufiger Fehlalarme aus
    upx=False,
    # Konsolenprogramm: Befehle wie "rechnung scan"/"rechnung nacht" schreiben
    # auf die Konsole bzw. in die Logdatei der geplanten Aufgabe.
    console=True,
    runtime_tmpdir=None,
)
