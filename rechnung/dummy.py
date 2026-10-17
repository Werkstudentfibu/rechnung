"""Der eine Dummy-Lieferant (Aaron 17.10.2026): "Wenn es eine Firma noch nicht
gibt, wird ein Dummy-Lieferant eingefuegt. Die Rechnung wird trotzdem normal
bearbeitet. Die Leute, die sie entgegennehmen, aendern das manuell um und
erstellen einen neuen Lieferanten."

* Es gibt genau EINEN, mit fester ID (``DUMMY_ID``) und ``Supplier.dummy=True``.
* ``Store.suppliers()``/``Store.supplier()`` blenden ihn aus: Lieferantenerkennung,
  Lernen (Template, Domain, IBAN/UID), Stammdaten-Import und Lernbericht sehen
  ihn nie. Nur ``Store.dummy_lieferant()`` liefert ihn.
* Beim Senden ersetzt er einen fehlenden/unbestaetigten Lieferanten NUR im
  JobRouter-Datensatz; der interne Entwurf mit den echten Daten bleibt und
  lernt weiter. Die erkannten echten Daten gehen als ``erkannter_lieferant``
  mit, damit niemand sie abtippen muss.
* Name und Fremdnummer per ``RECHNUNG_DUMMY_LIEFERANT_NAME`` /
  ``RECHNUNG_DUMMY_LIEFERANT_NR`` aenderbar.
"""

from __future__ import annotations

import os

from .identify import Supplier, norm

DUMMY_ID = "dummy-lieferant"
STANDARD_NAME = "Dummy Lieferant"
STANDARD_NR = "DUMMY"


def name() -> str:
    return (os.environ.get("RECHNUNG_DUMMY_LIEFERANT_NAME") or "").strip() or STANDARD_NAME


def nummer() -> str:
    return (os.environ.get("RECHNUNG_DUMMY_LIEFERANT_NR") or "").strip() or STANDARD_NR


def ist_dummy_id(sid: str | None) -> bool:
    return (sid or "") == DUMMY_ID


def ist_dummy_zeile(werte: dict[str, str]) -> bool:
    """Zeile der Excel-Liste, die den Dummy meint (Name beginnt mit "Dummy"
    bzw. ist der eingestellte Name, oder Kontonummer = Dummy-ID/-Nr.)."""
    # Vor dem Zusammensetzen steht der Name noch in name1/bezeichnung1
    for feld in ("name", "name1", "bezeichnung1", "bezeichnung"):
        n = norm(werte.get(feld, ""))
        if n and (n == norm(name()) or n.startswith("dummy")):
            return True
    k = (werte.get("kreditorennummer") or "").strip().lower()
    return bool(k) and k in (DUMMY_ID, nummer().lower())


def neu() -> Supplier:
    """Der Dummy mit neutralen Platzhaltern. Nur was JobRouter-Datensatz/
    Stammdaten-Block braucht (ID, Name, Anschrift) ist gefuellt -- bewusst
    erkennbar unecht. IBAN, USt-IdNr, Steuernummer, Bank bleiben leer."""
    return Supplier(
        id=DUMMY_ID, name=name(), external_id=nummer(),
        address_lines=["Dummy-Strasse 1", "00000 Dummy-Ort"],
        notes="Platzhalter fuer unbekannte Lieferanten -- bitte in JobRouter "
              "durch den echten Lieferanten ersetzen.",
        bestaetigt=True, dummy=True)


def sicherstellen(store) -> Supplier:
    """Legt den Dummy an, falls er fehlt; passt Name/Nummer an die Einstellung
    an. Idempotent, schreibt nur bei Aenderung."""
    cur = store.dummy_lieferant()
    soll = neu()
    if cur is None:
        store._save_dummy(soll)
        return soll
    if cur.name != soll.name or cur.external_id != soll.external_id or not cur.dummy \
            or not cur.bestaetigt:
        cur.name, cur.external_id = soll.name, soll.external_id
        cur.dummy, cur.bestaetigt = True, True
        store._save_dummy(cur)
    return cur


def noetig(store, res) -> bool:
    """Wird diese Rechnung als Dummy gesendet? Ja, wenn kein oder nur ein
    unbestaetigter Lieferant (Entwurf) da ist; ein bestaetigter echter
    Lieferant hat immer Vorrang."""
    sup = store.supplier(res.supplier_id) if res.supplier_id else None
    return sup is None or not sup.bestaetigt


def erkannter_lieferant(store, res) -> dict:
    """Die vom Programm erkannten echten Lieferantendaten (aus dem Entwurf,
    sonst aus den gelesenen Feldern), damit die Kollegen den neuen Lieferanten
    nicht abtippen muessen."""
    from . import stammdaten

    sup = store.supplier(res.supplier_id) if res.supplier_id else None
    iban = res.fields.get("iban")
    iban = iban.value if iban is not None and isinstance(iban.value, str) else None
    if sup is not None:
        d = stammdaten.lieferant(sup, iban) or {}
        out = {k: d.get(k, "") for k in ("name", "strasse", "plz", "ort", "land", "iban",
                                          "bic", "ustid", "steuernummer")}
    else:
        out = {"name": res.supplier_name or "", "strasse": "", "plz": "", "ort": "",
               "land": "", "iban": (iban or "").replace(" ", "").upper(), "bic": "",
               "ustid": "", "steuernummer": ""}
    return {k: v for k, v in out.items() if v}


def bemerkung(erkannt: dict) -> str:
    """Kurzer Text fuers Bemerkungsfeld in JobRouter."""
    teile = [f"Lieferant unbekannt – als {name()} gesendet. Erkannt:"]
    if erkannt.get("name"):
        teile.append(erkannt["name"])
    adr = " ".join(x for x in (erkannt.get("strasse"), erkannt.get("plz"), erkannt.get("ort"),
                               erkannt.get("land")) if x)
    if adr:
        teile.append(adr)
    for k, lab in (("iban", "IBAN"), ("ustid", "USt-IdNr"), ("steuernummer", "Steuernr.")):
        if erkannt.get(k):
            teile.append(f"{lab} {erkannt[k]}")
    return "; ".join(teile)
