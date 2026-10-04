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
    # nur Anhaenge mit diesen Endungen (kommagetrennt). .xml fuer reine
    # E-Rechnungen (XRechnung); ZUGFeRD steckt im PDF und braucht das nicht.
    extensions: tuple[str, ...] = tuple(
        e.strip().lower()
        for e in os.environ.get("RECHNUNG_IMAP_EXTENSIONS", ".pdf,.xml").split(",")
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


def process_timeout() -> float:
    """Zeitgrenze je Rechnung in Sekunden fuer die komplette Verarbeitung
    inkl. OCR und KI (guard.py). 0 = ohne Zeitgrenze im selben Prozess."""
    try:
        return max(0.0, float(os.environ.get("RECHNUNG_TIMEOUT", "120")))
    except ValueError:
        return 120.0


def operator() -> str:
    """Wer bedient das Programm gerade – fuer den Audit-Trail."""
    return (os.environ.get("RECHNUNG_OPERATOR")
            or os.environ.get("USER") or os.environ.get("USERNAME") or "unbekannt")


@dataclass(frozen=True)
class LlmConfig:
    """Optionaler semantischer Fallback (semantic.py), siehe SPEC-v1.md §7f.

    Standardmaessig AUS: ohne ``RECHNUNG_LLM_ENABLED=1`` verhaelt sich das
    Programm exakt wie ohne dieses Modul (kein Netzwerkzugriff, keine neue
    Abhaengigkeit -- reines stdlib ``urllib`` gegen ein lokales Ollama).
    ``model`` ist bewusst austauschbar ohne Code-Aenderung: kleineres Modell
    auf einem Laptop, groesseres sobald Server-Hardware feststeht.
    """
    enabled: bool = _b("RECHNUNG_LLM_ENABLED", False)
    # "ollama" (lokales Ollama, /api/generate) oder "openai" (beliebiger
    # OpenAI-kompatibler Endpoint auf eigener Hardware, z.B. vLLM oder
    # llama.cpp-Server: /chat/completions). Bewusst KEIN fest eingebauter
    # Cloud-Anbieter -- die URL zeigt immer auf einen selbst betriebenen Dienst.
    backend: str = os.environ.get("RECHNUNG_LLM_BACKEND", "ollama").strip().lower()
    # Ollama: http://127.0.0.1:11434 -- OpenAI-kompatibel: Basis inkl. /v1,
    # z.B. http://llm-server.intern:8000/v1
    base_url: str = os.environ.get("RECHNUNG_LLM_URL", "http://127.0.0.1:11434").rstrip("/")
    model: str = os.environ.get("RECHNUNG_LLM_MODEL", "qwen2.5:3b-instruct")
    # nur fuer "openai", falls der interne Server einen Schluessel verlangt
    api_key: str = os.environ.get("RECHNUNG_LLM_API_KEY", "")
    # Stufe 2: ohne Template (unbekannter Lieferant / noch nicht angelernt)
    # die komplette Rechnung auslesen statt nur einzelne Luecken zu fuellen.
    # Greift nur, wenn ``enabled`` an ist.
    full_extraction: bool = _b("RECHNUNG_LLM_FULL", True)
    # Positionen mit auslesen lassen (laengere Antwort, langsamer)
    line_items: bool = _b("RECHNUNG_LLM_LINE_ITEMS", False)
    num_ctx: int = int(os.environ.get("RECHNUNG_LLM_CTX", "4096"))
    timeout: int = int(os.environ.get("RECHNUNG_LLM_TIMEOUT", "30"))
    # Konfidenz-Deckel: ein LLM-Treffer soll nie allein auf 'auto' hochfahren
    # koennen (pipeline.CONF_MIN = 0.7) -- neuer, unbewiesener Weg bleibt erst
    # mal immer 'review', bis Praxisdaten etwas anderes rechtfertigen.
    max_confidence: float = float(os.environ.get("RECHNUNG_LLM_MAX_CONFIDENCE", "0.6"))
    # Ollamas eigener Default (5 Minuten) reicht in der Trainings-UI oft nicht:
    # ein Mensch braucht zwischen zwei Rechnungen mit Luecken manchmal laenger
    # zum Pruefen/Anlernen als 5 Minuten, das Modell wird dann entladen und
    # der naechste Aufruf zahlt die Ladezeit erneut. 10 Minuten sind ein
    # Kompromiss -- laenger blockiert unnoetig VRAM, das ein anderer lokaler
    # Dienst (Ollama laedt bei Bedarf ohnehin nur ein Modell gleichzeitig)
    # zwischenzeitlich brauchen koennte.
    keep_alive: str = os.environ.get("RECHNUNG_LLM_KEEP_ALIVE", "10m")


def imap() -> ImapConfig:
    return ImapConfig()


def jobrouter() -> JobRouterConfig:
    return JobRouterConfig()


def llm() -> LlmConfig:
    return LlmConfig()
