"""Zwischenspeicher fuer den Excel-Stammdaten-Import (Befund Aaron 16.10.2026:
"das Programm merkt sich die eingelesenen Excel-Tabellen nicht, die sind weg,
wenn man das Programm schliesst").

Bisher lebten hochgeladene Listen, Spaltenzuordnung und die beantworteten
Fragen nur im Browser-/Fensterspeicher der Seite -- Fenster zu, alles weg,
solange noch nicht auf "uebernehmen" geklickt war. Jetzt legt die Seite ihren
Arbeitsstand nach jeder Aenderung hier ab und holt ihn beim Oeffnen zurueck.

Der Entwurf ist KEINE Uebernahme: in die Bibliothek (Lieferanten, Mandanten,
Postfaecher) gelangt weiterhin nur, was Aaron im Schritt "uebernehmen" sieht
und bestaetigt. Der Entwurf wird nach der Uebernahme bzw. "Alles verwerfen"
geloescht.

Passwoerter (Postfach-Liste) stehen NIE im Klartext im Entwurf: Spalten, die als
Passwort zugeordnet sind oder so heissen, werden aus der Entwurfsdatei entfernt
und ueber ``secrets`` (Windows: DPAPI) getrennt abgelegt."""

from __future__ import annotations

import copy
import json
import threading
from datetime import datetime
from pathlib import Path

from . import secrets as secrets_mod

ENTWURF_DATEI = "stammdaten_entwurf.json"
GEHEIMNIS_ID = "stammdaten-entwurf"      # ein Eintrag fuer alle Entwurfs-Passwoerter
VERSION = 1

_LOCK = threading.Lock()


def _pfad(store) -> Path:
    return store.root / ENTWURF_DATEI


def _passwort_spalten(headers: list, mapping: dict) -> list[int]:
    """Spalten, deren Zellen Passwoerter enthalten koennten -- zugeordnet als
    Passwort ODER nach der Ueberschrift so aussehend (falls die Zuordnung
    spaeter auf "ignorieren" geaendert wurde, darf das Passwort trotzdem nicht
    im Klartext liegen bleiben)."""
    from .stammdaten_import import vorschlag_fuer_spalte

    out = []
    for i, h in enumerate(headers):
        if (mapping or {}).get(h) == "passwort" or vorschlag_fuer_spalte(str(h)) == "passwort":
            out.append(i)
    return out


def _pruefen_slots(slots) -> None:
    if not isinstance(slots, dict):
        raise ValueError("slots muss ein Objekt sein")
    for sid, d in slots.items():
        sheets = d.get("sheets") if isinstance(d, dict) else None
        if not isinstance(sheets, dict):
            raise ValueError(f"Bereich {sid}: Blaetter fehlen")
        for name, b in sheets.items():
            if not (isinstance(b, dict) and isinstance(b.get("headers"), list)
                    and isinstance(b.get("rows"), list)
                    and all(isinstance(r, list) for r in b["rows"])):
                raise ValueError(f"Bereich {sid}, Blatt {name}: Tabelle ungueltig")


def _maskieren(slots: dict) -> tuple[dict, dict]:
    """-> (Kopie ohne Passwort-Zellen, {Schluessel: Passwort})."""
    kopie = copy.deepcopy(slots)
    geheim: dict[str, str] = {}
    for sid, d in kopie.items():
        for name, b in d["sheets"].items():
            spalten = _passwort_spalten(b["headers"], b.get("mapping") or {})
            if not spalten:
                continue
            for r, row in enumerate(b["rows"]):
                for c in spalten:
                    if c < len(row) and row[c]:
                        geheim[f"{sid}|{name}|{r}|{c}"] = str(row[c])
                        row[c] = ""
    return kopie, geheim


def _geheimnis_abgleichen(store, geheim: dict, operator: str) -> None:
    """Nur schreiben, wenn sich etwas geaendert hat (jeder Schreibvorgang
    steht im Audit-Protokoll, ein Autospeichern soll es nicht fluten)."""
    if not geheim:
        if secrets_mod.has_password(store, GEHEIMNIS_ID):
            secrets_mod.delete_password(store, GEHEIMNIS_ID, operator)
        return
    neu = json.dumps(geheim, sort_keys=True, ensure_ascii=False)
    if secrets_mod.get_password(store, GEHEIMNIS_ID) != neu:
        secrets_mod.set_password(store, GEHEIMNIS_ID, neu, operator)


