"""JobRouter-Uebergabe – eigener, per Konfiguration abschaltbarer Schritt.

Standardmaessig **Trockenlauf**: ``submit`` schreibt die Nutzlast nur als JSON in
den out-Ordner. Erst mit ``RECHNUNG_JOBROUTER_ENABLED=1`` und gesetzter URL geht
ein echter POST raus (mit Retry + exponentiellem Backoff).

Warteschlange: bestaetigte Rechnungen landen als ``<sha>.json`` unter
``data/queue/``. ``push_queue`` arbeitet sie ab; Erfolge wandern nach
``data/queue/done/``, Fehlschlaege bleiben liegen (mit Zaehler ``_attempts``).

Rueckmeldungen (Korrekturen/Ablehnungen, die ein Sachbearbeiter spaeter in
JobRouter eintraegt): die genaue JobRouter-API dafuer ist noch unbekannt
(der Kunde hat sie noch nicht spezifiziert). ``FeedbackAdapter`` ist deshalb
eine duenne Adapter-Schnittstelle -- ``FileFeedbackAdapter`` (dateibasiert,
fuer Tests/Simulation) implementiert sie schon vollstaendig,
``RestFeedbackAdapter`` ist ein Platzhalter-Pfad, der angepasst werden muss,
sobald die echte API feststeht. **Noch nicht gegen ein echtes JobRouter
getestet.** ``apply_feedback`` spielt eine Rueckmeldung als Korrektur
(Operator ``"jobrouter"``) in ``store.corrections``, die Schattenstatistik
und das Ereignisprotokoll ein und stoesst ggf. Neu-Lernen der Vorlage an.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
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


# ---- Rueckmeldungen (Korrekturen/Ablehnungen) ----------------------------

def queued_invoices(data_root: str | Path) -> list[dict]:
    """Bereits an JobRouter uebergebene Rechnungen (``data/queue/done/``) --
    Basis fuer ``fetch_feedback()``: pro dieser Rechnungen wird nach einer
    Rueckmeldung gefragt."""
    done = queue_dir(data_root) / "done"
    out = []
    for p in sorted(done.glob("*.json")):
        try:
            data = json.loads(p.read_text("utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({"sha256": data.get("sha256"),
                   "file": Path(data.get("file") or "").name})
    return out


@dataclass
class Feedback:
    """Eine Rueckmeldung zu genau einer uebergebenen Rechnung. ``fields``
    sind vom Sachbearbeiter in JobRouter korrigierte Feld->Wert-Paare
    (unser Feldname, siehe ``template.STANDARD_FIELDS``); ``status``
    ``"rejected"`` heisst abgelehnt (keine Korrektur, nur ``reason``)."""
    sha256: str
    file: str = ""
    status: str = "corrected"   # "corrected" | "rejected" | "approved"
    fields: dict[str, str] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict:
        return {"sha256": self.sha256, "file": self.file, "status": self.status,
                "fields": self.fields, "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict) -> "Feedback":
        return cls(sha256=d.get("sha256", ""), file=d.get("file", ""),
                  status=d.get("status", "corrected"), fields=d.get("fields") or {},
                  reason=d.get("reason", ""))


class FeedbackAdapter:
    """Basis fuer Rueckmeldungs-Quellen. Die genaue JobRouter-API ist noch
    unbekannt -- neue Implementierungen tauschen nur ``fetch()`` aus, der
    Rest des Programms (``apply_feedback``) bleibt unberuehrt."""

    def fetch(self, queued: list[dict]) -> list["Feedback"]:
        raise NotImplementedError


class FileFeedbackAdapter(FeedbackAdapter):
    """Dateibasierter Adapter fuer Tests/Simulation ohne echtes JobRouter:
    liest Rueckmeldungen aus ``<ordner>/<beliebiger-name>.json``
    (``Feedback.to_dict()``-Format), eine Datei pro Rueckmeldung. Keine
    Netzwerkanbindung."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder)

    def fetch(self, queued: list[dict]) -> list[Feedback]:
        if not self.folder.exists():
            return []
        shas = {q.get("sha256") for q in queued if q.get("sha256")}
        out = []
        for p in sorted(self.folder.glob("*.json")):
            try:
                fb = Feedback.from_dict(json.loads(p.read_text("utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
            if not shas or fb.sha256 in shas:
                out.append(fb)
        return out


class RestFeedbackAdapter(FeedbackAdapter):
    """Echter Abruf ueber die JobRouter-REST-API -- Endpunkt/Antwortformat
    sind ein Platzhalter (``cfg.feedback_endpoint``, ein GET je Rechnung),
    die Kunden-IT hat die genaue Schnittstelle noch nicht spezifiziert.
    **Noch nicht gegen ein echtes JobRouter getestet** -- vor dem scharfen
    Einsatz hier an die tatsaechliche API anpassen (Pfad, Antwortformat,
    ggf. Batch-Abruf statt einem Request pro Rechnung)."""

    def __init__(self, cfg: JobRouterConfig):
        self.cfg = cfg

    def fetch(self, queued: list[dict]) -> list[Feedback]:
        out = []
        for q in queued:
            sha = q.get("sha256")
            if not sha:
                continue
            url = f"{self.cfg.base_url}{self.cfg.feedback_endpoint}/{sha}"
            headers = {}
            if self.cfg.token:
                headers["Authorization"] = f"Bearer {self.cfg.token}"
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=self.cfg.timeout) as r:  # noqa: S310
                    body = json.loads(r.read().decode("utf-8", "replace"))
            except (urllib.error.URLError, OSError, ValueError):
                continue
            if body:
                out.append(Feedback.from_dict({**body, "sha256": body.get("sha256") or sha}))
        return out


def fetch_feedback(data_root: str | Path, queued: list[dict] | None = None,
                   cfg: JobRouterConfig | None = None,
                   adapter: FeedbackAdapter | None = None) -> list[Feedback]:
    """Rueckmeldungen zu den uebergebenen (bzw. ``queued``, Standard: alle
    bereits an JobRouter uebergebenen) Rechnungen abholen. Ohne
    ``RECHNUNG_JOBROUTER_ENABLED=1`` automatisch der dateibasierte
    Test-Adapter (``data/jobrouter_feedback/``) statt eines echten Requests."""
    cfg = cfg or _jr_cfg()
    if queued is None:
        queued = queued_invoices(data_root)
    if adapter is None:
        adapter = (RestFeedbackAdapter(cfg) if cfg.live
                  else FileFeedbackAdapter(Path(data_root) / "jobrouter_feedback"))
    return adapter.fetch(queued)


def apply_feedback(store, feedback: list[Feedback], operator: str = "jobrouter") -> list[dict]:
    """Rueckmeldungen als Korrekturen einspielen: ``store.corrections``,
    Ereignisprotokoll (``eventlog.record_correction``) und Schattenstatistik
    (``shadow.record_feedback_correction``) nachtragen, danach versuchen,
    aus den korrigierten Werten eine (neue) Vorlage zu lernen
    (``learn.learn_from_confirmed``) -- genau wie bei einer Korrektur im
    Training-UI, nur mit Operator ``"jobrouter"`` statt eines Menschen am
    Rechner. **Noch nicht gegen ein echtes JobRouter getestet.**"""
    from . import eventlog, learn, shadow
    from .pipeline import load_document, process as pipeline_process

    out = []
    for fb in feedback:
        if not fb.sha256:
            continue
        rec = next((r for r in store.processed() if r.get("sha256") == fb.sha256), None)
        supplier_id = rec.get("supplier_id") if rec else None
        supplier_name = rec.get("supplier_name") if rec else None
        filename = (rec.get("file") if rec else None) or fb.file

        for fname, value in fb.fields.items():
            alt = store.corrections(fb.sha256).get(fname)
            store.save_correction(fb.sha256, fname, value, operator)
            eventlog.record_correction(store, fb.sha256, fname, alt, value, operator,
                                       supplier_id=supplier_id, supplier_name=supplier_name,
                                       file=filename)

        learned = None
        if fb.fields:
            shadow.record_feedback_correction(store, fb.sha256, list(fb.fields), operator)
            archived = _find_archived(store, fb.sha256)
            if archived is not None:
                doc, o = load_document(archived, store)
                if doc is not None:
                    res = pipeline_process(archived, store, use_llm=False)
                    learned = learn.learn_from_confirmed(store, res, doc, operator).to_dict()
        out.append({"sha256": fb.sha256, "status": fb.status,
                   "fields": list(fb.fields), "learned": learned})
    return out


def _find_archived(store, sha256: str) -> Path | None:
    """Pfad des unveraendert archivierten Originals (``store.archive_pdf``)
    zu einem sha256 -- egal ob ``.pdf`` oder ``.xml`` (E-Rechnung)."""
    arch = store.root / "archive"
    for suffix in (".pdf", ".xml"):
        p = arch / f"{sha256}{suffix}"
        if p.exists():
            return p
    return None
