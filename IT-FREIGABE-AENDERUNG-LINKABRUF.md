# Änderungsvorschlag IT-Freigabe (Entwurf v2) – Rechnungen hinter Links abrufen

**Status: vorbereitet, ausgeschaltet.** Die Funktion ist im Programm enthalten,
öffnet aber keinen einzigen Link, solange sie nicht ausdrücklich eingeschaltet
wird. Eingeschaltet wird erst nach Klärung mit der IT. Noch nicht in
`IT-FREIGABE-ENTWURF-v2.md` eingearbeitet.

## 1. Anlass

Einige Lieferanten schicken keine PDF im Anhang, sondern einen Link
(„Rechnung herunterladen“). Diese Mails landen heute im Ordner „Menschliche
Bearbeitung“, und die Rechnung wird von Hand über den Link geholt. Der
Befehl `rechnung link-mails` zählt, wie viele Mails das betrifft und welcher
Art die Links sind (direkter Download, Kundenportal mit Anmeldung, kein
Link). Er öffnet dabei keinen Link.

## 2. Was das Programm tun würde

Für eine Mail ohne Rechnungsanhang holt das Programm die PDF über den Link
und legt sie in den Eingang. Von dort läuft sie denselben Weg wie ein
Mailanhang. Die Mail selbst bleibt mit einem Vermerk beim Menschen, bis sie
von Hand erledigt wird.

**Es wird nie pauschal geöffnet.** Ein Link wird nur geholt, wenn *alle*
Bedingungen erfüllt sind:

1. Der Absender gehört zu einem **bestätigten Lieferanten** aus den
   Stammdaten (Mail-Domain). Entwürfe und unbekannte Absender sind
   ausgeschlossen.
2. Für **genau diesen Lieferanten** hat ein Mensch die Link-Domain
   ausdrücklich freigegeben (`rechnung link-freigabe`). Ohne Freigabe wird
   nichts geholt. Freigaben und Entzug stehen im Audit-Protokoll.
3. Der Link sieht nach einem **direkten PDF-Download** aus. Kundenportale
   mit Anmeldung werden nie angesteuert.
4. **HTTPS auf Port 443**, ohne Benutzername oder Passwort in der Adresse.
   Das Zertifikat muss zum Namen passen.
5. Der Name löst nur auf **öffentliche Adressen** auf, also nie ins
   Firmennetz, auf den eigenen Rechner oder auf Cloud-Metadaten-Adressen.
   Verbunden wird genau mit der geprüften Adresse, ein zweites Auflösen
   kann nicht umlenken.
6. **Weiterleitungen:** höchstens 3, und jede wird erneut nach 4. und 5.
   geprüft, einschließlich der Freigabe der Domain.
7. Die Antwort muss eine **PDF** sein (Inhaltstyp und Dateikennung
   `%PDF-`), höchstens **20 MB**, innerhalb von **20 Sekunden**. Alles
   andere wird verworfen, zum Beispiel HTML-Seiten oder Programmdateien.

Außerdem gilt:
- kein Browser, kein JavaScript, keine Cookies, keine Anmeldung, nur `GET`
- höchstens 20 Abrufe je Lauf
- keine Zugangsdaten werden gespeichert

## 3. Protokoll

Jeder Abruf (`link_abruf`) und jeder Fehlschlag (`link_abruf_fehler`) steht
in `data/audit.jsonl`, und zwar mit Lieferant, Domain, Größe und Prüfsumme
der PDF. Die vollständige Adresse wird **nicht** protokolliert, weil sie
einen Zugriffsschlüssel enthalten kann.

## 4. Ein- und Ausschalten

| Schritt | Wie |
|---|---|
| Probelauf (ohne Netz) | `rechnung link-abruf` zeigt bei ausgeschalteter Funktion nur, welche Mails geholt *würden* |
| Domain freigeben / entziehen | `rechnung link-freigabe --lieferant <ID> --domain <domain> [--entfernen]` |
| Einschalten | `RECHNUNG_LINK_ABRUF=an` in `rechnung.env` (nur genau „an“ schaltet ein) |
| Ausschalten | Zeile entfernen oder auf `aus` setzen |
| Wann wird abgerufen | nur auf Befehl `rechnung link-abruf`, kein automatischer Lauf (Nachtlauf o. Ä.) eingebaut |

## 5. Restrisiken

- **Gefälschter Absender:** Die Absender-Adresse einer Mail lässt sich
  fälschen. Deshalb reicht der Absender allein nicht: Der Link muss
  zusätzlich auf eine für diesen Lieferanten freigegebene Domain zeigen. Ein
  Angreifer bräuchte also eine Datei auf dem Server des Lieferanten. Als
  zusätzliche Sicherung kann das Programm verlangen, dass der Mailserver
  die Mail als echt bestätigt hat (DKIM/SPF im Kopf `Authentication-Results`,
  siehe Frage 3).
- **Schädliche PDF:** Das Risiko entspricht dem eines Mailanhangs, den das
  Programm schon heute verarbeitet. Die PDF wird nur gelesen, nicht
  ausgeführt. Für das Lesen gilt dieselbe Zeitgrenze je Rechnung wie für
  Anhänge. Im Stapellauf (`rechnung scan`, Nachtlauf) läuft es in einem
  eigenen Arbeitsprozess, der bei Hängern beendet wird. In der
  Bedienoberfläche läuft es in einem Hintergrund-Thread.
- **Lesebestätigung:** Der Abruf zeigt dem Lieferanten, dass der Link benutzt
  wurde. Das ist dasselbe, als hätte ein Mensch ihn angeklickt.
- **Einmal-Links:** Manche Links gelten nur einmal. Nach dem Abruf liegt die
  PDF im Eingang. Der Link selbst funktioniert danach eventuell nicht mehr.

## 6. Abschnitt 6, Tabelle – neue Zeile (erst nach Freigabe)

| Verbindung | Richtung | Ziel / Port | Wann | Standard |
|---|---|---|---|---|
| Rechnungen hinter Links | ausgehend | nur freigegebene Lieferanten-Domains, `:443` (HTTPS), direkte Verbindung | nur auf Befehl `rechnung link-abruf` | **aus** (`RECHNUNG_LINK_ABRUF`) |

## 7. Fragen an die IT

1. Ist der Abruf unter den Bedingungen in Abschnitt 2 grundsätzlich erlaubt?
2. Darf das Programm direkt ins Internet verbinden (Port 443), oder muss es
   über einen Firmen-Proxy gehen?
3. Soll zusätzlich verlangt werden, dass die Mail laut Mailserver echt ist
   (DKIM/SPF bestanden)? Das ist schnell eingebaut, setzt aber voraus, dass
   IONOS diese Prüfung in die Mail schreibt.
4. Wer darf Link-Domains freigeben: nur die Finanzbuchhaltung, oder mit
   Vier-Augen-Prinzip bzw. durch die IT?
5. Soll jede geholte PDF vor der Verarbeitung vom Virenscanner geprüft
   werden, zum Beispiel über einen Quarantäne-Ordner?
6. Reicht das Protokoll in Abschnitt 3, oder braucht die IT weitere Angaben?
