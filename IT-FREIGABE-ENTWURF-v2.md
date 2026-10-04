# IT-Freigabe „rechnung“ – Entwurf v2

*Entwurf zur Abstimmung mit der IT · Stand: 2026-10-04*
*Ersetzt nach Abstimmung die Kurzbeschreibung `IT-FREIGABE.md` (Stand 2026-09-18). Diese bleibt bis dahin unverändert gültig.*

## 1. Zweck dieses Dokuments

`IT-FREIGABE.md` beschreibt einen Einzelplatz-Prototyp ohne Netzwerkzugriff,
bei dem jede Rechnung von Hand bestätigt wird. Seitdem sind Funktionen
dazugekommen, die diese Beschreibung nicht mehr abdeckt (Postfach-Abholung,
Übergabe an JobRouter, Texterkennung, optionales lokales Sprachmodell,
unbeaufsichtigter Nachtlauf, autonomer Betriebsmodus). Dieses Dokument
beschreibt den neuen Stand vollständig und benennt, wozu eine Entscheidung
der IT benötigt wird (Abschnitt 13).

## 2. Änderungen gegenüber v1 (Überblick)

| Punkt | v1 (`IT-FREIGABE.md`) | v2 (dieser Entwurf) |
|---|---|---|
| Einsatzort | Firmen-Laptop | zunächst Firmen-Laptop (Windows), später interner Server (eigene Abstimmung, Abschnitt 11) |
| Netzwerk | keines | optional: IONOS-Postfach (IMAP, ausgehend), JobRouter (HTTPS, ausgehend); lokal: Bedienoberfläche und Ollama nur auf `127.0.0.1` |
| Texterkennung (OCR) | nicht vorhanden | Tesseract, lokal installiertes Programm, kein Netzwerk |
| Sprachmodell | optional, lokal (Ollama) | unverändert optional und standardmäßig aus; Laptop: kleines Modell auf CPU |
| Freigabe je Rechnung | immer Klick im Programm | Testphase: Klick im Programm. Modus „autonom“: sichere Rechnungen ohne Klick an JobRouter, dort Freigabe durch einen Menschen |
| Unsichere Rechnungen | Anzeige „review“ | eigener Ordner „Menschliche Bearbeitung“, nichts wird geraten |
| Zeitgesteuerter Lauf | keiner | optional: Windows-Aufgabenplanung („Nachtlauf“) |
| Vorab-Lernen | – | alte Rechnungen eines lokalen Ordners werden ausgewertet, um Vorlagen vorzuschlagen; aktiv erst nach Bestätigung durch einen Menschen; keine Übergabe an JobRouter |
| Installation | Einzeldatei, keine Adminrechte | Betrieb weiterhin ohne Adminrechte; Build und Installation von Tesseract/Ollama mit Adminrechten |

## 3. Einsatzszenario

**Phase A – Firmen-Laptop (jetzt):** Windows 10/11, ein Benutzer,
voraussichtlich ohne Grafikkarte. Das Programm wird auf dem Laptop selbst aus
dem Quellcode gebaut (`build-windows.ps1`, Anleitung `INSTALL-WINDOWS.md`) und
liegt danach als Einzeldatei `rechnung.exe` mit Startskripten in
`C:\Rechnung`. Ziel dieser Phase: Kalibrierung mit echten Rechnungen in der
Testphase.

**Phase B – interner Server (später):** Betrieb auf einem Server der Firma.
Erfordert eine eigene Freigaberunde; die absehbaren Punkte stehen in
Abschnitt 11.

## 4. Funktionsweise

1. **Eingang:** Rechnungen (PDF, E-Rechnung XRechnung/ZUGFeRD, Scans als
   PDF/Bild) liegen in einem Eingangsordner – manuell abgelegt oder optional
   aus einem IONOS-Postfach abgeholt.
2. **Auslesen:** in fester Reihenfolge: E-Rechnung (Werte aus dem XML) →
   gelernte Vorlage je Lieferant → optional lokales Sprachmodell. Scans
   laufen vorher durch die Texterkennung. Lieferant und Rechnungsadresse
   werden ausschließlich gegen die gepflegten Stammdaten abgeglichen.
