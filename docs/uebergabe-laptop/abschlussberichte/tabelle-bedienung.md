## Abschlussbericht

**Gebaut** (Worktree `/tmp/rw-tab`, Branch `emmy/tabelle-bedienung`, 2 neue Commits):

**Baustein 5 — Spaltenwörterbuch + Layout-Familie + Rangfolge** (`508e22b`)
- `rechnung/spaltenwoerter.py` (neu): globales Wörterbuch Kopfwort → LI_STD-Spalte, zählt bestätigte Zuordnungen; entscheidet erst ab 2 Bestätigungen, nie gegen einen gleich starken/stärkeren Widerspruch.
- `store.py`: persistiert `data/spaltenwoerter.json`, auditiert jede Änderung.
- Gefüttert automatisch bei jeder erfolgreich gelernten Tabellen-Spec (`learn.learn_line_items`, ohne Extraschritt) **und** explizit aus der Rahmen-Markierung (`TabellePost.spalten_zuordnung`, `server.save_tabelle_ep`).
- `tabelle.py`: `_match_header_word` nutzt das Wörterbuch zusätzlich zu den eingebauten Schlüsselwörtern; neue `pruefe_kandidat()` für Tabellen aus fremden Quellen.
- `pipeline._tabelle_rangfolge`: eine zentrale Stelle — eigene (ggf. veraltete) Spec des Lieferanten → Layout-Familie (ähnlicher Lieferant über `suggest.similar_suppliers`) → Schlüsselwörter/-wörterbuch; jede Stufe nur bei bestandener Rechen-Prüfung. Herkunft erscheint als Hinweis im UI.

**Baustein 3 — Zeilen-Vorschlag** (`3124636`)
- `tabelle.zeilen_vorschlag`/`zeilen_vorschlag_zulaessig`: aus einer per Rahmen bestätigten Zelle die übrigen Spalten derselben Zeile ableiten (Wortlücken-Segmentierung, feste Spaltenreihenfolge), nur anbieten, wenn Baustein 4 nicht widerspricht.
- Neuer Endpoint `POST /api/tabelle/zeile_vorschlag`; UI zeigt die Vorschläge gelb, ein Klick übernimmt sie.
- Die „Spalte“-Richtung (Werte anderer Zeilen aus einer markierten Spalte ableiten) ist **offen geblieben** — der explizit in der SPEC beschriebene UI-Fall (Zeile) ist fertig, die Spalten-Variante würde zusätzliche Zustandsverwaltung im Frontend brauchen.

**Messwerte**
- Testsuite: 791/791 grün (763 vorher + 28 neue), auch mit `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1 RECHNUNG_IMAP_USER=x`.
- UI per Playwright: Zelle per Rahmen gefüllt → 5 gelbe Vorschläge erschienen (Beschreibung/Menge/Einzelpreis/MwSt/Währung), Klick auf „Menge“-Vorschlag hat die Zelle korrekt befüllt, keine Konsolenfehler.

**Offene Punkte**: Spalten-Vorschlag (vertikale Richtung, Baustein 3), tiefere UI-Kennzeichnung der Rangfolge-Herkunft (aktuell nur als Text-Hinweis, keine eigene Badge-Farbe je Quelle).

ATTACH: /tmp/uidemo-tab/1_rechnung_offen.png
ATTACH: /tmp/uidemo-tab/2_zelle_gefuellt_vorschlag_gelb.png
ATTACH: /tmp/uidemo-tab/3_vorschlag_uebernommen.png
