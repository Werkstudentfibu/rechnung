"""Postfach-Stammdatensatz (Entscheidung Aaron 15.10.2026, ~50 IONOS-
Postfaecher, je Mandant eines). Das Passwort ist bewusst KEIN Feld dieser
Klasse -- es wird nie zusammen mit den uebrigen Stammdaten serialisiert/
geloggt/angezeigt, siehe ``secrets.py``. ``to_dict()``/``from_dict()`` koennen
dadurch gar nicht versehentlich ein Passwort mit ausgeben."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_HOST = "imap.ionos.de"
DEFAULT_PORT = 993


@dataclass
class Postfach:
    id: str
    mandant_id: str = ""     # Verweis auf BillingAddress.id, falls aufgeloest
    mandant_name: str = ""   # Anzeige/Rohwert aus der Excel-Spalte "Mandant"
    user: str = ""           # Postfach-Adresse (E-Mail), IMAP-Benutzername
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    ssl: bool = True
    source_folder: str = "INBOX"
    done_folder: str = ""
    aktiv: bool = True

    @property
    def label(self) -> str:
        return self.mandant_name or self.mandant_id or self.user or self.id

    def to_dict(self) -> dict:
        return {"id": self.id, "mandant_id": self.mandant_id, "mandant_name": self.mandant_name,
                "user": self.user, "host": self.host, "port": self.port, "ssl": self.ssl,
                "source_folder": self.source_folder, "done_folder": self.done_folder,
                "aktiv": self.aktiv}

    @classmethod
    def from_dict(cls, d: dict) -> "Postfach":
        return cls(id=d["id"], mandant_id=d.get("mandant_id", ""),
                  mandant_name=d.get("mandant_name", ""), user=d.get("user", ""),
                  host=d.get("host") or DEFAULT_HOST, port=int(d.get("port") or DEFAULT_PORT),
                  ssl=d.get("ssl", True), source_folder=d.get("source_folder") or "INBOX",
                  done_folder=d.get("done_folder", ""), aktiv=d.get("aktiv", True))
