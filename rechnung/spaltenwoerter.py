"""Lieferantenuebergreifendes Spaltenwoerterbuch (SPEC Baustein 5, Aaron
12.10.2026): "Viele Lieferanten nutzen dieselbe Rechnungssoftware -- gleiches
Tabellenlayout". Jede bestaetigte Zuordnung eines Kopfworts im PDF zu einer
LI_STD-Spalte (z.B. "Anz." -> Quantity, "Bez." -> Description, "E-Preis" ->
Unit price) zaehlt hier mit, je Zielspalte -- egal ob sie aus der
Rahmen-Markierung (Dropdown im UI, server.save_tabelle_ep) oder automatisch
aus einer erfolgreich gelernten Tabellen-Spec (learn.learn_line_items) stammt.

Dient ``tabelle.py`` als ZUSAETZLICHER Hinweis bei einem Lieferanten ohne
eigene Spec -- nie bei einem bereits gesicherten/gelernten Lieferanten
(dort entscheidet dessen eigene Spec, siehe Rangfolge in ``pipeline.py``).
Ein Wort zaehlt erst ab zwei Bestaetigungen (bei mind. einem Lieferanten) UND
nie gegen einen Widerspruch (ein ANDERES Ziel genauso oft oder oefter
bestaetigt) -- lieber kein Hinweis als ein unsicherer."""

from __future__ import annotations

from .textnorm import norm

MIN_BESTAETIGUNGEN = 2

# Diese "Ziele" sind keine echte Spaltenzuordnung -- werden nie gezaehlt.
_NICHT_ZAEHLEN = frozenset({"", "ignorieren", None})


def ziel(counts: dict[str, int] | None) -> str | None:
    """Woerterbuch-Eintrag (Zielspalte -> Anzahl Bestaetigungen) ->
    entschiedenes Ziel, oder ``None`` (zu wenige Bestaetigungen bzw. ein
    genauso oft/oefter bestaetigtes anderes Ziel widerspricht)."""
    if not counts:
        return None
    best_ziel, best_n = max(counts.items(), key=lambda kv: kv[1])
    if best_n < MIN_BESTAETIGUNGEN:
        return None
    if any(n >= best_n for z, n in counts.items() if z != best_ziel):
        return None
    return best_ziel


def hinweis(woerterbuch: dict[str, dict[str, int]] | None, text: str) -> str | None:
    """``norm(text)`` im Woerterbuch nachschlagen -> entschiedene Zielspalte
    oder ``None``."""
    if not woerterbuch:
        return None
    return ziel(woerterbuch.get(norm(text)))


def lernen(woerterbuch: dict[str, dict[str, int]], text: str,
          ziel_spalte: str | None) -> dict[str, dict[str, int]]:
    """Woerterbuch um eine bestaetigte Zuordnung ergaenzen -> NEUES dict
    (das uebergebene bleibt unveraendert). Liefert bei einer ungueltigen
    Eingabe (kein Text, kein Ziel, "ignorieren") dasselbe Objekt unveraendert
    zurueck (per ``is``-Vergleich als "nichts geaendert" erkennbar)."""
    key = norm(text)
    if not key or ziel_spalte in _NICHT_ZAEHLEN:
        return woerterbuch
    out = {k: dict(v) for k, v in woerterbuch.items()}
    counts = out.setdefault(key, {})
    counts[ziel_spalte] = counts.get(ziel_spalte, 0) + 1
    return out
