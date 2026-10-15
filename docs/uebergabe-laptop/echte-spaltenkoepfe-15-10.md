# Spaltenköpfe-Referenz (15.10.2026) -- fürs Wortlisten-Follow-up

**Hinweis Emmy (15.10.2026, beim Kopieren nach GitHub):** Die ursprüngliche Version dieser Datei enthielt eine
echte Beispielzeile mit einem realen Kreditor- und einem realen Mandanten-Namen/Adresse, die Aaron am 15.10. aus
der echten Excel-Liste abgeschrieben hatte. Nach Aarons eigener Regel ("echte Daten bleiben ausschließlich auf
dem Firmenlaptop, nie auf dem Server") habe ich die reale Beispielzeile beim Kopieren hierher durch einen
generischen Platzhalter ersetzt. Alle Spaltennamen, Ableitungen und Aarons Entscheidungen (Q&A) sind unverändert
und vollständig -- nur die konkreten Werte in den Beispielzeilen sind jetzt Platzhalter. Die Original-Datei mit
der echten Beispielzeile bleibt unverändert auf dem Server liegen (nicht committed).

NICHT mit echten Daten weiterverarbeiten (nur Platzhalter als Referenz für den Spaltennamen-Abgleich,
keine neuen Zeilen/Mandanten/IBAN hier ablegen -- echte Daten bleiben auf dem Firmenlaptop).

## Kreditoren Deutschland -- Kopfzeile + 1 Platzhalter-Beispielzeile

Konto | Bezeichnung 1 | Name1 | SammelKt | Name2 | Straße | Land | PlzOrt | Ortsbezeichnung
70000 | Musterfirma Beratungs- und Dienstleist | Musterfirma Beratungs- und | 1604 | Dienstleistungsgesellschaft mbH | Musterstraße 1 | DE | 12345 | Musterstadt

### Ableitung (zu bestätigen, nicht raten -> Aaron fragen falls unklar)
- Konto -> Kreditorennummer
- Bezeichnung 1 -> vollständiger (abgeschnittener) Firmenname, 40 Zeichen -- vermutlich redundant zu Name1+Name2
- Name1 + Name2 -> Firmenname in zwei festlängige Felder gesplittet (typisches DATEV/ERP-Muster), zusammen = Bezeichnung 1
- Straße -> Straße+Hausnummer in einem Feld
- Land -> ISO-Ländercode (DE)
- PlzOrt -> ACHTUNG: enthält im Beispiel NUR die PLZ, nicht PLZ+Ort trotz Name -- bestätigen!
- Ortsbezeichnung -> Ort

### Auffällig / offene Fragen an Aaron
- Keine IBAN/BIC/USt-IdNr/Steuernummer-Spalte in dieser Kopfzeile -- stehen die weiter rechts (abgeschnitten
  beim Kopieren) oder in einem anderen Blatt?
- Keine Mandant-Spalte -- ist diese Kreditorenliste über alle Mandanten gemeinsam, oder gibt es sie auch
  getrennt je Mandant?
- Schweiz-Liste: gleicher Aufbau oder andere Spalten (z. B. PLZ 4-stellig, UID CHE-...)?
- Mandanten-Liste und Postfach-Liste (E-Mail/Passwort/Server): noch keine Kopfzeile bekannt.

## Verwendung
Sobald der Worker (emmy/excel-postfaecher) bei der Spalten-Wortliste ankommt, diese Begriffe (Konto,
Bezeichnung 1, Name1, Name2, SammelKt, Straße, Land, PlzOrt, Ortsbezeichnung) als bekannte Synonyme ergänzen,
NICHT raten bei PlzOrt/Ortsbezeichnung-Zuordnung, sondern wie SPEC vorsieht als Frage markieren bis Aaron
bestätigt hat.


## Mandanten -- Kopfzeile + 1 Platzhalter-Beispielzeile

Mandanten Nr. | Bezeichnung | WJ | LM
001 | ABC - Beispiel-Mandant | 2026 | 12

### Ableitung (zu bestätigen)
- Mandanten Nr. -> company_code (BillingAddress.company_code / "Mandant / Firmennummer")
- Bezeichnung -> name (enthält offenbar einen Kurzcode + Klartext, getrennt durch " - ": Kürzel + Klartextname)
- WJ, LM -> Bedeutung unklar (Vermutung: Wirtschaftsjahr / Länge-Monate -- NICHT geraten, Aaron fragen), evtl. für das
  Programm irrelevant und nur in notes abzulegen statt in ein eigenes Feld

### WICHTIGER BEFUND -- an Aaron weiterzugeben, nicht selbst entscheiden
Diese Mandanten-Liste hat KEINE Adresse (keine Straße/PLZ/Ort) und KEINE USt-IdNr -- nur Nr.+Name+WJ+LM.
`BillingAddress` (= Mandant im Programm) nutzt `lines` (Anschrift zur Anzeige) und `match_terms` (Text, der im
Empfängerblock der Rechnung gesucht wird) um eine Rechnung dem richtigen Mandanten zuzuordnen. Ohne Adresse bleibt
nur der Name als match_term -- das kann reichen (bei eindeutigen Namen), ist aber ungeprüft.
Fragen an Aaron:
1. Was bedeuten WJ und LM?
2. Reicht der Name allein zum Wiedererkennen des Mandanten auf der Rechnung, oder gibt es die vollständige Anschrift
   (Straße/PLZ/Ort, USt-IdNr) woanders (eigenes Blatt, eigene Datei)?

### Aarons Antwort (15.10.2026, 13:42)
1. Mehr als Nr./Bezeichnung/WJ/LM gibt es nicht -- keine Adresse, keine USt-IdNr fuer Mandanten. Matching also nur
   ueber den Namen moeglich; wie bei Kreditoren-IBAN/UID muss die Pipeline Adress-Feinheiten (falls ueberhaupt
   gebraucht) aus bestaetigten Rechnungen selbst lernen, nicht aus dem Import.
2. WJ/LM: Aaron weiss es selbst nicht, vermutet Abbyy-interne Felder, die unser Programm nicht braucht.
   -> Entscheidung: WJ/LM beim Import NICHT in eigene Felder uebernehmen (ignorieren, allenfalls roh in notes,
   kein Pflichtfeld, keine Frage/Ampel dafuer).


## Postfächer (IONOS) -- nur Kopfzeile (kein Beispiel/Passwort geschickt -- gut so)

MDT | E-Mail | Passwort | Bezeichnung | Name

### Ableitung (zu bestätigen)
- MDT -> vermutlich "Mandant"-Kuerzel, verweist auf "Mandanten Nr." (001 etc.) -- das ist genau die Verknuepfung
  Postfach<->Mandant, die Baustein 2 (Mandant aus Postfach vs. Rechnungsadresse) braucht.
- E-Mail -> IMAP-Benutzername/Login
- Passwort -> Klartext-Passwort in der Excel-Datei (daher Aarons Hinweis an sich selbst nach dem Import: Datei
  loeschen/Passwortspalte leeren)
- Bezeichnung, Name -> vermutlich Hotel-/Mandantenname zur Anzeige (Ueberlappung mit Mandanten.Bezeichnung moeglich,
  im Zweifel beim Import als Frage markieren statt zu raten, welches das "fuehrende" ist)

### Aarons Antwort zu MDT/Bezeichnung/Name (15.10.2026, 13:46)
1. MDT ist eine 3-stellige Nummer -- passt zum Format der "Mandanten Nr." (z.B. "001").
2. Bestaetigt: MDT = Mandanten-Verweis (Postfach<->Mandant ueber diese Nummer).
3. Bezeichnung und Name ueberschneiden sich tatsaechlich. Regel: aus "Bezeichnung" nur das KUERZEL nehmen
   (steht immer als Erstes in der Zelle, vor einem Trenner wie " - "); aus "Name" den Wert KOMPLETT uebernehmen.
   -> Beim Import: kuerzel = Bezeichnung.split(" - ",1)[0].strip() (oder erstes Token, falls kein " - " vorhanden --
   im Zweifel als Frage markieren statt zu raten, welcher Trenner gemeint ist, wenn das Beispiel nicht passt).
