"""Zentrale Konfiguration ueber Umgebungsvariablen.

Alle Schnittstellen (IONOS-IMAP, JobRouter) sind standardmaessig AUS und werden
erst durch gesetzte ENV-Variablen scharf. Ohne Konfiguration bleibt das
Programm rein lokal und offline (Abnahme-relevant, siehe SPEC).

Empfohlen: eine Datei ``rechnung.env`` neben dem Programm, die der Aufrufer
vor dem Start sourct (Linux) bzw. per ``set`` laedt (Windows). Wir lesen keine
.env automatisch ein, um "Magie" bei der IT-Abnahme zu vermeiden.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _b(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on", "ja")


@dataclass(frozen=True)
class ImapConfig:
    host: str = os.environ.get("RECHNUNG_IMAP_HOST", "imap.ionos.de")
    port: int = int(os.environ.get("RECHNUNG_IMAP_PORT", "993"))
    user: str = os.environ.get("RECHNUNG_IMAP_USER", "")
    password: str = os.environ.get("RECHNUNG_IMAP_PASSWORD", "")
    ssl: bool = _b("RECHNUNG_IMAP_SSL", True)
    source_folder: str = os.environ.get("RECHNUNG_IMAP_FOLDER", "INBOX")
    # Nach erfolgreichem Abholen dorthin verschieben (leer = nur als gelesen markieren)
    done_folder: str = os.environ.get("RECHNUNG_IMAP_DONE_FOLDER", "")
    # nur Anhaenge mit diesen Endungen (kommagetrennt)
    extensions: tuple[str, ...] = tuple(
        e.strip().lower()
        for e in os.environ.get("RECHNUNG_IMAP_EXTENSIONS", ".pdf").split(",")
        if e.strip()
    )

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password)


@dataclass(frozen=True)
class JobRouterConfig:
    enabled: bool = _b("RECHNUNG_JOBROUTER_ENABLED", False)
    base_url: str = os.environ.get("RECHNUNG_JOBROUTER_URL", "").rstrip("/")
    token: str = os.environ.get("RECHNUNG_JOBROUTER_TOKEN", "")
    # relativer Pfad des Endpunkts, an den die Rechnung uebergeben wird
    endpoint: str = os.environ.get(
        "RECHNUNG_JOBROUTER_ENDPOINT", "/rest/v2/application/incidents"
    )
    timeout: int = int(os.environ.get("RECHNUNG_JOBROUTER_TIMEOUT", "30"))
    max_retries: int = int(os.environ.get("RECHNUNG_JOBROUTER_RETRIES", "3"))
    # nur diese Status uebergeben (kommagetrennt); leer = alle bestaetigten
    allow_status: tuple[str, ...] = tuple(
        s.strip()
        for s in os.environ.get("RECHNUNG_JOBROUTER_ALLOW_STATUS", "auto,review").split(",")
        if s.strip()
    )

    @property
    def live(self) -> bool:
        return bool(self.enabled and self.base_url)


def data_dir() -> Path:
    return Path(os.environ.get("RECHNUNG_DATA", "./data"))


def inbox_dir() -> Path:
    """Ablage fuer per IMAP abgeholte PDFs (Standard: <data>/inbox)."""
    v = os.environ.get("RECHNUNG_INBOX")
    return Path(v) if v else data_dir() / "inbox"


def category_fuzzy_min() -> float:
    """Schwelle fuer den Fuzzy-Textvergleich der Kategorie-Vorschlaege
    (suggest.py). Bewusst konfigurierbar statt hart codiert -- der Standard
    ist eine Schaetzung, solange noch keine echten Rechnungen zur Kalibrierung
    vorliegen (siehe SPEC-v1.md)."""
    try:
        return float(os.environ.get("RECHNUNG_CATEGORY_FUZZY_MIN", "0.72"))
    except ValueError:
        return 0.72


def operator() -> str:
    """Wer bedient das Programm gerade – fuer den Audit-Trail."""
    return (os.environ.get("RECHNUNG_OPERATOR")
            or os.environ.get("USER") or os.environ.get("USERNAME") or "unbekannt")


def imap() -> ImapConfig:
    return ImapConfig()


def jobrouter() -> JobRouterConfig:
    return JobRouterConfig()