3. **Prüfen:** Netto + USt = Brutto, IBAN-Prüfziffer, IBAN gegen
   Stammdaten, Datum plausibel, Dublette, weitere Summenprüfungen.
4. **Entscheiden:**
   - Ist das Programm bei einer Rechnung nicht sicher (Lieferant/Adresse
     nicht eindeutig, IBAN weicht ab, Pflichtfeld fehlt, Prüfung rot,
     Texterkennung unsicher, Wert mit niedriger Konfidenz, Zeitgrenze
     überschritten), wird die Rechnung als Kopie in den Ordner
     **„Menschliche Bearbeitung“** gelegt, mit einer Begleitdatei (Gründe,
     betroffene Felder, Vorschlagswerte mit Herkunft). Es wird kein Wert
     geraten.
   - Sichere Rechnungen: in der **Testphase** bestätigt ein Mensch jede
     Rechnung im Programm; im Modus **autonom** gehen sie ohne Klick in die
     Warteschlange für JobRouter.
5. **Lernen:** Aus jeder vom Menschen bearbeiteten und bestätigten Rechnung
   lernt das Programm die Vorlage des Lieferanten (nur wenn die Vorlage die
   bestätigten Werte exakt reproduziert). Zusätzlich können **alte
   Rechnungen** aus einem lokalen Ordner vorab ausgewertet werden
   („Vorlernen“): das Programm schlägt je Lieferant/Layout eine Vorlage vor,
   ein Mensch prüft alle Werte der Gruppe und bestätigt; erst dann ist die
   Vorlage aktiv. Diese Alt-Rechnungen werden nicht an JobRouter übergeben
   und nicht als Live-Rechnungen gezählt.
6. **Übergabe:** an JobRouter (REST, mit Originaldatei als Anhang). In
   JobRouter gibt ein Mensch die Rechnung frei; Korrekturen von dort
   werden zurückgelesen und fließen in Fehlerprotokoll und Lernen ein.
7. **Ressourcen:** ausgelegt auf einen Rechner ohne Grafikkarte. Das
   Sprachmodell wird nur für fehlende Pflichtfelder bzw. unbekannte Layouts
   gefragt, mit gekürztem Text; Texterkennung nur für Seiten ohne
   Textebene, Anzahl der Prozessorkerne begrenzbar (`RECHNUNG_OCR_JOBS`).
   Das Modell wird nach einem Stapel aus dem Arbeitsspeicher entladen.
   Zeitgrenze je Rechnung 120 s.

## 5. Betriebsmodi: Testphase und autonom

| | Testphase (Standard) | autonom |
|---|---|---|
| Sichere Rechnung | Bestätigungsklick im Programm, dann JobRouter | ohne Klick an JobRouter |
| Unsichere Rechnung | Ordner „Menschliche Bearbeitung“ | Ordner „Menschliche Bearbeitung“ |
| Freigabe in JobRouter | durch einen Menschen | durch einen Menschen |
| Kennzeichnung im Datensatz | `freigabe: mensch` | `freigabe: autonom` |

- **Umschalten** ausschließlich per Befehl mit Namensangabe
  (`rechnung modus autonom --operator NAME`), jeder Wechsel wird mit Name,
  Zeitpunkt und Stand der Messung im Audit-Protokoll festgehalten. Es gibt
  kein automatisches Umschalten.
- **Richtwert** für den Wechsel (Anzeige, keine Automatik): 50 sichere
  Rechnungen in Folge ohne Korrektur von mindestens 10 verschiedenen
  Lieferanten. Gemessen wird im Schattenbetrieb: für jede bestätigte
  Rechnung wird festgehalten, ob das Programm sie allein durchgelassen hätte
  und ob der Mensch etwas korrigieren musste.
- **Sicherung im autonomen Modus** ist die zweite, menschliche Freigabe in
  JobRouter. Auf weitere Zusatzsicherungen wurde auf Wunsch des
  Auftraggebers verzichtet; zwei optionale Einstellungen sind vorhanden und
  standardmäßig aus: eine Stichprobe (Anteil sicherer Rechnungen, die
  trotzdem zum Menschen gehen) und ein automatischer Rückfall in die
  Testphase bei einer Korrektur aus JobRouter.
