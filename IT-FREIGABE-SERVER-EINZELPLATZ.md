# IT-Freigabe „rechnung“ – Serverbetrieb als Einzelplatz per Remote (Entwurf)

**Status: Entwurf zur Abstimmung mit der IT.** Ergänzt `IT-FREIGABE-ENTWURF-v2.md`
(Abschnitt 11 „Späterer Serverbetrieb“) für das gewählte Betriebsmodell. Noch
nicht umgesetzt: siehe Abschnitt 8.

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
- Vorbereiten: Postfächer abholen, Rechnungen vorab lesen
- Nachtlauf: Rückmeldungen, Berichte, Sicherung
- optional Anlernen

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
| Software | Python 3.12 (eigene Umgebung im Programmordner), Tesseract (Texterkennung, deutsch), Microsoft Edge WebView2-Laufzeit, Git (nur bei Selbst-Update, siehe 6.) |
| Rechenleistung | Texterkennung von Scans ist der größte Verbraucher (auf dem Laptop teils Minuten je Rechnung). Größe vorab mit `rechnung messen` auf einer Test-VM ermitteln |
| Plattenplatz | Programm unter 1 GB (ohne lokales KI-Modell, das ausgeschaltet ist). Daten wachsen mit dem GoBD-Archiv (jede Rechnung als Original-PDF) und den Texterkennungs-Zwischenständen. Startklar-Check warnt unter 5 GB frei |
| Ablage | `C:\Rechnung\` (Programm `app\`, Daten `data\`, Eingang `eingang\`) auf einem lokalen Laufwerk des Servers |

## 4. Konten und geplante Aufgaben (Entscheidung der IT)

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

Weitere Zugangsdaten (JobRouter-Token, Langdock-Schlüssel, Mail für den
Lernbericht) stehen heute im Klartext in `C:\Rechnung\rechnung.env`.
Mindestens nötig sind NTFS-Rechte nur für das Programmkonto. Besser ist der
Geheimnisspeicher der IT, falls gewünscht.

Bisher werden die Aufgaben ohne Konto angelegt und laufen nur, solange der
Benutzer angemeldet ist. Das Einrichtungsskript erweitere ich, sobald das
Konto feststeht.

## 5. Netzwerk (nur ausgehend)

| Verbindung | Ziel / Port | Wann | Standard |
|---|---|---|---|
| IONOS-Postfächer | `imap.ionos.de:993` (IMAP/TLS) | Vorbereiten-Aufgabe, Abholung im Programm | an |
| JobRouter | JobRouter-Server der Firma (HTTPS) | Übergabe, Rückmeldungen im Nachtlauf | aus, bis angeschlossen |
| Programm-Updates | `ssh.github.com:443` (nur lesend) | nur bei Selbst-Update (siehe 6.) | – |
| Claude-Lehrer (Anlernphase) | Langdock (HTTPS) | Aufgabe „Rechnung Anlernen“ | nur falls auf dem Server weitergeführt |
| Rechnungen hinter Links | freigegebene Lieferanten-Domains `:443` | nur auf Befehl | **aus**, eigene Freigabe: `IT-FREIGABE-AENDERUNG-LINKABRUF.md` |

Eingehend: keine.

## 6. Updates (Entscheidung der IT)

- **Wie in der Testphase:** Das Programm holt neue Stände selbst aus dem
  privaten Repository. Der Schlüssel erlaubt nur Lesen und gilt nur für
  dieses Repository. Vor der Aktivierung müssen alle automatischen Tests
  bestehen, sonst bleibt der alte Stand aktiv. Jeder Versuch steht in
  `data\update.log`.
- **Oder kontrolliert:** Die IT spielt geprüfte Stände selbst ein, und das
  Selbst-Update wird abgeschaltet.

## 7. Sicherung, Überwachung, Aufbewahrung

- **Sicherung:** Das Programm legt täglich ein ZIP von `data\` an (die
  letzten 14, Ziel `RECHNUNG_SICHERUNG_ZIEL`). Zusätzlich sollte
  `C:\Rechnung\data` in die Serversicherung der IT. Liegt das ZIP-Ziel auf
  einer Netzfreigabe, braucht das Aufgabenkonto Schreibrechte dort.
- **Überwachung:** Den Nachtbericht (`data\berichte\`) und den
  Startklar-Check (Postfächer, JobRouter, Speicher, Sicherung, WebView2)
  kann die IT einsehen. Optional verschickt das Programm einen Bericht per
  Mail.
- **Aufbewahrung:** Originale bleiben unverändert im GoBD-Archiv
  (`data\archive\`). Lösch- und Aufbewahrungsfristen für Arbeitskopien
  (`menschlich\erledigt\`, `out\`, `ocr\`) legt die IT bzw. Buchhaltung
  fest.

## 8. Voraussetzungen vor dem Umzug (unabhängig vom Server)

1. **JobRouter-Anschluss:** Prozess, Schritt, Feldnamen, Zugangsdaten und
   Rückmeldeweg vom JobRouter-Administrator (Checkliste im README), dann ein
   Test gegen ein Testsystem. Bis dahin kommt im Zielsystem nichts an.
2. **Qualitätsnachweis:** Schattenbetrieb bis zum Richtwert (50 sichere
   Rechnungen in Folge, mindestens 10 Lieferanten) bzw. eine ausreichende
   Fehlerquote in den Stichproben.
3. **Konto-Modell (4.)** und **Update-Weg (6.)** festgelegt, danach
   Einrichtungsskript für den Server.
4. **Rückfall:** Bis der Server läuft, bleibt der Laptop bzw. die
   Erfassung über Abbyy der Plan B. Die Daten (`data\`) lassen sich als
   Ganzes umziehen.

## 9. Fragen an die IT

1. Welcher Server bzw. welche VM (Windows-Version, Kerne, Arbeitsspeicher,
   Platte)? Ist eine Test-VM für `rechnung messen` möglich?
2. Konto-Modell: Variante A (ein Programmkonto) oder B (Dienstkonto plus
   Personenkonten, mit Programmänderung)?
3. Wo sollen die Zugangsdaten liegen: in `rechnung.env` mit NTFS-Rechten
   oder im Geheimnisspeicher der IT?
4. Updates: Selbst-Update wie in der Testphase oder Einspielen durch die IT?
5. Dürfen die ausgehenden Verbindungen aus Abschnitt 5 direkt gehen, oder
   muss ein Proxy dazwischen?
6. Sicherung: Nimmt die IT `C:\Rechnung\data` in die Serversicherung, und wo
   soll das tägliche ZIP liegen?
7. Wer darf sich per Remotedesktop am Server anmelden?
