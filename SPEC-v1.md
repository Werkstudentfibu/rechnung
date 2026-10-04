# SPEC v1 – Template-basierte Rechnungsextraktion

Stand: 2026-09-10. Diese Spec beschreibt **v1** – bewusst klein gehalten, damit
das Werkzeug auf jedem Firmen-Laptop ohne Installation, ohne Admin-Rechte und
ohne Netzwerkzugriff läuft und von der IT abgenommen werden kann.

## 1. Zweck

Aus PDF-Rechnungen mit **Textlayer** die Kopf-Felder, optional die
Positionszeilen und die USt-Aufstellung je Steuersatz auslesen. Pro Lieferant
wird das Layout **einmalig manuell angelernt** (1–3 Rechnungen), danach laufen
weitere Rechnungen desselben Lieferanten automatisch. Kein OCR, kein ML-Modell
in v1.

Ziel ist die schrittweise Ablösung von FlexiCapture 12 für den Anteil der
Lieferanten mit stabilem Layout (Pareto: wenige Lieferanten = Großteil des
Volumens). Einmal-Lieferanten bleiben vorerst manuell.

## 2. Funktionsumfang v1

| Bereich | v1 |
|---|---|
| Eingabe | lokaler Ordner mit `*.pdf` |
| Lieferanten-Erkennung | USt-IdNr → IBAN → Absender-Domain → Firmenname → Kopf-Fingerprint (gewichteter Score, Schwelle 0.5) |
| Template-Schlüssel | `(supplier_id, variant)` – eine Lieferanten-ID kann mehrere Layout-Varianten haben |
| Anlernen | lokale Web-UI auf `127.0.0.1`: PDF-Seite als Bild, Wörter anklickbar; pro Feld optional ein **Anker** (Label) + der/die **Wert-Wörter** |
| Feldmodell | Anker + relative Region (`rel`) – robuster gegen kleine Layout-Verschiebungen als absolute Koordinaten; Fallback absolute Seitenanteile (`abs_frac`) |
| Anwenden | bekannter Lieferant + Template → Felder ziehen, Konfidenz je Feld |
| Template „gesichert" | nach 3 bestätigten Rechnungen (`secured_at`) |
| Felder | rechnungsnummer, rechnungsdatum, netto, ust_betrag, ust_satz, brutto, bestellnummer -- plus automatisch aus dem Lieferanten: iban, ustid, steuernummer, adresse (§7e) |
| Validierung | netto + ust = brutto (±0,02); IBAN-Prüfziffer (ISO 7064); Datum plausibel (−10 J … +14 T); Dublettenprüfung je Lieferant + Rechnungsnummer |
| Status | `auto` / `review` / `no_template` / `unknown_supplier` / `scan_no_textlayer` |
| Ausgabe | JSON + CSV je Rechnung unter `./data/out/` |

## 3. Status-Logik

- `auto` – Template gesichert **und** alle Pflichtfelder gefunden **und**
  Mindestkonfidenz 0,7 **und** keine fehlgeschlagene Prüfung. Reines
  Konfidenz-Signal ("sieht sehr sicher aus") -- **keine** Freigabe fürs
  automatische Durchlaufen. Siehe §7b: Bestätigungspflicht.
- `review` – Template vorhanden, aber Konfidenz/Prüfung schwach → Mensch schaut drauf.
- `no_template` – Lieferant bekannt, noch kein Template → anlernen.
- `unknown_supplier` – Lieferant nicht erkannt → anlernen.
- `scan_no_textlayer` – historisch, wird seit §7n nicht mehr vergeben (Scans
  laufen automatisch durch die Texterkennung).
- `zurueckgestellt` – kein/mehrdeutiger Lieferant oder keine/mehrdeutige
  Rechnungsadresse aus der Bibliothek, IBAN weicht von den Stammdaten ab, OCR
  nicht verfuegbar/fehlgeschlagen, Zeitgrenze ueberschritten → Mensch muss ran
  (§7l/§7m/§7n).

## 4. Daten & Speicherorte

Alles liegt **lokal** neben dem Programm (bzw. unter `$RECHNUNG_DATA`):

```
data/
  suppliers.json          Lieferanten-Merkmale (USt-IdNr, IBAN, Domain, Fingerprint)
  templates/<id>.json     Feld-Definitionen
  processed.jsonl         Verarbeitungs-Log (Dublettenprüfung, Audit)
  events.jsonl            Ereignisprotokoll (§7o): je Verarbeitung/Korrektur ein Eintrag
  berichte/<datum>_<zeit>.json  Nachtlauf-Berichte (§7r)
  nacht.lock              Lock-Datei waehrend eines laufenden Nachtlaufs (§7r)
  out/<rechnung>.json|csv Extraktionsergebnis
```

Es werden **keine** Rechnungs-PDFs kopiert, nur der SHA-256-Hash im Log.

## 5. Sicherheits-/Abnahme-relevante Eigenschaften

- **Kein Netzwerkzugriff.** Die Web-UI bindet ausschließlich an `127.0.0.1`,
  keine Authentifizierung nötig (Einzelplatz). Kein ausgehender Traffic.
- **Keine Admin-Rechte, keine Installation.** Reines Nutzerkontext-Programm.
- **Keine Telemetrie, kein Auto-Update.**
- **Abhängigkeiten** (alle OSI-Lizenzen, siehe `pyproject.toml`):
  pdfplumber (MIT), pypdfium2 (BSD-3/Apache – PDFium), pillow (HPND/MIT-CMU),
  python-dateutil (Apache-2.0/BSD), fastapi (MIT), uvicorn (BSD-3), jinja2 (BSD-3),
  python-multipart (Apache-2.0). Dev-only: reportlab (BSD), pytest (MIT).
- **Quellcode** vollständig einsehbar, ~1,4k Zeilen, keine Obfuskation.
- **JobRouter/IONOS**: in v1 **nicht** angebunden – kein Zugriff auf Postfächer
  oder Firmen-APIs. Ausgabe ist eine Datei, die ein Mensch weiterreicht.

## 6. Verteilung

- Entwicklung: `python -m venv .venv && .venv/bin/pip install -e ".[dev]"`.
- Weitergabe zwischen Büros: **ein PyInstaller-Onefile-Build** (`build.md`),
  Windows-`.exe` bzw. Linux-Binary, ~40–60 MB, keine Runtime-Vorinstallation.
- Anzeige: `rechnung app` öffnet die Training-UI als eigenständiges Fenster
  (`pywebview`, Windows-Backend Edge WebView2 – i.d.R. bereits über Edge
  vorhanden), kein Browser-Tab nötig. `rechnung web` bleibt für Entwicklung/
  Linux als Browser-Variante bestehen; identischer FastAPI-Code in beiden Fällen.
- Kein Versand per E-Mail-Anhang (Mailfilter) – über Firmen-Git oder freigegebenes
  Laufwerk.

## 7. Nicht in v1 (bewusst ausgeklammert)

1. **OCR für Scans** (Tesseract) – kommt als Fallback, sobald v1 im Feld steht.
2. **Generisches NER/ML-Modell** für Einmal-Lieferanten – der Teil, der „läuft
   auf jedem Laptop" gefährdet (Modellgröße, Laufzeit).
5. **Rechnungspositionen (Line-Items)**.
6. **Mehrbenutzer / Server-Betrieb / zentrale Template-Verwaltung**.

## 7a. In v1.1 nachgezogen

