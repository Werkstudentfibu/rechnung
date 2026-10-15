# Auftrag: Das kleine KI-Modell (qwen2.5:1.5b, CPU) durch Aufgabenzuschnitt ausreichend machen

Repo `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app`. Arbeite in einem **eigenen Worktree**:
`git worktree add /tmp/rw-klein -b emmy/kleines-modell origin/release` (im Haupt-Checkout liegt fremde WIP — nicht
anfassen). Venv: `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app/.venv/bin/python` (im Worktree
ggf. Symlink `.venv`, vor dem Commit wieder entfernen). Commit je Baustein. **Nicht pushen, nie auf `release`.**

Entscheidung Aaron (12.10.2026): erst das Programm besser auf die Aufgaben zuschneiden, damit das kleine Modell reicht,
bevor ein größeres Modell (3B, doppelte Zeit) genommen wird. Laptop: Windows, ~8 GB RAM, keine GPU.

Lies zuerst: `rechnung/semantic.py` (extract_full, _targeted_excerpt, _PROMPT_FULL, VARIANTE_A/pruefe_variante,
_dynamic_num_ctx, Beleg-Pflicht `_check_value`/`verify_in_text`), `rechnung/pipeline.py` (`_llm_full`, variante),
`rechnung/summen.py` (Beträge ohne KI), `rechnung/positionen.py`, `rechnung/template.py` (apply_line_items),
`rechnung/pdf.py` (Wörter mit Koordinaten), `rechnung/testsatz.py` (Messung, Variante A/B/C), `rechnung/swissqr.py`.

Befunde an einer echten Zürcher QR-Rechnung (Scan, OCR): Das kleine Modell schlug rechnungsdatum "0",
faelligkeit "30", brutto 10055.15 vor und lieferte **keine Positionen**, obwohl `RECHNUNG_LLM_LINE_ITEMS=1`.
Beträge stimmen inzwischen dank QR + summen.py.

## PRIORITÄT (Rückmeldung Aaron 12.10.2026, nach dem Auftrag)
"Die Felder hat das Programm gut erkannt und eingefügt, nur die Tabelle ist noch nicht gut."
→ **Schwerpunkt Positionen**: Baustein 2 (Tabelle ohne KI) und der Positions-Block aus Baustein 3 (Variante B nur für
Positionen, mit Tabellenausschnitt) zuerst und gründlich; Positions-Plausibilität aus Baustein 4. Baustein 1 (Kopf)
nur als schmale Absicherung gegen Unsinn wie Datum "0", NICHT die funktionierende Kopf-Erkennung umbauen. Die Messung
konzentriert sich auf Positionen (gefunden/korrekt/Summe passt, Zeit), Kopf-Felder nur als Kontrolle "nicht schlechter".

## Prinzip
Alles, was sich **ohne KI deterministisch** bestimmen lässt, zuerst und mit Vorrang; die KI nur für den Rest, mit
**kleinen, eng geschnittenen Fragen** auf **kurzen Ausschnitten**. Kein Raten, Beleg-Pflicht bleibt.

## Bausteine
1. **Kopf ohne KI** (`rechnung/kopf.py`): Rechnungsnummer und -datum über Beschriftungen (de/en/fr/it: Rechnungsnummer,
   Rechnung Nr., Re.-Nr., Invoice No., Facture n°, Fattura n., Beleg-Nr., Datum, Rechnungsdatum, Date, …) im
   Wort-Layout (rechts daneben oder darunter, wie eine Vorlage ohne Anlernen). Datumsformate de/CH/ISO/Monatsnamen.
   Mehrdeutig → nichts. Wert gewinnt vor KI, Vorlage/Korrektur gewinnt vor ihm.
2. **Positionen ohne KI** (`rechnung/tabelle.py`): Tabelle aus dem Wort-Layout erkennen — Zeilen mit ≥ 2 Beträgen
   rechtsbündig in gleichen Spalten, Kopfzeile über Schlüsselwörter (Beschreibung/Bezeichnung/Artikel/Menge/Anz./
   Einzelpreis/Preis/Betrag/Total/MwSt/MWST …), Zeile gilt, wenn menge × einzelpreis ≈ gesamt ODER Summe der Gesamtspalte
   = Netto (bzw. Brutto bei Brutto-Rechnungen). Mehrzeilige Beschreibungen zusammenführen, Zwischensummen/Überträge
   über Seitenwechsel ignorieren. Ergebnis nur übernehmen, wenn die Summenprüfung aufgeht; sonst KI bzw. leer.
   Positionsmodus je Lieferant (einzeln/zusammenfassung/eine_position) respektieren.
3. **Variante B (Blöcke) in semantic.py** — nur für das, was nach 1+2 noch fehlt: je Block ein eigener kleiner Prompt
   mit kleinem Schema und nur dem relevanten Ausschnitt: (a) Kopf (erste Seite oben, ~1200 Zeichen) für
   Nummer/Datum/Fälligkeit; (b) Parteien (Briefkopf/Adressfeld/Fußzeile) für Lieferant/Empfänger/Anschrift/IBAN/UID;
   (c) Positionen nur mit dem Tabellenbereich (Zeilen zwischen Kopfzeile und Summenblock), seitenweise wenn lang.
   Kurze deutsche Anweisungen mit 1 Beispiel (few-shot) je Block. `num_ctx` passend klein. Ein Block wird nur
   gefragt, wenn seine Felder fehlen. Variante A bleibt Standard, bis gemessen; `RECHNUNG_KI_VARIANTE=A|B`
   schaltet; `pruefe_variante` entsprechend erweitern (C weiter nicht bauen).
4. **Plausibilität der KI-Antworten** vor Übernahme: Datum im Bereich (nicht "0", nicht > heute+1, nicht < heute−2J),
   Fälligkeit ≥ Rechnungsdatum oder als "+N Tage" deuten, Beträge ≤ Brutto, Positionen-Summe ≈ Netto. Verworfenes
   mit Grund in `llm_rejected` wie bisher.

## Messen (Pflicht, Ergebnis in den Abschlussbericht)
Synthetische, realistische Testrechnungen erzeugen (`rechnung/synth.py`, als Text-PDF UND als 300-dpi-Scan mit OCR;
Tesseract unter `/tmp/mm/tess/bin/tesseract`, `TESSDATA_PREFIX=/tmp/mm/tess/share/tessdata`): mind. 8 Stück — DE 19 %,
DE 7/19 gemischt, CH-QR 8,1/2,6, mehrseitig mit Übertrag, Rechnung mit Rabattzeile, Gutschrift, Kleinbetrag,
englische Rechnung. Lokal läuft Ollama mit `qwen2.5:1.5b` (CPU erzwingen: options num_gpu=0, num_thread=4, wie in
`/tmp/bench/ch.py`). Vergleiche "heute (release)" vs. "neu A" vs. "neu B": je Feld richtig/falsch/fehlt, Positionen
korrekt, Sekunden je Rechnung. Tabelle in den Bericht. Ehrlich: nur Gemessenes berichten.

## Qualität
Tests je Baustein; Suite grün, auch mit `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1 RECHNUNG_IMAP_USER=x`.
`.ps1`/`.cmd` nicht anfassen. Deutsche Kommentare mit "Aaron 12.10.2026".

## Abschlussbericht
Deutsch, kurz: Bausteine + Dateien, Messtabelle, Commit-Hashes, Empfehlung A oder B, offene Punkte.
