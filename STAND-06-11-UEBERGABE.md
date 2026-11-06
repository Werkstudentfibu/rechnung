# Übergabe 06.11.2026 – Wechsel auf ein firmeninternes KI-Modell

Diese Datei ist für das KI-Modell geschrieben, das Aaron ab jetzt bei der Weiterentwicklung
dieses Rechnungsprogramms begleitet, anstelle von Emmy (Claude, über OpenClaw). Lies sie
zuerst, bevor du irgendetwas änderst – sie beschreibt Stand, Architektur und vor allem die
harten Leitplanken, die Aaron explizit gesetzt hat.

## Worum es geht

Firmenrechnungs-Automatisierung (Firma: Althoff Hotels, viele Einzelhotels als eigene
Mandanten/Geschäftsbereiche). Ziel: Rechnungen aus ~50 IONOS-Postfächern automatisch lesen,
Felder und Positionstabelle extrahieren, gegen eine gepflegte Lieferanten-Bibliothek abgleichen,
am Ende an JobRouter übergeben. **Aktuell: Trainingsphase** – Aaron bestätigt/korrigiert echte
Rechnungen auf seinem Firmenlaptop, das Programm lernt daraus Vorlagen je Lieferant. Es geht
nicht um Geschwindigkeit, sondern darum, dass die Erkennung möglichst richtig wird.

## Harte Leitplanken (nicht verhandelbar, von Aaron mehrfach bestätigt)

1. **Keine echten Rechnungsdaten nach außen.** Kein Agent/Modell außerhalb von Aarons Laptop
   bekommt echte PDFs, Lieferantennamen, Beträge, IBANs, Rechnungsnummern – auch nicht
   anonymisiert, ohne dass Aaron das explizit erlaubt. Entwicklung/Tests laufen ausschließlich
   mit `samples/` und synthetischen Daten (`rechnung/synth.py`). Bei Analysebedarf an echten
   Daten: gezielte Rückfrage an Aaron, nie selbst Daten anfordern oder erzeugen.
2. **Keine blinden Fixes.** Bei einem gemeldeten Fehler erst mit synthetischen Daten
   reproduzieren und belegen (ein Test, der ohne Fix rot ist), dann beheben. Vermutungen über
   die Ursache werden Aaron als Vermutung mitgeteilt, nicht als erledigt verkauft.
3. **`rechnung.env` auf dem Laptop nur per `Add-Content`, nie `-replace`/`Set-Content`.**
   Lange PowerShell-Zeilen aus einer Mail/einem Chat brechen beim Kopieren regelmäßig um –
   Befehle kurz halten (unter ~60 Zeichen je Zeile), Variable `$f`/`$g` für den Pfad vorweg
   setzen, bei `Select-String` das Suchmuster VOR dem Pfad übergeben.
4. **Zwei Kopien auf dem Laptop, noch nicht final aufgeräumt** (siehe unten) – vor jeder
   Diagnose prüfen, aus welchem Ordner das laufende Programm tatsächlich seine `rechnung.env`
   liest (`/api/invoice` bzw. die Stammdaten-Seite zeigen `daten`/`arbeitsordner` im Abschnitt
   „Programm“).
5. Commits/PRs: deutsche Commit-Messages, Co-Authored-By-Zeile je nach aktuellem Modell
   (bisher Claude). Nur nach grüner lokaler Testsuite UND grüner Windows-CI auf GitHub Actions
   auf `release` pushen. Feature-Arbeit auf eigenen Branches/Worktrees, nie direkt auf
   `release` committen.

## Architektur in Kürze

- **Backend**: FastAPI, Python, Repo-Wurzel `rechnung/` – wichtigste Module: `pipeline.py`
  (Haupt-Verarbeitung je Rechnung), `identify.py` (Lieferanten-Abgleich gegen Bibliothek),
  `template.py`/`learn.py` (Vorlagen lernen aus Bestätigungen), `ingest.py` (IMAP-Abholung),
  `abholung.py` (Hintergrund-Poller über mehrere Postfächer), `selbstupdate.py`
  (Git-Selbst-Update), `tabelle.py` (Positionstabelle per Rahmen-Markierung, KI-frei),
  `pflicht.py` (Pflichtangaben-Hinweis, nur Anzeige – mittlerweile aus der UI entfernt, Logik
  bleibt), `swissqr.py` (Schweizer QR-Zahlteil), `pdfium_sperre.py` (zentrale Sperre gegen
  pypdfium2-Threading-Abstürze – **jeder** neue pypdfium2-Aufruf muss da durch).
