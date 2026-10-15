HINWEIS: Fruehere Laeufe brachen ab. Baustein 1 ist committet (6fbc3f2). Der Branch emmy/effizienz wurde eben mit origin/release zusammengefuehrt (enthaelt jetzt u.a. Senden-Knopf /api/senden, Markieren per Rahmen, Startklar, Lernbericht, Sicherung). Halbfertige Aenderungen eines frueheren Laufs an config.py/ocr.py liegen im git stash 'effizienz-WIP-vor-merge-12.10' -- ansehen, uebernehmen oder verwerfen. Pruefe 'git log -8' und mache mit Baustein 2 weiter. WICHTIG: nicht auf release pushen.

# Auftrag: Effizienter bei gleich niedriger Fehlerquote (4 Bausteine)

Repo: `/home/aaron/.openclaw/workspace/tasks/rechnungsautomatisierung/app` (Python, `.venv`, Tests `.venv/bin/python -m pytest -q`).
Kontext: Rechnungsprogramm (IONOS-Postfach → PDF → Lieferant/Rechnungsadresse erkennen → Felder per Vorlage/KI/Texterkennung → Prüfung → JobRouter). Läuft vorerst NUR auf einem schwachen Firmenlaptop (Windows, ohne Adminrechte, Ollama `qwen2.5:1.5b-instruct`, portable Tesseract unter `C:\Rechnung\tesseract`). Lies `README.md`, `SPEC-v1.md` und `git log -15`, bevor du anfängst.

