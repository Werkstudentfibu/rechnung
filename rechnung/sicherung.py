"""Taegliche Datensicherung der Lerndaten (Aaron 11.10.2026): alles, was das
Programm lernt (Vorlagen, Lieferanten, Geschaeftsbereiche, Goldstandard,
Ereignisse, Archiv), liegt sonst nur auf dem Laptop.

Ziel ``RECHNUNG_SICHERUNG_ZIEL`` (z. B. Netzlaufwerk der Firma -- die Daten
bleiben im Unternehmen). Leer = aus. Je Tag eine ZIP-Datei
``rechnung-daten-JJJJMMTT.zip``, die letzten ``RECHNUNG_SICHERUNG_ANZAHL``
(Standard 14) bleiben. Zwischenspeicher (Texterkennung, KI-Antworten) werden
nicht gesichert -- sie entstehen bei Bedarf neu."""

from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path

AUSLASSEN = {"ocr", "ki-cache", "__pycache__", "teile"}
PREFIX = "rechnung-daten-"


def ziel() -> Path | None:
    v = os.environ.get("RECHNUNG_SICHERUNG_ZIEL", "").strip()
    return Path(v) if v else None


def _anzahl() -> int:
    try:
        return max(1, int(os.environ.get("RECHNUNG_SICHERUNG_ANZAHL", "14")))
    except ValueError:
        return 14


def letzte(z: Path | None = None) -> Path | None:
    z = z or ziel()
    if z is None or not z.is_dir():
        return None
    d = sorted(z.glob(f"{PREFIX}*.zip"))
    return d[-1] if d else None


def faellig() -> bool:
    z = ziel()
    if z is None:
        return False
    l = letzte(z)
    return l is None or l.name != f"{PREFIX}{time.strftime('%Y%m%d')}.zip"


def sichern(data_root: str | Path) -> dict:
    z = ziel()
    if z is None:
        return {"ok": None, "detail": "RECHNUNG_SICHERUNG_ZIEL nicht gesetzt"}
    root = Path(data_root)
    z.mkdir(parents=True, exist_ok=True)
    datei = z / f"{PREFIX}{time.strftime('%Y%m%d')}.zip"
    tmp = datei.with_name(datei.name + ".part")
    n = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if not p.is_file() or AUSLASSEN & set(rel.parts) or p.suffix == ".lock":
                continue
            zf.write(p, rel.as_posix())
            n += 1
    tmp.replace(datei)
    for alt in sorted(z.glob(f"{PREFIX}*.zip"))[:-_anzahl()]:
        alt.unlink()
    return {"ok": True, "detail": f"{n} Dateien -> {datei}", "datei": str(datei)}
