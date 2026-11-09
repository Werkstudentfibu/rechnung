"""Integrate an explicit Excel-import merge with an existing creditor.

The regular Excel import remains in ``stammdaten_import.py``. This small
adapter hooks its existing preview, answer and apply functions so the UI can
ask the user whether a row should merge into the matched creditor.
"""
from __future__ import annotations

import re
from contextvars import ContextVar

OPTION = "mit bestehendem Kreditor zusammenführen (ein Kreditor bleibt)"
_IN_APPLY = ContextVar("stammdaten_import_merge_in_apply", default=False)


def _notiz(sup, text: str) -> None:
    from . import stammdaten_import as si
    sup.notes = si._notizen_ergaenzen(sup.notes, text)


def _liste_union(alt: list[str], neu: list[str], normalisieren=lambda x: x) -> list[str]:
    out = list(alt or [])
    bekannte = {normalisieren(x) for x in out if x}
    for x in neu:
        if x and normalisieren(x) not in bekannte:
            out.append(x)
            bekannte.add(normalisieren(x))
    return sorted(out)


def _bibliothek_merge(store, zeile: dict, operator: str) -> tuple[str, bool]:
    """Keep the existing creditor and merge the imported row into it.
    -> (sid, unveraendert), siehe ``stammdaten_import._anwenden_kreditor``."""
    from . import stammdaten_import as si

    sid = zeile.get("bibliothek_id")
    sup = store.supplier(sid) if sid else None
    if sup is None:
        raise ValueError("Der ausgewählte Bibliotheks-Kreditor ist nicht mehr vorhanden. Bitte Vorschau neu laden.")
    werte = zeile["werte"]
    land = "CH" if zeile["art"] == si.ART_KREDITOR_CH else "DE"
    fingerabdruck = si._zeilen_fingerabdruck(
        {"art": zeile["art"], "werte": werte, "bibliothek_merge": True})
    if sup.stammdaten_import_hash and sup.stammdaten_import_hash == fingerabdruck:
        return sid, True

    name = (werte.get("name") or "").strip()
    if name:
        if not sup.name:
            sup.name = name
        elif si.norm(sup.name) != si.norm(name):
            _notiz(sup, f"Name aus Excel ({land}): {name}")

    adresse = [x for x in (werte.get("strasse", "").strip(),
                            " ".join(x for x in (werte.get("plz", "").strip(),
                                                 werte.get("ort", "").strip()) if x)) if x]
    if adresse:
        bestehend = list(sup.address_lines or [])
        if bestehend and bestehend[-1] in ("DE", "CH"):
            bestehend = bestehend[:-1]
        if not bestehend:
            sup.address_lines = adresse + [land]
        elif [si.norm(x) for x in bestehend] != [si.norm(x) for x in adresse]:
            _notiz(sup, f"Anschrift aus Excel ({land}): {', '.join(adresse)}")

    if werte.get("notiz"):
        sup.notes = si._notizen_ergaenzen(sup.notes, werte["notiz"])
    if werte.get("ustid"):
        sup.ustids = _liste_union(sup.ustids, [werte["ustid"]], si.norm_ustid)
    if werte.get("iban"):
        sup.ibans = _liste_union(sup.ibans, [werte["iban"]],
                                 lambda x: re.sub(r"\s+", "", x).upper())

    for feld, wert in (("bic", werte.get("bic", "").strip()),
                       ("creditor_id", werte.get("steuernummer", "").strip())):
        if not wert:
            continue
        bisher = getattr(sup, feld, "") or ""
        if not bisher:
            setattr(sup, feld, wert)
        elif si.norm(bisher) != si.norm(wert):
            bezeichnung = "BIC" if feld == "bic" else "Steuernummer"
            _notiz(sup, f"{bezeichnung} aus Excel ({land}): {wert}")

    nummer = (werte.get("kreditorennummer") or "").strip()
    if nummer:
        land_nr = (sup.external_ids or {}).get(land, "")
        if land_nr and land_nr != nummer:
            _notiz(sup, f"Kreditorennummer aus Excel ({land}): {nummer}")
        elif not land_nr:
            gleiche_waehrung = sup.currency == ("CHF" if land == "CH" else "EUR")
            if sup.external_id and gleiche_waehrung and sup.external_id != nummer:
                # Alte Datensaetze ohne Laender-Mapping: bestehende Nummer bewahren.
                _notiz(sup, f"Kreditorennummer aus Excel ({land}): {nummer}")
            else:
                sup.external_ids = {**(sup.external_ids or {}), land: nummer}
        if not sup.external_id:
            sup.external_id = nummer

    neue_waehrung = "CHF" if land == "CH" else "EUR"
    if sup.currency and sup.currency != neue_waehrung:
        _notiz(sup, f"Währung aus Excel ({land}): {neue_waehrung}")
    else:
        sup.currency = neue_waehrung

    verweis = (werte.get("mandant_verweis") or "").strip()
    if verweis:
        address_id = si._slug(verweis)
        sup.addresses = sorted(set(sup.addresses or []) | {address_id})

    sup.stammdaten_import_hash = fingerabdruck
    store.save_supplier(sup)
    store.audit("stammdaten_kreditor_zusammengefuehrt",
                {"id": sid, "blatt": zeile["blatt"], "zeile": zeile["zeile"]}, operator)
    return sid, False