- **IONOS-IMAP-Abholung** – `rechnung fetch` holt PDF-Anhänge aus dem Postfach
  in `<data>/inbox`. Standardmäßig aus; scharf nur mit `RECHNUNG_IMAP_*`
  (siehe `rechnung.env`-Vorlage). Nur stdlib (`imaplib`/`email`), kein
  ausgehender Traffic ohne gesetzte Zugangsdaten.
- **JobRouter-REST-Übergabe** – `rechnung push` arbeitet die Warteschlange
  (`data/queue/`) ab: echter POST mit Retry/Backoff, `allow_status`-Filter.
  Ohne `RECHNUNG_JOBROUTER_ENABLED=1` reiner Trockenlauf (JSON in `out/`).
- **`rechnung scan --finalize`** – geprüfte Rechnungen in die Bücher übernehmen:
  Original-PDF unverändert nach `data/archive/<sha>.pdf` (GoBD), Audit-Trail
  (`audit.jsonl`, wer/wann/was), append-only Journal, JSON/CSV nach `out/`,
  Einreihung für JobRouter. Idempotent über `sha256` + Template.
- **Manueller Korrektur-Workflow** – pro Rechnung (`sha256`) übersteuerbare
  Feldwerte (`data/corrections/`), gewinnen in Pipeline und Web-UI.
- **Härtung Validierung** – Betrags-Plausibilität, Positionssummen-Abgleich,
  länderspezifische IBAN-Längenprüfung, Journal-Dedup über `sha256`.

## 7b. In v1.2 nachgezogen — leichte Vorschlags-Hilfen (weiterhin kein ML-Modell)

Zwei Assistenz-Bausteine, beide reine Textvergleiche (stdlib `difflib`,
Mengen-Jaccard auf dem ohnehin vorhandenen Kopf-Fingerprint) statt Modell
oder Gewichte — bleiben deterministisch, ohne neue Abhängigkeit, auditierbar:

- **Kategorie-Vorschlag je Position** (`rechnung/suggest.py`,
  `Store.category_examples`): aus bestätigten/korrigierten
  Positionstexten gelernte Nachschlagetabelle (`data/categories.json`),
  gilt lieferantenübergreifend. Reiner Vorschlag mit Konfidenz — der Mensch
  bestätigt/korrigiert im Training-UI, nie automatische Buchung.
- **Ähnliche-Lieferanten-Vorschlag beim Anlernen** (`similar_suppliers`
  in `suggest.py`, Endpunkt `/api/template/copy`): bei neuem/unbekanntem
  Lieferanten werden Layouts mit ähnlichem Kopf-Fingerprint vorgeschlagen;
  ein Klick kopiert deren Template als Startpunkt. Das kopierte Template
  startet mit `confirmations=0` und muss wie jedes andere erst an echten
  Rechnungen bestätigt werden, bevor es auf `auto` springen kann — keine
  Abkürzung bei der Vertrauens-Schwelle.

## 7c. Bestätigungspflicht (v1.2) -- gilt für JEDE Rechnung in der Testphase

*Seit §7v gilt dieser Abschnitt im Betriebsmodus `testphase` (Standard). Im
Modus `autonom` duerfen sichere Rechnungen ohne Klick uebernommen werden.*

`finalize()` (Archivierung, Audit-Log, Journal, JobRouter-Einreihung) verweigert
jede Rechnung, die nicht zuvor im Training-UI per "Extraktion bestätigen"
explizit bestätigt wurde (`Store.mark_confirmed`/`is_confirmed`,
`data/confirmations.jsonl`, append-only wie das Audit-Log). Das gilt unabhängig
vom Status -- auch eine als `auto` erkannte Rechnung eines längst gesicherten
Templates läuft nicht ohne diesen Klick durch.

Damit ist `auto` vs. `review` nur noch ein Konfidenz-Signal für die Anzeige
(grün/orange im UI), keine Automatisierungs-Freigabe: die Kategorisierung und
Feld-Vorbefüllung passieren automatisch (ab der 2. Rechnung eines Lieferanten),
aber jede einzelne Rechnung braucht weiterhin einen Menschen, der bestätigt
(und dabei jederzeit korrigieren kann) -- egal wie oft dieser Lieferant schon
bestätigt wurde. `rechnung scan --finalize` (CLI-Batchlauf) überspringt
unbestätigte Treffer entsprechend und meldet das.

## 7d. Härtung (v1.3) -- Reaktion auf eine Risiko-Durchsicht

- **Mehrdeutige Lieferanten-Erkennung** (`identify.py`): Bei zu knappem
  Abstand zwischen bestem und zweitbestem Fingerprint-Treffer (< 0,15) gibt
  `identify()` jetzt `None` zurück statt zu raten -- verhindert eine stille
  Verwechslung zwischen zwei Lieferanten mit ähnlichem Kopf-Layout (z.B.
  gleiche Buchhaltungssoftware). Ein exakter USt-IdNr./IBAN-Treffer gilt als
  eindeutig genug und überspringt diese Prüfung.
- **Mehrdeutige Anker** (`template.py`): Kommt ein Ankertext mehrfach auf der
  Seite vor, sinkt die Konfidenz von 0,92 auf 0,55 -- der beim Anlernen
  gespeicherte `occurrence`-Index ist dann nicht mehr garantiert stabil.
  Fällt damit unter `CONF_MIN` (0,7) und landet auf `review`, statt
  selbstbewusst falsch als `auto` durchzulaufen.
- **Kategorie-Verwaltung**: neuer Reiter "Kategorien" im Training-UI
  (`/api/categories`, GET/DELETE) -- macht die gelernte Nachschlagetabelle
  sichtbar und korrigierbar/löschbar, statt dass ein einzelner Tippfehler
  unsichtbar in künftige Vorschläge einfließt.
- **Datensicherheit** (`store.py`): alle Zustandsdateien (Lieferanten,
  Templates, Kategorien, Adressen, Korrekturen) werden atomar geschrieben
  (Temp-Datei + Rename) und heben vor jedem Überschreiben eine `.bak`-Kopie
  auf. Eine beschädigte Datei (Stromausfall, volle Platte mitten im
  Schreiben) lässt das Programm nicht mehr abstürzen: erst wird die `.bak`
  versucht, sonst wird die kaputte Datei zur Seite gelegt (nicht gelöscht)
  und mit leerem Stand weitergemacht. Append-only-Logs (`processed.jsonl`,
  `audit.jsonl`, `confirmations.jsonl`) überspringen einzelne beschädigte
  Zeilen statt komplett unlesbar zu werden.
- **Kategorie-Vergleichstext**: nur die erste Text-Spalte der Positionstabelle
  (typischerweise "Bezeichnung"), nicht mehr alle Text-Spalten zusammen --
  eine zusätzliche Freitext-Spalte hätte den Fuzzy-Vergleich verwässert.
- **Fuzzy-Schwelle konfigurierbar**: `RECHNUNG_CATEGORY_FUZZY_MIN`
  (Standard 0,72) -- reine Schätzung bisher, da noch keine echten Rechnungen
  zur Kalibrierung vorliegen; über ENV justierbar ohne Code-Änderung.
- **JobRouter-Warnung**: `_post` warnt auf stderr, wenn ein Bearer-Token über
  eine nicht-`https://`-URL geschickt würde.

**Bewusst nicht angefasst:** mehrseitige Positionstabellen mit
Seitenumbruch (§8) -- ohne echte Beispielrechnungen mit diesem Layout wäre
jede "Reparatur" ungetestete Spekulation. Bleibt offen bis reale Fälle
vorliegen.

## 7e. Lieferanten-Stammdaten statt Pro-Rechnung-Extraktion (2026-09-12)

