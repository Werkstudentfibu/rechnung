# Änderung IT-Freigabe – Anlernphase mit Claude (Cloud-KI)

*Stand: 2026-11-06 · ergänzt `IT-FREIGABE-ENTWURF-v2.md` (Abschnitte 2, 4, 6, 7, 13)
und ersetzt die Aussage „keine Cloud-KI“ in `IT-FREIGABE.md`.*

**Entscheidung:** Aaron hat am 06.11.2026 entschieden, dass die bisherige Regel
„keine Verbindung zu Cloud-KI-Anbietern“ für die Anlernphase nicht mehr gilt. Die
Freigabe-Dokumente werden hiermit an die neue Lage angepasst. Ob die IT dafür eine
eigene Zustimmung verlangt, ist offen (Abschnitt 6 unten).

## 1. Was sich ändert

In der **Anlernphase** prüft nicht mehr ein Mensch jede Übungsrechnung, sondern
Claude (Anthropic) übernimmt die Rolle des Prüfers (`rechnung/lehrer.py`,
Anleitung `ANLERNEN-CLAUDE.md`). Dazu wird die Rechnung an den Dienst von
Anthropic übertragen. Das ist die erste und einzige Stelle, an der
Rechnungsinhalte den Rechner in Richtung eines externen KI-Anbieters verlassen.

Alles andere bleibt wie in `IT-FREIGABE-ENTWURF-v2.md` beschrieben: Auslesen,
Prüfen und Lernen laufen lokal; das lokale Sprachmodell (Ollama) bleibt davon
unberührt.

## 2. Was genau übertragen wird

| Inhalt | Übertragung |
|---|---|
| Die Übungsrechnung als Datei (PDF bzw. Bild, vollständig, inkl. aller personenbezogenen Daten und Bankverbindungen darauf) | ja, je Rechnung einmal |
| Die vom Programm ausgelesenen Werte (Rechnungsnummer, Datum, Beträge, IBAN …) als Vergleichsgrundlage | ja |
| Ein fester Auftragstext („gib je Feld den Wert an, der tatsächlich auf der Rechnung steht; erfinde nichts“) | ja |
| Lieferanten-Stammdaten, andere Rechnungen, Journal, Archiv, Einstellungen, Zugangsdaten | **nein** |

Zurück kommt nur eine kurze JSON-Antwort mit den Feldwerten und einer
Sicher/Unsicher-Angabe je Feld.

**Welche Rechnungen:** nur Dateien aus dem Übungsordner (`RECHNUNG_UEBUNG`,
derzeit `C:\Rechnung\app\data\menschlich`, also die Kopien echter Rechnungen,
bei denen das Programm unsicher war), höchstens 10 je Runde. Rechnungen mit
unbekanntem oder noch nicht bestätigtem Lieferanten werden **nicht**
übertragen (Stammdaten entscheidet immer ein Mensch). Bereits von Claude
geprüfte Rechnungen werden nicht erneut geschickt.

## 3. Übertragungsweg

Das Programm kennt zwei Wege (`RECHNUNG_CLAUDE_BACKEND`); auf dem Laptop ist
**`cli`** eingestellt:

| | `cli` (aktiv) | `api` (Alternative) |
|---|---|---|
| Programm | Claude Code (`claude.exe`, Anthropic), installiert im Benutzerprofil `C:\Users\<name>\.local\bin`, keine Adminrechte | keines; Aufruf aus dem Programm heraus (Python-Standardbibliothek) |
| Anmeldung / Abrechnung | Firmen-Login des Benutzers (Abo), kein API-Schlüssel; ein ggf. gesetzter `ANTHROPIC_API_KEY` wird bewusst nicht weitergegeben | API-Schlüssel aus der Claude Console (Firmenkonto), Abrechnung nach Verbrauch |
| Ziel | `api.anthropic.com` (HTTPS, ausgehend). Claude Code baut daneben eigene Verbindungen zu Anthropic auf (Anmeldung, Versionsprüfung, Nutzungsstatistik); nicht notwendige Verbindungen lassen sich mit `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` abschalten | nur `api.anthropic.com` (HTTPS, ausgehend) |
| Rechte | Claude Code darf ausschließlich Dateien **lesen** (`--allowedTools Read`), höchstens 4 Schritte, Arbeitsordner = Übungsordner. Kein Schreiben, keine Befehle, kein Netz außer Anthropic | – |
| Modell | Voreinstellung von Claude Code (derzeit Claude Opus 5.5), einstellbar über `RECHNUNG_CLAUDE_MODELL` | wie links |

## 4. Wann