Der Vorgänger hat auf Branch `emmy/qualitaet` gebaut (Testsatz/Goldstandard, Lieferanten-Gedächtnis/Plausibilität, Pflichtangaben, Fehlerstatistik). Lege von dort den Branch `emmy/effizienz` an und arbeite NUR darauf. Nicht auf `release` pushen, nicht mergen. Committe je Baustein einzeln mit aussagekräftiger Nachricht (deutsch, Grund + „Entscheidung Aaron 10.10.2026“) und der Zeile `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

Leitlinie (Aaron): Fehlerquote darf NICHT steigen. Jeder Baustein muss abschaltbar sein (Umgebungsvariable `RECHNUNG_…`, Standard = an, wenn sicher) und mit dem Testsatz/Goldstandard des Vorgängers (`rechnung/testsatz.py`) bzw. `rechnung/messen.py` vorher/nachher gemessen werden (Zeit je Rechnung UND Trefferquote je Feld). Zahlen in den Abschlussbericht.

## Baustein 1: Absender der Mail als Lieferanten-Hinweis
Heute geht die Mail-Herkunft (Absender, Betreff, Message-ID) beim Abholen (`ingest.py`, `_save`) verloren; nur der Ordner „Menschliche Bearbeitung“ kennt sie.
- Beim Abholen je gespeichertem Anhang die Herkunft festhalten (Registry nach sha256 wie `anlagen.py`, z. B. `data/herkunft/<sha>.json`: von, Domain, Betreff, Message-ID, Datum). Auch beim Duplikat-Fall nichts kaputtmachen.
- Weitergeleitete Mails: Aaron leitet Rechnungen aus dem Firmen-Outlook weiter (Absender `…@althoffhotels.com`). Eigene Domains sind KEIN Lieferanten-Hinweis → konfigurierbare Liste `RECHNUNG_EIGENE_DOMAINS` (Standard `althoffhotels.com`); bei Weiterleitungen den ursprünglichen Absender aus dem zitierten Kopf („Von:“/„From:“ im Text bzw. angehängter .eml/message/rfc822) nehmen, wenn eindeutig. Freemail-Domains (gmail, gmx, web.de, outlook, hotmail, t-online, icloud, yahoo …) und Rechnungsportale ebenfalls nie als Identität werten.
- In der Pipeline: Absender-Domain gegen `Supplier.domains` (nur Kandidaten unter der erkannten Rechnungsadresse, siehe `identify.kandidaten`). Nur als **Hinweis/Eingrenzung**, nie allein Identität: passt die Domain zu genau einem Lieferanten und widerspricht keine Kennung auf der Rechnung (USt-IdNr/IBAN eines ANDEREN Lieferanten → Mensch, Betrugsschutz bleibt!), darf er Kandidat/Vorschlag sein bzw. den Abgleich beschleunigen. Widerspruch Domain ↔ IBAN = Warnung.
- Lernen: beim Bestätigen eines Lieferanten die Absender-Domain zu `domains` hinzufügen (nur nicht-eigene, nicht-Freemail).
- Anzeige: in der Prüfmaske dezent „Absender: …“.

## Baustein 2: Früh aufhören
Bekannter, bestätigter Lieferant + Vorlage gesichert + alle Pflichtfelder aus der Vorlage + alle Rechenprüfungen grün → KEINE KI-Stufe und bei Scans keine Texterkennung weiterer Seiten (nur was die Vorlage braucht; siehe `ocr.partial_pages`, Hintergrund-OCR). Erst wenn eine Prüfung rot ist oder ein Pflichtfeld fehlt, die teureren Stufen nachschalten. Ergebnis muss identisch zu heute sein, wenn die Prüfungen grün sind — das per Test beweisen (gleiche Felder mit/ohne Früh-Ausstieg auf dem Testsatz). Zeitgewinn messen und ausweisen (`timing.py`/Stufenzeiten).

## Baustein 3: Stufenweise KI
Erst das kleine Modell (`RECHNUNG_LLM_MODEL`). Sind danach Rechenprüfungen rot oder Pflichtfelder leer, ein zweites, größeres Modell (`RECHNUNG_LLM_MODEL_2`, Standard leer = aus) NUR für die fehlenden/strittigen Felder (Lücken-Prompt wie `semantic.extract`), mit derselben Beleg-Pflicht. Achtung Laptop-RAM: nie zwei Modelle gleichzeitig geladen halten (vor dem großen das kleine mit keep_alive=0 entladen, siehe `semantic.py` Baustein 5), Zeitgrenze beachten (`guard.py`). Ist Modell 2 nicht installiert/nicht erreichbar → still überspringen, Hinweis im Ergebnis. Mit Mock-Tests abdecken (kein echtes Modell in Tests).

## Baustein 4: Unsichere Stellen gezielt nachlesen
Tesseract liefert Wortkonfidenzen (`ocr.py`, tsv). Für Wörter in Betrags-/Datums-/Nummernfeldern (und IBAN) unter `RECHNUNG_OCR_WORD_MIN_CONF`: nur den Ausschnitt (Wort-Box + Rand) in höherer Auflösung (z. B. 450–600 dpi) neu rendern und mit Tesseract `--psm 7` (eine Zeile) und Zeichen-Whitelist für Zahlen erneut lesen; bei besserer Konfidenz das Wort ersetzen. Nur Ausschnitte, nicht ganze Seiten (Tempo!). Optional zweites Sprachpaket (`RECHNUNG_TESSDATA_GENAU`, z. B. tessdata_best) nur für diese Ausschnitte, wenn vorhanden. Tesseract ist in den Tests evtl. nicht vorhanden → Logik testbar machen (Mock/`pytest.importorskip`), Windows-CI `texterkennung-windows` deckt echt ab.

## Qualität
- Alle bisherigen Tests müssen grün bleiben; neue Tests je Baustein. Tests dürfen nicht von `rechnung.env` abhängen (`tests/conftest.py`).
- Windows denken: Pfade, keine Konsolenfenster (`ocr._run`), CRLF bei .cmd/.ps1.
- Keine neuen Pflicht-Abhängigkeiten ohne Not (Laptop ohne Admin; pip geht).
- README und SPEC-v1.md kurz ergänzen (neuer Abschnitt „Effizienz 10.10.2026“ mit den Schaltern).

## Abschlussbericht (letzte Ausgabe)
Je Baustein: was gebaut, Schalter, Testnamen, gemessene Zeit- und Trefferquoten vorher/nachher auf dem Testsatz, offene Risiken. Zum Schluss `git log --oneline` des Branches.
