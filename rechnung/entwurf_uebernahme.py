"""Lieferanten-Entwurf in einen bestehenden Lieferanten uebernehmen
(Entscheidung Aaron, 09.11.2026).

Lage: die 11.219 Lieferanten aus dem Excel-Import (``stammdaten_import.py``)
haben weder USt-IdNr noch IBAN. Jede Rechnung eines solchen Lieferanten legt
deshalb einen automatischen Entwurf ``Unbekannt (DE...)`` an
(``pipeline._auto_draft_pdf``, ``Supplier.bestaetigt=False``), der ueber die
harte Kennung gefunden wird. Bestaetigt man den Entwurf
(``pipeline.confirm_supplier``), entsteht ein ZWEITER Lieferant neben dem
Excel-Eintrag -- eine Dublette.

Dieser Weg ist die Alternative zum Bestaetigen: der Entwurf wandert in den
bestehenden, bestaetigten Lieferanten.

- Kennungen und Stammdaten des Entwurfs (USt-IdNrn, IBANs, Domains,
  Fingerprint, Rechnungsadressen, Bank/BIC/BLZ/Konto, Glaeubiger-ID,
  Kontakt, Adresszeilen, feste Werte) gehen in den Ziel-Lieferanten:
  **Listen werden vereinigt, vorhandene Werte des Ziels bleiben** (ein Feld
  des Ziels wird nur gefuellt, wenn es leer ist; Name, Lieferantennummer,
  Waehrung, Positionsmodus des Ziels bleiben immer).
- Vorlagen des Entwurfs haengen danach am Ziel (Id ``<ziel>__<variante>``,
  bei Belegung die naechste freie Version wie in ``learn._next_template_id``).
- Der Entwurf wird geloescht.
- Alles, was die ``supplier_id`` des Entwurfs gespeichert hat, zeigt danach
  auf das Ziel: ``processed.jsonl`` (damit die Dublettenpruefung die
  Rechnungen des Entwurfs beim Ziel kennt), ``events.jsonl``,
  ``shadow.jsonl``, ``lehrer.jsonl``, Begleitdateien
  ``menschlich/*.grund.json``, Vorlern-Gruppen ``vorlernen/*.json``.
  ``audit.jsonl`` bleibt unveraendert (append-only, GoBD).
  ``liste-cache.json`` braucht keine Nacharbeit: seine Eintraege haengen am
  Stand des Lieferanten (``listencache._Kontext.stand``) und veralten mit
  dem Loeschen des Entwurfs bzw. der Aenderung des Ziels von selbst; der
  laufende Server wirft die Eintraege des Entwurfs zusaetzlich sofort weg
  (``Listencache.vergessen``).
- Audit-Eintrag ``lieferant_uebernommen`` (Operator, Entwurf, Ziel, was
  uebernommen wurde, wie viele Eintraege umgehaengt) und Ereignis
  ``lieferant``/``uebernommen`` (``eventlog.record_supplier``).

Sicherheitsregel (Aaron): Stammdaten bestaetigt immer ein Mensch. Diese
Funktion wird NIE automatisch aufgerufen -- nur ueber den bewussten Klick
"In bestehenden Lieferanten uebernehmen" (``POST
/api/suppliers/{entwurf_id}/uebernehmen``). Der Hinweis ``ENTWURF_HINWEIS``
der Pipeline (Name eines bestehenden Lieferanten steht auf der Rechnung)
belegt in der Oberflaeche nur die Auswahl vor, entscheidet aber nichts.
"""

from __future__ import annotations

from .identify import Supplier
from .store import Store

AUDIT_AKTION = "lieferant_uebernommen"
EREIGNIS_AKTION = "uebernommen"

# Listen: Vereinigung, Reihenfolge des Ziels zuerst, dann Neues vom Entwurf.
LISTEN = ("ustids", "ibans", "domains", "fingerprint")
# Einzelwerte: nur gefuellt, wenn sie beim Ziel leer sind.
EINZELWERTE = ("bank_name", "bic", "bank_code", "bank_account", "creditor_id",
               "contact", "default_payment_terms", "default_tax_rate", "notes")


def _vereinige(ziel: list[str], entwurf: list[str]) -> tuple[list[str], list[str]]:
    """-> (vereinigte Liste, neu hinzugekommene Werte)."""
    vorhanden = {x for x in ziel}
    neu = [x for x in dict.fromkeys(entwurf) if x and x not in vorhanden]
    return list(ziel) + neu, neu


def zusammenfuehren(ziel: Supplier, entwurf: Supplier) -> dict:
    """Werte des Entwurfs in ``ziel`` eintragen (in place) -> was uebernommen
    wurde (nur die tatsaechlich neuen Werte, fuer den Audit-Eintrag)."""
    uebernommen: dict = {}
    for feld in LISTEN:
        gesamt, neu = _vereinige(getattr(ziel, feld), getattr(entwurf, feld))
        if neu:
            setattr(ziel, feld, gesamt)
            uebernommen[feld] = neu
    # Rechnungsadressen: leer beim Ziel heisst "gilt fuer alle" -- das bleibt.
    if ziel.addresses and entwurf.addresses:
        gesamt, neu = _vereinige(ziel.addresses, entwurf.addresses)
        if neu:
            ziel.addresses = gesamt
            uebernommen["addresses"] = neu
    # Adresszeilen sind EINE Anschrift, keine Liste zum Mischen: nur
    # uebernehmen, wenn das Ziel noch keine hat.
    if not ziel.address_lines and entwurf.address_lines:
        ziel.address_lines = list(entwurf.address_lines)
        uebernommen["address_lines"] = list(entwurf.address_lines)
    for feld in EINZELWERTE:
        if not getattr(ziel, feld) and getattr(entwurf, feld):
            setattr(ziel, feld, getattr(entwurf, feld))
            uebernommen[feld] = getattr(entwurf, feld)
    neu_fest = {k: v for k, v in (entwurf.fixed_fields or {}).items()
                if v and k not in (ziel.fixed_fields or {})}
    if neu_fest:
        ziel.fixed_fields = {**(ziel.fixed_fields or {}), **neu_fest}
        uebernommen["fixed_fields"] = neu_fest
    return uebernommen


