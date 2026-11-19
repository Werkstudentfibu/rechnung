# IT-Freigabe „rechnung“ – Serverbetrieb als Einzelplatz per Remote (Entwurf)

**Status: Entwurf zur Abstimmung mit der IT.** Ergänzt `IT-FREIGABE-ENTWURF-v2.md`
(Abschnitt 11 „Späterer Serverbetrieb“) für das gewählte Betriebsmodell. Noch
nicht umgesetzt: siehe Abschnitt 9.

## 1. Betriebsmodell

Das Programm läuft auf einem Windows-Server statt auf dem Laptop. Bedient
wird es **von einer Person zur Zeit per Remotedesktop** auf dem Server, im
Programmfenster wie bisher auf dem Laptop. Die Oberfläche bleibt nur auf dem
Server selbst erreichbar (`127.0.0.1`). Es gibt **keine Freigabe ins Netz,
keinen Browserzugriff von Arbeitsplätzen und keine eingehenden
Verbindungen**. Anmeldung, TLS und Rollen für die Oberfläche sind deshalb
nicht nötig. Die Anmeldung erfolgt über das Windows-Konto der
Remote-Sitzung.

Im Hintergrund laufen geplante Aufgaben, auch ohne angemeldeten Benutzer:
- Vorbereiten: Postfächer abholen, Rechnungen vorab lesen (Texterkennung
  und lokales Sprachmodell, siehe 4.)
- Nachtlauf: Rückmeldungen, Berichte, Sicherung

## 2. Gegenüber dem Laptop bereits umgesetzt

- **Nur ein Programmfenster je Datenordner.** Ein zweiter Start, etwa von
  einem zweiten Remote-Benutzer oder nach einer getrennten Sitzung, meldet,
  wer das Programm geöffnet hat, und startet nicht. Damit schreiben nie zwei
  Programmfenster auf dieselben Daten. Die Sperre wird laufend erneuert.
  Nach einem Absturz ist sie spätestens nach 2 Minuten frei, bei einem
  beendeten Prozess sofort.
- **Startklar-Check „Programmfenster (WebView2)“.** Das Programmfenster
  braucht die Microsoft-Edge-WebView2-Laufzeit, die auf Windows Server oft
  fehlt. Der Check meldet das. Ausweg ohne WebView2: die Oberfläche im
  Browser des Servers öffnen (`rechnung web`, ebenfalls nur `127.0.0.1`).

## 3. Anforderungen an den Server