`iban`, `ustid`, `steuernummer` (aus `creditor_id`) und `adresse` bleiben von
Rechnung zu Rechnung desselben Lieferanten identisch -- sie stehen deshalb
nicht mehr in `STANDARD_FIELDS` (nicht mehr als Anker-Feld anlernbar) und
werden auch nicht ueber "feste Werte" dupliziert. Stattdessen mischt
`pipeline.process()` sie automatisch aus dem Lieferanten-Stammdatensatz ein
(Quelle `library`), sobald der Lieferant erkannt ist -- Anschrift, Bank/IBAN,
USt-IdNr und Gläubiger-ID/Steuernummer pflegt man einmal im Reiter
"Lieferanten", nie pro Rechnung. Die Training-UI zeigt das als eigenen,
schreibgeschützten "Lieferant – Stammdaten"-Block statt als Extraktionszeile
in der Feldtabelle.

Das Feld "Zahlbar bis" (`zahlungsziel`) als Pro-Rechnung-Extraktionsfeld
entfaellt ersatzlos -- nicht gebraucht. `Supplier.default_payment_terms`
("Zahlungsziel (Standard)") bleibt bestehen; das ist eine freitextliche Notiz
zum Lieferanten (z. B. "14 Tage netto"), kein extrahiertes Rechnungsdatum.

## 7f. Semantischer Fallback (2026-09-18) -- Revision von §7.2

§7.2 hatte ein generisches ML-Modell bewusst ausgeklammert, um die
Zero-Install-Eigenschaft nicht zu gefaehrden. Nach Rueckmeldung der IT steht
inzwischen mehr Hardware in Aussicht als urspruenglich angenommen (Server
oder Laptop, noch nicht final entschieden) -- Grund genug, diesen Punkt neu
zu bewerten, ohne die bestehenden Zusagen (§5) zu brechen.

Umsetzung als **optionale Ergaenzung**, nicht als Ersatz:

- `rechnung/semantic.py` fragt bei Feldern, die die Template-Extraktion nicht
  oder nur schwach liefert, ein lokal laufendes **Ollama** (eigener Prozess,
  nicht Teil dieses Programms) nach einer semantischen Interpretation des
  Volltexts. Das gelernte Template (falls vorhanden) geht als *Hinweis* mit,
  nicht als Zwang -- dadurch werden Anker zunehmend zur Leitlinie statt zur
  starren Regel, ohne dass ein Lieferant bei Layout-Abweichungen neu
  angelernt werden muss.
- **Standardmaessig aus** (`RECHNUNG_LLM_ENABLED=0`). Ohne das verhaelt sich
  das Programm exakt wie zuvor -- kein Netzwerkzugriff, keine neue
  Pip-Abhaengigkeit (reines stdlib `urllib`, kein SDK).
- **Kein Widerspruch zu §5, aber Praezisierung noetig:** "kein trainiertes
  KI-Modell" gilt nicht mehr uneingeschraenkt, sobald dieser Schalter an
  ist. `IT-FREIGABE.md` muss vor einer erneuten Vorlage entsprechend
  aktualisiert werden -- siehe dortigen Hinweis.
- **Konfidenz-Deckel:** ein LLM-Treffer bekommt hoechstens
  `RECHNUNG_LLM_MAX_CONFIDENCE` (Standard 0,6) -- unter `CONF_MIN` (0,7).
  Kann also nie allein `auto` ausloesen; landet immer auf `review`. Die
  Bestaetigungspflicht (§7c) gilt unveraendert fuer jede Rechnung.
- **Modell frei waehlbar** (`RECHNUNG_LLM_MODEL`) ohne Code-Aenderung --
  kleines Modell auf einem Laptop, groesseres sobald die Hardware-Frage
  (Server vs. Laptop) geklaert ist. Kein Vendor-Lock: jedes Ollama-kompatible
  Modell funktioniert.
- **Scans/OCR (§7.1) bleiben davon unberuehrt** -- dieser Fallback liest nur
  bereits vorhandenen Text (Template-Textlayer oder spaeter OCR-Ausgabe),
  er ersetzt keinen Bilderkennungsschritt.
- Bewusst (noch) nicht Teil dieses Fallbacks: Positionstabelle und
  USt-Aufstellung (`taxblock`) -- strukturierte Mehrfeld-Extraktion braucht
  ein eigenes Prompt-/Validierungsdesign, erst nachziehen, wenn sich der
  einfache Fallback in der Praxis bewaehrt hat.
- `semantic.extract()` laeuft NICHT in `rechnung scan`/`/api/scan` (Batch-
  Uebersicht ueber ggf. viele offene Rechnungen) -- nur in `/api/invoice`
  (Review vor dem Bestaetigen) und `pipeline.process()` (beim Bestaetigen
  selbst). Grund: die gedeckelte Konfidenz kann `auto`/`review` ohnehin nie
  aendern (siehe oben), der Batch-Scan wuerde also nur 30-50s/Rechnung Zeit
  kosten ohne den angezeigten Status zu beeinflussen.

## 7g. Anker direkt aus der Korrektur heraus setzen (2026-09-18)

Bisher war "einen Wert korrigieren" (Freitext-Eingabe, gilt nur fuer genau
diese eine Rechnung) und "einen Anker anlernen" (Wort im PDF anklicken, gilt
fuer alle kuenftigen Rechnungen des Lieferanten) getrennt und unterschiedlich
sichtbar. Jetzt gibt es bei jedem Feld ohne verlaesslichen Anker (Quelle
fehlt/`llm`/Konfidenz < 0,7) direkt einen Button „📍 im PDF markieren" neben
dem Korrekturfeld -- ein Klick waehlt das Feld aus, der naechste Wortklick im
PDF (optional Shift+Klick fuer einen Anker) plus „Template speichern" legt
den Anker an. Dabei zwei Haertungen, die vorher fehlten:

- `/api/template` mergt Felder jetzt **namentlich** statt die komplette
  Feldliste zu ersetzen -- vorher hat das gezielte Nachtragen eines einzelnen
  Feldes auf einer bereits trainierten Rechnung stillschweigend alle anderen,
  laengst gesicherten Anker geloescht (`STATE.assign` startet beim Oeffnen
  einer Rechnung immer leer).
- Ein frisch gesetzter Anker loescht eine vorher gespeicherte manuelle
  Korrektur fuer dasselbe Feld/dieselbe Rechnung -- sonst zeigt das Feld dort
  fuer immer Quelle "manual", obwohl die Positions-Extraktion es jetzt selbst
  zuverlaessig findet.

## 7h. Neuer Lieferant: Vorschlag statt Automatik (2026-09-18)

Aaron pflegt Lieferanten-/Rechnungsadress-Stammdaten (Name, Adresse, IBAN,
BLZ, ggf. USt-IdNr/Steuernummer) aktuell in Abbyy FlexiCapture; ein sauberer
Bulk-Export/Import steht noch aus (Abbyy-Liste muss dafuer erst exportiert
werden) und kann erst nach der Platzierung des Programms im Unternehmen
sinnvoll passieren. Bis dahin: wenn ein Lieferant nicht erkannt wird, zeigt
`/api/invoice` trotzdem die aus dem PDF automatisch gelesenen Kennungen
(USt-IdNr/IBAN/Domain, `identify.supplier_from_doc()`) in einem eigenen
Kasten „Neuer Lieferant erkannt". Das legt **nichts von selbst an** -- erst
ein expliziter Klick auf „✓ Als neuen Lieferanten anlegen" (POST
`/api/suppliers`) erzeugt den Stammdatensatz, mit Lieferant-ID/Name, die der
Mensch vorher eintraegt. Ab dann wird der Lieferant bei jeder weiteren
Rechnung erkannt (USt-IdNr/IBAN-Treffer reicht, kein Template noetig) --
Grundlage fuer den spaeteren Bulk-Import, der denselben Weg ueber
`/api/suppliers` nehmen wird, nur mit vielen Datensaetzen auf einmal statt
einem.

