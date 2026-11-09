"""Postfach-Passwoerter verschluesselt ablegen (Entscheidung Aaron
15.10.2026). Grundsatz: nie im Klartext in ``data/`` oder in Logs -- ein
``Postfach``-Datensatz (``postfach.py``) kennt sein Passwort gar nicht erst,
nur diese separate, eigene Datei tut es.

- **Windows**: DPAPI (``CryptProtectData``/``CryptUnprotectData`` ueber
  ``ctypes``, reine Standardbibliothek) -- an den Windows-Benutzer gebunden,
  kein eigener Schluessel zu verwalten. Das ist dieselbe Technik, mit der z.B.
  der Windows-Zugangsdaten-Manager Passwoerter ablegt.
- **Alles andere (Linux/macOS, hier: Entwicklung/Tests)**: DPAPI gibt es
  nicht -- klar markierter Klartext-Fallback in einer eigenen, per Dateiname
  als unsicher gekennzeichneten Datei mit Rechten ``0600``, einmal mit
  WARNUNG auf der Konsole. Nur fuer Tests/Entwicklung gedacht, nie fuer den
  echten Betrieb (der laeuft auf dem Windows-Laptop).

Das Web-UI zeigt zu jedem Postfach ausschliesslich "gesetzt"/"nicht gesetzt"
(``has_password``) -- ``get_password`` wird nur intern beim Abholen benutzt,
nie ueber die API nach aussen gegeben."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

WINDOWS_DATEI = "postfach_secrets.json"
UNSICHER_DATEI = "postfach_secrets_unsicher.json"

_warnung_gezeigt = False


def _ist_windows() -> bool:
    return sys.platform == "win32"


def _pfad(store) -> Path:
    return store.root / (WINDOWS_DATEI if _ist_windows() else UNSICHER_DATEI)


def _lesen(pfad: Path) -> dict:
    try:
        return json.loads(pfad.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _schreiben(pfad: Path, daten: dict) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_name(pfad.name + ".tmp")
    tmp.write_text(json.dumps(daten, indent=2), "utf-8")
    tmp.replace(pfad)
    if not _ist_windows():
        try:
            os.chmod(pfad, stat.S_IRUSR | stat.S_IWUSR)   # 0600
        except OSError:
            pass


def _dpapi_protect(data: bytes) -> bytes:   # pragma: no cover -- nur unter Windows ausfuehrbar
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    in_blob = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob), "rechnung-postfach", None, None, None, 0, ctypes.byref(out_blob))
    if not ok:
        raise OSError("CryptProtectData fehlgeschlagen")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:   # pragma: no cover -- nur unter Windows ausfuehrbar
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    in_blob = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob))
    if not ok:
        raise OSError("CryptUnprotectData fehlgeschlagen")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def set_password(store, postfach_id: str, password: str, operator: str = "") -> None:
    global _warnung_gezeigt
    pfad = _pfad(store)
    daten = _lesen(pfad)
    if _ist_windows():
        import base64
        daten[postfach_id] = base64.b64encode(_dpapi_protect(password.encode("utf-8"))).decode("ascii")
    else:
        if not _warnung_gezeigt:
            print(f"WARNUNG: Passwort-Verschluesselung (DPAPI) ist nur unter Windows "
                  f"verfuegbar -- auf diesem Rechner liegt das Postfach-Passwort "
                  f"unverschluesselt in {pfad.name} (Datei-Rechte 0600). Nur fuer "
                  f"Tests/Entwicklung gedacht, nicht fuer den echten Betrieb.")
            _warnung_gezeigt = True
        daten[postfach_id] = password
    _schreiben(pfad, daten)
    store.audit("postfach_passwort_gesetzt", {"postfach_id": postfach_id}, operator)


def get_password(store, postfach_id: str) -> str:
    pfad = _pfad(store)
    roh = _lesen(pfad).get(postfach_id)
    if not roh:
        return ""
    if not _ist_windows():
        return roh
    import base64
    try:
        return _dpapi_unprotect(base64.b64decode(roh)).decode("utf-8")
    except (OSError, ValueError):
        return ""


def has_password(store, postfach_id: str) -> bool:
    return postfach_id in _lesen(_pfad(store))


def delete_password(store, postfach_id: str, operator: str = "") -> None:
    pfad = _pfad(store)
    daten = _lesen(pfad)
    if postfach_id in daten:
        del daten[postfach_id]
        _schreiben(pfad, daten)
        store.audit("postfach_passwort_geloescht", {"postfach_id": postfach_id}, operator)
