# Testphase auf dem Firmenlaptop – mit automatischen Updates

Während der Testphase läuft das Programm auf dem Laptop **direkt aus dem
Quellcode** statt als `rechnung.exe`. Ein Fix ist damit in Sekunden auf dem
Laptop, ohne neuen exe-Bau und ohne dass der Virenscanner jedes Mal anschlägt.
Die `.exe` (`INSTALL-WINDOWS.md`) kommt erst, wenn das Programm fertig
kalibriert ist.

## Einmalig einrichten

Gebraucht: Adminrechte, Internet, ca. 30–60 Minuten.

1. Das Einrichtungspaket `rechnung-laptop-einrichtung.zip` (enthält
   `laptop-einrichten.ps1` und den Ordner `zugang\`) auf den Laptop kopieren
   und entpacken, z.B. nach `C:\rechnung-einrichtung`.
2. Startmenü → „PowerShell“ → **Rechtsklick → Als Administrator ausführen**.
3. ```
   cd C:\rechnung-einrichtung
   ```
   ```
   powershell -ExecutionPolicy Bypass -File .\laptop-einrichten.ps1 -MitTesseract -MitOllama -MitNachtlauf
   ```

Das Skript installiert bei Bedarf Git und Python, holt den Programmcode nach
`C:\Rechnung\app`, baut die Python-Umgebung, lässt die Tests laufen und legt
Starter, Desktop-Verknüpfung „Rechnung“ und (mit `-MitNachtlauf`) die nächtliche
Aufgabe an. Protokoll: `C:\Rechnung\einrichten.log`.

Erneut ausführen ist unschädlich – `data\` und `rechnung.env` bleiben.

## Wie Updates ankommen

Der Entwickler schiebt einen geprüften Stand auf den Zweig **`release`**. Der
Laptop holt ihn sich:

| Wann | Wie |
|---|---|
| sofort | im Programm oben rechts **„Update holen“** → nach Bestätigung Neustart mit neuem Stand |
| beim Start | „Rechnung“ auf dem Desktop prüft zuerst auf Updates |
| nachts | vor dem Nachtlauf (02:30) |

Bei jedem Update laufen zuerst die Tests auf dem Laptop. **Sind sie nicht alle
grün, bleibt der alte Stand aktiv** – es kann also kein kaputter Stand
„durchrutschen“. Ohne Internet startet das Programm einfach mit dem
vorhandenen Stand.

Oben rechts im Programm steht der aktuelle Stand (Datum; Maus drüber zeigt die
Änderung). Jeder Update-Versuch steht in `C:\Rechnung\data\update.log`.

## Aufbau

```
C:\Rechnung\
  app\                    Programmcode (Git-Klon, Zweig release) + .venv
  data\                   alle Daten – von Updates nie angefasst
  eingang\                Rechnungen hier hineinlegen
  rechnung.env            Einstellungen (Vorlage: app\rechnung.env.example)
  zugang\                 Lese-Schlüssel (nur dieses Repo, nur lesen)
  Rechnung starten.cmd    Start mit Update
  nachtlauf.cmd           Nachtlauf mit Update (Aufgabenplanung)
```

## Sicherheit

- Der Schlüssel in `zugang\` ist ein **GitHub-Deploy-Key**: er darf genau
  dieses eine private Repo **nur lesen**. Geht der Laptop verloren, löscht der
  Entwickler den Schlüssel bei GitHub – fertig.
- Verbindung über SSH auf Port 443 mit fest hinterlegtem GitHub-Fingerabdruck
  (`known_hosts`) – ein untergeschobener Server wird abgewiesen.
- Ins Repo gehen nur Programmcode und Tests, **keine Rechnungen und keine
  Daten** (`data/`, `samples/`, `rechnung.env` sind ausgeschlossen).

## Wenn etwas hakt

- **Update meldet „offline“:** Internet prüfen. Blockiert die Firma GitHub
  bzw. Port 443 zu `ssh.github.com`, dem Entwickler Bescheid geben.
- **„zurückgerollt – Tests fehlgeschlagen“:** nichts tun, der alte Stand läuft
  weiter. `data\update.log` an den Entwickler schicken.
- **Einrichtung bricht ab:** `C:\Rechnung\einrichten.log` (und ggf.
  `C:\Rechnung\app\build-windows.log`) an den Entwickler schicken.