## 7i. Stufe 1: Vorlagen automatisch lernen (2026-10-04)

`rechnung/learn.py`. Beim Bestaetigen (`/api/confirm`, auch ohne Template)
sucht das Programm die bestaetigten Werte (Extraktion + Korrekturen) im PDF
wieder -- gemeinsame Normalisierung `textnorm.same_value` (Betraege
`1.190,00`/`1190.00`, Datumsformate inkl. Monatsnamen, Rechnungsnummern
ohne Trennzeichen verglichen) -- und erzeugt je Feld Anker + `rel`-Region
im selben Format wie das manuelle Anlernen. Leitplanken:

- nur aus bestaetigten Rechnungen (`Store.is_confirmed`), nie aus
  ungepruefter KI-Ausgabe; Stammdaten/feste Werte und E-Rechnungen werden
  nicht gelernt.
- Anker = naechstgelegenes Label links/oberhalb, das auf der Seite nur
  einmal vorkommt. Mehrfach vorkommende Werte (Brutto = Zahlbetrag,
  USt-Satz in jeder Position) entscheidet eine Stichwort-Heuristik je Feld;
  bleibt es unentschieden, wird das Feld weggelassen.
- Probelauf je Feld und Gesamtprobe: gespeichert wird nur, wenn das Template
  die Quell-Rechnung exakt reproduziert (Konfidenz >= 0,7).
- neue Templates starten mit `confirmations=0`; ungesicherte werden
  praezisiert, gesicherte nur ergaenzt. Widerspricht ein gesichertes
  Template einer bestaetigten Rechnung, wird nichts gespeichert und
  `template_learn_rejected` auditiert. Erfolg: Audit `template_learned`.

## 7j. Stufe 2: KI-Extraktion als volle Stufe (2026-10-04) -- Erweiterung von §7f

`semantic.extract_full()`: ohne Template liest das Modell die komplette
Rechnung (alle einfachen `STANDARD_FIELDS`, Lieferant Name/IBAN/USt-IdNr,
optional Positionen) als JSON, gegen ein festes Schema geprueft.

- **Belegpflicht:** jeder Modellwert muss im PDF-Text wiederzufinden sein
  (dieselbe Normalisierung wie §7i), sonst verworfen und in
  `Result.llm_rejected` angezeigt. Gilt auch fuer den Luecken-Fallback.
- **Backends:** `ollama` (wie bisher) oder `openai` = OpenAI-kompatibler
  Endpoint auf eigener Hardware (vLLM/llama.cpp, `RECHNUNG_LLM_BACKEND`,
  `_URL`, `_MODEL`, optional `_API_KEY`). Kein Cloud-Dienst fest eingebaut,
  Standard weiterhin aus.
- Herkunft je Feld als `origin` (`einvoice`/`template`/`llm`/`library`/
  `manual`) in Result und `/api/invoice`.
- Konfidenz-Deckel (§7f) und Status-Logik unveraendert: ohne Template bleibt
  der Status `no_template`/`unknown_supplier`.
- Antwort-Cache je Prompt: `/api/confirm` uebernimmt exakt den Vorschlag,
  den der Mensch in `/api/invoice` gesehen hat.
- **Hinweis IT-Freigabe:** `IT-FREIGABE.md` beschreibt das Modell noch als
  "nur `127.0.0.1`". Mit `RECHNUNG_LLM_BACKEND=openai` auf einem Firmen-
  server gehen Rechnungstexte an diesen Server -- vor dem Einschalten dort
  nachziehen.

## 7k. Schattenbetrieb-Messung (2026-10-04) -- Datengrundlage fuer §9

`rechnung/shadow.py`. Pro Rechnung entscheidet die Pipeline auf dem Stand
vor manuellen Korrekturen, ob sie sie selbst durchgelassen haette (alle
Pflichtfelder, keine Pruefung rot, bei neuem Lieferanten kein Pflichtfeld
nur aus der KI). Beim ersten Bestaetigen wird das mit dem Bestaetigten
verglichen (`data/shadow.jsonl`, append-only): Korrektur = abweichender
manueller Wert oder an dieser Rechnung trainierter Anker (Audit
`template_trained`). Auswertung je Lieferant: `rechnung schatten`,
`GET /api/shadow`. **Reine Messung** -- §7c gilt unveraendert.

## 7l. Bibliothek als einzige Quelle fuer Lieferant/Rechnungsadresse (2026-10-04)

Umsetzung einer Auftraggeber-Entscheidung: Lieferanten und Rechnungsadressen
sind **1:1 aus der Bibliothek** vorgegeben (Abbyy-Import). Das Programm
erkennt nur per Abgleich gegen die hinterlegten Stammdaten -- es legt nie von
selbst einen Lieferanten an und leitet die Identitaet nie aus KI-Vorschlaegen
oder dem Verkaeufernamen einer E-Rechnung ab (die manuelle "+ Neuer
Lieferant"-Erfassung im Training-UI, §7h, bleibt unveraendert: ein Mensch
pflegt die Bibliothek, das Programm erfindet nichts).

- **Status `zurueckgestellt` – menschlich bearbeiten** (ersetzt das fruehere
  `unknown_supplier`; `UNKNOWN` bleibt im Code als Alias) greift, sobald
  `identify_with_reason()` bzw. `billing.match_text()` keinen oder einen
  mehrdeutigen Treffer liefert -- Grund steht in `Result.hold_reasons`
  (`identify.py`, `billing.py`). Die Extraktion laeuft trotzdem durch, damit
  der Mensch die gelesenen Werte gleich vor sich hat.
- **Stammdaten haben Vorrang.** IBAN, USt-IdNr, Steuernummer und Adresse
  kommen immer aus dem Lieferanten-/Adress-Datensatz (`library`), nie aus der
  Rechnung, der E-Rechnungs-XML oder einem KI-Vorschlag. Das automatische
  Anlernen (§7i) uebernimmt diese Felder entsprechend nicht mehr aus der
  Rechnung in bestehende Stammdaten.
- **Betrugsschutz:** steht auf der Rechnung eine IBAN, die nicht in den
  Lieferanten-Stammdaten hinterlegt ist, ist die Pruefung `iban-stammdaten`
  rot mit dem Grund "IBAN weicht von Stammdaten ab (Betrugsschutz)"
  (`identify.IBAN_DEVIATES`) -- die Rechnung geht **immer** an den Menschen
  und zaehlt im Schattenbetrieb nie als "wuerde durchlassen". Dieselbe Logik
  gilt fuer eine unbekannte/mehrdeutige Rechnungsadresse.
- Bewusst **keine** Auto-Freigabe pro Lieferant und **keine** automatische
  Dokumentklassifikation -- jede Datei, die im Postfach ankommt, wird wie
  eine Rechnung behandelt (§7c/§9 gelten unveraendert).

## 7m. Zeitgrenze pro Rechnung (2026-10-04)

`rechnung/guard.py`. Jede Rechnung bekommt fuer die komplette Verarbeitung
(Lieferant/Vorlage-Erkennung, OCR, KI, Pruefungen) hoechstens
`RECHNUNG_TIMEOUT` Sekunden (Standard 120, konfigurierbar, `0` = aus).