def install() -> None:
    """Install once into the existing preview/answer/apply flow."""
    from . import stammdaten_import as si
    if getattr(si, "_bibliothek_merge_erweiterung", False):
        return

    alt_konflikt = si._konflikt_mit_bibliothek
    alt_antwort = si._antworten_einmischen
    alt_supplier_id = si._supplier_id
    alt_anwenden_kreditor = si._anwenden_kreditor
    alt_anwenden_import = si.anwenden

    def konflikt(bib, zeile):
        alt_konflikt(bib, zeile)
        if zeile.get("zusammengefasst") or zeile.get("art") not in si.ARTEN_KREDITOR:
            return
        treffer = bib.treffer(zeile["werte"])
        if treffer is None:
            return
        if _IN_APPLY.get():
            zeile["bibliothek_id"] = treffer.id
        land = "CH" if zeile["art"] == si.ART_KREDITOR_CH else "DE"
        werte = zeile["werte"]
        nr_alt = (treffer.external_ids or {}).get(land) or treffer.external_id
        nr_neu = (werte.get("kreditorennummer") or "").strip()
        nummer_weicht_ab = bool(nr_alt and nr_neu and nr_alt != nr_neu)
        frage = next((f for f in zeile["fragen"] if f["feld"] == "bibliothek"), None)
        if nummer_weicht_ab and frage is None:
            frage = si._frage(
                "bibliothek", f"Gibt es schon in der Bibliothek ({treffer.id}) mit "
                "anderer Kreditorennummer -- wählen Sie, was bleiben soll:",
                antworten=["bestehenden Datensatz aktualisieren (Excel-Werte übernehmen)",
                           OPTION, "als neuen, separaten Datensatz anlegen",
                           "Zeile weglassen (nichts ändern)"])
            zeile["fragen"].append(frage)
            if zeile["status"] != si.STATUS_ROT:
                zeile["status"] = si.STATUS_GELB
        elif frage is not None:
            frage["antwortmoeglichkeiten"] = [
                "bestehenden Datensatz aktualisieren (Excel-Werte übernehmen)",
                OPTION, "als neuen, separaten Datensatz anlegen",
                "Zeile weglassen (nichts ändern)"]

    def antwort(zeile, antworten):
        alt_antwort(zeile, antworten)
        key = si._antwort_schluessel(zeile["schluessel"], "bibliothek")
        antwort_daten = antworten.get(key) or {}
        if (_IN_APPLY.get() and zeile.get("bibliothek_id") and zeile.get("beantwortet")
                and not zeile.get("weglassen") and not zeile.get("neu_anlegen")):
            # Internal target ID only while server-side applying; preview data
            # sent to the browser does not expose it.
            zeile["werte"]["_bibliothek_id"] = zeile["bibliothek_id"]
        if (_IN_APPLY.get() and
                (antwort_daten.get("antwort") or "").strip().lower() == OPTION.lower()):
            zeile["bibliothek_zusammenfuehren"] = True

    def supplier_id(werte, verschmelzen=None):
        if werte.get("_bibliothek_id"):
            return werte["_bibliothek_id"]
        return alt_supplier_id(werte, verschmelzen)

    def anwenden_kreditor(store, zeile, operator):
        if zeile.get("bibliothek_zusammenfuehren"):
            return _bibliothek_merge(store, zeile, operator)
        ziel = zeile.get("bibliothek_id")
        if ziel and not zeile.get("neu_anlegen"):
            werte = zeile["werte"]
            werte["_bibliothek_id"] = ziel
            try:
                return alt_anwenden_kreditor(store, zeile, operator)
            finally:
                werte.pop("_bibliothek_id", None)
        return alt_anwenden_kreditor(store, zeile, operator)

    def anwenden(store, sheets, antworten, operator="", fortschritt=None, vor=None):
        token = _IN_APPLY.set(True)
        try:
            return alt_anwenden_import(store, sheets, antworten, operator,
                                       fortschritt=fortschritt, vor=vor)
        finally:
            _IN_APPLY.reset(token)

    si._konflikt_mit_bibliothek = konflikt
    si._antworten_einmischen = antwort
    si._supplier_id = supplier_id
    si._anwenden_kreditor = anwenden_kreditor
    si.anwenden = anwenden
    si.ANTWORT_BIBLIOTHEK_ZUSAMMENFUEHREN = OPTION
    si._bibliothek_merge_erweiterung = True
