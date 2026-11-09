"""Ohne JobRouter-Anbindung bleiben freigegebene Rechnungen in der
Warteschlange (Aaron 11.10.2026)."""

from pathlib import Path

from rechnung import jobrouter
from rechnung.config import JobRouterConfig

from test_jobrouter_feedback import _secured_setup  # noqa: F401
from rechnung import shadow
from rechnung.pipeline import finalize, process


def test_trockenlauf_bleibt_in_warteschlange(tmp_path: Path):
    store, samples = _secured_setup(tmp_path)
    res = process(samples / "a.pdf", store)
    store.mark_confirmed(res.sha256, "aaron")
    shadow.record(store, res, "aaron")
    assert finalize(res, store)["finalized"]
    q = jobrouter.queue_dir(store.root)
    vorher = sorted(p.name for p in q.glob("*.json"))
    assert vorher
    r = jobrouter.push_queue(store.root, JobRouterConfig(), out_dir=tmp_path / "out",
                             trockenlauf_aufheben=True)
    assert r["dry_run"] and r["sent"] == 0
    assert sorted(p.name for p in q.glob("*.json")) == vorher