- Zwei Betriebsarten: ein eigener Arbeitsprozess je Rechnung fuer Stapellaeufe
  (`rechnung scan`, Nachtlauf) -- bei Zeitueberschreitung hart beendet und fuer
  die naechste Rechnung neu gestartet, auch eine in einem C-Parser
  haengenbleibende Rechnung blockiert die Schlange damit nie; ein
  Hintergrund-Thread im selben Prozess fuer das Training-UI (teilt den
  KI-Antwort-Cache mit `/api/invoice`, §7j).
- Bei Abbruch: Status `zurueckgestellt`, Grund ("Zeitgrenze ... ueberschritten"
  bzw. "Verarbeitungsfehler: ...") in `Result.hold_reasons`, Audit-Eintrag
  `verarbeitung_abgebrochen` mit Dauer. Zaehlt im Schattenbetrieb nie als
  "wuerde durchlassen".

## 7n. Texterkennung fuer Scans (OCR) (2026-10-04)

`rechnung/ocr.py`. PDFs ohne Textebene sowie Bild-Anhaenge (`.jpg`, `.jpeg`,
`.png`, `.tif`, `.tiff`, auch aus der IONOS-Abholung, §7a) werden per
**Tesseract** (Sprache `deu`) in ein PDF mit Textebene gewandelt und laufen
danach ganz normal durch dieselbe Pipeline -- kein separater Pfad, keine
Dokumentklassifikation. Rein lokal: Tesseract ist ein eigenes Programm auf
demselben Rechner, es geht nichts ins Netz.

- **Voraussetzung, keine Python-Abhaengigkeit.** Tesseract muss separat
  installiert sein (siehe `build.md`). Ist es nicht installiert oder fehlt
  das deutsche Sprachpaket, degradiert das Programm sauber: die Rechnung wird
  mit dem Grund "OCR nicht verfuegbar" zurueckgestellt statt abzustuerzen.
- Das Original bleibt unveraendert -- Archiv, sha256, Korrekturen und
  Bestaetigung haengen immer am Original. Die erkannte Textebene liegt als
  Arbeitskopie unter `data/ocr/<sha>.pdf` (+ `.json` mit den Konfidenzen) und
  wird pro Original nur einmal erzeugt (Cache).
- **Konfidenz-Herabstufung:** liegt die mittlere Wortkonfidenz unter
  `RECHNUNG_OCR_MIN_CONF` (Standard 80) ist die Pruefung `ocr-konfidenz` rot;
  enthaelt ein gelesenes Feld ein Wort unter `RECHNUNG_OCR_WORD_MIN_CONF`
  (Standard 60), faellt dessen Feldkonfidenz unter `CONF_MIN`. Beides: nie
  `auto`, nie "wuerde durchlassen" im Schattenbetrieb (§7k).
  Felder, deren Wert aus der Texterkennung stammt, sind im Ergebnis
  zusaetzlich mit `via: "ocr"` markiert.
- Die Zeitgrenze (§7m) gilt fuer OCR mit -- eine haengende Texterkennung wird
  wie jede andere Rechnung abgebrochen und zurueckgestellt.
- Der fruehere Status `scan_no_textlayer` ("v1 nicht unterstuetzt", §3) wird
  dadurch nicht mehr vergeben, bleibt aber als Konstante/UI-Darstellung
  bestehen.

## 7o. Vollstaendiges Fehler-/Ereignisprotokoll + Fehlerbericht (2026-10-04)

`rechnung/eventlog.py`. Jede Verarbeitung (CLI `scan`, Bestaetigen im
Training-UI) haengt ein Ereignis an `data/events.jsonl` an: Datei, sha256,
Lieferant, welche Stufen/Herkunftsquellen Werte beigetragen haben (`stages`,
inkl. `ocr`), Herkunft und Konfidenz je Feld, alle Pruefungen mit
Ergebnis/Grund, Dauer, Endstatus, Rueckstellgruende. Rein additiv wie
`audit.jsonl`/`shadow.jsonl` -- jeder Verarbeitungsdurchlauf (auch ein per
Zeitgrenze/OCR-Fehler abgebrochener, der nie bestaetigt wird) bekommt sein
eigenes Ereignis, nichts wird ueberschrieben.

- **Korrekturen**: `POST /api/correction` haengt zusaetzlich ein
  Korrektur-Ereignis an (Feld, alter Wert der vorherigen Korrektur bzw.
  `null` bei der ersten, neuer Wert, Lieferant falls erkennbar).
- **Fehlerbericht**: `rechnung fehlerbericht [--seit JJJJ-MM-TT] [--json]`
  bzw. `GET /api/fehlerbericht?seit=...` (`eventlog.report()`) gruppiert die
  Ereignisse -- rote Pruefungen je Pruefung+Lieferant, fehlende/Felder mit
  Konfidenz unter `CONF_MIN` je Feld+Lieferant, Korrekturen je Feld+Lieferant
  -- jeweils mit bis zu 5 Beispiel-Rechnungen, absteigend nach Haeufigkeit.
  Reine Auswertung wie der Schattenbetrieb (§7k) -- aendert keine Pipeline-
  Entscheidung.
- Eigenstaendig von `shadow.py`: Schattenbetrieb misst "haette die Maschine
  durchgelassen", dieses Protokoll sammelt die rohen Ereignisse fuer die
  Fehlersuche -- unabhaengige Fragen auf denselben Ergebnissen.

## 7p. Layoutwechsel erkennen (2026-10-04)

`rechnung/learn.py` (`track_template_health`, `_active_template`,
`_next_template_id`). Findet eine **gesicherte** Vorlage bei einer
bestaetigten Rechnung ein Pflichtfeld nicht (`Result.shadow["sources"]`,
also auf dem maschinellen Stand vor Korrekturen) oder geht eine
Kernpruefung (`netto+ust=brutto`, `betraege-plausibel`) nicht auf, zaehlt
das als Fehlschlag. Zwei Fehlschlaege in Folge (`STALE_THRESHOLD = 2`) markieren
die Vorlage als `stale` ("veraltet") -- Audit `template_stale` mit Grund und
Fehlschlagzahl. Andere rote Pruefungen (z.B. `iban-stammdaten`, Bibliotheks-
Abgleich) zaehlen bewusst nicht -- die haben keinen Layout-Bezug und wuerden
eine intakte Vorlage faelschlich veralten lassen. Ein Erfolg setzt den
Zaehler zurueck.

- **Nicht geloescht.** `pipeline._pick_template` waehlt eine veraltete
  Vorlage nicht mehr aus -- die naechsten Rechnungen dieses Lieferanten
  laufen ueber die KI-Stufe (§7j) bzw. Handeingabe, bis wieder ein Template
  greift.
- **Automatisches Neulernen.** Aus der naechsten bestaetigten Rechnung ohne
  Template lernt Stufe 1 (§7i) automatisch eine neue Vorlage -- unter einer
  neuen Id (`<supplier>__<variant>__v2`, `__v3`, ...), falls die
  Standard-Id noch von der veralteten Version belegt ist. Die veraltete
  Vorlage bleibt unter ihrer alten Id gespeichert (Nachvollziehbarkeit/Audit),
  wird aber nicht mehr angewendet.
- Nur fuer gesicherte Vorlagen relevant -- eine noch nicht gesicherte ist
  ohnehin in der Lernphase und wird durch Fehlschlaege nicht "veraltet".

## 7q. Rueckmeldung aus JobRouter (2026-10-04)

`rechnung/jobrouter.py` (`FeedbackAdapter`, `FileFeedbackAdapter`,
`RestFeedbackAdapter`, `fetch_feedback`, `apply_feedback`). Die genaue
JobRouter-API fuer Rueckmeldungen (Korrekturen/Ablehnungen, die ein
Sachbearbeiter spaeter in JobRouter eintraegt) ist noch unbekannt -- die
Kunden-IT hat sie noch nicht spezifiziert. Deshalb eine duenne Adapter-
Schnittstelle (`FeedbackAdapter.fetch(queued)`):

