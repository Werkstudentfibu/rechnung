"""Lokale Persistenz: Lieferanten, Templates, Verarbeitungs-Log. Alles JSON."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from .identify import Supplier
from .template import Template

DEFAULT_DATA = Path(os.environ.get("RECHNUNG_DATA", "./data"))


class Store:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root or DEFAULT_DATA)
        (self.root / "templates").mkdir(parents=True, exist_ok=True)
        (self.root / "out").mkdir(parents=True, exist_ok=True)
        self._suppliers = self.root / "suppliers.json"
        self._processed = self.root / "processed.jsonl"

    # ---- Lieferanten --------------------------------------------------
    def suppliers(self) -> list[Supplier]:
        if not self._suppliers.exists():
            return []
        return [Supplier.from_dict(d) for d in json.loads(self._suppliers.read_text("utf-8"))]

    def save_supplier(self, s: Supplier) -> None:
        rows = {x.id: x for x in self.suppliers()}
        rows[s.id] = s
        self._suppliers.write_text(
            json.dumps([x.to_dict() for x in rows.values()], indent=2, ensure_ascii=False),
            "utf-8",
        )

    def supplier(self, supplier_id: str) -> Supplier | None:
        return next((s for s in self.suppliers() if s.id == supplier_id), None)

    # ---- Templates --------------------------------------------------
    def _tpl_path(self, tid: str) -> Path:
        return self.root / "templates" / f"{tid}.json"

    def template(self, tid: str) -> Template | None:
        p = self._tpl_path(tid)
        if not p.exists():
            return None
        return Template.from_dict(json.loads(p.read_text("utf-8")))

    def save_template(self, t: Template) -> None:
        self._tpl_path(t.id).write_text(
            json.dumps(t.to_dict(), indent=2, ensure_ascii=False), "utf-8"
        )

    def templates_for(self, supplier_id: str) -> list[Template]:
        out = []
        for p in (self.root / "templates").glob("*.json"):
            t = Template.from_dict(json.loads(p.read_text("utf-8")))
            if t.supplier_id == supplier_id:
                out.append(t)
        return out

    def all_templates(self) -> list[Template]:
        return [
            Template.from_dict(json.loads(p.read_text("utf-8")))
            for p in (self.root / "templates").glob("*.json")
        ]

    # ---- Verarbeitungs-Log ----------------------------------------
    def mark_processed(self, rec: dict) -> None:
        with self._processed.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def processed(self) -> list[dict]:
        if not self._processed.exists():
            return []
        return [json.loads(l) for l in self._processed.read_text("utf-8").splitlines() if l.strip()]

    def seen_invoice(self, supplier_id: str, invoice_no: str) -> bool:
        for r in self.processed():
            if r.get("supplier_id") == supplier_id and r.get("rechnungsnummer") == invoice_no:
                return True
        return False

    def write_output(self, name: str, data: dict) -> Path:
        p = self.root / "out" / f"{name}.json"
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
        csv_path = p.with_suffix(".csv")
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, delimiter=";")
            writer.writerow(["feld", "wert"])
            for k, v in data.items():
                writer.writerow([k, "" if v is None else v])
        return p