def _vorlagen_umhaengen(store: Store, entwurf_id: str, ziel_id: str) -> list[tuple[str, str]]:
    """Vorlagen des Entwurfs unter dem Ziel ablegen -> [(alte Id, neue Id)].
    Muss VOR ``store.delete_supplier(entwurf_id)`` laufen, das loescht sonst
    die Vorlagen mit."""
    from .learn import _next_template_id

    umgehaengt = []
    for tpl in store.templates_for(entwurf_id):
        alt = tpl.id
        neu = f"{ziel_id}__{tpl.variant}"
        if store.template(neu) is not None:
            neu = _next_template_id(store, ziel_id, tpl.variant)
        tpl.id, tpl.supplier_id = neu, ziel_id
        store.save_template(tpl)
        store.delete_template(alt)
        umgehaengt.append((alt, neu))
    return umgehaengt


def uebernehmen(store: Store, entwurf_id: str, ziel_id: str, operator: str = "") -> dict:
    """Entwurf ``entwurf_id`` in den bestaetigten Lieferanten ``ziel_id``
    uebernehmen. ``KeyError``: einer der beiden fehlt. ``ValueError``: Entwurf
    ist schon bestaetigt, Ziel ist selbst ein Entwurf, beide gleich oder der
    Dummy-Lieferant. -> Zusammenfassung (``ziel``, ``uebernommen``,
    ``umgehaengt``, ``vorlagen``)."""
    from . import eventlog
    from .config import operator as _op
    from .dummy import ist_dummy_id

    if entwurf_id == ziel_id:
        raise ValueError("Entwurf und Ziel sind derselbe Lieferant")
    if ist_dummy_id(entwurf_id) or ist_dummy_id(ziel_id):
        raise ValueError("Der Dummy-Lieferant kann weder uebernommen werden noch Ziel sein")
    entwurf = store.supplier(entwurf_id)
    if entwurf is None:
        raise KeyError(f"Lieferant {entwurf_id!r} nicht gefunden")
    ziel = store.supplier(ziel_id)
    if ziel is None:
        raise KeyError(f"Lieferant {ziel_id!r} nicht gefunden")
    if entwurf.bestaetigt:
        raise ValueError("Nur ein unbestaetigter Lieferanten-Entwurf kann uebernommen werden")
    if not ziel.bestaetigt:
        raise ValueError("Das Ziel muss ein bestaetigter Lieferant sein -- "
                         "ein Entwurf kann nicht in einen anderen Entwurf uebernommen werden")

    uebernommen = zusammenfuehren(ziel, entwurf)
    vorlagen = _vorlagen_umhaengen(store, entwurf_id, ziel_id)
    store.save_supplier(ziel)
    store.delete_supplier(entwurf_id)
    umgehaengt = store.lieferant_umhaengen(entwurf_id, ziel_id, ziel.name)
    umgehaengt.update(_begleitdateien_umhaengen(store, entwurf_id, ziel_id, ziel.name))

    op = operator or _op()
    detail = {
        "entwurf": entwurf_id, "entwurf_name": entwurf.name,
        "ziel": ziel_id, "ziel_name": ziel.name,
        "uebernommen": uebernommen,
        "vorlagen": [{"alt": a, "neu": n} for a, n in vorlagen],
        "umgehaengt": umgehaengt,
    }
    store.audit(AUDIT_AKTION, detail, op)
    eventlog.record_supplier(store, ziel_id, ziel.name, EREIGNIS_AKTION, op,
                             detail={"entwurf": entwurf_id, "entwurf_name": entwurf.name})
    return {"ziel": ziel, "uebernommen": uebernommen, "umgehaengt": umgehaengt,
            "vorlagen": vorlagen}


def _begleitdateien_umhaengen(store: Store, alt: str, neu: str, neu_name: str) -> dict[str, int]:
    """Begleitdateien im Ordner "Menschliche Bearbeitung" (``lieferant``/
    ``lieferant_name``), Vorlern-Gruppen (``supplier_id``/``supplier_name``)
    und das Lehrer-Protokoll (``lieferant``: Name oder Id) -> Anzahl je Ort."""
    from . import human, vorlernen

    n_mensch = 0
    for p in human._sidecars(human.human_dir(store.root)):
        rec = human._read(p)
        if rec and rec.get("lieferant") == alt:
            rec["lieferant"], rec["lieferant_name"] = neu, neu_name
            human._write(p, rec)
            n_mensch += 1
    n_vorlernen = 0
    for g in vorlernen.list_groups(store):
        if g.get("supplier_id") == alt and g.get("gruppe_id"):
            g["supplier_id"], g["supplier_name"] = neu, neu_name
            vorlernen._write_group(store, g)
            n_vorlernen += 1
    return {"menschlich": n_mensch, "vorlernen": n_vorlernen}
