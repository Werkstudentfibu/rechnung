"""Lokale Persistenz: Lieferanten, Templates, Verarbeitungs-Log. Alles JSON."""

from __future__ import annotations

import csv
import json
import os
import shutil
import time
from pathlib import Path

from .billing import BillingAddress
from .identify import Supplier
from .template import Template
from .textnorm import norm

DEFAULT_DATA = Path(os.environ.get("RECHNUNG_DATA", "./data"))


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
        self._confirmations_log = self.root / "confirmations.jsonl"
        self._processed_cache: list[dict] | None = None

    # ---- Lieferanten --------------------------------------------------
    def suppliers(self) -> list[Supplier]:
        return [Supplier.from_dict(d) for d in _read_json_safe(self._suppliers, [])]

    def save_supplier(self, s: Supplier) -> None:
        rows = {x.id: x for x in self.suppliers()}
        rows[s.id] = s
        _atomic_write(
            self._suppliers,
            json.dumps([x.to_dict() for x in rows.values()], indent=2, ensure_ascii=False),
        )

    def supplier(self, supplier_id: str) -> Supplier | None:
        return next((s for s in self.suppliers() if s.id == supplier_id), None)

    def delete_supplier(self, supplier_id: str) -> None:
        rows = [x for x in self.suppliers() if x.id != supplier_id]
        _atomic_write(
            self._suppliers, json.dumps([x.to_dict() for x in rows], indent=2, ensure_ascii=False)
        )
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

    def delete_address(self, address_id: str) -> None:
        rows = [x for x in self.addresses() if x.id != address_id]
        _atomic_write(
            self._addresses, json.dumps([x.to_dict() for x in rows], indent=2, ensure_ascii=False)
        )

    # ---- Templates --------------------------------------------------
    def _tpl_path(self, tid: str) -> Path:
        return self.root / "templates" / f"{tid}.json"

    def template(self, tid: str) -> Template | None:
        data = _read_json_safe(self._tpl_path(tid), None)
        return Template.from_dict(data) if data is not None else None

    def save_template(self, t: Template) -> None:
        _atomic_write(self._tpl_path(t.id), json.dumps(t.to_dict(), indent=2, ensure_ascii=False))

    def delete_template(self, tid: str) -> None:
        self._tpl_path(tid).unlink(missing_ok=True)
        self._tpl_path(tid).with_name(f"{tid}.json.bak").unlink(missing_ok=True)

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

    def save_correction(self, sha: str, field: str, value, operator: str = "") -> None:
        self._corrections.mkdir(parents=True, exist_ok=True)
        p = self._corrections / f"{sha}.json"
        cur = _read_json_safe(p, {"sha256": sha, "fields": {}})
        cur["fields"][field] = value
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

    def write_output(self, name: str, data: dict) -> Path:
        p = self.root / "out" / f"{name}.json"
        _atomic_write(p, json.dumps(data, indent=2, ensure_ascii=False))

        # Kopf-CSV: nur skalare Felder (Listen kommen in eigene Dateien)
        csv_path = p.with_suffix(".csv")
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(["feld", "wert"])
            for k, v in data.items():
                if isinstance(v, (list, dict)):
                    continue
                writer.writerow([k, "" if v is None else v])

        # Positionen als eigene Tabelle
        items = data.get("_positionen") or []
        if items:
            cols = list({k for it in items for k in it if not k.startswith("_")})
            with p.with_name(f"{name}.positionen.csv").open(
                    "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(cols)
                for it in items:
                    w.writerow(["" if it.get(c) is None else it.get(c) for c in cols])

        # USt-Aufstellung je Satz
        tax = data.get("_ust_aufstellung") or []
        if tax:
            with p.with_name(f"{name}.ust.csv").open(
                    "w", encoding="utf-8", newline="") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(["satz", "netto", "ust", "brutto"])
                for r in tax:
                    w.writerow([r.get("satz"), r.get("netto"), r.get("ust"), r.get("brutto")])
        return p