- `FileFeedbackAdapter` (dateibasiert, `data/jobrouter_feedback/*.json`) ist
  der Standard ohne `RECHNUNG_JOBROUTER_ENABLED=1` und fuer Tests/Simulation
  voll funktionsfaehig.
- `RestFeedbackAdapter` ist ein Platzhalter-Pfad (`cfg.feedback_endpoint`,
  ein GET je Rechnung) fuer den scharfen Betrieb. **Noch nicht gegen ein
  echtes JobRouter getestet** -- Pfad/Antwortformat muessen angepasst
  werden, sobald die Kunden-IT die echte Schnittstelle liefert.

`apply_feedback()` spielt eine Rueckmeldung mit korrigierten Feldern als
Korrektur ein -- Operator `"jobrouter"` statt eines Menschen am Rechner,
sonst derselbe Weg wie eine manuelle Korrektur im Training-UI:

- `store.corrections` (ueberschreibt den Wert fuer die naechste
  Verarbeitung) + Audit `correct`.
- Ereignisprotokoll (`eventlog.record_correction`, §7o) -- taucht im
  Fehlerbericht als Korrektur auf.
- Schattenstatistik (`shadow.record_feedback_correction`): eine vorher als
  "wuerde durchlassen" gewertete Rechnung, die JobRouter nachtraeglich
  korrigiert, zaehlt dann als Fehlentscheidung. Haengt einen neuen Eintrag
  an (append-only); `shadow.stats()` wertet seitdem je sha256 den zuletzt
  angehaengten Eintrag statt immer des ersten.
- Neu-Lernen der Vorlage (`learn.learn_from_confirmed`) auf Basis der
  korrigierten Werte -- kann z.B. eine Vorlage praezisieren oder (bei einer
  bereits als veraltet markierten, §7p) eine neue Version anstossen.

## 7r. Nachtlauf (2026-10-04)

`rechnung/nightly.py` (`run_nightly`, CLI `rechnung nacht [--json]`).
Fasst die unbeaufsichtigten Wartungsschritte in einem Lauf zusammen:

1. JobRouter-Rueckmeldungen holen und einspielen (§7q:
   `jobrouter.fetch_feedback`/`apply_feedback`).
2. Layoutwechsel pruefen (§7p): als veraltet markierte Vorlagen auflisten
   (`Template.stale`) -- neu gelernt wird dabei ggf. schon ueber die gerade
   eingespielten Rueckmeldungen, ein eigener Scan-Schritt laeuft im
   Nachtlauf bewusst nicht (siehe Punkt 4).
3. Fehlerbericht (§7o, `eventlog.report`) + Tagesbericht
   (`nightly.tagesbericht`: verarbeitet, an Mensch, Fehlerquote,
   Dauer-Verteilung, Rueckstand unbestaetigter Dateien im Sample-Ordner) als
   JSON unter `data/berichte/<datum>_<zeit>.json`.
4. Gesundheitschecks (`nightly.health_checks`): IONOS-Anmeldung moeglich
   (`None`, wenn `RECHNUNG_IMAP_*` nicht gesetzt ist), JobRouter-
   Warteschlange (`data/queue/*.json`) nicht zu lang, genug freier
   Plattenplatz (Schwellen `QUEUE_WARN`/`DISK_WARN_MB`/`DISK_WARN_PCT` --
   grobe Schaetzungen ohne echte Betriebsdaten, siehe §7d-Vorbild).

**Lock-Datei** (`rechnung/lock.py`, `data/nacht.lock`): `run_nightly` laeuft
exklusiv -- ein zweiter gleichzeitiger Aufruf bricht sofort mit
`RuntimeError` ab. `ingest.fetch` und `guard.Guard.process` (also jede
Stapellauf-Verarbeitung: `rechnung scan`, Training-UI) pruefen die
Lock-Datei zu Beginn und pausieren, solange sie besteht -- eine Verzoegerung
ist dabei ausdruecklich in Ordnung. Ohne bestehende Lock-Datei (Normalfall)
entsteht kein Mehraufwand (ein Datei-Existenz-Check). Ein Sicherheitsnetz
(`MAX_WAIT_S`, 6 Stunden) verhindert ein ewiges Haengen, falls eine
Lock-Datei nach einem Absturz liegen bleibt.

## 7s. JobRouter-Uebergabe fuer den echten Anschluss vorbereiten (2026-10-04)

Die echten Prozessdaten kommen erst vom JobRouter-Admin des Kunden -- bis
dahin bleibt alles Trockenlauf (`RECHNUNG_JOBROUTER_ENABLED=0`) und
**nicht gegen ein echtes JobRouter getestet**.

- `config.JobRouterConfig` bekommt `process`/`process_version`/`step`/
  `user`/`role` (`RECHNUNG_JOBROUTER_PROCESS`/`_VERSION`/`_STEP`/`_USER`/
  `_ROLE`) -- fliessen in `jobrouter.payload_from_result()` als eigener
  `process`-Block + `user`/`role` ein, bewusst konfigurierbar statt fest im
  Code.
- `jobrouter.load_mapping()` liest die Feldzuordnung (unser Feldname ->
  JobRouter-Feldname) aus einer JSON-Datei (`cfg.mapping_path`, Standard
  `<data>/jobrouter-mapping.json`, `RECHNUNG_JOBROUTER_MAPPING` ueberschreibt
  den Pfad) -- Beispiel `jobrouter-mapping.example.json` im Projektwurzel-
  verzeichnis. Fehlt die Datei oder ein Feld darin, bleibt der eigene
  Feldname unveraendert (additiv, nie Pflicht).
- `jobrouter._build_multipart`/`_post_with_attachment`: das Original (PDF
  bzw. XML bei E-Rechnungen, aus `data/archive/`) geht als
  `multipart/form-data`-Anhang mit, wie es die JobRouter-REST-API fuer
  Incidents mit Dateien erwartet -- nur stdlib (`uuid` fuer die Boundary),
  kein neues Paket. `push_queue` findet das archivierte Original automatisch
  ueber den sha256 und haengt es an; `submit()` faellt ohne Original auf
  reines JSON zurueck.
- `rechnung jobrouter-test` (`jobrouter.check_connection`): ein GET auf die
  Basis-URL mit demselben Bearer-Token wie `_post`, **ohne** eine Rechnung
  anzulegen. `rechnung imap-test` (`ingest.check_connection`): IMAP-
  Anmeldung + Ordnerliste + Anzahl ungelesener Mails, **ohne** etwas
  abzuholen oder als gelesen zu markieren (`SELECT ... readonly`). Beide
  liefern `ok=None`, wenn die jeweilige Schnittstelle nicht konfiguriert
  ist (kein Fehlschlag, nur "nichts zu pruefen").
- README-Checkliste "Was wir vom Kunden fuer den Anschluss brauchen"
  (IONOS: Host/Benutzer/Passwort/Ordner/Zielordner; JobRouter: URL/Token,
  Prozess/Version/Schritt, Feldnamen, Rueckmelde-Weg).

## 7t. Vor dem Serverbetrieb: was in IT-FREIGABE.md angepasst werden muss (2026-10-04)

