"""Leichte, ML-freie Vorschlagslogik.

Zwei Bausteine, beide reine Textvergleiche (stdlib ``difflib``/Mengen-
Jaccard) statt Modell/Gewichte -- bleiben damit so deterministisch und
auditierbar wie der Rest von v1 (siehe SPEC-v1.md §7: kein ML-Modell):

- ``suggest_category``: Kategorie je Rechnungsposition, gelernt aus
  bestätigten/korrigierten Positionen (``Store.category_examples``).
- ``similar_suppliers``: ähnliche bekannte Lieferanten anhand des Kopf-
  Fingerprints -- Starthilfe beim Anlernen eines neuen Lieferanten,
  nie eine automatische Zuordnung.
"""

from __future__ import annotations

from difflib import SequenceMatcher, get_close_matches

from .config import category_fuzzy_min
from .identify import Supplier
from .textnorm import norm


def suggest_category(text: str, examples: dict[str, dict],
                     fuzzy_min: float | None = None) -> tuple[str, float] | None:
    """-> (kategorie, konfidenz) oder None.

    ``examples``: norm(text) -> {"kategorie": ..., "text": ...}, wie von
    ``Store.category_examples()`` geliefert. ``fuzzy_min`` per Default aus
    ``config.category_fuzzy_min()`` (env ``RECHNUNG_CATEGORY_FUZZY_MIN``).
    """
    key = norm(text)
    if not key or not examples:
        return None
    hit = examples.get(key)
    if hit:
        return hit["kategorie"], 0.95
    cutoff = fuzzy_min if fuzzy_min is not None else category_fuzzy_min()
    close = get_close_matches(key, examples.keys(), n=1, cutoff=cutoff)
    if not close:
        return None
    ratio = SequenceMatcher(None, key, close[0]).ratio()
    return examples[close[0]]["kategorie"], round(ratio * 0.85, 2)


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def similar_suppliers(fingerprint: set[str], suppliers: list[Supplier],
                      limit: int = 3, min_score: float = 0.12
                      ) -> list[tuple[Supplier, float]]:
    """Aehnlichste bekannte Lieferanten nach Kopf-Fingerprint -- auch unterhalb
    der harten Erkennungs-Schwelle aus ``identify.identify``. Dient nur als
    Anlern-Starthilfe ("sieht aus wie X"), niemals als automatische
    Lieferanten-Zuordnung."""
    scored = []
    for s in suppliers:
        score = _jaccard(fingerprint, set(s.fingerprint))
        if score >= min_score:
            scored.append((s, round(score, 3)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]
