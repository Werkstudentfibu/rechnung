Alle vier Commits liegen auf `emmy/kleines-modell` (Worktree `/tmp/rw-klein`), nicht gepusht, nicht auf `release`. Hier der Abschlussbericht:

## Abschlussbericht: Kleines Modell (qwen2.5:1.5b) durch Aufgabenzuschnitt ausreichend machen

**Branch:** `emmy/kleines-modell` (Worktree `/tmp/rw-klein`, Basis `origin/release` @ `de9c6fa`)

### Bausteine

| Baustein | Datei(en) | Status |
|---|---|---|
| 2 — Positionstabelle ohne KI | `rechnung/tabelle.py` (neu) | gebaut, Schwerpunkt |
| 3 — Positions-Block (Variante B) | `rechnung/semantic.py` (`positions_block`, `_PROMPT_POSITIONEN`) | gebaut, Schwerpunkt |
| 4 — Positions-Plausibilität | `rechnung/semantic.py` (`positionen_plausibel`, `_plausibilitaet_nach_extraktion`) | gebaut, Schwerpunkt |
| 1 — Kopf-Absicherung | `rechnung/semantic.py` (`_datum_plausibel`) | schmal, wie gewünscht — Kopf-Erkennung selbst unverändert |
| Produktiv-Schalter | `rechnung/config.py` (`ki_variante()`), `pipeline.process()` | `RECHNUNG_KI_VARIANTE=A\|B` |

**Baustein 2** erkennt die Tabelle über Kopfzeilen-Schlüsselwörter (de/en/fr/it) im Wort-Layout, clustert Zeilen wie eine Vorlage, führt mehrzeilige Beschreibungen zusammen, ignoriert Zwischensummen/Überträge über Seitenwechsel. Übernommen wird nur, wenn die vorhandene Rechen-Prüfung aufgeht (`validate.line_item_row`/`check_line_items_total`) — sonst leer. Läuft in der Pipeline **vor** der KI; ein zweiter Versuch nach `rechnen.rechne` fängt Tabellen ohne Mengen-Spalte.

**Baustein 3 (Variante B)** fragt Positionen nur noch mit einem eigenen kleinen Prompt + Few-Shot-Beispiel auf dem reinen Tabellenausschnitt, statt im großen Rundum-Prompt. Kopf/Parteien bleiben wie Variante A (bewusst nicht umgebaut).

**Baustein 4** verwirft KI-Positionen, deren Summe nicht zum Netto passt, sowie Netto/USt > Brutto und Fälligkeit vor Rechnungsdatum.

### Messung (9 synthetische Rechnungen: DE 19%, DE 7/19, CH-QR 8,1%/2,6%, mehrseitig mit Übertrag, Rabattzeile, Gutschrift, Kleinbetrag, Englisch — je als Text-PDF und 300-dpi-Scan+Tesseract; qwen2.5:1.5b, CPU, `num_gpu=0 num_thread=4`)

| Format | Stand | Positionen korrekt | Summe passt | Zeit Ø | Zeit max |
|---|---|---|---|---|---|
| Text | heute (release) | 17/34 | 4/9 | 27,8 s | 60,2 s |
| Text | neu A | **34/34** | **9/9** | 3,6 s | 8,0 s |
| Text | neu B | **34/34** | **9/9** | 4,5 s | 16,3 s |
| Scan | heute (release) | 24/34 | 7/9 | 6,7 s | 18,9 s |
| Scan | neu A | **34/34** | **9/9** | 5,0 s | 9,8 s |
| Scan | neu B | **34/34** | **9/9** | 4,7 s | 9,8 s |

Kopf-Felder (Kontrolle "nicht schlechter"): rechnungsnummer/netto/ust_betrag/brutto bei neu A/B durchgehend 9/9 (heute: 6–9/9 je Format). rechnungsdatum bleibt schwach (0–2/9) — **in heute genauso wie in neu**, also kein Rückschritt, aber ein offener Punkt (Modell liest z.B. "14" statt "14.02.2026").

**Baustein 2 allein löste bereits alle 9 Standardfälle** — A und B zeigen dort deshalb kein Unterschied. Ein zehnter, absichtlich schwieriger Fall (Pauschale ohne Mengenspalte, Kopf-Netto weicht vom Positionsbetrag ab — Baustein 2 lehnt hier korrekt ab) zeigt den eigentlichen A/B-Unterschied: Variante A vermischte im großen Prompt die Spalten und zeigte eine falsche Position (950 statt 900 — zufällig gleich dem Netto, von keiner Prüfung zu fangen); Variante B lieferte dort lieber **keine** Position statt einer falschen.

### Empfehlung

**B für Produktion vorbereiten, aber A als Standard lassen**, bis mehr echte (nicht synthetische) Rechnungen gemessen sind — exakt wie in der Aufgabenstellung vorgesehen. `RECHNUNG_KI_VARIANTE=B` ist bereits scharf geschaltet und kann jederzeit ohne Code-Änderung getestet werden. Der große Gewinn kam ohnehin von Baustein 2 (KI für Positionen oft gar nicht mehr nötig) — B ist die zusätzliche Absicherung für die Fälle, die Baustein 2 nicht lösen kann.

### Offene Punkte
- **rechnungsdatum** bleibt beim 1,5B-Modell unzuverlässig (nicht Teil dieses Auftrags laut Aaron: Kopf-Erkennung nicht umbauen).
- Ein Modellfehler, der zufällig mit dem Netto übereinstimmt, kann auch Baustein 4 nicht fangen (im zehnten Testfall beobachtet).
- Gemessen wurde mit 9+1 synthetischen Rechnungen, nicht mit der echten Zürcher QR-Rechnung aus dem Befund — eine Nachmessung mit echten Belegen wäre der nächste sinnvolle Schritt.
- Variante C (Überblick + Blöcke) weiterhin nicht gebaut, wie vorgegeben.

### Commits
1. `9d61deb` — Baustein 2: `tabelle.py`
2. `da29fc0` — Baustein 1/3/4: Datum-Plausibilität, Positions-Block, Positions-Summe
3. `a68d1c9` — Pipeline-Tests + Variante-B-Fallback ohne Tabellenkopf
4. `fd29655` — `RECHNUNG_KI_VARIANTE` Produktivschalter

Testsuite: 746/746 grün, auch mit `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1 RECHNUNG_IMAP_USER=x`.
