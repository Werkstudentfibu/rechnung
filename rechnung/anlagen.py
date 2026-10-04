"""Anlagen eines Belegs (Postfach-Baustein 8).

Andere beschriftete Anhaenge derselben Mail (AGB, Lieferschein,
Auftragsbestaetigung, Datenblatt, ... -- siehe ``belegart.ANLAGEN``) werden
NICHT als eigene Rechnung verarbeitet, sondern an den Beleg derselben Mail
gehaengt: im Archiv UND bei der JobRouter-Uebergabe als Anhang.

Registry ``data/anlagen/<beleg-sha>.json`` (Liste ``{dateiname, art}``),
die Dateien selbst unter ``data/anlagen/<beleg-sha>/<dateiname>`` -- analog
zu split.py (Sammel-PDF-Teile tragen den Verweis aufs Original), hier
umgekehrt: der Beleg traegt den Verweis auf seine Anlagen. Einmal
geschrieben, nie geaendert (GoBD) -- genau wie das Archiv."""

from __future__ import annotations

import json
from pathlib import Path


def _root(store) -> Path:
    p = Path(store.root) / "anlagen"
    p.mkdir(parents=True, exist_ok=True)
    return p


def save(store, beleg_sha256: str, anlagen: list[tuple[str, bytes, str]]) -> None:
    """``anlagen``: (Dateiname, Inhalt, Anlagenart) -- an den Beleg mit
    diesem sha256 (Hash der Originaldatei, wie ``Document.sha256``)
    gehaengt. Ohne Anlagen passiert nichts (keine leere Registry)."""
    if not beleg_sha256 or not anlagen:
        return
    d = _root(store) / beleg_sha256
    d.mkdir(parents=True, exist_ok=True)
    reg = []
    for name, content, art in anlagen:
        (d / name).write_bytes(content)
        reg.append({"dateiname": name, "art": art})
    (_root(store) / f"{beleg_sha256}.json").write_text(
        json.dumps(reg, indent=2, ensure_ascii=False), "utf-8")


def info(store, beleg_sha256: str) -> list[dict]:
    """-> Liste ``{dateiname, art}`` -- leer, wenn der Beleg keine Anlagen hat."""
    if not beleg_sha256:
        return []
    p = _root(store) / f"{beleg_sha256}.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def files(store, beleg_sha256: str) -> list[Path]:
    """Pfade der Anlagen-Dateien (fuer einen Datei-Anhang an JobRouter)."""
    d = _root(store) / (beleg_sha256 or "")
    if not d.exists():
        return []
    return [d / a["dateiname"] for a in info(store, beleg_sha256)
            if (d / a["dateiname"]).exists()]
