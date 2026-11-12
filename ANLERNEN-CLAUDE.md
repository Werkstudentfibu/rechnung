# Anlernphase mit Claude als Lehrer

Statt jede Übungsrechnung von Hand zu prüfen, übernimmt Claude (Anthropic-API)
in der Anlernphase die Rolle des Prüfers (`rechnung/lehrer.py`).

## Einschalten

In `rechnung.env` ergänzen (Standard: aus):

```
RECHNUNG_CLAUDE_LEHRER=1
RECHNUNG_CLAUDE_API_KEY=sk-ant-...        # aus der Claude Console (Firmenkonto)
# optional:
# RECHNUNG_CLAUDE_MODELL=claude-sonnet-5-5
# RECHNUNG_CLAUDE_STICHPROBE=10           # % Kontrolle durch einen Menschen
# RECHNUNG_CLAUDE_TIMEOUT=120
# RECHNUNG_CLAUDE_LOKALE_KI=1          # lokale KI (Ollama) beim Anlernen mitlaufen lassen
```

### Über Langdock (EU-Hosting, Firmenvertrag)

Langdock bietet eine Anthropic-kompatible Schnittstelle. Dafür in
`rechnung.env`:

```
RECHNUNG_CLAUDE_BACKEND=api
RECHNUNG_CLAUDE_API_URL=https://api.langdock.com/anthropic/eu/v1/messages
RECHNUNG_CLAUDE_API_KEY=<Langdock-Schlüssel mit Berechtigung Completion API>
RECHNUNG_CLAUDE_MODELL=<Modellname laut Langdock-Modellliste>
```

Angemeldet wird bei Langdock-Adressen automatisch per `Authorization: Bearer`
(`RECHNUNG_CLAUDE_API_AUTH` erzwingt sonst `bearer` oder `x-api-key`).
Abgerechnet wird über das Langdock-Budget der Firma, nicht über das Abo.

### Alternativ: über Claude Code statt API-Schlüssel

Statt eines API-Schlüssels kann der Lehrer Claude Code (`claude -p`) benutzen.
Dann wird über das Claude-Abo des Logins abgerechnet, nicht nach Verbrauch.

```
RECHNUNG_CLAUDE_LEHRER=1
RECHNUNG_CLAUDE_BACKEND=cli
# optional, falls "claude" nicht im PATH liegt:
# RECHNUNG_CLAUDE_CLI=C:\Users\<name>\.local\bin\claude.exe
```

Voraussetzungen und Hinweise:

- Claude Code muss auf dem Laptop installiert und **mit dem Firmen-Login**
  angemeldet sein (einmal `claude` im Terminal starten und anmelden). Mit
  einem privaten Login landen die Firmenrechnungen im Privatkonto.
- Claude Code darf dabei nur Dateien lesen (`--allowedTools Read`), sonst nichts.
- Ein gesetzter `ANTHROPIC_API_KEY` wird bewusst nicht weitergegeben, damit
  wirklich über das Abo abgerechnet wird.
- Das Anlernen teilt sich die Nutzungslimits mit deinem eigenen Arbeiten mit
  Claude. Anthropic bezeichnet die Abo-Limits als für „gewöhnliche,
  individuelle Nutzung“ gedacht und empfiehlt für geteilte Produktions-
  Automatisierung einen API-Schlüssel; die Regeln für `claude -p` mit Abo
  können sich ändern. Ist das Limit erschöpft, bricht die Runde ab und die
  nächste Runde macht weiter, sobald wieder Kontingent da ist.

## Benutzen

```
python -m rechnung.lehrer --data <data-Ordner> <ordner-mit-uebungsrechnungen>
python -m rechnung.lehrer --data <data-Ordner> <ordner> --limit 10   # erst mal 10
python -m rechnung.lehrer --data <data-Ordner> <ordner> --minuten 50 # Zeitbudget
python -m rechnung.lehrer --data <data-Ordner> --bilanz              # Stand
```

`--data` ist derselbe Datenordner, den das Programm sonst nutzt (Vorlagen,
Lieferanten). Ohne Angabe: `RECHNUNG_DATA` bzw. `./data`.

## Automatisch im Hintergrund (Laptop, Arbeitszeit)

Einmalig in PowerShell (keine Adminrechte nötig):

```
powershell -ExecutionPolicy Bypass -File C:\Rechnung\app\windows\anlernen-einrichten.ps1
```

Das legt die Aufgabe „Rechnung Anlernen“ an: Montag bis Freitag ab 08:00,
neun Stunden lang alle 10 Minuten eine Runde (`windows\anlernen.cmd`), ohne
sichtbares Fenster, nur solange du angemeldet bist. Je Runde höchstens
`RECHNUNG_CLAUDE_LIMIT` Rechnungen (Standard 40) und höchstens
`RECHNUNG_CLAUDE_MINUTEN` Minuten (Standard 50; danach endet die Runde
zwischen zwei Rechnungen) aus `RECHNUNG_UEBUNG` (Standard
`C:\Rechnung\uebung`). Läuft eine Runde noch, fällt der nächste Start aus,
so arbeitet der Lehrer praktisch durch, solange etwas zu tun ist. Bereits
von Claude bestätigte Rechnungen werden übersprungen und kosten nichts.
Ohne `RECHNUNG_CLAUDE_LEHRER=1` tut die Aufgabe nichts; läuft gerade der
Nachtlauf, setzt sie eine Runde aus. Nach einer Änderung der Zeiten das
Einrichtungsskript erneut ausführen, erst dann gilt der neue Takt.

