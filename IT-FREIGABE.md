# Kurzbeschreibung zur IT-Freigabe: Rechnungsverarbeitung-Prototyp „rechnung"

*Allgemeine Erklärung zur Vorlage bei der IT · Stand: 2026-09-18*

**Wichtiger Hinweis vorab:** Das hier ist ausdrücklich erst ein **Konzept/Prototyp**, kein
fertiges Produkt. Ich habe es zunächst so gebaut und beschrieben, dass es auf meinem
**Firmen-Laptop** läuft, weil ich davon ausgegangen bin, dass das der naheliegendste Ort
dafür ist. Falls die IT eine andere Vorstellung hat, wo bzw. wie so etwas laufen sollte
(z. B. ein separater Rechner, ein Server, andere Rahmenbedingungen), bin ich dafür
komplett offen – das hier ist ein Vorschlag zur Diskussion, keine feste Festlegung.

## Worum geht es?

Ich habe ein eigenes, kleines Programm entwickelt, das eingehende PDF-Rechnungen liest
und die wichtigsten Felder (Rechnungsnummer, Datum, Beträge, IBAN, ggf. Positionen)
automatisch herausliest, statt das wie bisher komplett manuell in FlexiCapture zu prüfen.

**Zweck:** ein Eigenversuch, um zu testen, ob und wie gut sich ein Teil der Rechnungs-
verarbeitung durch ein selbst entwickeltes, vollständig einsehbares Werkzeug abdecken
lässt. Kein Ersatz für FlexiCapture zum jetzigen Zeitpunkt, sondern ein Test im
kleinen Rahmen, bevor über einen größeren Umbau überhaupt entschieden wird.

Ich möchte es auf meinem Firmen-Laptop laufen lassen, um damit die ersten echten
Rechnungen einzulesen und zu sehen, wie gut es in der Praxis funktioniert.

## Wie funktioniert es (in einfachen Worten)

1. Ich öffne eine Rechnung einmal in einer kleinen Bedienoberfläche, die als
   eigenständiges Programmfenster läuft (kein Browser-Tab, nur lokal auf meinem
   Rechner, nirgendwo sonst erreichbar) und markiere per Mausklick, wo
   Rechnungsnummer, Datum, Beträge etc. stehen.
2. Das Programm merkt sich das pro Lieferant. Kommt eine weitere Rechnung desselben
   Lieferanten, wendet es das gelernte Muster automatisch an.
3. Es prüft die Werte gegeneinander (Netto + USt = Brutto, IBAN-Prüfziffer, doppelte
   Rechnungsnummern) und zeigt mir das Ergebnis.
4. **Jede einzelne Rechnung muss ich manuell bestätigen** (und kann dabei jederzeit
   korrigieren), bevor irgendetwas als „fertig" gilt. Es gibt keinen Modus, in dem
   Rechnungen unbeaufsichtigt komplett durchlaufen.

In der Grundversion steckt kein trainiertes KI-Modell und keine Cloud-KI dahinter,
sondern feste Regeln und einfache Textvergleiche – nachvollziehbar und ohne Blackbox.

**Update, abhängig von der Hardware-Entscheidung:** Es gibt inzwischen einen
*optionalen*, standardmäßig ausgeschalteten Zusatzbaustein, der ein Feld gezielt dann
per lokalem Sprachmodell interpretieren lässt, wenn die feste Regel es nicht sicher
erkennt (z. B. bei einem Lieferanten mit leicht abweichendem Layout). Das Modell läuft
komplett lokal auf meinem Rechner (über die freie Software „Ollama", keine
Cloud-Anbindung, kein Internetzugriff) – nichts verlässt das Gerät. Ich schalte diesen
Baustein erst ein, sobald geklärt ist, welche Hardware dafür zur Verfügung steht; bis
dahin läuft der Test weiter mit der Grundversion ohne KI-Modell.

