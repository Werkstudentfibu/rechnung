"""Vorab-Lernen aus Altbestand (SPEC Effizienz-Baustein 6 -- groesster Gewinn
fuer die Testphase).

``rechnung vorlernen <ordner>`` liest einen Ordner mit ALTEN Rechnungen (z.B.
die ~170 Stueck vom Firmenlaptop des Kunden) und gruppiert sie nach
Lieferant (Bibliothek) + Layout. Je Datei wird ein Werte-Vorschlag
ermittelt -- ueber eine schon aktive Vorlage des Lieferanten, sonst mit
derselben KI-Volllesung + Beleg-Pflicht wie bei einer einzelnen unbekannten
Rechnung (``semantic.extract_full``). Nichts
davon ist sofort aktiv: das Training-UI zeigt pro Gruppe alle Rechnungen mit
ihren Werten nebeneinander (Reiter "Vorlernen"), einzelne Dateien koennen
korrigiert/ausgeschlossen werden, und erst ein ausdruecklicher Klick
"bestaetigen" lernt daraus eine echte Vorlage (``learn.learn_template`` --
dieselbe Pruefung/Probelauf-Logik wie beim Lernen aus einer einzelnen
bestaetigten Rechnung).

Vorgelernte Rechnungen werden NIE nach JobRouter exportiert und zaehlen NIE
in der Schattenstatistik als Live-Rechnung -- dieses Modul ruft dafuer
bewusst nie ``dispatch()``/``finalize()``/``shadow.record()`` auf, auch nicht
beim Bestaetigen einer Gruppe (nur die gelernte Vorlage selbst wird
gespeichert, die Alt-Rechnungen bleiben aussen vor)."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import billing, einvoice, semantic
from .identify import extract_identifiers, identify_with_reason
from .learn import LEARNABLE_TYPES, _active_template, _next_template_id, learn_template
from .template import STANDARD_FIELDS, apply_template, ausgeblendet
from .textnorm import coerce

GROUP_DIR = "vorlernen"


def _group_path(store, gruppe_id: str) -> Path:
    return store.root / GROUP_DIR / f"{gruppe_id}.json"


def _read_group(store, gruppe_id: str) -> dict | None:
    p = _group_path(store, gruppe_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_group(store, group: dict) -> None:
    p = _group_path(store, group["gruppe_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(group, indent=2, ensure_ascii=False, default=str), "utf-8")
    tmp.replace(p)


def list_groups(store) -> list[dict]:
    d = store.root / GROUP_DIR
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text("utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def get_group(store, gruppe_id: str) -> dict | None:
    return _read_group(store, gruppe_id)


def _fingerprint_key(doc) -> str:
    """Stabiler Schluessel fuer ein Layout ohne bekannten Lieferanten --
    gleicher Kopf-Fingerabdruck gruppiert gleich, unabhaengig vom Dateinamen."""
    fp = sorted(extract_identifiers(doc)["fingerprint"])[:15]
    return hashlib.sha1(",".join(fp).encode("utf-8")).hexdigest()[:10]


def _group_key(doc, store) -> tuple[str, str | None, str, str]:
    """-> (gruppe_id, supplier_id, supplier_name, variant).

    Lieferant je Rechnungsadresse (09.10.2026): die Adresse wird ZUERST
    bestimmt, dann nur unter den ihr zugeordneten Lieferanten gesucht --
    sonst wuerden zwei Altrechnungen verschiedener Adressen mit demselben
    (fuer die andere Adresse eigentlich fremden) Lieferanten gruppiert."""
    addr = billing.detect(doc, store.addresses())
    variant = addr[0].id if addr else "standard"
    address_id = addr[0].id if addr else None
    has_addr = bool(store.addresses())
    match, _why = identify_with_reason(
        doc, store.suppliers(), index=store.supplier_index(address_id),
        address_id=address_id, has_addresses=has_addr)
    if match:
        supplier, _score = match
        return f"{supplier.id}__{variant}", supplier.id, supplier.name, variant
    return f"unbekannt__{_fingerprint_key(doc)}", None, "", variant


def _extract_values(doc, supplier, store) -> tuple[dict, dict]:
    """Werte wie bei einer einzelnen Live-Rechnung: gibt es fuer den
    Lieferanten schon eine aktive Vorlage, liest zuerst sie (schnell, kein
    Modell); die KI-Volllesung (Beleg-Pflicht) fuellt nur, was dann noch
    fehlt bzw. liest unbekannte Layouts komplett. Lieferanten-Stammdaten
    kommen wie immer aus der Bibliothek. Dieselben Helfer wie
    ``pipeline`` -- bewusst importiert statt dupliziert."""
    from .pipeline import _inject_library_fields, _pick_template

    fields: dict = {}
    if supplier is not None:
        tpls = [t for t in store.templates_for(supplier.id) if not t.stale]
        tpl = _pick_template(doc, tpls)
        if tpl is not None:
            fields = {n: fr for n, fr in apply_template(doc, tpl).items()
                      if fr.source != "missing"}
    rejected: dict = {}
    missing_required = [n for n, (_t, req) in STANDARD_FIELDS.items()
                        if req and n not in fields and n not in ausgeblendet()]
    if missing_required:
        full = semantic.extract_full(doc.text)
        rejected = dict(full.rejected)
        for n, fr in full.fields.items():
            fields.setdefault(n, fr)
    if supplier is not None:
        _inject_library_fields(fields, supplier)
    werte = {n: {"value": fr.value, "raw": fr.raw, "confidence": fr.confidence,
                "source": fr.source} for n, fr in fields.items()}
    return werte, rejected


@dataclass
class ScanReport:
    gruppen: int = 0
    dateien: int = 0
    e_rechnungen: int = 0
    ohne_werte: int = 0      # weder Vorlage noch KI lieferte etwas -> von Hand
    fehler: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"gruppen": self.gruppen, "dateien": self.dateien,
                "e_rechnungen": self.e_rechnungen, "ohne_werte": self.ohne_werte,
                "fehler": self.fehler}


def scan(folder: str | Path, store) -> ScanReport:
    """Ordner mit alten Rechnungen lesen, gruppieren, Vorschlaege anlegen
    bzw. um neue Dateien ergaenzen. Bereits bestaetigte Gruppen werden dabei
    nicht mehr veraendert -- ihre Vorlage ist schon aktiv. E-Rechnungen
    brauchen kein Layout und werden uebersprungen (nur gezaehlt). Eine
    einzelne kaputte Alt-Rechnung bricht den Lauf nicht ab."""
    from .pipeline import invoice_files, load_document

    folder = Path(folder)
    report = ScanReport()
    groups: dict[str, dict] = {}
    for path in invoice_files(folder):
        if einvoice.load(path) is not None:
            report.e_rechnungen += 1
            continue
        try:
            doc, o = load_document(path, store)
            if doc is None:
                report.fehler.append({"datei": path.name, "grund": o.error})
                continue
            sha = doc.sha256
            gruppe_id, supplier_id, supplier_name, variant = _group_key(doc, store)
            supplier = store.supplier(supplier_id) if supplier_id else None
            werte, rejected = _extract_values(doc, supplier, store)
        except Exception as e:  # noqa: BLE001 -- eine kaputte Alt-Rechnung darf den Lauf nicht abbrechen
            report.fehler.append({"datei": path.name, "grund": f"{type(e).__name__}: {e}"})
            continue

        group = groups.get(gruppe_id) or _read_group(store, gruppe_id) or {
            "gruppe_id": gruppe_id, "supplier_id": supplier_id,
            "supplier_name": supplier_name, "variant": variant,
            "erstellt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "bestaetigt": False, "bestaetigt_am": None, "bestaetigt_von": None,
            "template_id": None, "abweichungen": {}, "dateien": [],
        }
        if group.get("bestaetigt"):
            groups[gruppe_id] = group
            continue
        by_sha = {d["sha256"]: d for d in group["dateien"]}
        prev = by_sha.get(sha, {})
        by_sha[sha] = {
            "datei": path.name, "sha256": sha, "pfad": str(path),
            "werte": {n: w for n, w in werte.items() if n not in ausgeblendet()},
            "rejected": rejected,
            "ausgeschlossen": prev.get("ausgeschlossen", False),
            "korrekturen": prev.get("korrekturen", {}),
        }
        group["dateien"] = list(by_sha.values())
        groups[gruppe_id] = group
        report.dateien += 1
        if not any(w.get("source") != "library" for w in werte.values()):
            report.ohne_werte += 1

    for group in groups.values():
        _write_group(store, group)
    report.gruppen = len(groups)
    return report


def set_correction(store, gruppe_id: str, sha256: str, field: str, value) -> dict | None:
    """Wert einer Datei der Gruppe von Hand setzen (leer = Korrektur
    zuruecknehmen). Standardfelder werden wie im Rechnungen-Reiter in ihren
    Typ umgewandelt; was sich nicht lesen laesst, wird abgelehnt statt
    still als Text gespeichert."""
    group = _read_group(store, gruppe_id)
    if group is None:
        return None
    raw = "" if value is None else str(value).strip()
    if raw and field in STANDARD_FIELDS:
        value, ok = coerce(raw, STANDARD_FIELDS[field][0])
        if not ok:
            raise ValueError(f"{field}: {raw!r} nicht lesbar")
    for d in group["dateien"]:
        if d["sha256"] == sha256:
            korr = d.setdefault("korrekturen", {})
            if raw:
                korr[field] = value
            else:
                korr.pop(field, None)
            break
    _write_group(store, group)
    return group


def set_excluded(store, gruppe_id: str, sha256: str, excluded: bool) -> dict | None:
    group = _read_group(store, gruppe_id)
    if group is None:
        return None
    for d in group["dateien"]:
        if d["sha256"] == sha256:
            d["ausgeschlossen"] = excluded
            break
    _write_group(store, group)
    return group


def assign_supplier(store, gruppe_id: str, supplier_id: str, supplier_name: str = "") -> dict | None:
    """Fuer eine Gruppe ohne erkannten Lieferanten (``unbekannt__...``) den
    Lieferanten von Hand zuordnen -- legt den Lieferanten selbst NICHT an
    (siehe SPEC §7h, "+ Neuer Lieferant" im Training-UI); erst ein bereits in
    der Bibliothek vorhandener (oder gerade frisch angelegter) ``supplier_id``
    kann bestaetigt werden."""
    group = _read_group(store, gruppe_id)
    if group is None:
        return None
    group["supplier_id"] = supplier_id
    group["supplier_name"] = supplier_name or group.get("supplier_name") or ""
    _write_group(store, group)
    return group


def discard(store, gruppe_id: str) -> bool:
    p = _group_path(store, gruppe_id)
    if not p.exists():
        return False
    p.unlink()
    return True


def _effective_values(d: dict) -> dict[str, object]:
    """Wert je Feld fuer eine Datei der Gruppe: eine manuelle Korrektur
    gewinnt, sonst der extrahierte/Stammdaten-Wert -- nie ein verworfener
    KI-Vorschlag (``source == "missing"`` kommt hier gar nicht erst vor)."""
    out: dict[str, object] = {}
    for name, w in (d.get("werte") or {}).items():
        if w.get("source") not in ("missing", None) and w.get("value") not in (None, ""):
            out[name] = w.get("value")
    out.update(d.get("korrekturen") or {})
    return out


def _learnable_values(d: dict) -> dict[str, object]:
    """Nur Felder, die eine Position im Layout haben (wie
    ``learn.confirmed_values``) -- Stammdaten aus der Bibliothek
    (IBAN/USt-IdNr/...) brauchen keine Vorlage; eine manuelle Korrektur
    zaehlt immer als gelesener Wert."""
    werte = d.get("werte") or {}
    korr = d.get("korrekturen") or {}
    out: dict[str, object] = {}
    for name, value in _effective_values(d).items():
        if STANDARD_FIELDS.get(name, ("",))[0] not in LEARNABLE_TYPES:
            continue
        if name not in korr and (werte.get(name) or {}).get("source") == "library":
            continue
        out[name] = value
    return out


# Felder, die innerhalb EINES Lieferanten/Layouts normalerweise gleich sind --
# weicht hier eine Rechnung von der Mehrheit ab, ist das verdaechtig
# (Lesefehler oder falsch gruppiert) und wird in der Tabelle markiert.
_STABLE_FIELDS = ("ustid", "iban", "steuernummer", "adresse", "ust_satz", "waehrung")


def deviations(group: dict) -> dict[str, list[str]]:
    """Feld -> sha256 der Dateien, deren Wert in der Stapel-Tabelle
    hervorgehoben werden soll: Wert fehlt, obwohl andere Rechnungen der
    Gruppe ihn haben, oder ein sonst stabiles Feld weicht vom Mehrheitswert
    ab (ohne klare Mehrheit: alle markiert). Ausgeschlossene Dateien zaehlen
    nicht mit."""
    aktiv = [d for d in group.get("dateien", []) if not d.get("ausgeschlossen")]
    eff = {d["sha256"]: _effective_values(d) for d in aktiv}
    out: dict[str, list[str]] = {}
    for name in sorted({n for v in eff.values() for n in v}):
        have = {sha: str(v[name]) for sha, v in eff.items() if v.get(name) not in (None, "")}
        bad = [sha for sha in eff if sha not in have]
        if name in _STABLE_FIELDS and have:
            counts: dict[str, int] = {}
            for v in have.values():
                counts[v] = counts.get(v, 0) + 1
            top = max(counts.values())
            mehrheit = [v for v, c in counts.items() if c == top]
            if len(mehrheit) == 1:
                bad += [sha for sha, v in have.items() if v != mehrheit[0]]
            elif len(counts) > 1:
                bad += list(have)
        if bad:
            out[name] = sorted(set(bad))
    return out


def confirm(store, gruppe_id: str, operator: str = "") -> dict:
    """Aus der Gruppe eine Vorlage lernen (``learn.learn_template``) -- erst
    danach ist sie aktiv (greift ab der naechsten LIVE-Rechnung dieses
    Lieferanten/Layouts, Bestaetigungspflicht fuer diese bleibt unveraendert).
    Anker-Rechnung ist die mit den meisten belegten Werten; die uebrigen
    Dateien der Gruppe werden gegen die fertige Vorlage geprueft und
    Abweichungen festgehalten (Diff-Tabelle im Training-UI). Bewusst KEIN
    ``finalize``/``dispatch``/``shadow.record`` -- die Alt-Rechnungen gehen
    nie nach JobRouter und zaehlen nie als Live-Rechnung."""
    from .config import operator as _op

    group = _read_group(store, gruppe_id)
    if group is None:
        raise ValueError(f"Vorlernen-Gruppe {gruppe_id!r} nicht gefunden")
    if not group.get("supplier_id"):
        raise ValueError("Erst einen Lieferanten zuordnen (kein Lieferant aus der "
                         "Bibliothek erkannt)")

    aktiv = [d for d in group["dateien"] if not d.get("ausgeschlossen")]
    if not aktiv:
        raise ValueError("Keine (nicht ausgeschlossene) Rechnung in der Gruppe")

    from .pipeline import load_document

    anchor = max(aktiv, key=lambda d: len(_learnable_values(d)))
    doc, o = load_document(anchor["pfad"], store)
    if doc is None:
        raise ValueError(f"Anker-Rechnung {anchor['datei']} nicht lesbar: {o.error}")
    values = _learnable_values(anchor)
    # Eine schon aktive Vorlage dieses Lieferanten/Layouts wird ergaenzt, nie
    # still ueberschrieben -- gleiche Regel wie learn.learn_from_confirmed().
    existing = _active_template(store, group["supplier_id"], group["variant"])
    tid = None if existing else _next_template_id(store, group["supplier_id"], group["variant"])
    tpl, outcome = learn_template(doc, group["supplier_id"], values, existing,
                                  group["variant"], template_id=tid)
    if tpl is None and existing is not None and outcome.kept and not outcome.skipped:
        tpl = existing  # aktive Vorlage liest die Gruppe schon vollstaendig richtig
    elif tpl is None:
        raise ValueError("Vorlage konnte nicht gelernt werden: "
                         + (outcome.reason or str(outcome.skipped)))
    else:
        store.save_template(tpl)

    abweichungen: dict[str, dict[str, dict]] = {}
    for d in aktiv:
        if d is anchor:
            continue
        try:
            other, _o = load_document(d["pfad"], store)
        except Exception:  # noqa: BLE001 -- Diff-Anzeige darf nicht abstuerzen
            continue
        if other is None:
            continue
        applied = apply_template(other, tpl)
        want_values = _learnable_values(d)
        for fname, want in want_values.items():
            fr = applied.get(fname)
            got = fr.value if fr and fr.source != "missing" else None
            if str(got) != str(want):
                abweichungen.setdefault(fname, {})[d["datei"]] = {
                    "gelernt": got, "erwartet": want}

    op = operator or _op()
    group["bestaetigt"] = True
    group["bestaetigt_am"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    group["bestaetigt_von"] = op
    group["template_id"] = tpl.id
    group["abweichungen"] = abweichungen
    _write_group(store, group)
    store.audit("vorlernen_bestaetigt", {
        "gruppe_id": gruppe_id, "template_id": tpl.id,
        "dateien": len(aktiv), "gelernt": outcome.learned,
    }, op)
    return {"template_id": tpl.id, "gelernt": outcome.learned, "kept": outcome.kept,
            "skipped": outcome.skipped, "abweichungen": abweichungen}