Protokoll: `C:\Rechnung\data\anlernen.log`. Sofort testen:
`Start-ScheduledTask -TaskName 'Rechnung Anlernen'`. Entfernen: dasselbe
Skript mit `-Entfernen`.

## Was passiert

Je Rechnung liest das Programm wie immer aus – nur **ohne die lokale KI**
(Ollama): Claude ist hier der Prüfer, und die lokale KI würde auf dem Laptop
je Rechnung bis zu zwei Minuten kosten, auch für Rechnungen, die danach nur
übersprungen werden. Der Schattenbetrieb misst in der Anlernphase also das
Programm ohne KI; `RECHNUNG_CLAUDE_LOKALE_KI=1` schaltet sie wieder dazu.
Claude bekommt das Original und
die Werte des Programms, nennt je Feld den richtigen Wert (mit „sicher ja/nein“),
und das Programm gleicht ab. Abweichungen werden als Korrektur gespeichert
(Operator `claude`, im Audit und im Fehlerbericht sichtbar), die Rechnung wird
neu gerechnet und – wenn danach alles sicher ist – von Claude bestätigt.
Daraus lernt Stufe 1 die Vorlage, der Schattenbetrieb misst wie bei einem
Menschen.

Zum Menschen (Ordner „Menschliche Bearbeitung“) geht die Rechnung stattdessen,
wenn

- Claude unsicher ist oder ein Wert von Claude **nicht im PDF-Text steht**
  (dieselbe Regel gegen Halluzination wie bei der lokalen KI),
- Claude bei einem Feld keinen Wert findet, das Programm aber schon,
- nach Claudes Korrekturen noch eine Prüfung rot ist,
- die Rechnung in der **Stichprobe** liegt: Claude prüft vor, ein Mensch
  kontrolliert und bestätigt. `--bilanz` zeigt, wie oft der Mensch Claude
  korrigieren musste.

Rechnungen, deren Lieferant unbekannt oder nur ein **Entwurf** ist, fasst
Claude standardmäßig nicht an: Stammdaten (z. B. die IBAN) gibt ein Mensch
frei. Sie erscheinen im Protokoll als „uebersprungen“ und werden automatisch
angelernt, sobald du den Lieferanten im Programm bestätigt hast.

**Übungsphase:** Mit `RECHNUNG_CLAUDE_STAMMDATEN=1` klärt Claude Entwürfe
selbst. Claude nennt den Lieferantennamen; steht er im PDF-Text und ist keine
eigene Gesellschaft, wird der Entwurf in den gleichnamigen bestehenden
Lieferanten übernommen oder, wenn es keinen gibt, bestätigt. Jeder so
behandelte Lieferant trägt einen Vermerk, `--bilanz` zählt sie. Vor dem
Wechsel auf „autonom“ siehst du sie durch (IBAN!) und nimmst den Schalter
wieder heraus.

Mit demselben Schalter prüft Claude außerdem:

- **Rechnungsadresse (Mandant):** Erkennt das Programm keine, nennt Claude
  den Rechnungsempfänger. Passt er zu genau einer Rechnungsadresse der
  Bibliothek, wird der Empfängertext dort als Suchbegriff ergänzt; ab dann
  erkennt das Programm den Mandanten selbst. Widerspricht der Postfach-
  Mandant, entscheidest du.
- **IBAN und USt-IdNr des Lieferanten:** Ungültige IBANs (falsche
  Prüfziffer) fliegen aus den Stammdaten. Claudes IBAN wird nur
  eingetragen, wenn der Lieferant danach keine gültige hat; eine zweite,
  abweichende IBAN entscheidest immer du (Betrugsschutz).

Ist Claude bei einem **freiwilligen Feld** (z. B. Kostenstelle,
Zahlungsreferenz) unsicher, bleibt der Wert des Programms stehen und die
Rechnung geht deswegen nicht an dich. Bei Pflichtfeldern (Nummer, Datum,
Beträge) bleibt es streng.

## Übung bleibt Übung

Claude bestätigt nur im eigenen Protokoll (`data/lehrer.jsonl`), nie im
Bestätigungs-Protokoll des Menschen. Für das Programm ist eine nur von Claude
geprüfte Rechnung damit **unbestätigt**: sie wird nicht archiviert, nicht ins
Journal geschrieben und nicht an JobRouter gegeben. Claude schaltet den Betriebsmodus
nie um; der Wechsel auf `autonom` bleibt ein bewusster Befehl
(`rechnung modus autonom --operator NAME`), wenn Schattenbetrieb und
Lehrer-Fehlerquote stimmen.

## Netzwerk und Daten

Ausgehend HTTPS zu `api.anthropic.com`, nur bei eingeschaltetem Lehrer. Dabei
wird die jeweilige Rechnung (PDF/Bild) an Anthropic übertragen. Claudes
Antworten werden je Rechnung unter `data/lehrer-cache/` gemerkt (jede Rechnung
kostet nur einmal), das Protokoll steht in `data/lehrer.jsonl`.