`IT-FREIGABE.md` beschreibt bewusst den **aktuellen Einzelplatz-Prototyp**
(Firmen-Laptop, `127.0.0.1`, kein Netzwerkzugriff ausser optional/manuell
IONOS/JobRouter) und wurde in diesem Durchlauf absichtlich **nicht
geaendert** -- das ist ein eigenes Gespraech mit der IT. Vor einer erneuten
Vorlage dort bzw. vor einem Serverbetrieb sind diese Punkte inzwischen
nicht mehr gedeckt und muessen nachgezogen werden:

1. **"Lokales Sprachmodell nur `127.0.0.1`" gilt nicht mehr uneingeschraenkt**
   (siehe schon §7f/§7j): `RECHNUNG_LLM_BACKEND=openai` kann auf einen
   Firmenserver zeigen -- dann gehen Rechnungstexte dorthin. Betrifft nur,
   wer das einschaltet (Standard weiterhin aus).
2. **Tesseract (OCR, §7n) ist eine neue, externe Abhaengigkeit** ausserhalb
   der Python-Pakete -- ein eigenes, separat zu installierendes Programm auf
   demselben Rechner (siehe `build.md`). Steht in der Bibliotheks-Tabelle
   von `IT-FREIGABE.md` noch nicht drin; rein lokal, aber systemseitig
   vorauszusetzen.
3. **Der Nachtlauf (§7r, `rechnung nacht`) ist kein interaktiver
   Handbetrieb mehr** -- er ist fuer **unbeaufsichtigten** Lauf per
   Zeitsteuerung (cron/Windows-Aufgabenplanung) gedacht und macht dabei
   aktiv Netzwerkzugriffe (IONOS-Anmeldung fuer den Gesundheitscheck,
   JobRouter-Rueckmeldungen abholen) **ohne** einen Menschen, der im
   selben Moment am Rechner sitzt und klickt. Das passt nicht mehr zu
   "Einzelplatz-Werkzeug ... keine Mehrbenutzer-/Servernutzung vorgesehen"
   in `IT-FREIGABE.md` und sollte dort als eigener, erklaerter Betriebsmodus
   nachgetragen werden, sobald IONOS/JobRouter tatsaechlich scharf gestellt
   werden (beides bleibt bis dahin Trockenlauf, siehe §7a/§7q/§7s).
4. **IONOS-/JobRouter-Anbindung selbst** (§7a/§7s) ist in `IT-FREIGABE.md`
   noch als "im jetzigen Testbetrieb nicht aktiv" beschrieben -- das bleibt
   korrekt, solange `RECHNUNG_IMAP_*`/`RECHNUNG_JOBROUTER_ENABLED` nicht
   gesetzt sind, muss aber bei der naechsten Freigabe-Runde aktualisiert
   werden, sobald echte Zugangsdaten vom Kunden kommen (siehe README-
   Checkliste in §7s).

**Nachtrag:** Der Entwurf fuer die naechste Freigaberunde, der diese Punkte
und die Erweiterungen §7u-§7w (Ordner "Menschliche Bearbeitung",
Betriebsmodus autonom, Windows-Laptop/Build) abdeckt, liegt in
`IT-FREIGABE-ENTWURF-v2.md`. `IT-FREIGABE.md` bleibt bis zur Abstimmung
unveraendert.

## 7u. Ordner "Menschliche Bearbeitung" (2026-10-04)

`rechnung/human.py`. Grundsatz: nichts wird geraten oder mit unsicheren
Werten weitergegeben. `human.gruende(res)` bestimmt, warum das Programm bei
einer Rechnung nicht sicher ist (leere Liste = sicher), je Grund
`{"art", "grund", "felder"}`:

| Art | Ausloeser |
|---|---|
| `zurueckgestellt` | Hold-Grund aus §7l (Lieferant/Rechnungsadresse nicht eindeutig) |
| `iban` | IBAN weicht von den Stammdaten ab (ersetzt die rote `iban-stammdaten`-Pruefung, keine Doppelmeldung) |
| `zeitgrenze` / `fehler` | Abbruch durch `guard.py` (§7m) |
| `ocr` | OCR nicht verfuegbar/fehlgeschlagen oder Pruefung `ocr-konfidenz` rot (§7n) |
| `pruefung` | jede andere rote Pruefung, betroffene Felder ueber eine feste Zuordnung (z.B. `netto+ust=brutto` -> netto/ust_betrag/brutto) |
| `pflichtfeld` | Pflichtfeld (`shadow.REQUIRED`) fehlt |
| `unsicher` | gelesener Wert (nicht Stammdaten/manuell) unter `CONF_MIN` 0,7 -- z.B. reiner KI-Wert (gedeckelt 0,6) oder unsicheres OCR-Wort |
| `nicht_lesbar` | weder PDF noch lesbare E-Rechnung |

`pipeline.dispatch(res, store)` (aufgerufen von `scan_folder` und
`/api/scan`): bereits uebernommene Rechnungen (`store.seen_sha`) bleiben
unberuehrt; unsichere legt `human.ablegen` als Arbeitskopie in den Ordner
(`RECHNUNG_MENSCHLICH`, Standard `<data>/menschlich/`) mit Begleitdatei
`<datei>.grund.json` (sha256, Original-Pfad, Lieferant, Status, Gruende,
betroffene Felder, Vorschlagswerte mit Herkunft/Konfidenz/`via`, Zeitpunkt
der ersten Ablage). Erneute Verarbeitung aktualisiert die Begleitdatei,
legt aber kein zweites Ereignis an; bereits bestaetigte Rechnungen werden
nie (wieder) abgelegt; bei Namensgleichheit mit einer anderen Rechnung wird
der Hash-Anfang vorangestellt statt zu ueberschreiben. Originale im
Eingangsordner bleiben unveraendert.

Training-UI: Reiter "Menschliche Bearbeitung" (`GET /api/menschlich`),
CLI `rechnung menschlich [--json]`. Dateinamen werden im UI zuerst im
Eingangsordner, dann im Ordner gesucht -- eine Rechnung laesst sich also
auch bearbeiten, wenn der Eingangsordner schon geleert ist. Beim
Bestaetigen (alle drei Pfade: Template, ohne Template, E-Rechnung) laeuft
erst das Lernen (§7i), dann `human.erledigt`: Kopie + Begleitdatei werden
entfernt, die Begleitdatei mit Bearbeiter, Zeitpunkt und korrigierten
Feldern (`shadow.corrected_fields`) unter `<ordner>/erledigt/<sha>.json`
abgelegt, Audit `menschlich_abgelegt`/`menschlich_erledigt`, Ereignis
`type: "menschlich"` (`aktion: abgelegt|erledigt`) in `events.jsonl`;
der Fehlerbericht zaehlt die Ablagen je Art und Lieferant (`menschlich`).

## 7v. Betriebsmodus Testphase -> Autonomie (2026-10-04) -- ergaenzt §7c/§9

`rechnung/modus.py`. Vorgabe des Auftraggebers: bewusst schlank, keine
strengen Zusatzsicherungen -- jede Rechnung wird in JobRouter ohnehin noch
einmal von einem Menschen freigegeben (zweite Freigabe als Sicherung).

- Modus programmweit in `<data>/betriebsmodus.json` (`testphase` Standard,
  `autonom`); ohne/mit kaputter Datei immer `testphase`.
- Umschalten ausschliesslich ueber `rechnung modus autonom|testphase
  --operator NAME [--grund TEXT]` (`modus.set_mode`, Operator Pflicht) mit
  Audit `betriebsmodus` (alt, neu, Grund, Richtwert erreicht?, Stand der
  Folge). Kein automatisches Umschalten. `GET /api/modus` ist nur lesend.
