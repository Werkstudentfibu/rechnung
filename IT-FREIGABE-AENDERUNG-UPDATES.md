# Änderungsvorschlag IT-Freigabe (Entwurf v2) – Selbst-Update in der Testphase

**Status: Vorschlag, noch nicht in `IT-FREIGABE-ENTWURF-v2.md` eingearbeitet.**
Erst nach Aarons Freigabe übernehmen.

*Hinweis 2026-11-06: Der unter Punkt 2 vorgeschlagene Satz „keine Verbindung zu
Cloud-KI-Anbietern“ ist überholt – seit der Anlernphase mit Claude gibt es eine
solche Verbindung, siehe `IT-FREIGABE-AENDERUNG-CLAUDE.md`.*

## 1. Abschnitt 6, Tabelle – neue Zeile

| Verbindung | Richtung | Ziel / Port | Wann | Standard |
|---|---|---|---|---|
| Programm-Updates (Testphase) | ausgehend | `ssh.github.com:443` (SSH, privates Repo, nur lesend) | beim Programmstart, auf Knopfdruck „Update holen“, vor dem Nachtlauf | an (nur Installation aus dem Quellcode, siehe `LAPTOP-UPDATE.md`) |

## 2. Abschnitt 6, Satz darunter – ersetzen

Alt:

> Es gibt keine Telemetrie, keine automatischen Updates und keine
> Verbindung zu Cloud-KI-Anbietern.

Neu:

> Es gibt keine Telemetrie und keine Verbindung zu Cloud-KI-Anbietern.
> Während der Testphase holt sich das Programm neue Programmstände selbst aus
> einem privaten Repository (nur Programmcode, nur lesender Zugriff mit einem
> Schlüssel, der ausschließlich für dieses Repository gilt). Dabei werden keine
> Daten hochgeladen. Jeder neue Stand muss vor der Aktivierung auf dem Rechner
> alle automatischen Tests bestehen, sonst bleibt der bisherige Stand aktiv;
> jeder Versuch wird in `data/update.log` protokolliert. Rechnungsdaten und
> Einstellungen werden von Updates nicht verändert. Für den Dauerbetrieb ist
> die Auslieferung als geprüfte Einzeldatei ohne Selbst-Update vorgesehen.

## 3. Abschnitt 7, Komponenten – ergänzen

> **Git für Windows** (GPL-2.0) – nur in der Testphase, zum Abholen der
> Programmstände.