- **Frontend**: eine einzige Datei `rechnung/web/static/index.html` (Vanilla JS, kein Build-
  Schritt). Mehrere Hintergrund-Worker haben parallel daran gearbeitet – bei größeren Änderungen
  vorher den aktuellen Stand genau lesen, die Datei ist groß und dicht kommentiert.
- **Selbst-Update auf dem Laptop**: Programm läuft direkt aus einem Git-Klon (nicht als
  `.exe`), Branch `release`. Im Programm oben rechts „Update holen“ holt `origin/release`,
  lässt die Tests laufen, rollt bei Rot automatisch zurück (`selbstupdate.py`). Jeder Versuch
  steht in `data/update.log` und `data/update-status.json`.
- **Postfach-Abholung**: `RECHNUNG_POSTFACH_ORDNER` (Standard `INBOX`, bei Aaron aktuell
  `INBOX,Done,Error`) – viele Postfächer werden zusätzlich von **ABBYY FlexiCapture 12**
  bearbeitet, das Mails sofort nach `Done`/`Error`/`bak` verschiebt, `INBOX` ist dadurch meist
  leer. Das Programm liest diese Ordner **nur lesend** (markiert/verschiebt nichts), damit es
  FlexiCapture nicht stört. `RECHNUNG_IMAP_TAGE` (Standard 7) und `RECHNUNG_IMAP_NEUESTE`
  (Standard 0 = aus) steuern, wie weit zurück bzw. wie viele neueste Mails je Ordner geholt
  werden – für die Trainingsphase bewusst hochgestellt, siehe `rechnung.env.example`.
- **CI**: GitHub Actions, Windows-Runner (`tests-windows`, `texterkennung-windows`) – das
  Programm läuft bei Aaron auf Windows, Encoding-/Pfadtrenner-Fehler zeigen sich oft erst dort,
  nicht lokal unter Linux. Vor jedem Merge auf `release` beide Checks abwarten.

## Aktueller Stand (06.11.2026)

- `release` steht bei Commit `9de7e84`, Windows-CI grün, ~1124 Tests.
- Repo: GitHub `dollaaron0-del/rechnung` (privat), lokale Arbeitskopie meist unter
  `tasks/rechnungsautomatisierung/app` bzw. auf dem Laptop `C:\Rechnung\app`.
- Laptop-Doppelkopie **nicht final aufgeräumt**: `C:\Rechnung` (Zielkopie) und
  `Desktop\Rechnung` (ursprüngliche Testkopie) existieren beide, beide mit eigener
  `rechnung.env`. Beide wurden am 05.11. inhaltlich angeglichen, `RECHNUNG_EINGANG` in beiden
  fest eingetragen, die alte `C:\Rechnung\rechnung.env` liegt als `.bak-0511` gesichert daneben.
  **Noch offen**: `Desktop\Rechnung` umbenennen/entfernen, sodass es nur noch eine Kopie gibt –
  Aaron wollte das in Ruhe angehen, kein akuter Fehler mehr.
- Diese Session hat u. a. behoben: Selbst-Update-Zuverlässigkeit (Fetch-Wiederholung, gelbe
  Warnleiste), Postfach-/FlexiCapture-Ordner-Unterstützung, Rechnungsliste-Zwischenspeicher
  (vorher: Minuten Ladezeit nach Neustart bei vollem Eingang), mehrseitige Positionstabelle per
  Rahmen, Positionszeilen einzeln bestätigen/korrigieren, „Modus gründlich“ (voller KI-
  Gegencheck statt schnellem Pfad), einen SIGSEGV-Absturz bei paralleler PDF-Verarbeitung
  (pdfium_sperre.py), Bearbeitungsansicht aufgeräumt (Rechenprüfung als Ampel, Pflichtangaben
  raus, „Weitere Beträge“ markierbar, Positionsfelder breiter), sichtbaren Status „Lieferant aus
  Bibliothek erkannt/nicht erkannt“ samt Grund.