| Punkt | Bedarf |
|---|---|
| Betriebssystem | Windows Server 2019/2022 (64 Bit) mit Remotedesktop |
| Software | Python 3.12 (eigene Umgebung im Programmordner), Tesseract (Texterkennung, deutsch), Ollama mit Sprachmodell (siehe 4.), Microsoft Edge WebView2-Laufzeit, Git (nur bei Selbst-Update, siehe 7.) |
| Rechenleistung | Größte Verbraucher sind die Texterkennung von Scans (auf dem Laptop teils Minuten je Rechnung) und das Sprachmodell (siehe 4.). Größe vorab mit `rechnung messen` auf einer Test-VM ermitteln |
| Plattenplatz | Programm unter 1 GB, dazu das Sprachmodell (1–9 GB je nach Größe, siehe 4.). Daten wachsen mit dem GoBD-Archiv (jede Rechnung als Original-PDF) und den Texterkennungs-Zwischenständen. Startklar-Check warnt unter 5 GB frei |
| Ablage | `C:\Rechnung\` (Programm `app\`, Daten `data\`, Eingang `eingang\`) auf einem lokalen Laufwerk des Servers |

## 4. Lokales Sprachmodell (Ollama)

**Wozu:** Das Sprachmodell liest Rechnungen von Lieferanten, für die es noch
keine Vorlage gibt, und gleicht bei bekannten Lieferanten die Werte der
Vorlage gegen. Es lernt selbst nicht dazu. Gelernt wird über Vorlagen je
Lieferant, und zwar nur aus Rechnungen, die ein Mensch bestätigt hat. Werte,
die nur das Modell geliefert hat, gelten immer als unsicher. Solche
Rechnungen gehen deshalb immer zur Prüfung an einen Menschen.

**Datenfluss:** Das Programm spricht das Modell nur über
`127.0.0.1:11434` auf demselben Server an. Im Betrieb verlassen keine
Rechnungstexte den Server. Ollama selbst hat keine Anmeldung. Deshalb darf
es nur auf `127.0.0.1` lauschen (Voreinstellung, `OLLAMA_HOST` nicht
ändern), und Port 11434 bleibt eingehend gesperrt.

**Betrieb ohne angemeldeten Benutzer:** Nach der normalen Installation
startet Ollama erst mit der Anmeldung eines Benutzers (Autostart-Eintrag
der Ollama-App). Die geplanten Aufgaben laufen aber auch ohne Anmeldung.
Deshalb soll auf dem Server nur der Ollama-Dienst laufen (`ollama serve`),
und zwar als Aufgabe beim Systemstart unter dem Programmkonto (siehe 5.).
Die Ollama-App gehört nicht auf den Server, weil sie laut Hersteller selbst
Updates von ollama.com lädt. Updates von Ollama spielt die IT ein. Die
Modelle liegen im Profil des Programmkontos (`.ollama\models`) oder per
`OLLAMA_MODELS` an einer festen Stelle auf dem lokalen Laufwerk. Ob Ollama
auf der gewählten Windows-Server-Version läuft, ist vorab auf der Test-VM
zu prüfen.

**Wenn Ollama nicht läuft,** liest das Programm ohne Sprachmodell weiter
(Vorlagen, Texterkennung, Rechenprüfungen). Der Startklar-Check meldet
„Ollama läuft nicht oder Modell fehlt“.

**Modellgröße (Entscheidung nach Messung):** Je größer das Modell, desto
besser liest es unbekannte Layouts, desto mehr Speicher und Rechenleistung
braucht es aber auch. Richtwerte:

| Größe | Beispiel | Datei | Speicherbedarf (Arbeitsspeicher bzw. Grafikspeicher) | Rechenleistung | Lizenz (laut Herausgeber) |
|---|---|---|---|---|---|
| klein | `qwen2.5:1.5b-instruct` | ca. 1 GB | ca. 3 GB | Prozessor reicht (wie auf dem Laptop) | Apache-2.0 |
| mittel | `qwen2.5:7b-instruct` | ca. 5 GB | ca. 8 GB | auf dem Prozessor möglich, aber langsam; Grafikkarte mit mind. 8 GB empfohlen | Apache-2.0 |
| groß | `qwen2.5:14b-instruct` | ca. 9 GB | ca. 12 GB | Grafikkarte mit mind. 12 GB | Apache-2.0 |

Das im Programm voreingestellte Modell `qwen2.5:3b-instruct` steht unter
einer abweichenden Lizenz des Herausgebers. Auf dem Server wird das Modell
deshalb ausdrücklich in `C:\Rechnung\rechnung.env` gesetzt
(`RECHNUNG_LLM_ENABLED=1`, `RECHNUNG_LLM_MODEL=…`). Vor der Nutzung prüft die
IT die Lizenz des gewählten Modells. Ausgewählt wird auf der Test-VM: Für
jedes Kandidaten-Modell liest `rechnung testsatz --blind` die bereits
bestätigten Rechnungen neu, so als wäre jeder Lieferant neu, und vergleicht
je Feld; `rechnung messen` misst die Zeit. Genommen wird das kleinste
Modell, das gut genug liest. Der Test läuft nur lokal und ändert weder
Rechnungen noch Vorlagen; er legt einen Bericht unter `data\berichte\` ab.

**Alternative:** ein Modellserver der IT im Firmennetz (OpenAI-kompatibel,
z. B. vLLM, `RECHNUNG_LLM_BACKEND=openai`). Dann gehen Rechnungstexte an
diesen Server. Ollama ist auf dem Rechnungsserver dann nicht nötig.

## 5. Konten und geplante Aufgaben (Entscheidung der IT)

Die Postfach-Passwörter speichert das Programm verschlüsselt mit Windows-DPAPI.
**Nur dasselbe Windows-Konto, das sie gespeichert hat, kann sie wieder
lesen.** Deshalb muss das Konto-Modell vor der Einrichtung feststehen:

- **Variante A (ohne Programmänderung):** ein eigenes Konto für das
  Programm, zum Beispiel `svc-rechnung`. Unter diesem Konto laufen sowohl die
  Remote-Sitzung als auch die geplanten Aufgaben, und zwar „unabhängig von
  der Benutzeranmeldung“ mit gespeichertem Kennwort.
- **Variante B (Programmänderung nötig):** Personenkonten für die
  Remote-Sitzung, ein Dienstkonto für die Aufgaben. Dann müssen die
  Passwörter maschinenweit verschlüsselt werden (DPAPI LocalMachine, lesbar
  für jedes Konto auf diesem Server) oder im Geheimnisspeicher der IT
  liegen.

Weitere Zugangsdaten (JobRouter-Token, Mail für den Lernbericht) stehen heute im Klartext in `C:\Rechnung\rechnung.env`.
Mindestens nötig sind NTFS-Rechte nur für das Programmkonto. Besser ist der
Geheimnisspeicher der IT, falls gewünscht.

Bisher werden die Aufgaben ohne Konto angelegt und laufen nur, solange der
Benutzer angemeldet ist. Das Einrichtungsskript erweitere ich, sobald das
Konto feststeht, dann auch um den Start von Ollama beim Systemstart (siehe 4.).

## 6. Netzwerk (nur ausgehend)

| Verbindung | Ziel / Port | Wann | Standard |
|---|---|---|---|
| IONOS-Postfächer | `imap.ionos.de:993` (IMAP/TLS) | Vorbereiten-Aufgabe, Abholung im Programm | an |
| JobRouter | JobRouter-Server der Firma (HTTPS) | Übergabe, Rückmeldungen im Nachtlauf | aus, bis angeschlossen |
| Lokales Sprachmodell | `127.0.0.1:11434`, nur auf dem Server selbst | Vorbereiten, Prüfen im Programm | aus, bis eingerichtet (siehe 4.) |
| Programm-Updates | `ssh.github.com:443` (nur lesend) | nur bei Selbst-Update (siehe 7.) | – |
| Ollama und Modell | Server von Ollama (ollama.com), HTTPS | nur bei Einrichtung und Updates durch die IT; entfällt, wenn die IT Programm und Modelldateien selbst auf den Server kopiert | – |
| Rechnungen hinter Links | freigegebene Lieferanten-Domains `:443` | nur auf Befehl | **aus**, eigene Freigabe: `IT-FREIGABE-AENDERUNG-LINKABRUF.md` |

Eingehend: keine. Eine Verbindung zu Cloud-KI-Anbietern gibt es nicht.

## 7. Updates (Entscheidung der IT)

- **Wie in der Testphase:** Das Programm holt neue Stände selbst aus dem
  privaten Repository. Der Schlüssel erlaubt nur Lesen und gilt nur für
  dieses Repository. Vor der Aktivierung müssen alle automatischen Tests
  bestehen, sonst bleibt der alte Stand aktiv. Jeder Versuch steht in
  `data\update.log`.
- **Oder kontrolliert:** Die IT spielt geprüfte Stände selbst ein, und das
  Selbst-Update wird abgeschaltet.

Ollama und das Sprachmodell aktualisiert in beiden Fällen die IT (siehe 4.).

## 8. Sicherung, Überwachung, Aufbewahrung

- **Sicherung:** Das Programm legt täglich ein ZIP von `data\` an (die
  letzten 14, Ziel `RECHNUNG_SICHERUNG_ZIEL`). Zusätzlich sollte
  `C:\Rechnung\data` in die Serversicherung der IT. Liegt das ZIP-Ziel auf
  einer Netzfreigabe, braucht das Aufgabenkonto Schreibrechte dort. Die
  Modelldateien müssen nicht gesichert werden, sie lassen sich neu laden.
- **Überwachung:** Den Nachtbericht (`data\berichte\`) und den
  Startklar-Check (Postfächer, JobRouter, Sprachmodell, Speicher, Sicherung,
  WebView2) kann die IT einsehen. Optional verschickt das Programm einen
  Bericht per Mail.
- **Aufbewahrung:** Originale bleiben unverändert im GoBD-Archiv
  (`data\archive\`). Lösch- und Aufbewahrungsfristen für Arbeitskopien
  (`menschlich\erledigt\`, `out\`, `ocr\`) legt die IT bzw. Buchhaltung
  fest.

## 9. Voraussetzungen vor dem Umzug (unabhängig vom Server)

1. **JobRouter-Anschluss:** Prozess, Schritt, Feldnamen, Zugangsdaten und
   Rückmeldeweg vom JobRouter-Administrator (Checkliste im README), dann ein
   Test gegen ein Testsystem. Bis dahin kommt im Zielsystem nichts an.
2. **Qualitätsnachweis:** Schattenbetrieb bis zum Richtwert (50 sichere
   Rechnungen in Folge, mindestens 10 Lieferanten) bzw. eine ausreichende
   Fehlerquote in den Stichproben.
3. **Konto-Modell (5.)**, **Update-Weg (7.)** und **Modellgröße (4.)**
   festgelegt, danach Einrichtungsskript für den Server.
4. **Rückfall:** Bis der Server läuft, bleibt der Laptop bzw. die
   Erfassung über Abbyy der Plan B. Die Daten (`data\`) lassen sich als
   Ganzes umziehen.

## 10. Fragen an die IT

1. Welcher Server bzw. welche VM (Windows-Version, Kerne, Arbeitsspeicher,
   Platte)? Ist eine Test-VM für `rechnung messen` möglich?
2. Konto-Modell: Variante A (ein Programmkonto) oder B (Dienstkonto plus
   Personenkonten, mit Programmänderung)?
3. Wo sollen die Zugangsdaten liegen: in `rechnung.env` mit NTFS-Rechten
   oder im Geheimnisspeicher der IT?
4. Updates: Selbst-Update wie in der Testphase oder Einspielen durch die IT?
5. Dürfen die ausgehenden Verbindungen aus Abschnitt 6 direkt gehen, oder
   muss ein Proxy dazwischen?
6. Sicherung: Nimmt die IT `C:\Rechnung\data` in die Serversicherung, und wo
   soll das tägliche ZIP liegen?
7. Wer darf sich per Remotedesktop am Server anmelden?
8. Sprachmodell: Bekommt der Server eine Grafikkarte, die Ollama unterstützt
   (z. B. NVIDIA, mindestens 8 GB Grafikspeicher; in einer VM nur mit
   durchgereichter Karte), oder läuft das Modell nur auf dem Prozessor?
   Davon hängt die Modellgröße ab (Abschnitt 4).
9. Darf Ollama als Aufgabe beim Systemstart unter dem Programmkonto laufen,
   und holt der Server Ollama und Modell selbst von ollama.com, oder bringt
   die IT beides auf den Server?