- **Abweichende IBAN** (Rechnung nennt eine IBAN, die nicht in den
  Stammdaten steht): Standard ist „zum Menschen“. Alternativ einstellbar:
  durchlassen mit deutlichem Warnfeld im JobRouter-Datensatz; als
  Zahlungs-IBAN bleibt in beiden Fällen die Stammdaten-IBAN eingetragen.
  **Empfehlung:** Standard beibehalten (Schutz gegen Rechnungsbetrug mit
  geänderter Bankverbindung).

## 6. Netzwerkverbindungen

| Verbindung | Richtung | Ziel / Port | Wann | Standard |
|---|---|---|---|---|
| Bedienoberfläche | lokal | `127.0.0.1:8000` (HTTP), nur dieser Rechner | während das Programm offen ist | an |
| IONOS-Postfach | ausgehend | `imap.ionos.de:993` (IMAP über TLS) | Abholung (`rechnung fetch`), Gesundheitscheck im Nachtlauf | **aus** (ohne Zugangsdaten keine Verbindung) |
| JobRouter | ausgehend | JobRouter-Server der Firma (HTTPS, REST) | Übergabe (`rechnung push`), Abruf von Rückmeldungen im Nachtlauf | **aus** (`RECHNUNG_JOBROUTER_ENABLED=0`, nur Trockenlauf) |
| Ollama (Sprachmodell) | lokal | `127.0.0.1:11434` | nur wenn die KI eingeschaltet ist | **aus** |
| Installation | ausgehend | pypi.org, python.org/winget, github.com (Tesseract-Sprachdaten), ollama.com (Programm und Modell) | nur beim Build/Installieren | – |

Es gibt keine Telemetrie, keine automatischen Updates und keine
Verbindung zu Cloud-KI-Anbietern. Rechnungsinhalte verlassen den Rechner
nur in Richtung des firmeneigenen JobRouter (und nur, wenn eingeschaltet).

Hinweis: Das Programm unterstützt alternativ einen OpenAI-kompatiblen
Modellserver im Firmennetz (`RECHNUNG_LLM_BACKEND=openai`). Dann gehen
Rechnungstexte an diesen Server. Für Phase A ist das nicht vorgesehen.

## 7. Komponenten und Lizenzen

**Programm:** Python-Quellcode (rund 8.000 Zeilen), vollständig einsehbar.
Auslieferung als Einzeldatei, gebaut mit PyInstaller (GPL-2.0 mit Ausnahme
für die Weitergabe gebauter Programme; nur Build-Werkzeug).

| Bibliothek | Zweck | Lizenz |
|---|---|---|
| pdfplumber / pdfminer.six | PDF-Text lesen | MIT |
| pypdfium2 | PDF rendern | BSD-3 / Apache-2.0 |
| pillow | Bildverarbeitung | MIT-CMU (HPND) |
| python-dateutil | Datumsformate | Apache-2.0 / BSD |
| fastapi, uvicorn, jinja2, python-multipart | lokale Bedienoberfläche | MIT / BSD-3 / BSD-3 / Apache-2.0 |
| pywebview | eigenes Programmfenster (Edge WebView2) | BSD-3 |
| pythonnet | Brücke zu WebView2 unter Windows | MIT |

**Zusätzliche Programme (separat installiert, optional):**

| Programm | Zweck | Lizenz | Hinweise |
|---|---|---|---|
| Tesseract OCR (Build UB Mannheim) mit deutschem Sprachpaket | Texterkennung für Scans | Apache-2.0 | installiert unter `C:\Program Files\Tesseract-OCR`, Adminrechte nötig, kein Netzwerk |
| Ollama | lokale Ausführung des Sprachmodells | MIT | Installation im Benutzerprofil; richtet einen Autostart-Eintrag ein und lauscht nur auf `127.0.0.1:11434` |
| Modell `qwen2.5:1.5b-instruct` | Sprachmodell für unbekannte Layouts | Apache-2.0 (laut Herausgeber) | ca. 1 GB; das im Programm voreingestellte größere Modell `qwen2.5:3b-instruct` steht unter einer abweichenden Lizenz des Herausgebers und sollte vor einer Nutzung geprüft werden |

