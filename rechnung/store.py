"""Lokale Persistenz: Lieferanten, Templates, Verarbeitungs-Log. Alles JSON."""

from __future__ import annotations

import csv
import json
import os
import shutil
import time
from pathlib import Path

from . import spaltenwoerter as spaltenwoerter_mod
from .billing import BillingAddress
from .identify import Supplier
from .positionen import ordered_columns
from .postfach import Postfach
from .template import Template
from .textnorm import norm

DEFAULT_DATA = Path(os.environ.get("RECHNUNG_DATA", "./data"))


def _herkunft_text(row: dict) -> str:
    """Herkunft einer Positions-/USt-Zeile als kompakter Text (SPEC
    Baustein 3), z.B. "einzelpreis_netto=errechnet;mwst_satz=kopf" -- leer,
    wenn jede Zelle der Zeile gelesen ist (``rechnen.py`` traegt dann keinen
    Eintrag in ``_herkunft`` ein)."""
    h = row.get("_herkunft") or {}
    return ";".join(f"{k}={v}" for k, v in sorted(h.items()))


def _atomic_write(path: Path, text: str) -> None:
    """Schreibt ueber eine Temp-Datei + atomaren Rename und hebt die
    bisherige Version als ``.bak`` auf. Verhindert eine korrupte/leere Datei,
    wenn der Schreibvorgang mitten drin abbricht (Stromausfall, volle
    Platte) -- die alte Version bleibt bis zum letzten Moment unberuehrt."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, "utf-8")
    if path.exists():
        try:
            path.replace(path.with_name(path.name + ".bak"))
        except OSError:
            pass
    tmp.replace(path)


_UNZULAESSIG = '<>:"/\\|?*'


def _dateiname(name: str) -> str:
    """Id -> unter Windows zulaessiger Dateiname: unzulaessige Zeichen
    (<>:"/\\|?*) und Steuerzeichen werden als %XX kodiert, Leerzeichen/
    Punkte am Ende ebenso. Unveraendert, wenn nichts davon vorkommt."""
    out = "".join(f"%{ord(c):02X}" if c in _UNZULAESSIG or ord(c) < 32 else c
                  for c in name)
    stamm = out.rstrip(" .")
    return stamm + "".join(f"%{ord(c):02X}" for c in out[len(stamm):])


def _file_sig(p: Path) -> tuple:
    """(mtime_ns, Groesse) -- (0, 0), wenn die Datei fehlt."""
    try:
        st = p.stat()
    except OSError:
        return (0, 0)
    return (st.st_mtime_ns, st.st_size)


def _read_json_safe(path: Path, default):
    """Liest JSON robust: bei Beschaedigung erst die ``.bak``-Sicherung
    versuchen, sonst die kaputte Datei zur Seite legen (nicht loeschen) und
    mit ``default`` weiterlaufen, statt das ganze Programm abstuerzen zu
    lassen."""
    bak = path.with_name(path.name + ".bak")
    for candidate in (path, bak):
        if not candidate.exists():
            continue
        try:
            data = json.loads(candidate.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if candidate == bak:
            print(f"WARNUNG: {path.name} war beschaedigt oder fehlte -- "
                  f"aus Sicherung {bak.name} wiederhergestellt.")
        return data
    if path.exists():
        corrupt = path.with_name(f"{path.name}.corrupt-{int(time.time())}")
        try:
            path.replace(corrupt)
        except OSError:
            pass
        print(f"WARNUNG: {path.name} war beschaedigt, keine Sicherung vorhanden -- "
              f"nach {corrupt.name} verschoben. Programm laeuft mit leerem Stand fuer "
              f"diese Datei weiter, bitte Datei pruefen.")
    return default


def _read_jsonl_safe(path: Path) -> list[dict]:
    """Wie ``_read_json_safe``, aber pro Zeile: eine einzelne beschaedigte
    Zeile (z.B. durch einen abgebrochenen Append) darf nicht den ganzen
    Log unlesbar machen."""
    if not path.exists():
        return []
    out: list[dict] = []
    bad = 0
    for line in path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
    if bad:
        print(f"WARNUNG: {bad} beschaedigte Zeile(n) in {path.name} uebersprungen.")
    return out


class Store:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or DEFAULT_DATA)
        (self.root / "templates").mkdir(parents=True, exist_ok=True)
        (self.root / "out").mkdir(parents=True, exist_ok=True)
        self._suppliers = self.root / "suppliers.json"
        self._addresses = self.root / "addresses.json"
        self._processed = self.root / "processed.jsonl"
        self._audit = self.root / "audit.jsonl"
        self._archive = self.root / "archive"
        self._corrections = self.root / "corrections"
        self._categories = self.root / "categories.json"
        self._spaltenwoerter = self.root / "spaltenwoerter.json"
        self._confirmations_log = self.root / "confirmations.jsonl"
        self._shadow = self.root / "shadow.jsonl"
        self._events = self.root / "events.jsonl"
        self._postfaecher = self.root / "postfaecher.json"
        self._dateien_mandant = self.root / "dateien_mandant.json"
        self._processed_cache: list[dict] | None = None
        self._supplier_index_cache: dict | None = None
        self._supplier_index_sig: tuple | None = None

    # ---- Lieferanten --------------------------------------------------
    def _alle_lieferanten(self) -> list[Supplier]:
        return [Supplier.from_dict(d) for d in _read_json_safe(self._suppliers, [])]

    def suppliers(self) -> list[Supplier]:
        """Echte Lieferanten -- der Dummy-Lieferant (dummy.py) ist bewusst
        nicht dabei, damit Erkennung, Lernen und Import ihn nie sehen."""
        return [s for s in self._alle_lieferanten() if not s.dummy]

    def dummy_lieferant(self) -> Supplier | None:
        return next((s for s in self._alle_lieferanten() if s.dummy), None)

    def _save_dummy(self, s: Supplier) -> None:
        self._schreibe_lieferanten([x for x in self._alle_lieferanten() if not x.dummy] + [s])

    def supplier_index(self, address_id: str | None = None) -> dict:
        """USt-IdNr/IBAN -> Lieferanten-IDs (identify.build_identifier_index),
        gecacht bis zur naechsten Aenderung (SPEC Effizienz-Baustein 4) --
        erspart bei jeder Rechnung das erneute Aufbauen ueber die ganze
        Bibliothek.

        Lieferant je Rechnungsadresse (09.10.2026): der Index wird nur ueber
        die zu ``address_id`` passenden Kandidaten (``identify.kandidaten``)
        gebaut, nicht ueber die ganze Bibliothek -- sonst waere ein per
        USt-IdNr/IBAN eindeutiger Treffer bei einem Lieferanten einer ANDEREN
        Adresse "mehrdeutig". Gecacht je Adress-ID (bzw. ``""`` fuer "keine
        erkannt"/"keine Adressen gepflegt") -- eine Aenderung an Lieferanten
        ODER Rechnungsadressen loescht den ganzen Cache, da das Vorhandensein
        von Rechnungsadressen selbst die Kandidatenmenge beeinflusst."""
        # Auch Aenderungen durch eine ANDERE Store-Instanz (Guard-Thread/
        # -Prozess legt Lieferanten-Entwuerfe an, 09.10.2026) muessen den
        # Cache verwerfen -- deshalb zusaetzlich an Dateistand gekoppelt.
        sig = (_file_sig(self._suppliers), _file_sig(self._addresses))
        if self._supplier_index_cache is None or self._supplier_index_sig != sig:
            self._supplier_index_cache = {}
            self._supplier_index_sig = sig
        key = address_id or ""
        if key not in self._supplier_index_cache:
            from .identify import build_identifier_index, kandidaten

            cands = kandidaten(self.suppliers(), address_id, bool(self.addresses()))
            self._supplier_index_cache[key] = build_identifier_index(cands)
        return self._supplier_index_cache[key]

    def _schreibe_lieferanten(self, rows: list[Supplier]) -> None:
        _atomic_write(
            self._suppliers,
            json.dumps([x.to_dict() for x in rows], indent=2, ensure_ascii=False),
        )
        self._supplier_index_cache = None

    def save_supplier(self, s: Supplier) -> None:
        from .dummy import ist_dummy_id

        if s.dummy or ist_dummy_id(s.id):
            raise ValueError("Der Dummy-Lieferant ist reserviert und wird nicht gespeichert/gelernt")
        rows = {x.id: x for x in self._alle_lieferanten()}
        rows[s.id] = s
        self._schreibe_lieferanten(list(rows.values()))

    def supplier(self, supplier_id: str) -> Supplier | None:
        return next((s for s in self.suppliers() if s.id == supplier_id), None)

    def delete_supplier(self, supplier_id: str) -> None:
        from .dummy import ist_dummy_id

        if ist_dummy_id(supplier_id):
            return   # der Dummy bleibt immer
        self._schreibe_lieferanten([x for x in self._alle_lieferanten() if x.id != supplier_id])
        for t in self.templates_for(supplier_id):
            self.delete_template(t.id)

    # ---- Rechnungsadressen ------------------------------------------
    def addresses(self) -> list[BillingAddress]:
        return [BillingAddress.from_dict(d) for d in _read_json_safe(self._addresses, [])]

    def address(self, address_id: str) -> BillingAddress | None:
        return next((a for a in self.addresses() if a.id == address_id), None)

    def save_address(self, a: BillingAddress) -> None:
        rows = {x.id: x for x in self.addresses()}
        rows[a.id] = a
        _atomic_write(
            self._addresses,
            json.dumps([x.to_dict() for x in rows.values()], indent=2, ensure_ascii=False),
        )
        # Ob es ueberhaupt Rechnungsadressen gibt, beeinflusst die Kandidaten-
        # menge des Lieferanten-Index (supplier_index) -- auch eine Adress-
        # Aenderung muss ihn daher invalidieren, nicht nur eine Lieferanten-
        # Aenderung.
        self._supplier_index_cache = None

    def delete_address(self, address_id: str) -> None:
        rows = [x for x in self.addresses() if x.id != address_id]
        _atomic_write(
            self._addresses, json.dumps([x.to_dict() for x in rows], indent=2, ensure_ascii=False)
        )
        self._supplier_index_cache = None

    # ---- Postfaecher (Excel-Stammdaten-Import, Entscheidung Aaron
    # 15.10.2026: ~50 IONOS-Postfaecher, je Mandant eines) -----------
    # Passwoerter liegen bewusst NICHT hier, sondern in secrets.py.
    def postfaecher(self) -> list[Postfach]:
        return [Postfach.from_dict(d) for d in _read_json_safe(self._postfaecher, [])]

    def postfach(self, postfach_id: str) -> Postfach | None:
        return next((p for p in self.postfaecher() if p.id == postfach_id), None)

    def save_postfach(self, p: Postfach) -> None:
        rows = {x.id: x for x in self.postfaecher()}
        rows[p.id] = p
        _atomic_write(
            self._postfaecher,
            json.dumps([x.to_dict() for x in rows.values()], indent=2, ensure_ascii=False),
        )

    def delete_postfach(self, postfach_id: str) -> None:
        rows = [x for x in self.postfaecher() if x.id != postfach_id]
        _atomic_write(
            self._postfaecher, json.dumps([x.to_dict() for x in rows], indent=2, ensure_ascii=False)
        )

    # ---- Mandant je abgeholter Datei (sha256 -> BillingAddress.id) --
    # Geschrieben beim Postfach-Abholen (ingest.handle_mail), damit die
    # Pipeline weiss, aus welchem Postfach (= welcher Mandant) eine Rechnung
    # kam -- starker Hinweis fuer den Geschaeftsbereich, siehe pipeline.py.
    def _dateien_mandant_map(self) -> dict:
        return _read_json_safe(self._dateien_mandant, {})

    def datei_mandant(self, sha: str) -> str | None:
        if not sha:
            return None
        return self._dateien_mandant_map().get(sha)

    def set_datei_mandant(self, sha: str, address_id: str) -> None:
        if not sha or not address_id:
            return
        m = self._dateien_mandant_map()
        if m.get(sha) == address_id:
            return
        m[sha] = address_id
        _atomic_write(self._dateien_mandant, json.dumps(m, indent=2, ensure_ascii=False))

    # ---- Templates --------------------------------------------------
    def _tpl_path(self, tid: str) -> Path:
        # Lieferanten-Ids aus dem Excel-Import enthalten teils "/" oder "\\"
        # ("70000-Kreditoren CH / Kreditoren Stammdaten Schweiz-1") -- als
        # Dateiname waere das ein Unterordner, das Speichern scheiterte
        # (Proberunde 12.11.2026). Unzulaessige Zeichen werden kodiert; Ids
        # ohne solche Zeichen behalten ihren bisherigen Dateinamen.
        return self.root / "templates" / f"{_dateiname(tid)}.json"

    def template(self, tid: str) -> Template | None:
        data = _read_json_safe(self._tpl_path(tid), None)
        return Template.from_dict(data) if data is not None else None

    def save_template(self, t: Template) -> None:
        _atomic_write(self._tpl_path(t.id), json.dumps(t.to_dict(), indent=2, ensure_ascii=False))

    def delete_template(self, tid: str) -> None:
        self._tpl_path(tid).unlink(missing_ok=True)
        p = self._tpl_path(tid)
        p.with_name(p.name + ".bak").unlink(missing_ok=True)

    def templates_for(self, supplier_id: str) -> list[Template]:
        return [t for t in self.all_templates() if t.supplier_id == supplier_id]

    def all_templates(self) -> list[Template]:
        out = []
        for p in sorted((self.root / "templates").glob("*.json")):
            data = _read_json_safe(p, None)
            if data is not None:
                out.append(Template.from_dict(data))
        return out

    # ---- Verarbeitungs-Log ----------------------------------------
    def mark_processed(self, rec: dict) -> None:
        with self._processed.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._processed_cache = None

    def processed(self) -> list[dict]:
        # process() ruft ueber seen_invoice/seen_sha/distinct_confirmations
        # bei jeder Datei erneut auf processed() zu -- ohne Cache liest und
        # parst jede einzelne Rechnung im Scan das komplette Verarbeitungs-
        # Log (processed.jsonl) neu von der Platte, und das Log waechst mit
        # jeder bestaetigten Rechnung dauerhaft weiter. Im Prozess-Cache
        # halten, bis mark_processed() ihn invalidiert -- alle Schreib-
        # zugriffe laufen ausschliesslich darueber, die Invalidierung ist
        # damit vollstaendig.
        if self._processed_cache is None:
            self._processed_cache = _read_jsonl_safe(self._processed)
        return self._processed_cache

    def seen_invoice(self, supplier_id: str, invoice_no: str) -> bool:
        for r in self.processed():
            if r.get("supplier_id") == supplier_id and r.get("rechnungsnummer") == invoice_no:
                return True
        return False

    def seen_sha(self, sha: str) -> bool:
        return any(r.get("sha256") == sha for r in self.processed())

    def seen_sha_template(self, sha: str, template_id: str | None) -> bool:
        """Wurde genau diese Rechnung (sha) schon mit genau diesem Template
        übernommen? Basis für idempotentes finalize()/confirm."""
        if not sha:
            return False
        return any(r.get("sha256") == sha and r.get("template_id") == template_id
                   for r in self.processed())

    def distinct_confirmations(self, template_id: str) -> int:
        """Anzahl *verschiedener* Rechnungen (sha256), mit denen ein Template
        bestaetigt wurde – nicht manipulierbar durch Mehrfach-Klick."""
        shas = {r.get("sha256") for r in self.processed()
                if r.get("template_id") == template_id and r.get("sha256")}
        return len(shas)

    # ---- Audit-Trail (GoBD: append-only, wer/wann/was) --------------
    def audit(self, action: str, detail: dict | None = None, operator: str = "") -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "operator": operator or "?",
               "action": action, "detail": detail or {}}
        with self._audit.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def audit_log(self) -> list[dict]:
        return _read_jsonl_safe(self._audit)

    # ---- Original-PDF unveraendert aufbewahren ---------------------
    def archive_pdf(self, src: str | Path, sha: str) -> Path | None:
        if not sha:
            return None
        self._archive.mkdir(parents=True, exist_ok=True)
        suffix = Path(src).suffix.lower() or ".pdf"
        target = self._archive / f"{sha}{suffix}"
        if not target.exists():
            shutil.copy2(src, target)
        return target

    # ---- Manuelle Korrekturen je Rechnung ------------------------
    def corrections(self, sha: str) -> dict:
        p = self._corrections / f"{sha}.json"
        return _read_json_safe(p, {}).get("fields", {})

    def confirmed_fields(self, sha: str) -> dict:
        """Felder, deren angezeigter Wert der Mensch ausdruecklich als
        richtig bestaetigt hat (ohne ihn zu aendern): Feld -> Wert. Liegt
        neben den Korrekturen, ist aber KEINE Korrektur -- ``corrections``
        und damit Quelle ``manual``/Fehlerzaehlung bleiben unberuehrt."""
        p = self._corrections / f"{sha}.json"
        return dict(_read_json_safe(p, {}).get("confirmed", {}))

    def save_confirmed_field(self, sha: str, field: str, value, operator: str = "") -> None:
        self._corrections.mkdir(parents=True, exist_ok=True)
        p = self._corrections / f"{sha}.json"
        cur = _read_json_safe(p, {"sha256": sha, "fields": {}})
        cur.setdefault("confirmed", {})[field] = value
        cur["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _atomic_write(p, json.dumps(cur, indent=2, ensure_ascii=False))
        self.audit("confirm_field", {"sha256": sha, "field": field, "value": value}, operator)

    def zeilen(self, sha: str) -> dict:
        """Zeilenstatus der Positionstabelle: ``{"bestaetigt": [fp...],
        "korrigiert": [fp...]}`` (Fingerabdruecke, siehe
        ``positionen.zeilen_fingerabdruck``)."""
        z = _read_json_safe(self._corrections / f"{sha}.json", {}).get("zeilen", {})
        return {"bestaetigt": list(z.get("bestaetigt", [])), "korrigiert": list(z.get("korrigiert", []))}

    def save_zeilen(self, sha: str, bestaetigt: list[str], korrigiert: list[str],
                    operator: str = "") -> tuple[list[str], list[str]]:
        """Zeilen als bestaetigt/korrigiert vormerken -> (neu bestaetigte,
        neu korrigierte) Fingerabdruecke (nur die, die vorher nicht schon so
        vermerkt waren -- Grundlage, jedes Ereignis nur EINMAL zu zaehlen).
        Eine Zeile ist nie beides: Korrigieren nimmt die Bestaetigung weg und
        umgekehrt."""
        self._corrections.mkdir(parents=True, exist_ok=True)
        p = self._corrections / f"{sha}.json"
        cur = _read_json_safe(p, {"sha256": sha, "fields": {}})
        z = cur.setdefault("zeilen", {"bestaetigt": [], "korrigiert": []})
        best, korr = list(z.get("bestaetigt", [])), list(z.get("korrigiert", []))
        neu_b = [f for f in dict.fromkeys(bestaetigt) if f not in best]
        neu_k = [f for f in dict.fromkeys(korrigiert) if f not in korr]
        best = [f for f in best if f not in korrigiert] + neu_b
        korr = [f for f in korr if f not in bestaetigt] + neu_k
        z["bestaetigt"], z["korrigiert"] = best, korr
        cur["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _atomic_write(p, json.dumps(cur, indent=2, ensure_ascii=False))
        self.audit("zeilen", {"sha256": sha, "bestaetigt": len(neu_b), "korrigiert": len(neu_k)}, operator)
        return neu_b, neu_k

    def save_correction(self, sha: str, field: str, value, operator: str = "") -> None:
        self._corrections.mkdir(parents=True, exist_ok=True)
        p = self._corrections / f"{sha}.json"
        cur = _read_json_safe(p, {"sha256": sha, "fields": {}})
        cur["fields"][field] = value
        # eine spaetere Korrektur hebt die ausdrueckliche Bestaetigung auf
        cur.get("confirmed", {}).pop(field, None)
        cur["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _atomic_write(p, json.dumps(cur, indent=2, ensure_ascii=False))
        self.audit("correct", {"sha256": sha, "field": field, "value": value}, operator)

    def delete_correction(self, sha: str, field: str, operator: str = "") -> None:
        """Manuelle Korrektur zurücknehmen -- fällt zurück auf den
        automatisch extrahierten/regulären Wert."""
        p = self._corrections / f"{sha}.json"
        cur = _read_json_safe(p, {"sha256": sha, "fields": {}})
        if field not in cur.get("fields", {}):
            return
        del cur["fields"][field]
        cur["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        _atomic_write(p, json.dumps(cur, indent=2, ensure_ascii=False))
        self.audit("uncorrect", {"sha256": sha, "field": field}, operator)

    # ---- Bestaetigungspflicht: JEDE Rechnung braucht einen expliziten,
    # geloggten Klick im Training-UI, bevor finalize() sie uebernehmen darf --
    # unabhaengig vom Status (auto/review ist nur noch ein Konfidenz-Signal,
    # keine Freigabe fuers automatische Durchlaufen). Append-only wie audit.
    def mark_confirmed(self, sha: str, operator: str = "") -> None:
        if not sha:
            return
        with self._confirmations_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "sha256": sha, "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "operator": operator or "?",
            }, ensure_ascii=False) + "\n")

    def is_confirmed(self, sha: str) -> bool:
        if not sha:
            return False
        return any(r.get("sha256") == sha for r in _read_jsonl_safe(self._confirmations_log))

    # ---- Schattenbetrieb (shadow.py): "haette durchgelassen?" vs. Mensch --
    # Reine Messung, append-only wie audit -- beeinflusst keine Entscheidung.
    def shadow_append(self, rec: dict) -> None:
        with self._shadow.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def shadow_log(self) -> list[dict]:
        return _read_jsonl_safe(self._shadow)

    # ---- Vollstaendiges Ereignisprotokoll (eventlog.py) -- append-only wie
    # audit/shadow. Jede Verarbeitung/Bestaetigung und jede spaetere
    # Korrektur haengt sich hier an; Basis fuer den Fehlerbericht
    # (haeufigste wiederkehrende Pruefungs-Fehlschlaege/Korrekturen).
    def event_append(self, rec: dict) -> None:
        with self._events.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def events(self) -> list[dict]:
        return _read_jsonl_safe(self._events)

    # ---- Lieferant in den Protokollen umhaengen (entwurf_uebernahme.py) ----
    def lieferant_umhaengen(self, alt_id: str, neu_id: str, neu_name: str) -> dict[str, int]:
        """Alle Eintraege mit ``supplier_id == alt_id`` in ``processed.jsonl``,
        ``events.jsonl``, ``shadow.jsonl`` und ``lehrer.jsonl`` auf ``neu_id``/
        ``neu_name`` umschreiben -> Anzahl je Datei. Einzige Stelle, die diese
        append-only-Protokolle nachtraeglich aendert -- bewusst nur fuer die
        Uebernahme eines Lieferanten-Entwurfs in einen bestehenden Lieferanten
        (ein Entwurf ``Unbekannt (DE...)`` war nie ein eigener Lieferant, seine
        Rechnungen gehoeren fachlich zum Ziel, z.B. fuer die Dublettenpruefung
        ueber ``seen_invoice``). ``audit.jsonl`` bleibt unberuehrt (GoBD)."""
        zaehler: dict[str, int] = {}
        for pfad, id_key, name_key in (
            (self._processed, "supplier_id", "supplier_name"),
            (self._events, "supplier_id", "supplier_name"),
            (self._shadow, "supplier_id", "supplier_name"),
            (self.root / "lehrer.jsonl", "lieferant", None),
        ):
            n = 0
            if not pfad.exists():
                zaehler[pfad.name] = 0
                continue
            zeilen = _read_jsonl_safe(pfad)
            for rec in zeilen:
                if rec.get(id_key) == alt_id:
                    rec[id_key] = neu_id if name_key else neu_name
                    if name_key:
                        rec[name_key] = neu_name
                    n += 1
            if n:
                _atomic_write(pfad, "".join(json.dumps(r, ensure_ascii=False) + "\n"
                                            for r in zeilen))
            zaehler[pfad.name] = n
        self._processed_cache = None
        return zaehler

    # ---- Kategorie-Lerntabelle (Positionstext -> Kategorie) ---------
    # Gilt lieferantenuebergreifend: einmal gelernte Zuordnungen ("Porto" ->
    # "Buerobedarf") schlagen bei jedem Lieferanten wieder vor. Ueber das
    # Training-UI ("Kategorien"-Reiter) einsehbar und korrigierbar/loeschbar --
    # eine einzelne falsche Zuordnung soll auffindbar, nicht unsichtbar sein.
    def category_examples(self) -> dict[str, dict]:
        return _read_json_safe(self._categories, {})

    def save_category_example(self, text: str, kategorie: str, operator: str = "") -> None:
        key = norm(text)
        if not key or not kategorie.strip():
            return
        rows = self.category_examples()
        rows[key] = {"text": text, "kategorie": kategorie.strip()}
        _atomic_write(self._categories, json.dumps(rows, indent=2, ensure_ascii=False))
        self.audit("category-learn", {"text": text, "kategorie": kategorie.strip()}, operator)

    def delete_category_example(self, key: str, operator: str = "") -> None:
        rows = self.category_examples()
        if key in rows:
            removed = rows.pop(key)
            _atomic_write(self._categories, json.dumps(rows, indent=2, ensure_ascii=False))
            self.audit("category-delete", {"key": key, **removed}, operator)

    # ---- Spaltenwoerterbuch (SPEC Baustein 5, Aaron 12.10.2026) -----
    # Lieferantenuebergreifend: Kopfwort (norm()) -> {Zielspalte -> Anzahl
    # Bestaetigungen}. Siehe ``spaltenwoerter.py`` fuer die Entscheidungs-
    # regel (ab 2 Bestaetigungen, nie gegen einen Widerspruch).
    def spaltenwoerter(self) -> dict[str, dict[str, int]]:
        return _read_json_safe(self._spaltenwoerter, {})

    def spaltenwort_lernen(self, text: str, ziel_spalte: str | None,
                           operator: str = "") -> None:
        bisher = self.spaltenwoerter()
        neu = spaltenwoerter_mod.lernen(bisher, text, ziel_spalte)
        if neu is bisher:
            return
        _atomic_write(self._spaltenwoerter, json.dumps(neu, indent=2, ensure_ascii=False))
        self.audit("spaltenwort-lernen", {"text": text, "ziel": ziel_spalte}, operator)

    def write_output(self, name: str, data: dict) -> Path:
        p = self.root / "out" / f"{name}.json"
        _atomic_write(p, json.dumps(data, indent=2, ensure_ascii=False))

        # Herkunft je Feld/Zelle (SPEC Baustein 3): gelesen/errechnet/kopf/
        # korrigiert -- ``_herkunft`` ist wie ``_positionen``/``_ust_aufstellung``
        # ein Unterstrich-Schluessel, kein Feld der Rechnung selbst.
        feld_herkunft = data.get("_herkunft") or {}

        # Kopf-CSV: nur skalare Felder (Listen kommen in eigene Dateien)
        # utf-8-sig (mit BOM): Excel unter Windows zeigt Umlaute sonst falsch an
        csv_path = p.with_suffix(".csv")
        with csv_path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(["feld", "wert", "herkunft"])
            for k, v in data.items():
                if isinstance(v, (list, dict)) or k.startswith("_"):
                    continue
                writer.writerow([k, "" if v is None else v, feld_herkunft.get(k, "")])

        # Positionen als eigene Tabelle -- feste Reihenfolge (SPEC Baustein 1:
        # Standardspalten zuerst, Rest stabil alphabetisch -- vorher ein
        # ``set()`` mit zufaelliger Reihenfolge je Lauf). Herkunft je Zeile
        # als eine kompakte Spalte (SPEC Baustein 3), z.B.
        # "einzelpreis_netto=errechnet;mwst_satz=kopf".
        items = data.get("_positionen") or []
        if items:
            cols = ordered_columns(items)
            with p.with_name(f"{name}.positionen.csv").open(
                    "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow([*cols, "herkunft"])
                for it in items:
                    w.writerow(["" if it.get(c) is None else it.get(c) for c in cols]
                              + [_herkunft_text(it)])

        # USt-Aufstellung je Satz
        tax = data.get("_ust_aufstellung") or []
        if tax:
            with p.with_name(f"{name}.ust.csv").open(
                    "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(["satz", "netto", "ust", "brutto", "herkunft"])
                for r in tax:
                    w.writerow([r.get("satz"), r.get("netto"), r.get("ust"), r.get("brutto"),
                               _herkunft_text(r)])
        return p
