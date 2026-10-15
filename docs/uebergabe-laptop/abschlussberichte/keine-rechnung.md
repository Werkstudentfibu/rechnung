Permission allow rule (.claude/settings.local.json): Bash(cp -r ~/Aktien1/deploy/ibgateway/ibc/* ~/ibc/) has a wildcard before the rest of the command, so it also matches any options inserted at that position and approves them without a prompt. Replace that * with the exact value you mean, or only use * after the subcommand.
Alles sauber committet (nur der `.venv`-Symlink bleibt unkommittet, wie vorgesehen), Suite grün (820 Tests, auch mit `RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=1 RECHNUNG_IMAP_USER=x`).

## Abschlussbericht

Der Lauf von heute Vormittag hatte den kompletten Auftrag bereits fertig umgesetzt (inkl. Nachtrag Löschen), war aber am Abo-Limit abgebrochen, bevor committet wurde. Ich habe den Stand geprüft, die fehlenden README/SPEC-Abschnitte ergänzt, die UI per Playwright verifiziert und alles in drei saubere Commits gebracht.

**Wo landet was (vorher → nachher)**

| Grund | vorher | nachher |
|---|---|---|
| Mail ohne Anhang/Rechnungs-Hinweis | Menschliche Bearbeitung | Keine Rechnung |
| Mail, alle Anhänge eindeutig Nicht-Beleg (AGB, Lieferschein, Angebot, Newsletter, Werbung, Kontoauszug, Vertrag, …) | Menschliche Bearbeitung | Keine Rechnung |
| Mail mit unklarem Anhang (auch gemischt mit AGB) | Menschliche Bearbeitung | **unverändert** Menschliche Bearbeitung |
| Pipeline-Dokument: Nicht-Beleg-Titel, keine Belegart, kein QR, kein Rechnungsnr./Summen-Label | Menschliche Bearbeitung | Keine Rechnung |
| Rechnung mit Lieferschein-Nr. im Text / Rechnung+Lieferschein im selben PDF / Rechnung ohne Titel | Menschliche Bearbeitung | **unverändert** Menschliche Bearbeitung |

**Schalter:** `RECHNUNG_KEINE_RECHNUNG_ORDNER=0` → exakt altes Verhalten. Ordner über `RECHNUNG_KEINE_RECHNUNG`, Standard `<data>/keine-rechnung/`.

**Bedienung:** Reiter „Keine Rechnung“ (Liste, Vorschau), „Doch eine Rechnung“ (zurück in den Eingang, zählt als Fehlsortierung), „Keine Rechnung“-Knopf in der Prüfmaske (von Hand aussortieren). Nachtrag 18:14: Löschen je Eintrag + Mehrfachauswahl, eigener Bestätigungsdialog, Pfad-Sicherheitsnetz gegen Traversal, Protokoll bleibt auch nach dem Löschen. Postfach/IMAP wird dabei nie angefasst.

**Tests:** 820 Tests grün, auch mit LLM/OCR/IMAP-Flags. UI per Playwright geprüft (Liste, Einzel- und Mehrfach-Löschdialog) – keine Konsolenfehler.

**Bekannte Grenzen:** Ein unklarer Postfach-Anhang bleibt bewusst bei „Menschliche Bearbeitung“ (könnte eine Rechnung sein); die Beschriftungsliste ist nicht vollständig – unbekannte Formulierungen laufen weiterhin normal, landen nicht fälschlich hier.

```
d02f9b1 Doku: Ordner "Keine Rechnung" in README und SPEC-v1
3a14559 Nicht-Rechnungen aussortieren: Web-API und Bedienung
7dc0df0 Nicht-Rechnungen aussortieren: Kernmodul, Postfach- und Pipeline-Anbindung
```

Nicht gepusht, Branch `emmy/keine-rechnung` in `/tmp/rw-keine`, Hauptcheckout unangetastet.

ATTACH: /tmp/uidemo-keine/01-liste.png
ATTACH: /tmp/uidemo-keine/02-loeschen-dialog.png
ATTACH: /tmp/uidemo-keine/03-mehrfachauswahl-dialog.png
ATTACH: /tmp/uidemo-keine/04-nach-loeschen.png
