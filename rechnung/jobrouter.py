"""JobRouter-Uebergabe. Eigener, per Konfiguration abschaltbarer Schritt.

Standardmaessig AUS. Ohne konfigurierte URL schreibt `submit` die Nutzlast nur
als JSON in den out-Ordner (Trockenlauf), damit v1 komplett offline testbar ist.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

ENABLED = os.environ.get("JOBROUTER_ENABLED", "0") == "1"
BASE_URL = os.environ.get("JOBROUTER_URL", "").rstrip("/")
TOKEN = os.environ.get("JOBROUTER_TOKEN", "")


def payload_from_result(res: dict) -> dict:
    """Flache Feld->Wert-Struktur, wie JobRouter sie typ. erwartet."""
    return {
        "sourceFile": Path(res["path"]).name,
        "sha256": res["sha256"],
        "supplier": res.get("supplier_name") or res.get("supplier_id"),
        "supplierId": res.get("supplier_id"),
        "fields": {k: v["value"] for k, v in res.get("fields", {}).items()},
        "confidence": res.get("min_confidence"),
        "checks": {c["name"]: c["ok"] for c in res.get("checks", [])},
    }


def submit(res: dict, out_dir: str | Path) -> dict:
    body = payload_from_result(res)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dry = out_dir / (Path(res["path"]).stem + ".jobrouter.json")
    dry.write_text(json.dumps(body, indent=2, ensure_ascii=False), "utf-8")

    if not (ENABLED and BASE_URL):
        return {"sent": False, "dry_run": str(dry)}

    req = urllib.request.Request(
        f"{BASE_URL}/rest/v2/incidents",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TOKEN}" if TOKEN else "",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - interne URL
        return {"sent": True, "status": r.status, "body": r.read(512).decode("utf-8", "replace")}