**Langfristige Perspektive** (nicht Teil dieses Tests): Wenn sich im Testbetrieb zeigt,
dass die Fehlerquote niedrig genug ist, ist das Ziel, schrittweise auf eine echte
Automatisierung ohne manuellen Klick hinzuarbeiten. Das ist ausdrücklich noch nicht
umgesetzt und würde – falls es so weit kommt – eine eigene, spätere Rücksprache mit der
IT brauchen, bevor Punkt 4 oben geändert wird.

## Sicherheits-relevante Eigenschaften

| Punkt | Status |
|---|---|
| Internet-/Cloud-Zugriff | **Keiner.** Das Programm braucht keine Netzwerkverbindung. (Eine optionale IONOS-Postfach-Abholung und JobRouter-Übergabe sind im Code vorbereitet, aber standardmäßig **ausgeschaltet** und für den jetzigen Testbetrieb nicht aktiv.) |
| Lokales Sprachmodell (optional) | **Standardmäßig ausgeschaltet.** Falls aktiviert: nur lokale Verbindung zu Ollama auf demselben Gerät (`127.0.0.1`), kein Cloud-Anbieter, keine Rechnungsdaten verlassen den Rechner. |
| Admin-/Installationsrechte | **Keine nötig.** Läuft als einzelne Programmdatei bzw. reiner Python-Code im Nutzerkontext, ohne Systemänderungen, ohne Registry-Einträge. |
| Datenspeicherort | Ausschließlich lokal auf meinem Rechner, in einem Ordner neben dem Programm. Keine Übertragung an Dritte. |
| Telemetrie / Auto-Update | Keine. |
| Authentifizierung | Nicht nötig (Einzelplatz-Werkzeug, keine Mehrbenutzer-/Servernutzung vorgesehen). |
| Quellcode | Vollständig einsehbar, ca. 3.000 Zeilen Python, keine Verschleierung. Kann jederzeit von der IT geprüft werden. |
| Protokollierung | Jede Aktion (wer/wann/was) wird nachvollziehbar mitgeschrieben; Original-PDFs werden unverändert archiviert (GoBD-relevant), Datendateien werden mit Sicherungskopie geschrieben. |
| Verwendete Bibliotheken | Ausschließlich etablierte Open-Source-Bibliotheken mit unkritischen Lizenzen (MIT, BSD, Apache-2.0) – siehe Tabelle unten. |

### Verwendete Bibliotheken

| Bibliothek | Zweck | Lizenz |
|---|---|---|
| pdfplumber | PDF-Text lesen | MIT |
| pypdfium2 | PDF rendern (Anzeige) | BSD-3 / Apache-2.0 |
| pillow | Bildverarbeitung (Anzeige) | HPND / MIT-CMU |
| python-dateutil | Datumsformate | Apache-2.0 / BSD |
| fastapi | lokale Bedienoberfläche | MIT |
| uvicorn | lokaler Webserver (nur `127.0.0.1`) | BSD-3 |
| jinja2 | Seitenvorlagen | BSD-3 |
| python-multipart | Formular-Uploads | Apache-2.0 |

## Verteilung / worum ich konkret bitte

- Erlaubnis, [entweder: die beiliegende einzelne Programmdatei (per PyInstaller erzeugt,
  keine Installation, kein Setup) auszuführen — ggf. mit einer Ausnahme im Virenscanner,
  da die Datei nicht kommerziell signiert ist / oder: den beiliegenden Python-Quellcode
  in einer eigenen virtuellen Umgebung laufen zu lassen].
- Keine Netzwerk-/Firewall-Freigabe nötig.
- Kein Zugriff auf Postfach oder JobRouter im jetzigen Testbetrieb – das kommt erst
  in einem späteren Schritt und nur mit eigener, expliziter Rücksprache.

Bei Rückfragen beantworte ich sie gerne, oder die vollständige technische Spezifikation
liegt im Anhang (`SPEC-v1.md`).