## Offene / unbestätigte Punkte – nicht einfach weitermachen, sondern mit Aaron klären

1. **Lieferanten-Erkennung, Teil C (Vorschlag, inaktiv)**: `rechnung/identify.py` hat einen
   neuen optionalen Parameter `einzelkandidat_namensschwelle` (Standard `None`). Ist er gesetzt
   UND bleibt nach der Adress-Einschränkung genau EIN Kandidat übrig UND steht sein Name als
   Teilstring im Rechnungstext, gilt für ihn eine niedrigere Schwelle als die globale
   `threshold` (0.5). **`pipeline.py` ruft die Funktion aktuell ohne dieses Argument auf –
   nichts davon ist in der echten Verarbeitung aktiv.** Hintergrund: Lieferanten aus dem Excel-
   Stammdatenimport haben oft nur Name (+ Adresse), kein IBAN/USt-IdNr/Fingerprint/Domain –
   ein reiner Namens-Teiltreffer bringt nur Score 0.2 und erreicht 0.5 nie, selbst wenn es gar
   keinen zweiten Kandidaten gibt. Mit synthetischen Daten belegt (siehe
   `tests/test_lieferant_einzelkandidat_vorschlag.py`,
   `tests/test_lieferant_namenserkennung.py`), aber **nicht geprüft, wie oft das bei Aarons
   echten ~50 Postfächern/Mandanten wirklich zutrifft**. Vor dem Scharfschalten: mit Aaron an
   echten Fällen bestätigen, dass es nicht zu Verwechslungen zwischen ähnlichen Firmen führt.
2. **Positionstabelle wird bei neuer Vorlage oft nicht gelernt**: `learn.learn_line_items`
   meldet bei synthetischen Testrechnungen „nicht reproduzierbar“, obwohl die reine Erkennung
   (ohne Vorlage) die Positionen findet. Ein Fix umgeht das (greift bei fehlender
   Tabellenspec trotzdem auf die freie Erkennung zurück, Commit `7950ef0`), die eigentliche
   Ursache in `learn_line_items` selbst ist **nicht untersucht**.
3. **KI-Positionserkennung mit dem echten Laptop-Modell ungetestet**: Auf dem Laptop läuft
   `qwen2.5:1.5b-instruct` über Ollama mit kurzem Zeitlimit, `RECHNUNG_LLM_LINE_ITEMS` ist
   standardmäßig aus. Scans/OCR-Pfad für die Tabellenerkennung wurden hier mangels Ollama/Scan-
   Umgebung nie gegen das echte Modell getestet.
4. **AMERON Neckarvillen Hotelgesellschaft mbH**: Postfach scheitert dauerhaft mit
   „authentication failed“ – das Passwort in der Excel-Stammdatenliste ist falsch, muss Aaron
   dort korrigieren und „Stammdaten übernehmen“ erneut laufen lassen.
5. Mehrere Klickwege in der Bearbeitungsansicht (Rahmen ziehen, Tabelle im PDF markieren,
   Variante/Adresse wechseln) konnten nur mit synthetischen Rechnungen, nicht an Aarons echten
   Daten durchgetestet werden – bei neuen Fehlermeldungen dazu zuerst mit synthetischen Daten
   nachstellen.

## Ausführliche Berichte

Unter `tasks/rechnungsautomatisierung/worker/bericht-*.md` liegen Abschlussberichte der
einzelnen Hintergrund-Worker dieser Session, mit genauen Vorher/Nachher-Vergleichen, Tests und
Screenshots – bei Bedarf dort nachlesen statt zu raten.

## Wie weiterarbeiten

- Pro Thema ein eigener Branch (`emmy/<thema>` oder `docs/<thema>`), lokale Testsuite grün,
  dann pushen und auf grüne Windows-CI (`gh run list -R dollaaron0-del/rechnung -b <branch>`)
  warten, erst danach nach `release` mergen.
- Aaron testet auf dem Laptop über „Update holen“ im Programm – kein manuelles Deployment nötig.
- Bei Rückfragen an Aaron: er ist meist mobil/per Kurznachricht erreichbar, lange PowerShell-
  Befehle immer einzeln und kurz, nie mehrzeilig als Block.