def speichern(store, daten: dict, operator: str = "") -> dict:
    """Arbeitsstand der Seite ablegen. Ohne hochgeladene Listen gibt es nichts
    zu merken -- dann wird ein alter Entwurf geloescht."""
    if not isinstance(daten, dict):
        raise ValueError("Daten ungueltig")
    slots = daten.get("slots") or {}
    _pruefen_slots(slots)
    if not slots:
        loeschen(store, operator)
        return {"gespeichert": None}
    kopie, geheim = _maskieren(slots)
    antworten = daten.get("antworten") or {}
    if not isinstance(antworten, dict):
        raise ValueError("antworten muss ein Objekt sein")
    zielfelder = [str(z) for z in (daten.get("zielfelder") or [])]
    jetzt = datetime.now().isoformat(timespec="seconds")
    inhalt = {
        "version": VERSION, "gespeichert": jetzt, "slots": kopie,
        "antworten": antworten, "zielfelder": zielfelder,
        "nurOffen": bool(daten.get("nurOffen", True)),
        "geheimnisse": sorted(geheim),
    }
    with _LOCK:
        # erst das Geheimnis, dann die Datei: scheitert die Verschluesselung,
        # entsteht kein Entwurf mit fehlenden Passwoertern
        _geheimnis_abgleichen(store, geheim, operator)
        pfad = _pfad(store)
        pfad.parent.mkdir(parents=True, exist_ok=True)
        tmp = pfad.with_name(pfad.name + ".tmp")
        tmp.write_text(json.dumps(inhalt, ensure_ascii=False), "utf-8")
        tmp.replace(pfad)
    return {"gespeichert": jetzt}


def laden(store) -> dict | None:
    """Gespeicherten Arbeitsstand samt Passwoertern zurueckgeben (oder
    ``None``). ``passwoerter_fehlen`` zaehlt Passwoerter, die nicht
    wiederhergestellt werden konnten (z. B. anderer Windows-Benutzer)."""
    try:
        roh = json.loads(_pfad(store).read_text("utf-8"))
        _pruefen_slots(roh["slots"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    schluessel = roh.get("geheimnisse") or []
    fehlen = 0
    if schluessel:
        try:
            geheim = json.loads(secrets_mod.get_password(store, GEHEIMNIS_ID) or "{}")
        except ValueError:
            geheim = {}
        for k in schluessel:
            wert = geheim.get(k) if isinstance(geheim, dict) else None
            if wert is None:
                fehlen += 1
                continue
            try:
                sid, rest = k.split("|", 1)
                name, r, c = rest.rsplit("|", 2)
                roh["slots"][sid]["sheets"][name]["rows"][int(r)][int(c)] = wert
            except (KeyError, IndexError, ValueError, TypeError):
                fehlen += 1
    roh["passwoerter_fehlen"] = fehlen
    return roh


def loeschen(store, operator: str = "") -> None:
    with _LOCK:
        try:
            _pfad(store).unlink()
        except FileNotFoundError:
            pass
        if secrets_mod.has_password(store, GEHEIMNIS_ID):
            secrets_mod.delete_password(store, GEHEIMNIS_ID, operator)


def registrieren(app, store) -> None:
    """Die drei Wege (lesen/speichern/loeschen) an die FastAPI-App haengen."""
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    from .config import operator as _operator

    @app.get("/api/stammdaten/entwurf")
    def stammdaten_entwurf_lesen() -> JSONResponse:
        return JSONResponse({"entwurf": laden(store)})

    @app.put("/api/stammdaten/entwurf")
    def stammdaten_entwurf_speichern(body: dict) -> JSONResponse:
        try:
            return JSONResponse(speichern(store, body, _operator()))
        except ValueError as e:
            raise HTTPException(400, str(e))
        except OSError as e:
            raise HTTPException(500, f"Zwischenspeichern nicht moeglich: {e}")

    @app.delete("/api/stammdaten/entwurf")
    def stammdaten_entwurf_loeschen() -> JSONResponse:
        loeschen(store, _operator())
        return JSONResponse({"ok": True})