- `modus.fortschritt()` auf Basis des Schattenbetriebs (§7k, je Rechnung
  erste Entscheidung, spaetere JobRouter-Korrektur beruecksichtigt):
  sicher + ohne Korrektur verlaengert die Folge, eine Fehlentscheidung setzt
  sie zurueck, unsichere (zum Menschen) zaehlen nicht und unterbrechen
  nicht. Richtwert `RECHNUNG_TESTPHASE_IN_FOLGE` (50) und
  `RECHNUNG_TESTPHASE_LIEFERANTEN` (10 verschiedene Lieferanten in der
  Folge), kein Mindestzeitraum. Anzeige: Kopfzeile Training-UI,
  `rechnung modus`, Nachtbericht `betrieb` (inkl. offene Rechnungen im
  Ordner §7u).
- `shadow.decide` bekommt als 4. Bedingung "kein gelesenes Pflichtfeld unter
  0,7" -- damit sind "wuerde durchlassen" (Messung) und `human.gruende`
  (Ordner) dieselbe Grenze.
- `pipeline.dispatch` im Modus `autonom`: sichere Rechnung (`human.gruende`
  leer) -> `finalize(..., autonomous=True)` ohne Bestaetigungs-Klick,
  `shadow.record` mit Operator `autonom`. `finalize` laesst das nur zu,
  wenn der Modus `autonom` ist UND die Rechnung sicher ist (sonst
  `not_autonomous` bzw. `unsicher`); vermerkt `freigabe: autonom|mensch` in
  Journal, Audit, Ausgabe und JobRouter-Nutzlast (`approval`). Unsichere
  Rechnungen gehen in beiden Modi in den Ordner (§7u).
- Bekannt gewordene Fehler: `jobrouter.apply_feedback` wie §7q (Korrektur,
  Ereignisprotokoll, Schattenbetrieb, Neu-Lernen). `learn.learn_from_confirmed`
  akzeptiert dafuer neben dem Klick auch eine autonome Uebernahme
  (`store.seen_sha`).
- Optional, Standard aus: `RECHNUNG_AUTONOM_STICHPROBE` (0-100 %, deterministisch
  ueber den sha256 -- dieselbe Rechnung wird immer gleich entschieden; Grund
  `stichprobe` im Ordner) und `RECHNUNG_AUTONOM_RUECKFALL=1` (JobRouter-
  Korrektur im Modus `autonom` -> zurueck in `testphase`, Operator
  `jobrouter`, Audit mit Grund).
- IBAN-Abweichung von den Stammdaten (`RECHNUNG_IBAN_ABWEICHUNG`):
  `mensch` (Standard, wie §7l: zurueckgestellt) oder `warnen`: kein
  Rueckstellgrund, die rote Pruefung `iban-stammdaten` bleibt sichtbar,
  zaehlt aber weder fuer `human.gruende` noch fuer `shadow.decide`; dafuer
  `Result.warnings` ("ACHTUNG IBAN weicht von den Stammdaten ab: ... -- vor
  Zahlung pruefen!") -> Ausgabe `warnung`, Audit, JobRouter-Feld `warnung`
  (ueber das Mapping zuordenbar) + Liste `warnings`. Das IBAN-Feld bleibt die
  Stammdaten-IBAN.

## 7w. Laptop-Tauglichkeit: Windows, nur CPU (2026-10-04)

Vorgabe: zuerst Installation und Kalibrierung auf dem Windows-Firmenlaptop
des Auftraggebers (vermutlich ohne Grafikkarte, knappe Ressourcen), spaeter
ein interner Server. Gebaut wird direkt auf dem Laptop (Adminrechte
vorhanden), kein GitHub/CI.

- Pfade: Dateinamen aus Ergebnissen werden mit `\` und `/` zerlegt
  (`human.file_name`, genutzt von eventlog/shadow/UI); Training-UI-Liste
  ebenso. Sonst durchgehend `pathlib`.
- Zeitgrenze (§7m): Arbeitsprozess per `multiprocessing` "spawn" (unter
  Windows die einzige Startart), `freeze_support()` in `run.py`.
- Tesseract (§7n): `RECHNUNG_TESSERACT` > `PATH` > Windows-Standardpfade
  (`%ProgramFiles%`, `%ProgramFiles(x86)%`, `%LOCALAPPDATA%\Programs`);
  `CREATE_NO_WINDOW`, Ausgaben als UTF-8; `RECHNUNG_OCR_ENABLED=0` schaltet
  die Texterkennung ab (Grund "OCR ausgeschaltet" -> Ordner §7u).
- Lock-Datei (§7r): liegengebliebene Sperren (aelter als `lock.STALE_S`,
  6 h, nach Aenderungszeit) werden ignoriert bzw. vom naechsten Nachtlauf
  mit Warnung entfernt -- keine PID-Pruefung per `os.kill` (unter Windows
  wuerde das den Prozess beenden).
- Kodierung: Konsole/Logdatei mit `errors="replace"` (cp1252 bei
  Umleitung), CSV-Ausgaben und `/api/journal.csv` mit UTF-8-BOM (Excel).
- Windows-Skripte `windows/start-rechnung.cmd`, `windows/nachtlauf.cmd`
  (ASCII, CRLF, `.gitattributes`): `cd /d "%~dp0"`, `rechnung.env` per
  `for /f "eol=# ..."`, Daten `data\`, Eingang `eingang\` (=`RECHNUNG_INBOX`),
  Nachtlauf-Log `data\nacht.log`, optional `MIT_VERARBEITUNG=1`
  (fetch/scan/push vor `nacht`). Aufgabenplanung per `schtasks`.
- Leichtes Laptop-Profil am Ende von `rechnung.env.example` (kleines Modell
  `qwen2.5:1.5b-instruct`, `RECHNUNG_LLM_CTX=2048`, laengere Zeitgrenzen,
  OCR an/aus).
- Build: `build-windows.ps1` (Python pruefen/Hinweis, venv, Abhaengigkeiten,
  Tests, PyInstaller ueber `rechnung.spec`, Starttest, Programmordner
  `dist\Rechnung`; optional Tesseract mit `deu` + Testbild, Ollama + kleines
  Modell + Testanfrage mit Zeitmessung), Anleitung `INSTALL-WINDOWS.md`.
  **Nicht auf einem echten Windows-Rechner ausgefuehrt** (Entwicklung unter
  Linux) -- geprueft sind Syntax-nahe Eigenschaften (ASCII/CRLF, referenzierte
  Dateien, Spec kompilierbar) und das Windows-Verhalten des Python-Codes per
  Tests mit simuliertem Windows. `rechnung.spec` wurde unter Linux mit
  PyInstaller 6.22 gebaut und die Einzeldatei getestet (`modus`, `scan` mit
  spawn-Arbeitsprozess, `menschlich`, `nacht`, `web` inkl. neuem Reiter).

## 8. Offene Punkte für v1.1

- Template-Export/-Import zwischen Kollegen (eine `.zip` mit `data/`).
- CSV-Sammelexport über alle Rechnungen eines Laufs.
- Konfigurierbare Konfidenz-Schwelle je Feld statt global 0,7.
- Umgang mit mehrseitigen Rechnungen mit Seitenumbruch in der Betragstabelle.

## 9. Langfristige Perspektive

Die Automatisierung ohne manuellen Bestätigungsklick ist seit §7v als
Betriebsmodus `autonom` umgesetzt; der Richtwert (§7v) ist eine
konfigurierbare Orientierung, kein automatischer Schalter. Vor dem Einsatz
von `autonom` mit echten Rechnungen ist die IT erneut einzubinden -- der
Entwurf dafuer liegt in `IT-FREIGABE-ENTWURF-v2.md`; `IT-FREIGABE.md`
beschreibt weiterhin den bisherigen Stand.
