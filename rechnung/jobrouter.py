"""JobRouter-Uebergabe – eigener, per Konfiguration abschaltbarer Schritt.

Standardmaessig **Trockenlauf**: ``submit`` schreibt die Nutzlast nur als JSON in
den out-Ordner. Erst mit ``RECHNUNG_JOBROUTER_ENABLED=1`` und gesetzter URL geht
ein echter POST raus (mit Retry + exponentiellem Backoff).

Warteschlange: bestaetigte Rechnungen landen als ``<sha>.json`` unter
``data/queue/``. ``push_queue`` arbeitet sie ab; Erfolge wandern nach
``data/queue/done/``, Fehlschlaege bleiben liegen (mit Zaehler ``_attempts``).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .config import JobRouterConfig, jobrouter as _jr_cfg


def payload_from_result(res: dict, cfg: JobRouterConfig | None = None) -> dict:
    """Result.to_dict() -> flache Struktur fuer JobRouter."""
    fields = res.get("fields", {})
    return {
        "sourceFile": Path(res.get("file", "")).name,
        "sha256": res.get("sha256"),
        "status": res.get("status"),
        "supplier": res.get("supplier_name") or res.get("supplier_id"),
        "supplierId": res.get("supplier_id"),
        "billingAddress": res.get("billing_address_name"),
        "billingAddressId": res.get("billing_address_id"),
        "confidence": res.get("overall_confidence"),
        "fields": {k: (v.get("value") if isinstance(v, dict) else v)
                   for k, v in fields.items()},
        "taxBreakdown": res.get("tax_breakdown", []),
        "lineItems": res.get("line_items", []),
        "checks": {c["name"]: c["ok"] for c in res.get("checks", [])},
    }


def _post(cfg: JobRouterConfig, body: dict) -> dict:
    url = f"{cfg.base_url}{cfg.endpoint}"
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if cfg.token:
        headers["Authorization"] = f"Bearer {cfg.token}"
        if not cfg.base_url.lower().startswith("https://"):
            print("WARNUNG: RECHNUNG_JOBROUTER_URL ist kein https:// -- der Bearer-Token "
                  "geht im Klartext ueber das Netzwerk.", file=sys.stderr)
    last = ""
    for attempt in range(1, cfg.max_retries + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=cfg.timeout) as r:  # noqa: S310
                return {"sent": True, "status": r.status,
                        "body": r.read(1024).decode("utf-8", "replace"), "attempts": attempt}
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}: {e.read(512).decode('utf-8', 'replace')}"
            if e.code < 500 and e.code != 429:
                return {"sent": False, "error": last, "attempts": attempt}
        except (urllib.error.URLError, OSError) as e:
            last = f"{type(e).__name__}: {e}"
        time.sleep(min(2 ** attempt, 30))
    return {"sent": False, "error": last, "attempts": cfg.max_retries}


def submit(res: dict, out_dir: str | Path, cfg: JobRouterConfig | None = None) -> dict:
    """Einzelne Rechnung uebergeben (bzw. Trockenlauf schreiben)."""
    cfg = cfg or _jr_cfg()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    body = payload_from_result(res, cfg)
    dry = out_dir / (Path(res.get("file", "rechnung")).stem + ".jobrouter.json")
    dry.write_text(json.dumps(body, indent=2, ensure_ascii=False), "utf-8")

    if not cfg.live:
        return {"sent": False, "dry_run": str(dry)}
    if cfg.allow_status and res.get("status") not in cfg.allow_status:
        return {"sent": False, "skipped": f"status {res.get('status')} nicht in allow_status"}
    return _post(cfg, body)


# ---- Warteschlange -------------------------------------------------------

def queue_dir(data_root: str | Path) -> Path:
    p = Path(data_root) / "queue"
    p.mkdir(parents=True, exist_ok=True)
    return p


def enqueue(data_root: str | Path, res: dict) -> Path | None:
    """Rechnung fuer die Uebergabe einreihen. Gibt ``None`` zurueck, wenn die
    Rechnung bereits erfolgreich uebergeben wurde (liegt unter ``queue/done/``)."""
    q = queue_dir(data_root)
    key = (res.get("sha256") or Path(res.get("file", "x")).stem)[:32]
    if (q / "done" / f"{key}.json").exists():
        return None
    target = q / f"{key}.json"
    target.write_text(json.dumps(res, indent=2, ensure_ascii=False), "utf-8")
    return target


def push_queue(data_root: str | Path, cfg: JobRouterConfig | None = None,
               out_dir: str | Path | None = None) -> dict:
    cfg = cfg or _jr_cfg()
    root = Path(data_root)
    q = queue_dir(root)
    done = q / "done"
    done.mkdir(exist_ok=True)
    out_dir = Path(out_dir) if out_dir else root / "out"
    results = []
    for f in sorted(q.glob("*.json")):
        res = json.loads(f.read_text("utf-8"))
        r = submit(res, out_dir, cfg)
        r["file"] = res.get("file")
        results.append(r)
        if r.get("sent") or r.get("dry_run") or r.get("skipped"):
            f.rename(done / f.name)
        else:
            res["_attempts"] = res.get("_attempts", 0) + 1
            res["_last_error"] = r.get("error")
            f.write_text(json.dumps(res, indent=2, ensure_ascii=False), "utf-8")
    sent = sum(1 for r in results if r.get("sent"))
    return {"processed": len(results), "sent": sent,
            "dry_run": not cfg.live, "results": results}