Windows-Aufgabe „Rechnung Anlernen“ (`windows\anlernen-einrichten.ps1`) im
Benutzerkontext: Montag bis Freitag ab 08:00 Uhr, 9 Stunden lang alle 30
Minuten eine Runde, ohne Fenster. Während ein Nachtlauf läuft, setzt die Runde
aus. Außerdem auf Knopfdruck (`python -m rechnung.lehrer …`). Ohne
`RECHNUNG_CLAUDE_LEHRER=1` in `rechnung.env` passiert nichts; nach Abschluss
der Anlernphase wird der Eintrag wieder entfernt, dann gibt es keine
Verbindung zu Anthropic mehr.

## 5. Sicherungen (unverändert zur Grundregel „nichts geht ungeprüft raus“)

- **Kein Weg in die Buchhaltung:** Claude bestätigt eine Rechnung nur im
  eigenen Protokoll `data\lehrer.jsonl`, nie im Bestätigungs-Protokoll des
  Menschen. Für das Programm gilt eine nur von Claude geprüfte Rechnung als
  unbestätigt: sie wird nicht archiviert, nicht ins Journal geschrieben und
  nicht an JobRouter übergeben. Die Anlernphase bleibt reine Übung.
- **Gegen erfundene Werte:** Jeder Wert von Claude muss wörtlich im Text der
  Rechnung stehen (dieselbe Prüfung wie für das lokale Sprachmodell), sonst
  gilt das Feld als unsicher und die Rechnung geht an den Menschen.
- **Stichprobe:** 10 % der Rechnungen (einstellbar, `RECHNUNG_CLAUDE_STICHPROBE`)
  prüft zusätzlich ein Mensch; `--bilanz` zeigt, wie oft Claude falsch lag.
- **Stammdaten** (Lieferant, IBAN) bestätigt ausschließlich ein Mensch im
  Programm; Rechnungen ohne bestätigten Lieferanten werden gar nicht geschickt.
- **Betriebsmodus:** Der Lehrer schaltet nie auf „autonom“; das bleibt ein
  bewusster Befehl mit Namensangabe (Abschnitt 5 des Entwurfs v2).
- **Ordner „Menschliche Bearbeitung“:** Der Lehrer löscht dort nichts; er
  setzt nur einen Vermerk `claude_geprueft` in der Begleitdatei.
- **Nachvollziehbarkeit:** Jede Korrektur durch Claude steht mit Operator
  `claude` im Audit-Protokoll (`audit.jsonl`); je Rechnung ein Eintrag in
  `lehrer.jsonl` (Zeitpunkt, Datei, Prüfsumme, Weg, Ergebnis, Gründe);
  Hintergrundrunden protokollieren nach `data\anlernen.log`.
- **Störungen:** Ist Anthropic nicht erreichbar oder das Kontingent
  erschöpft, bricht die Runde ab; die nächste Runde macht weiter. Scheitert
  eine Rechnung dreimal, ruht sie, bis ein Mensch sie ansieht.

## 6. Offene Punkte für die IT

1. **Vertragliche Grundlage** für die Übertragung von Rechnungen (personen-
   bezogene Daten, Bankverbindungen) an Anthropic: Welcher Konto-/Vertragstyp
   steckt hinter dem Firmen-Login (Datenschutz-/Auftragsverarbeitungs-
   vereinbarung, keine Nutzung der Inhalte zum Training, Speicherort und
   -dauer bei Anthropic)? Das ist nicht im Programm, sondern im Vertrag geregelt
   und muss von der IT bzw. dem Datenschutz geprüft werden.
2. **Firewall:** ausgehend HTTPS (443) zu `api.anthropic.com` (und zu den
   Anmelde-/Update-Adressen von Claude Code) vom Laptop.
3. **Claude Code** auf dem Laptop (Programm von Anthropic, proprietär,
   Installation ohne Adminrechte im Benutzerprofil, selbstaktualisierend):
   Zustimmung zur Nutzung bzw. zur Abschaltung der nicht notwendigen
   Verbindungen.
4. **Befristung:** Der Lehrer ist nur für die Anlernphase gedacht. Vorschlag:
   `RECHNUNG_CLAUDE_LEHRER` wird nach Erreichen des Richtwerts (50 sichere
   Rechnungen in Folge von mindestens 10 Lieferanten) wieder entfernt.

## 7. Änderungen in den bestehenden Dokumenten

- `IT-FREIGABE.md` (Kurzbeschreibung v1): Hinweis am Anfang, dass die Aussage
  „keine Cloud-KI“ für die Anlernphase nicht mehr gilt; Verweis hierher.
- `IT-FREIGABE-ENTWURF-v2.md`: Zeile in der Übersicht (Abschnitt 2), Schritt
  „Lernen“ (Abschnitt 4), Netzwerktabelle und Satz darunter (Abschnitt 6),
  Komponenten (Abschnitt 7), Bitte an die IT (Abschnitt 13).
- `IT-FREIGABE-AENDERUNG-UPDATES.md`: der dort vorgeschlagene Satz „keine
  Verbindung zu Cloud-KI-Anbietern“ ist durch diesen Vorschlag überholt.