## 8. Installation und Rechte

- **Build/Installation (einmalig, mit Adminrechten):** Python 3.12,
  Abhängigkeiten in einer eigenen virtuellen Umgebung im Projektordner,
  automatische Tests, Bau der `rechnung.exe`; optional Tesseract und Ollama.
  Alle Schritte und Fehlermeldungen werden in `build-windows.log`
  protokolliert.
- **Betrieb (ohne Adminrechte):** Programmordner `C:\Rechnung` mit
  `rechnung.exe`, `start-rechnung.cmd`, `nachtlauf.cmd`, Einstellungsdatei
  `rechnung.env`, Datenordner `data\`, Eingangsordner `eingang\`. Keine
  Registry-Einträge, kein Dienst.
- **Nachtlauf (optional):** geplante Aufgabe im Benutzerkontext
  (`schtasks`, täglich 2:30 Uhr). Läuft nur, wenn der Benutzer angemeldet
  ist. Er holt Rückmeldungen aus JobRouter, schreibt Berichte, prüft
  Postfach-Erreichbarkeit, Warteschlange und Plattenplatz; auf Wunsch
  zusätzlich Abholung, Verarbeitung und Übergabe. Damit finden – sobald
  IONOS/JobRouter eingeschaltet sind – Netzwerkzugriffe ohne anwesenden
  Benutzer statt.
- **Virenscanner:** Die selbst gebaute, nicht signierte `rechnung.exe` kann
  von Microsoft Defender/SmartScreen gemeldet werden. Optionen: Ausnahme für
  den Ordner oder Signatur mit dem Code-Signing-Zertifikat der Firma.

## 9. Daten und Protokollierung

Alle Daten liegen in `C:\Rechnung\data` (keine Datenbank, Dateien im
JSON-/CSV-Format):

| Inhalt | Ablage |
|---|---|
| Lieferanten-Stammdaten, Rechnungsadressen, gelernte Vorlagen | `suppliers.json`, `addresses.json`, `templates\` |
| Vorlernen: Gruppen-Vorschläge mit gelesenen Werten alter Rechnungen (Pfad zur Datei, keine Kopie) | `vorlernen\` |
| Originale unverändert (GoBD) | `archive\<Prüfsumme>.pdf/.xml` |
| Audit-Protokoll (wer/wann/was, inkl. Moduswechsel), nur anhängend | `audit.jsonl` |
| Ereignisprotokoll je Verarbeitung, Korrekturen, Ablagen in „Menschliche Bearbeitung“ | `events.jsonl` |
| Bestätigungen, Schattenbetrieb | `confirmations.jsonl`, `shadow.jsonl` |
| Unsichere Rechnungen (Kopie + Begleitdatei), erledigte mit Bearbeitervermerk | `menschlich\`, `menschlich\erledigt\` |
| JobRouter-Warteschlange, Ergebnisse, Nachtberichte | `queue\`, `out\`, `berichte\` |

Rechnungen enthalten personenbezogene Daten (Ansprechpartner, ggf.
Einzelunternehmer) und Bankverbindungen. **Empfehlungen:** Laufwerks-
verschlüsselung (BitLocker) auf dem Laptop, regelmäßige Sicherung von
`C:\Rechnung\data`, Festlegung einer Aufbewahrungs-/Löschfrist für Kopien
außerhalb des GoBD-Archivs (`menschlich\erledigt\`, `out\`, `ocr\`).

## 10. Zugangsdaten

Postfach-Passwort und JobRouter-Token stehen im Klartext in
`C:\Rechnung\rechnung.env` (wie bei Konfigurationsdateien üblich; die Datei
ist von der Versionsverwaltung ausgeschlossen). **Empfehlungen:**

- eigenes Postfach bzw. eigenes JobRouter-Konto nur für dieses Programm,
  mit minimalen Rechten (JobRouter: nur Anlegen im vorgesehenen Prozess-
  schritt und Lesen der Rückmeldungen);
- bei IONOS ein eigenes Passwort für diesen Zugang;
- NTFS-Rechte auf `rechnung.env` nur für den ausführenden Benutzer;
- für den Serverbetrieb: Ablage in einem Passwort-/Geheimnisspeicher der IT.

## 11. Späterer Serverbetrieb (Phase B) – absehbare Punkte

Nicht Teil dieser Freigabe; wird gesondert vorgelegt. Absehbar nötig:

- **Dienstkonto** statt Benutzerkonto; Nachtlauf und Verarbeitung als
  geplante Aufgabe bzw. Dienst unter diesem Konto.
- **Bedienoberfläche:** heute ohne Anmeldung, weil nur auf `127.0.0.1`
  erreichbar. Für Zugriff von Arbeitsplätzen sind Anmeldung, TLS und
  Rollen nötig – das ist noch nicht umgesetzt.
- **Mehrbenutzerbetrieb:** gleichzeitige Bearbeitung derselben Rechnung ist
  heute nicht abgesichert (Einzelplatzannahme).
- **Speicherort und Sicherung** der Daten auf Serverlaufwerken,
  Überwachung (Nachtbericht, Logdatei) durch die IT.
- **Sprachmodell:** auf dem Server ggf. größer bzw. mit Grafikkarte, lokal
  per Ollama oder als OpenAI-kompatibler Modellserver im Firmennetz.
- **Firewall-Freigaben:** ausgehend IMAP/TLS zu IONOS, HTTPS zu JobRouter;
  eingehend nur, falls die Oberfläche im Netz erreichbar sein soll.

## 12. Stand der Erprobung und bekannte Grenzen

- IONOS-Abholung und JobRouter-Übergabe/-Rückmeldung sind implementiert,
  aber **noch nicht gegen die echten Systeme getestet**; die JobRouter-
  Prozessdaten und der Rückmeldeweg kommen vom JobRouter-Administrator.
  Bis dahin bleibt die Übergabe ein Trockenlauf.
- Das Build-Skript für Windows ist geschrieben, aber **noch nicht auf dem
  Laptop ausgeführt**; die Erstinstallation ist zugleich der erste Test.
- Der Richtwert für den Wechsel in den autonomen Modus ist eine Schätzung
  ohne Betriebsdaten und konfigurierbar.
- Das Sprachmodell auf einem Laptop ohne Grafikkarte ist langsam (Sekunden
  bis Minuten je Rechnung; gemessen bisher nur auf dem Entwicklungsserver mit
  synthetischen Rechnungen – auf dem Laptop mit `rechnung messen` zu
  ermitteln); seine Werte gelten in der Standardeinstellung
  als unsicher (Konfidenz gedeckelt) und führen allein nie zu einer
  autonomen Übergabe.

## 13. Worum wir die IT bitten

1. Zustimmung zum Betrieb in Phase A auf dem Firmen-Laptop wie in den
   Abschnitten 3–10 beschrieben.
2. Freigabe der Installation von Python 3.12 und optional Tesseract und
   Ollama (mit kleinem Modell) auf dem Laptop.
3. Umgang mit der nicht signierten `rechnung.exe`: Virenscanner-Ausnahme
   für `C:\Rechnung` bzw. `C:\rechnung-build\dist` oder Signatur mit dem
   Firmenzertifikat.
4. Freigabe der ausgehenden Verbindungen zu IONOS (IMAP/TLS, Port 993) und
   zum JobRouter-Server (HTTPS), sobald die Zugangsdaten vorliegen; dazu ein
   eigenes Postfach-/JobRouter-Konto mit minimalen Rechten.
5. Zustimmung zum zeitgesteuerten Nachtlauf im Benutzerkontext.
6. Zustimmung zum autonomen Betriebsmodus mit der Freigabe in JobRouter als
   zweiter, menschlicher Kontrolle – einschließlich der Entscheidung zur
   Behandlung abweichender IBANs (Empfehlung: Standard „zum Menschen“).
7. Vorgaben zu Verschlüsselung, Sicherung und Aufbewahrungsfristen
   (Abschnitt 9).

Die technische Spezifikation (`SPEC-v1.md`), die Installationsanleitung
(`INSTALL-WINDOWS.md`) und der Quellcode liegen bei.
