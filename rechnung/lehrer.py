"""Claude als Lehrer in der Anlernphase (``python -m rechnung.lehrer``).

Ersetzt in der Anlernphase den Menschen beim Pruefen/Korrigieren/Bestaetigen
von UEBUNGS-Rechnungen, damit die Vorlagen (Stufe 1, learn.py) schneller
reifen. Ablauf je Rechnung:

1. Das Programm liest die Rechnung wie immer aus (``pipeline.process``),
   aber ohne die lokale KI (Ollama): Claude ist hier der Pruefer, und die
   lokale KI kostet auf dem Laptop je Rechnung bis zu zwei Minuten
   (``RECHNUNG_CLAUDE_LOKALE_KI=1`` schaltet sie wieder dazu).
2. Claude bekommt das Original (PDF/Bild) und die Werte des Programms und
   gibt je Feld den richtigen Wert zurueck, samt "sicher ja/nein".
3. Gegen Halluzination gilt dieselbe Regel wie fuer die lokale KI
   (semantic.py): jeder Wert von Claude muss im PDF-Text belegt sein, sonst
   gilt das Feld als unsicher.
4. Abweichungen werden als Korrektur gespeichert (Operator ``claude``, im
   Audit/Fehlerbericht sichtbar), die Rechnung neu gerechnet und -- wenn
   danach alles sicher ist -- von Claude bestaetigt. Daraus lernt Stufe 1
   die Vorlage, der Schattenbetrieb misst wie bei einem Menschen.
5. Zum Menschen (Ordner "Menschliche Bearbeitung") geht die Rechnung statt
   dessen, wenn Claude unsicher ist, ein Wert nicht belegt ist, Claude und
   Programm sich bei einem Feld widersprechen, das Claude fuer nicht
   vorhanden haelt, eine Pruefung rot bleibt -- oder die Rechnung in der
   Stichprobe liegt (Kontrolle des Lehrers).
6. Ist der Lieferant unbekannt oder nur ein Entwurf, wird Claude gar nicht
   gefragt: Stammdaten entscheidet ein Mensch. Die Rechnung wartet und
   wird automatisch aufgegriffen, sobald der Lieferant bestaetigt ist.
   AUSNAHME Uebungsphase (``RECHNUNG_CLAUDE_STAMMDATEN=1``, Entscheidung
   Aaron 12.11.2026): Claude nennt zusaetzlich den Lieferantennamen; steht
   er im PDF-Text und ist keine eigene Gesellschaft, wird der Entwurf in
   den gleichnamigen bestehenden Lieferanten uebernommen
   (entwurf_uebernahme.py) oder -- gibt es keinen -- selbst bestaetigt.
   Jede solche Entscheidung steht mit Operator ``claude`` im Audit, als
   ``stammdaten`` in lehrer.jsonl und als Vermerk im Lieferanten; die
   Bilanz zaehlt sie, damit ein Mensch sie vor ``autonom`` durchsieht.

Sicherungen:

- Standardmaessig AUS (``RECHNUNG_CLAUDE_LEHRER=1`` schaltet ein).
- Claude bestaetigt NICHT ueber das Bestaetigungs-Protokoll des Menschen
  (``Store.mark_confirmed``), sondern nur im eigenen Protokoll
  (``data/lehrer.jsonl``). Fuer ``finalize`` ist eine nur von Claude
  gepruefte Rechnung also unbestaetigt: sie wird nicht archiviert, nicht ins
  Journal geschrieben und nicht an JobRouter gegeben. Die Anlernphase bleibt
  reine Uebung. (Im Betriebsmodus "autonom" entscheidet -- wie fuer jede
  Rechnung -- allein die Sicherheit des Programms, nie Claudes Bestaetigung.)
- Stichprobe (``RECHNUNG_CLAUDE_STICHPROBE``, Standard 10 %): diese
  Rechnungen prueft Claude vor, bestaetigt aber ein Mensch. ``bilanz()``
  zaehlt, wie oft der Mensch Claude korrigieren musste.
- Der Wechsel in den Betriebsmodus "autonom" bleibt ein bewusster Befehl
  (modus.py) -- der Lehrer schaltet nie selbst um.

Zwei Wege zu Claude (``RECHNUNG_CLAUDE_BACKEND``):

- ``api`` (Standard): Claude API mit API-Schluessel aus der Claude Console,
  Abrechnung nach Verbrauch. Netzwerk: nur ``api.anthropic.com`` (HTTPS),
  reines stdlib-``urllib`` wie semantic.py, keine neue Abhaengigkeit.
- ``cli``: ueber Claude Code (``claude -p``), angemeldet mit einem Claude-
  Abo (Firmen-Login!). Claude Code liest die Rechnung selbst (nur das Lese-
  Werkzeug ist erlaubt); abgerechnet wird ueber das Abo dieses Logins.
  Ein gesetzter ``ANTHROPIC_API_KEY`` wird dabei bewusst NICHT
  weitergegeben, sonst wuerde Claude Code doch ueber die API abrechnen.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

OPERATOR = "claude"
API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODELL = "claude-sonnet-5-5"

# Typen, die Claude als einfachen Wert prueft (wie semantic._SIMPLE_TYPES);
# USt-Aufstellung und Positionen bleiben beim Menschen/den Rechen-Pruefungen.
_SIMPLE_TYPES = {"text", "number", "date", "amount"}

_MEDIA = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg",
          ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}

ERG_BESTAETIGT = "bestaetigt"
ERG_MENSCH = "mensch"
ERG_UEBERSPRUNGEN = "uebersprungen"
ERG_FEHLER = "fehler"

# So oft darf eine Rechnung an sich scheitern (unlesbare Antwort, Zeitgrenze
# der Verarbeitung), dann ruht sie. Netz-/Limitprobleme zaehlen nicht.
MAX_FEHLVERSUCHE = 3

# Art des Grundes im Ordner "Menschliche Bearbeitung" (human.py)
ART_CLAUDE = "claude"


class ClaudeNichtErreichbar(RuntimeError):
    """Claude gerade nicht nutzbar (Netz, Ueberlast, Nutzungslimit, Login):
    liegt nicht an der Rechnung -- Runde beenden, spaeter erneut versuchen,
    nicht als Fehlversuch der Rechnung zaehlen."""


# ---- Konfiguration (zur Laufzeit gelesen, nicht beim Import) ---------------

def an() -> bool:
    return os.environ.get("RECHNUNG_CLAUDE_LEHRER", "0").strip().lower() in (
        "1", "true", "yes", "on", "ja")


def api_key() -> str:
    return (os.environ.get("RECHNUNG_CLAUDE_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def api_url() -> str:
    """Adresse der Messages-API. Standard Anthropic; ``RECHNUNG_CLAUDE_API_URL``
    z.B. fuer Langdock (Anthropic-kompatibel, EU-Hosting, Firmenvertrag):
    ``https://api.langdock.com/anthropic/eu/v1/messages`` (Aaron 12.11.2026)."""
    return os.environ.get("RECHNUNG_CLAUDE_API_URL", "").strip() or API_URL


def _auth_header() -> dict:
    """Anthropic erwartet ``x-api-key``, Langdock ``Authorization: Bearer``.
    ``RECHNUNG_CLAUDE_API_AUTH=bearer|x-api-key`` erzwingt eine Art; ohne
    Angabe Bearer fuer Langdock-Adressen, sonst ``x-api-key``."""
    art = os.environ.get("RECHNUNG_CLAUDE_API_AUTH", "").strip().lower()
    if not art:
        art = "bearer" if "langdock" in api_url().lower() else "x-api-key"
    if art == "bearer":
        return {"Authorization": f"Bearer {api_key()}"}
    return {"x-api-key": api_key()}


def backend() -> str:
    """``api`` (Standard) oder ``cli`` (Claude Code, ``claude -p``)."""
    v = os.environ.get("RECHNUNG_CLAUDE_BACKEND", "api").strip().lower()
    return "cli" if v == "cli" else "api"


def cli_befehl() -> str | None:
    """Pfad zu Claude Code: ``RECHNUNG_CLAUDE_CLI`` oder ``claude`` im PATH."""
    import shutil
    eigen = os.environ.get("RECHNUNG_CLAUDE_CLI", "").strip()
    if eigen:
        return eigen if Path(eigen).exists() else shutil.which(eigen)
    return shutil.which("claude")


def bereit() -> str:
    """Leer, wenn der gewaehlte Weg zu Claude benutzbar ist, sonst der Grund."""
    if backend() == "cli":
        if not cli_befehl():
            return ("Claude Code nicht gefunden (Befehl 'claude'). Installieren und "
                    "mit dem Firmen-Login anmelden, oder RECHNUNG_CLAUDE_CLI setzen.")
        return ""
    if not api_key():
        return "Kein API-Schluessel: RECHNUNG_CLAUDE_API_KEY setzen."
    return ""


def modell() -> str:
    eigen = os.environ.get("RECHNUNG_CLAUDE_MODELL", "").strip()
    if eigen:
        return eigen
    # Claude Code nimmt ohne Angabe das Modell seines Logins
    return "" if backend() == "cli" else DEFAULT_MODELL


def stichprobe_prozent() -> int:
    try:
        v = int(os.environ.get("RECHNUNG_CLAUDE_STICHPROBE", "10"))
    except ValueError:
        v = 10
    return max(0, min(100, v))


def timeout_s() -> int:
    try:
        return max(10, int(os.environ.get("RECHNUNG_CLAUDE_TIMEOUT", "120")))
    except ValueError:
        return 120


def stammdaten_an() -> bool:
    """Darf der Lehrer Lieferanten-Entwuerfe selbst klaeren (bestaetigen
    oder in einen bestehenden Lieferanten uebernehmen)? Standard NEIN.
    ``RECHNUNG_CLAUDE_STAMMDATEN=1`` nur fuer die Uebungsphase (Entscheidung
    Aaron 12.11.2026) -- vor dem Wechsel auf ``autonom`` sieht ein Mensch
    alle so entstandenen Stammdaten durch (``--bilanz``)."""
    return os.environ.get("RECHNUNG_CLAUDE_STAMMDATEN", "").strip().lower() in (
        "1", "true", "yes", "on", "ja")


STAMMDATEN_VERMERK = ("Stammdaten in der Uebungsphase von Claude bestaetigt "
                      "({datum}) -- vor dem Wechsel auf 'autonom' pruefen")


def _gleicher_name(a_n: str, b_n: str) -> bool:
    """Normalisierte Namen gleich oder der eine als ganze Wortfolge im
    anderen ("muster-handels" / "muster-handels-gmbh")."""
    if not a_n or not b_n:
        return False
    return a_n == b_n or f"-{a_n}-" in f"-{b_n}-" or f"-{b_n}-" in f"-{a_n}-"


def stammdaten_klaeren(store, entwurf, doc, antwort: dict) -> tuple[dict | None, str]:
    """Lieferanten-Entwurf mit Claudes Lieferantennamen klaeren (nur mit
    ``stammdaten_an()``). -> (Aktion, "") oder (None, Grund).

    - Name fehlt / Claude unsicher / Name nicht im PDF-Text / Name ist eine
      eigene Gesellschaft (Rechnungsadresse): nichts tun.
    - genau EIN bestaetigter Lieferant, dessen Name auf der Rechnung steht
      und zu Claudes Namen passt: Entwurf dorthin uebernehmen.
    - mehrere passen: nichts tun (Mensch).
    - keiner passt: Entwurf bestaetigen (Platzhaltername ersetzt)."""
    from .entwurf_uebernahme import uebernehmen
    from .identify import _ist_empfaenger, empfaenger_namen, name_auf_rechnung, namenstreffer
    from .pipeline import _is_placeholder_name, confirm_supplier
    from .textnorm import norm

    eintrag = (antwort.get("felder") or {}).get("lieferant_name") or {}
    name = str(eintrag.get("wert") or "").strip() if isinstance(eintrag, dict) else ""
    if not name or not (isinstance(eintrag, dict) and eintrag.get("sicher") is True):
        return None, "Claude nennt keinen sicheren Lieferantennamen"
    text_n = norm(doc.text)
    if not name_auf_rechnung(name, text_n):
        return None, f"Lieferantenname {name!r} steht nicht im PDF-Text"
    emp = empfaenger_namen(store.addresses())
    name_n = norm(name)
    if _ist_empfaenger(name_n, emp):
        return None, f"{name!r} ist eine eigene Gesellschaft (Rechnungsadresse)"
    bestaetigte = [s for s in store.suppliers()
                   if s.bestaetigt and not s.dummy and s.id != entwurf.id]
    passend = [s for s in namenstreffer(bestaetigte, text_n, emp)
               if _gleicher_name(norm(s.name), name_n)]
    if len(passend) > 1:
        return None, ("mehrere bestehende Lieferanten passen: "
                      + ", ".join(s.name for s in passend[:4]))
    if len(passend) == 1:
        ziel = passend[0]
        uebernehmen(store, entwurf.id, ziel.id, OPERATOR)
        ziel = store.supplier(ziel.id)
        _vermerk_lieferant(store, ziel)
        return {"aktion": "uebernommen", "entwurf": entwurf.id,
                "entwurf_name": entwurf.name, "ziel": ziel.id,
                "ziel_name": ziel.name}, ""
    sup = store.supplier(entwurf.id)
    alt_name = sup.name
    if _is_placeholder_name(sup.name):
        sup.name = name
    store.save_supplier(sup)
    _vermerk_lieferant(store, sup)
    confirm_supplier(store, sup.id, OPERATOR)
    return {"aktion": "bestaetigt", "id": sup.id, "name": sup.name,
            "alter_name": alt_name}, ""


def _claude_wert(antwort: dict, name: str) -> tuple[str, bool]:
    e = (antwort.get("felder") or {}).get(name)
    if not isinstance(e, dict):
        return "", False
    return str(e.get("wert") or "").strip(), e.get("sicher") is True


def kennungen_pruefen(store, supplier_id: str | None, doc, antwort: dict) -> dict | None:
    """C (Plan 12.11.2026): IBAN/USt-IdNr des Lieferanten mit Claude abgleichen
    (nur mit ``stammdaten_an()``). Ungueltige IBANs (Pruefziffer falsch, z.B.
    eine als IBAN gelesene franzoesische USt-IdNr) fliegen aus den
    Stammdaten. Claudes IBAN/USt-IdNr wird NUR eingetragen, wenn der
    Lieferant danach noch KEINE gueltige hat -- eine zweite, abweichende
    IBAN bleibt Sache des Menschen (Betrugsschutz). Wert muss im PDF-Text
    stehen und Claude sicher sein. -> Aenderungen oder ``None``."""
    from .semantic import _check_value
    from .textnorm import iban_valid

    sup = store.supplier(supplier_id) if supplier_id else None
    if sup is None or sup.dummy:
        return None
    aend: dict = {}
    kaputt = [i for i in sup.ibans if not iban_valid(i)]
    if kaputt:
        sup.ibans = [i for i in sup.ibans if iban_valid(i)]
        aend["iban_entfernt"] = kaputt
    for feld, liste, typ in (("iban", "ibans", "iban"), ("ustid", "ustids", "ustid")):
        wert, sicher = _claude_wert(antwort, feld)
        if not wert or not sicher or getattr(sup, liste):
            continue
        value, grund = _check_value(doc.text, feld, wert, typ)
        if grund or not value:
            continue
        setattr(sup, liste, [str(value)])
        aend[f"{feld}_neu"] = str(value)
    if not aend:
        return None
    store.save_supplier(sup)
    _vermerk_lieferant(store, sup)
    store.audit("lieferant_kennungen_claude", dict(aend, id=sup.id, name=sup.name), OPERATOR)
    return dict(aend, id=sup.id, name=sup.name)


def _adress_begriffe(a) -> set[str]:
    from .textnorm import norm
    namen = [a.name, a.name.split(" - ", 1)[-1]] + list(a.match_terms or [])
    return {norm(n) for n in namen if len(norm(n)) >= 4}


def mandant_klaeren(store, doc, antwort: dict, vorschlag_id: str | None = None
                    ) -> tuple[dict | None, str]:
    """B (Plan 12.11.2026): keine Rechnungsadresse erkannt -> Claudes
    Rechnungsempfaenger GENAU EINER Rechnungsadresse der Bibliothek zuordnen
    und den Empfaengertext als Suchbegriff ergaenzen; ab dann erkennt das
    Programm den Mandanten selbst. Nur mit ``stammdaten_an()``.
    Widerspricht der Postfach-Mandant (``vorschlag_id``), entscheidet der
    Mensch. -> (Aktion, "") oder (None, Grund)."""
    from .identify import _ist_empfaenger, name_auf_rechnung
    from .textnorm import norm

    name, sicher = _claude_wert(antwort, "rechnungsempfaenger")
    if not name or not sicher:
        return None, "Claude nennt keinen sicheren Rechnungsempfaenger"
    if not name_auf_rechnung(name, norm(doc.text)):
        return None, f"Rechnungsempfaenger {name!r} steht nicht im PDF-Text"
    name_n = norm(name)
    adressen = store.addresses()
    # Proberunde 12.11.2026: Claude nennt den Firmennamen ("Hotel Regent
    # GmbH"), die Bibliothek fuehrt den Mandanten anders ("HRI - Hotel Regent
    # Koeln"). Deshalb waehlt Claude zuerst aus der mitgeschickten Liste;
    # nur die Auswahl zaehlt, die exakt einem Listeneintrag entspricht.
    wahl, wahl_sicher = _claude_wert(antwort, "mandant")
    treffer = []
    if wahl and wahl_sicher:
        treffer = [a for a in adressen if norm(a.name) == norm(wahl)]
    if not treffer:
        treffer = [a for a in adressen if _ist_empfaenger(name_n, _adress_begriffe(a))]
    if not treffer:
        return None, f"Rechnungsempfaenger {name!r} passt zu keiner Rechnungsadresse"
    if len(treffer) > 1:
        return None, (f"Rechnungsempfaenger {name!r} passt zu mehreren Rechnungsadressen: "
                      + ", ".join(a.name for a in treffer[:4]))
    a = treffer[0]
    if vorschlag_id and vorschlag_id != a.id:
        return None, (f"Claude sagt {a.name}, das Postfach gehoert zu einem anderen "
                      "Mandanten -- bitte entscheiden")
    if name_n not in {norm(t) for t in a.match_terms}:
        a.match_terms = list(a.match_terms) + [name]
        store.save_address(a)
        store.audit("rechnungsadresse_suchbegriff_claude",
                    {"id": a.id, "name": a.name, "suchbegriff": name}, OPERATOR)
    return {"aktion": "suchbegriff", "id": a.id, "name": a.name,
            "suchbegriff": name}, ""


def _vermerk_lieferant(store, sup) -> None:
    vermerk = STAMMDATEN_VERMERK.format(datum=time.strftime("%d.%m.%Y"))
    if "von Claude bestaetigt" in (sup.notes or ""):
        return
    sup.notes = (sup.notes + "\n" if sup.notes else "") + vermerk
    store.save_supplier(sup)


def lokale_ki() -> bool:
    """Lokale KI (Ollama, semantic.py) beim Anlernen mitlaufen lassen?
    Standard NEIN: Claude prueft ohnehin jedes Feld, die lokale KI kostet
    auf einem Laptop ohne Grafikkarte je Rechnung bis zu
    ``RECHNUNG_LLM_TIMEOUT`` Sekunden -- auch fuer Rechnungen, die danach
    nur uebersprungen werden (Lieferant noch Entwurf) -- und ihre Werte
    gelten ohnehin als unsicher. Der Schattenbetrieb misst dann das
    Programm ohne KI. ``RECHNUNG_CLAUDE_LOKALE_KI=1`` schaltet sie zu."""
    return os.environ.get("RECHNUNG_CLAUDE_LOKALE_KI", "").strip().lower() in (
        "1", "true", "yes", "on", "ja")


def stichprobe(sha256: str) -> bool:
    """Deterministisch: geht diese Rechnung zur Kontrolle an den Menschen?
    Nutzt andere Bits des Hashes als modus.stichprobe -- die beiden
    Stichproben sind voneinander unabhaengig."""
    p = stichprobe_prozent()
    if p <= 0 or not sha256:
        return False
    try:
        return int(sha256[8:16], 16) % 100 < p
    except ValueError:
        return False


# ---- Claude-Aufruf ---------------------------------------------------------

_SYSTEM = (
    "Du pruefst die automatische Auslesung einer Eingangsrechnung fuer die "
    "Finanzbuchhaltung. Du bekommst das Original und die Werte, die ein "
    "Programm ausgelesen hat. Gib fuer JEDES angefragte Feld den Wert an, der "
    "tatsaechlich auf der Rechnung steht. Erfinde nichts und rechne nichts "
    "aus: steht ein Wert nicht auf der Rechnung, ist er null. Bist du dir "
    "nicht sicher (unleserlich, mehrdeutig, mehrere Kandidaten), setze "
    "sicher=false. Antworte ausschliesslich mit JSON ohne Markdown."
)

MANDANT_FORMAT = ("die Rechnungsadresse (unser Unternehmen als Empfaenger) -- genau EINER "
                  "dieser Eintraege, exakt so geschrieben, oder null, wenn keiner passt: ")

_FORMAT = {
    "date": "Datum als JJJJ-MM-TT",
    "amount": "Betrag als Zahl mit Punkt als Dezimaltrenner, ohne Waehrung/Tausenderpunkte",
    "number": "genau wie auf der Rechnung geschrieben",
    "text": "genau wie auf der Rechnung geschrieben",
    "name": "Firmenname genau wie auf der Rechnung geschrieben",
}


def _prompt(felder: dict[str, str], programm: dict) -> str:
    zeilen = []
    for name, vtype in felder.items():
        zeilen.append(f"- {name} ({_FORMAT.get(vtype, vtype)}): Programm sagt "
                      f"{json.dumps(programm.get(name), ensure_ascii=False)}")
    return (
        "Felder und Werte des Programms:\n" + "\n".join(zeilen) + "\n\n"
        "Erlaeuterungen: netto = Nettobetrag gesamt, ust_betrag = Umsatzsteuer "
        "gesamt, ust_satz = Steuersatz in Prozent (nur wenn es genau einen gibt), "
        "brutto = Rechnungs-/Gesamtbetrag. Bei Gutschriften die Betraege so, wie "
        "sie auf dem Beleg stehen.\n\n"
        'Antwortformat: {"felder": {"<feldname>": {"wert": <Wert oder null>, '
        '"sicher": true|false, "beleg": "<kurzer Originaltext von der Rechnung>"}}, '
        '"hinweis": "<optional, max. ein Satz>"}'
    )


def _post(body: dict) -> dict:
    req = urllib.request.Request(
        api_url(), data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", "anthropic-version": API_VERSION,
                 **_auth_header()})
    with urllib.request.urlopen(req, timeout=timeout_s()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cli_aufruf(befehl: list[str], eingabe: str, cwd: Path, env: dict,
                timeout: int) -> tuple[int, str, str]:
    """Claude Code starten -> (Rueckgabecode, stdout, stderr). Der Auftrag
    geht ueber stdin, nicht als Argument: unter Windows reicht der npm-Starter
    ``claude.cmd`` Argumente durch cmd.exe, das an Zeichen wie ``|``, ``<``,
    ``>`` oder Zeilenumbruechen scheitert. Eigene Funktion fuer Tests."""
    import subprocess
    r = subprocess.run(befehl, input=eingabe, cwd=str(cwd), env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def _frage_cli(path: Path, prompt: str) -> str:
    """Claude Code (``claude -p``) liest die Rechnung selbst und antwortet
    -> Antworttext. Nur das Lese-Werkzeug ist freigegeben."""
    import subprocess
    exe = cli_befehl()
    if not exe:
        raise ClaudeNichtErreichbar(bereit())
    auftrag = (_SYSTEM + "\n\n"
               f"Lies die Rechnung in der Datei \"{path.name}\" im aktuellen Ordner "
               "(Werkzeug Read) und beantworte dann:\n\n" + prompt)
    befehl = [exe, "-p", "--output-format", "json",
              "--allowedTools", "Read", "--max-turns", "4"]
    if modell():
        befehl += ["--model", modell()]
    env = {k: v for k, v in os.environ.items()
           if k not in ("ANTHROPIC_API_KEY", "RECHNUNG_CLAUDE_API_KEY")}
    try:
        code, out, err = _cli_aufruf(befehl, auftrag, path.parent, env, timeout_s())
    except subprocess.TimeoutExpired as e:
        raise ClaudeNichtErreichbar(f"Claude Code: Zeitgrenze {timeout_s()} s") from e
    except OSError as e:
        raise ClaudeNichtErreichbar(f"Claude Code nicht startbar: {e}") from e
    daten = None
    for zeile in reversed((out or "").strip().splitlines()):
        try:
            daten = json.loads(zeile)
            break
        except json.JSONDecodeError:
            continue
    if not isinstance(daten, dict):
        raise ClaudeNichtErreichbar(f"Claude Code: unerwartete Ausgabe (Code {code}) "
                                    + (err or out or "")[:300].strip())
    if daten.get("is_error") or code != 0:
        raise ClaudeNichtErreichbar("Claude Code: " + str(daten.get("result") or daten.get("subtype")
                                                 or err or code)[:300])
    return str(daten.get("result") or "")


def _parse(raw: str) -> dict | None:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip())
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict) or not isinstance(data.get("felder"), dict):
        return None
    return data


def _cache_path(store, sha256: str, key: str) -> Path:
    return Path(store.root) / "lehrer-cache" / f"{sha256}-{key}.json"


def frage_claude(store, path: Path, sha256: str, felder: dict[str, str],
                 programm: dict, *, post=None) -> dict:
    """-> geparste Antwort ``{"felder": {...}, "hinweis": ...}``.
    Antworten werden je Rechnung/Modell/Frage auf der Platte gemerkt -- ein
    zweiter Lauf ueber denselben Ordner kostet nichts. ``post`` ist fuer
    Tests austauschbar. Wirft ``RuntimeError`` bei Fehlern."""
    path = Path(path)
    media = _MEDIA.get(path.suffix.lower())
    if media is None:
        raise RuntimeError(f"Dateityp {path.suffix} kann Claude nicht direkt lesen")
    prompt = _prompt(felder, programm)
    key = hashlib.sha256((backend() + ":" + modell() + "\n" + prompt)
                         .encode("utf-8")).hexdigest()[:16]
    cp = _cache_path(store, sha256, key)
    if cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    if post is None and backend() == "cli":
        parsed = _parse(_frage_cli(path, prompt))
        if parsed is None:
            raise RuntimeError("Antwort von Claude Code nicht als JSON lesbar")
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
        return parsed
    if post is None:
        if not api_key():
            raise ClaudeNichtErreichbar("kein API-Schluessel (RECHNUNG_CLAUDE_API_KEY)")
        post = _post
    data_b64 = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    block_type = "document" if media == "application/pdf" else "image"
    body = {
        "model": modell(),
        "max_tokens": 2000,
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": [
            {"type": block_type,
             "source": {"type": "base64", "media_type": media, "data": data_b64}},
            {"type": "text", "text": prompt},
        ]}],
    }
    try:
        resp = post(body)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # noqa: BLE001
            pass
        raise ClaudeNichtErreichbar(f"Claude-API HTTP {e.code} {detail}".strip()) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ClaudeNichtErreichbar(f"Claude-API nicht erreichbar: {e}") from e
    text = "".join(b.get("text", "") for b in (resp or {}).get("content", [])
                   if isinstance(b, dict) and b.get("type") == "text")
    parsed = _parse(text)
    if parsed is None:
        raise RuntimeError("Antwort von Claude nicht als JSON lesbar")
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    return parsed


# ---- Abgleich Programm <-> Claude -----------------------------------------

@dataclass
class Abgleich:
    korrekturen: dict[str, str] = field(default_factory=dict)  # Feld -> Rohwert
    unsicher: dict[str, str] = field(default_factory=dict)     # Feld -> Grund
    werte: dict[str, object] = field(default_factory=dict)     # Claudes Werte (geparst)
    # freiwillige Felder, bei denen Claude unsicher war: kein Grund fuer den
    # Menschen, der Wert des Programms bleibt (Plan Aaron 12.11.2026, D)
    hinweise: dict[str, str] = field(default_factory=dict)
    # Felder, die das Programm nur ERRECHNET hatte (gleicher Wert wie Claude)
    # und die trotzdem als Korrektur gehen, damit die Vorlage sie lernt
    errechnet: list[str] = field(default_factory=list)


def _gleich(a, b, vtype: str) -> bool:
    from .textnorm import compact, norm
    if a is None or b is None:
        return a is None and b is None
    if vtype == "amount":
        try:
            return abs(float(a) - float(b)) < 0.005
        except (TypeError, ValueError):
            return False
    if vtype == "number":
        return compact(str(a)) == compact(str(b))
    return norm(str(a)) == norm(str(b))


# Uebungsrechnungen sind oft Jahre alt: fuer den Lehrer gilt dieselbe Grenze
# wie fuer die Pruefung des Programms (validate.check_date: 10 Jahre), nicht
# die enge 2-Jahres-Grenze der lokalen KI (Befund Proberunde 09.11.2026).
DATUM_JAHRE_ZURUECK = 10

# Waehrung steht auf der Rechnung meist als Zeichen, nicht als ISO-Code.
_WAEHRUNG_BELEGE = {
    "EUR": ("eur", "euro", "\u20ac"),
    "CHF": ("chf", "fr.", "sfr", "franken"),
    "USD": ("usd", "$", "us-dollar"),
    "GBP": ("gbp", "\u00a3", "pfund"),
}

_WAEHRUNG_ZEICHEN = {"€": "EUR", "euro": "EUR", "fr.": "CHF", "sfr": "CHF",
                     "$": "USD", "£": "GBP"}


def _waehrung_belegt(text: str, wert) -> str | None:
    """ISO-Code der Waehrung, wenn Code oder Zeichen im Text stehen
    ("EUR" ist auch durch "\u20ac" belegt); sonst ``None``."""
    roh = str(wert or "").strip()
    # Claude antwortet manchmal mit dem Zeichen statt dem Code ("€")
    code = _WAEHRUNG_ZEICHEN.get(roh.lower(), roh.upper())
    belege = _WAEHRUNG_BELEGE.get(code)
    if not belege:
        return None
    t = (text or "").lower()
    return code if any(b in t for b in belege) else None


def abgleichen(text: str, felder: dict[str, str], programm: dict, antwort: dict,
               quellen: dict[str, str] | None = None) -> Abgleich:
    """Claudes Werte gegen die des Programms. ``quellen``: Feldquelle je
    Programmwert (``FieldResult.source``). Hat das Programm einen Wert nur
    errechnet (rechnen.py, z.B. Netto/USt aus Brutto) und steht derselbe
    Wert belegt im PDF, geht er trotzdem als Korrektur: sonst lernt die
    Vorlage seine Position nie, und der Schattenbetrieb -- der VOR der
    Rechenstufe entscheidet -- zaehlt ihn bei jeder Rechnung als "fehlt"
    (Nachtlauf 12./13.11.2026: 30x Amazon fehlerfrei, nie "sicher").
    learn.py lernt errechnete Werte weiterhin nicht von selbst: hier ist
    der Wert durch Claudes Beleg im PDF gefunden, kein Zufallstreffer."""
    from .rechnen import ERRECHNET, KOPF
    from .semantic import _check_value
    from .shadow import REQUIRED

    out = Abgleich()
    claude = antwort.get("felder") or {}
    for name, vtype in felder.items():
        eintrag = claude.get(name)
        prog = programm.get(name)
        if not isinstance(eintrag, dict):
            if prog not in (None, "") or name in REQUIRED:
                out.unsicher[name] = "keine Angabe von Claude"
            continue
        wert = eintrag.get("wert")
        sicher = eintrag.get("sicher") is True
        if wert in (None, ""):
            out.werte[name] = None
            if prog in (None, ""):
                if name in REQUIRED:
                    out.unsicher[name] = "Pflichtfeld fehlt (auch laut Claude)"
                continue
            # Programm hat einen Wert, Claude sagt "steht nicht drauf": nicht
            # einfach loeschen -- das entscheidet ein Mensch.
            out.unsicher[name] = "Claude findet keinen Wert, Programm schon"
            continue
        if vtype == "number" and isinstance(wert, str):
            # "R 811658": die Nummer-Pruefung nimmt sonst nur das erste Wort
            # ("R") und verwirft Claudes richtigen Wert (Testrunde Langdock
            # 12.11.2026). Ohne Leerzeichen wird sie gegen den Text belegt.
            wert = re.sub(r"\s+", "", wert)
        if name == "waehrung" and _waehrung_belegt(text, wert):
            value, grund = _waehrung_belegt(text, wert), ""
        else:
            value, grund = _check_value(text, name, wert, vtype,
                                        datum_jahre_zurueck=DATUM_JAHRE_ZURUECK)
        if grund:
            out.unsicher[name] = f"Claude-Wert {wert!r}: {grund}"
            continue
        out.werte[name] = value
        if not sicher:
            out.unsicher[name] = "Claude ist unsicher"
            continue
        if not _gleich(prog, value, vtype):
            out.korrekturen[name] = str(wert)
        elif (quellen or {}).get(name) in (ERRECHNET, KOPF):
            out.korrekturen[name] = str(wert)
            out.errechnet.append(name)
    for name in [n for n in out.unsicher if n not in REQUIRED]:
        out.hinweise[name] = out.unsicher.pop(name)
    return out


# ---- Eine Rechnung anlernen ------------------------------------------------

def _felder() -> dict[str, str]:
    from .template import sichtbare_felder
    return {n: t for n, (t, _req) in sichtbare_felder().items() if t in _SIMPLE_TYPES}


def _protokoll(store, rec: dict) -> None:
    p = Path(store.root) / "lehrer.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def protokoll(store) -> list[dict]:
    p = Path(store.root) / "lehrer.jsonl"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


class _LernStore:
    """Sicht auf den Store, in der genau diese eine Rechnung als bestaetigt
    gilt -- nur fuer ``learn.learn_from_confirmed``. Der echte Store (und
    damit finalize/JobRouter) sieht Claudes Pruefung nie als Bestaetigung."""

    def __init__(self, store, sha256: str):
        self._store = store
        self._sha = sha256

    def __getattr__(self, name):
        return getattr(self._store, name)

    def is_confirmed(self, sha: str) -> bool:
        return sha == self._sha or self._store.is_confirmed(sha)


def _wartete_auf_lieferant(r: dict) -> bool:
    """Protokolleintrag, der nur am fehlenden/unbestaetigten Lieferanten hing
    (auch aeltere Eintraege, die noch als 'mensch' gezaehlt wurden)."""
    return bool(r.get("wartet_auf_lieferant")) or any(
        "Lieferant unbekannt" in g or "Lieferant ist nur ein Entwurf" in g
        for g in (r.get("gruende") or []))


def angelernt(store, sha256: str) -> bool:
    """Hat Claude diese Rechnung schon bestaetigt (eigenes Protokoll)?"""
    return any(r.get("sha256") == sha256 and r.get("ergebnis") == ERG_BESTAETIGT
               for r in protokoll(store))


def _bestaetigen(store, path: Path, res, doc):
    """Wie der Klick "Extraktion bestaetigen" im Training-UI
    (web/server.py ``confirm``), aber als Operator ``claude``, OHNE
    Journal-Eintrag und OHNE Eintrag im Bestaetigungs-Protokoll des
    Menschen: Uebungsrechnungen landen nie in den Buechern."""
    from . import eventlog, learn, shadow
    from .pipeline import process

    if res.template_id:
        tpl = store.template(res.template_id)
        if tpl is not None:
            was_secured = tpl.secured
            tpl.confirmations += 1
            store.save_template(tpl)
            if tpl.secured != was_secured:
                res = process(path, store, use_llm=lokale_ki())
    shadow.record(store, res, OPERATOR)
    eventlog.record(store, res, OPERATOR)
    gelernt = learn.learn_from_confirmed(_LernStore(store, res.sha256), res, doc, OPERATOR)
    _vermerk_im_ordner(store, res.sha256)
    return res, gelernt


def _vermerk_im_ordner(store, sha256: str) -> None:
    """Liegt die Rechnung im Ordner "Menschliche Bearbeitung", bleibt sie dort
    (NICHT ``human.erledigt``: das loescht die Arbeitskopie, und die kann die
    einzige Kopie sein). Sie bekommt nur einen Hinweis, dass Claude sie
    geprueft hat -- die Bestaetigung fuer die Buecher bleibt beim Menschen."""
    from . import human

    gefunden = human.finde(store, sha256)
    if gefunden is None:
        return
    side, daten = gefunden
    vermerk = "Von Claude geprueft und bestaetigt (Anlernphase) -- Vorlage daraus gelernt"
    hinweise = list(daten.get("hinweise") or [])
    if vermerk not in hinweise:
        hinweise.append(vermerk)
    human._write(side, dict(daten, hinweise=hinweise,
                            claude_geprueft=time.strftime("%Y-%m-%dT%H:%M:%S%z")))


def anlernen_datei(store, path: str | Path, *, post=None, erneut: bool = False) -> dict:
    """Eine Uebungsrechnung von Claude pruefen, korrigieren und bestaetigen
    lassen (oder an den Menschen geben). -> Ergebnis-Datensatz."""
    from . import eventlog, human
    from .pipeline import load_document, process

    path = Path(path)
    rec: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "datei": path.name,
                 "weg": backend(), "modell": modell()}

    try:
        doc, o = load_document(path, store)
    except Exception as e:  # kaputte Datei (z.B. kein PDF): Runde nicht abbrechen
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        rec["sha256"] = sha
        fehl = [r for r in protokoll(store)
                if r.get("sha256") == sha and r.get("ergebnis") == ERG_FEHLER]
        if len(fehl) >= MAX_FEHLVERSUCHE:
            rec.update(ergebnis=ERG_UEBERSPRUNGEN,
                       gruende=[f"{MAX_FEHLVERSUCHE}x nicht lesbar -- siehe lehrer.jsonl"])
            return rec
        rec.update(ergebnis=ERG_FEHLER,
                   gruende=[f"nicht lesbar: {type(e).__name__}: {str(e)[:120]}"])
        _protokoll(store, rec)
        return rec
    if doc is None:
        rec.update(ergebnis=ERG_MENSCH, gruende=[f"nicht lesbar: {getattr(o, 'error', '')}"])
        return rec
    rec["sha256"] = doc.sha256
    if store.is_confirmed(doc.sha256) or angelernt(store, doc.sha256):
        rec.update(ergebnis=ERG_UEBERSPRUNGEN, gruende=["schon bestaetigt"])
        return rec
    # Dauerbetrieb (windows/anlernen.cmd): was schon beim Menschen liegt oder
    # wiederholt scheiterte, nicht in jeder Runde erneut anfassen -- sonst
    # verbraucht es das Rundenlimit und neue Rechnungen kommen nie dran.
    frueher = [r.get("ergebnis") for r in protokoll(store)
               if r.get("sha256") == doc.sha256 and not r.get("claude_weg")
               and not _wartete_auf_lieferant(r)]
    # ``erneut``: Rechnungen, die schon einmal an den Menschen gingen, noch
    # einmal pruefen (z.B. nach neuen Regeln) -- bestaetigte bleiben unberuehrt.
    if ERG_MENSCH in frueher and not erneut:
        rec.update(ergebnis=ERG_UEBERSPRUNGEN, gruende=["liegt beim Menschen"])
        return rec
    if frueher.count(ERG_FEHLER) >= MAX_FEHLVERSUCHE:
        rec.update(ergebnis=ERG_UEBERSPRUNGEN,
                   gruende=[f"{MAX_FEHLVERSUCHE}x gescheitert -- siehe lehrer.jsonl"])
        return rec

    res = process(path, store, use_llm=lokale_ki())
    if res.aborted:
        rec.update(ergebnis=ERG_FEHLER, gruende=list(res.hold_reasons or ["abgebrochen"]))
        _protokoll(store, rec)
        return rec
    rec["lieferant"] = res.supplier_name or res.supplier_id
    if res.extraction == "einvoice":
        # Werte stehen im XML -- da gibt es kein Layout anzulernen.
        rec.update(ergebnis=ERG_UEBERSPRUNGEN, gruende=["E-Rechnung (nichts anzulernen)"])
        return rec

    def _zum_menschen(gruende: list[str], r=res) -> dict:
        liste = human.gruende(r) + [{"art": ART_CLAUDE, "grund": g, "felder": []}
                                    for g in gruende]
        human.ablegen(store, r, liste, OPERATOR)
        rec.update(ergebnis=ERG_MENSCH, gruende=gruende)
        _protokoll(store, rec)
        return rec

    # Stammdaten entscheidet immer ein Mensch. Bis dahin nur ueberspringen
    # (kostet nichts, zaehlt nicht zum Rundenlimit) -- sobald der Lieferant
    # bestaetigt ist, greift die naechste Runde die Rechnung von selbst auf.
    sup = store.supplier(res.supplier_id) if res.supplier_id else None
    if sup is None and stammdaten_an():
        # Uebungsphase: Lieferant ganz unbekannt -> Entwurf anlegen wie das
        # Programmfenster (create_draft_supplier) und von Claude klaeren
        # lassen. Bisher wurden solche Rechnungen nur uebersprungen, weil
        # Entwuerfe nur bei offenem Programmfenster entstehen (Abholung
        # 12.11.2026: 154 von 235 neuen Rechnungen).
        res = process(path, store, use_llm=lokale_ki(), create_draft_supplier=True)
        sup = store.supplier(res.supplier_id) if res.supplier_id else None
        if sup is not None:
            rec["entwurf_angelegt"] = sup.id
    entwurf = sup if (sup is not None and not getattr(sup, "bestaetigt", True)
                      and stammdaten_an()) else None
    if entwurf is None and (sup is None or not getattr(sup, "bestaetigt", True)):
        rec.update(ergebnis=ERG_UEBERSPRUNGEN, wartet_auf_lieferant=True, gruende=[
            "Lieferant unbekannt -- bitte im Programm anlegen" if sup is None else
            "Lieferant ist nur ein Entwurf -- bitte im Programm bestaetigen"])
        return rec

    felder = _felder()
    programm = {n: (res.fields[n].value if n in res.fields
                    and res.fields[n].source != "missing" else None) for n in felder}
    frage = dict(felder)
    if entwurf is not None:
        # Uebungsphase: Claude nennt auch den Lieferanten (stammdaten_klaeren)
        frage["lieferant_name"] = "name"
        programm["lieferant_name"] = None
    ohne_mandant = (stammdaten_an() and not res.billing_address_id
                    and bool(store.addresses()))
    if stammdaten_an():
        # C: Kennungen des Lieferanten mitpruefen
        for kenn in ("iban", "ustid"):
            frage[kenn] = kenn
            fr = res.fields.get(kenn)
            programm[kenn] = fr.value if fr is not None and fr.source != "missing" else None
    if ohne_mandant:
        # B: Rechnungsempfaenger (wie auf der Rechnung) und Mandant (aus der Liste)
        frage["rechnungsempfaenger"] = "name"
        programm["rechnungsempfaenger"] = None
        frage["mandant"] = MANDANT_FORMAT + " | ".join(
            sorted(a.name for a in store.addresses()))
        programm["mandant"] = None
    try:
        antwort = frage_claude(store, path, doc.sha256, frage, programm, post=post)
    except ClaudeNichtErreichbar as e:
        # liegt nicht an der Rechnung: nicht als Fehlversuch zaehlen
        rec.update(ergebnis=ERG_FEHLER, gruende=[str(e)], claude_weg=True)
        _protokoll(store, rec)
        return rec
    except RuntimeError as e:
        rec.update(ergebnis=ERG_FEHLER, gruende=[str(e)])
        _protokoll(store, rec)
        return rec

    if entwurf is not None:
        aktion, grund = stammdaten_klaeren(store, entwurf, doc, antwort)
        if aktion is None:
            return _zum_menschen(["Stammdaten: " + grund])
        rec["stammdaten"] = aktion
        res = process(path, store, use_llm=lokale_ki())
        rec["lieferant"] = res.supplier_name or res.supplier_id

    neu_rechnen = False
    if ohne_mandant:
        m_aktion, m_grund = mandant_klaeren(
            store, doc, antwort, getattr(res, "billing_address_suggestion_id", None))
        if m_aktion is not None:
            rec["mandant"] = m_aktion
            neu_rechnen = True
        else:
            rec["mandant_offen"] = m_grund
    if stammdaten_an():
        k = kennungen_pruefen(store, res.supplier_id, doc, antwort)
        if k:
            rec["kennungen"] = k
            neu_rechnen = True
    if neu_rechnen:
        res = process(path, store, use_llm=lokale_ki())
    programm = {n: (res.fields[n].value if n in res.fields
                    and res.fields[n].source != "missing" else None) for n in felder}

    quellen = {n: res.fields[n].source for n in felder if n in res.fields}
    ab = abgleichen(doc.text, felder, programm, antwort, quellen=quellen)
    rec["claude"] = {k: v for k, v in ab.werte.items()}
    rec["korrigiert"] = sorted(ab.korrekturen)
    if ab.errechnet:
        rec["errechnet_gelernt"] = sorted(ab.errechnet)
    if ab.hinweise:
        rec["hinweise_felder"] = dict(ab.hinweise)
    rec["hinweis"] = antwort.get("hinweis") or ""

    vorher = store.corrections(doc.sha256)
    for name, roh in ab.korrekturen.items():
        quelle = res.fields[name].source if name in res.fields else "missing"
        store.save_correction(doc.sha256, name, roh, OPERATOR)
        eventlog.record_correction(store, doc.sha256, name, vorher.get(name), roh, OPERATOR,
                                   supplier_id=res.supplier_id, supplier_name=res.supplier_name,
                                   file=path.name, template_id=res.template_id,
                                   quelle_vorher=quelle)
    if ab.korrekturen:
        res = process(path, store, use_llm=lokale_ki())

    gruende = [f"{n}: {g}" for n, g in sorted(ab.unsicher.items())]
    if gruende:
        return _zum_menschen(["Claude unsicher -- " + "; ".join(gruende)], res)
    rest = human.gruende(res)
    if rest:
        return _zum_menschen(["nach Claudes Pruefung noch offen: "
                              + "; ".join(g["grund"] for g in rest)], res)
    if stichprobe(doc.sha256):
        rec["stichprobe"] = True
        return _zum_menschen(["Stichprobe: Claudes Pruefung bitte kontrollieren"
                              + (f" (Claude hat {', '.join(rec['korrigiert'])} korrigiert)"
                                 if rec["korrigiert"] else "")], res)

    res, gelernt = _bestaetigen(store, path, res, doc)
    rec.update(ergebnis=ERG_BESTAETIGT, gelernt=gelernt.to_dict(),
               vorlage=res.template_id, status=res.status)
    _protokoll(store, rec)
    return rec


def anlernen(store, ordner: str | Path, *, limit: int = 0, post=None,
             fortschritt=None, zeitbudget_s: float = 0.0, erneut: bool = False) -> list[dict]:
    """Alle Rechnungen eines Ordners nacheinander anlernen. ``limit``:
    hoechstens so viele nicht uebersprungene Rechnungen; ``zeitbudget_s``:
    nach Ablauf keine weitere Rechnung mehr anfangen (0 = unbegrenzt) --
    so endet eine Hintergrundrunde (windows/anlernen.cmd) sauber zwischen
    zwei Rechnungen, statt von der Aufgabenplanung abgeschossen zu werden."""
    from .pipeline import invoice_files

    start = time.monotonic()
    out = []
    for p in invoice_files(ordner):
        if limit and len([r for r in out if r["ergebnis"] != ERG_UEBERSPRUNGEN]) >= limit:
            break
        # die erste Rechnung faengt immer an, danach zaehlt das Budget
        if zeitbudget_s and out and time.monotonic() - start >= zeitbudget_s:
            break
        try:
            r = anlernen_datei(store, p, post=post, erneut=erneut)
        except Exception as e:  # Programmfehler bei EINER Rechnung: Runde laeuft weiter
            r = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "datei": Path(p).name,
                 "weg": backend(), "modell": modell(), "ergebnis": ERG_FEHLER,
                 "gruende": [f"Programmfehler: {type(e).__name__}: {str(e)[:160]}"]}
            try:
                r["sha256"] = hashlib.sha256(Path(p).read_bytes()).hexdigest()
            except OSError:
                pass
            _protokoll(store, r)
        out.append(r)
        if fortschritt:
            fortschritt(r)
        if r.get("claude_weg"):
            break  # Claude gerade nicht nutzbar (Limit/Netz): naechste Runde
    return out


# ---- Bilanz: wie gut ist der Lehrer, wie weit ist der Schueler? -----------

def bilanz(store) -> dict:
    """Zahlen fuer die Entscheidung "reif fuer den Echtbetrieb?".

    - ``lehrer``: wie oft musste ein Mensch Claudes Ergebnis in der
      Stichprobe korrigieren (Claude-Fehlerquote).
    - ``schueler``: Schattenbetrieb des Programms (modus.fortschritt).
    """
    from . import modus

    log = protokoll(store)
    letzte: dict[str, dict] = {}
    for r in log:
        if r.get("sha256"):
            letzte[r["sha256"]] = r
    zaehl = {ERG_BESTAETIGT: 0, ERG_MENSCH: 0, ERG_FEHLER: 0}
    for r in letzte.values():
        if r.get("ergebnis") in zaehl and not _wartete_auf_lieferant(r):
            zaehl[r["ergebnis"]] += 1

    stich = {s: r for s, r in letzte.items() if r.get("stichprobe")}
    geprueft = fehler = 0
    fehler_felder: dict[str, int] = {}
    if stich:
        menschliche = {}
        for a in store.audit_log():
            if a.get("action") != "correct" or a.get("operator") == OPERATOR:
                continue
            d = a.get("detail") or {}
            if d.get("sha256") in stich:
                menschliche.setdefault(d["sha256"], {})[d.get("field")] = d.get("value")
        felder = _felder()
        from .textnorm import coerce
        for sha, r in stich.items():
            if not store.is_confirmed(sha):
                continue  # Mensch hat noch nicht kontrolliert
            geprueft += 1
            falsch = []
            for f, v in (menschliche.get(sha) or {}).items():
                vt = felder.get(f, "text")
                val, ok = coerce(str(v), vt)
                if not _gleich((r.get("claude") or {}).get(f), val if ok else v, vt):
                    falsch.append(f)
            if falsch:
                fehler += 1
                for f in falsch:
                    fehler_felder[f] = fehler_felder.get(f, 0) + 1
    stammdaten = {"bestaetigt": 0, "uebernommen": 0}
    for r in log:
        a = (r.get("stammdaten") or {}).get("aktion")
        if a in stammdaten:
            stammdaten[a] += 1
    return {
        "rechnungen": len(letzte),
        "stammdaten_von_claude": stammdaten,
        "von_claude_bestaetigt": zaehl[ERG_BESTAETIGT],
        "an_menschen": zaehl[ERG_MENSCH],
        "fehler": zaehl[ERG_FEHLER],
        "lehrer": {"stichprobe_kontrolliert": geprueft, "claude_lag_falsch": fehler,
                   "felder": fehler_felder,
                   "quote": round(fehler / geprueft, 3) if geprueft else None},
        "schueler": modus.fortschritt(store),
    }


# ---- Aufruf: python -m rechnung.lehrer ------------------------------------

def main(argv=None) -> int:
    """``python -m rechnung.lehrer [--data DIR] <ordner> [--limit N]``
    bzw. ``python -m rechnung.lehrer --bilanz``."""
    import argparse
    import sys

    from .modus import text as modus_text
    from .store import Store

    p = argparse.ArgumentParser(prog="python -m rechnung.lehrer",
                                description="Anlernphase: Claude prueft Uebungsrechnungen")
    p.add_argument("ordner", nargs="?", help="Ordner mit Uebungsrechnungen")
    p.add_argument("--data", default=os.environ.get("RECHNUNG_DATA", "./data"))
    p.add_argument("--limit", type=int, default=0,
                   help="hoechstens so viele Rechnungen je Lauf (0 = alle)")
    p.add_argument("--erneut", action="store_true",
                   help="auch Rechnungen pruefen, die schon beim Menschen liegen (einmalig nach neuen Regeln)")
    p.add_argument("--minuten", type=float, default=0,
                   help="nach so vielen Minuten keine weitere Rechnung anfangen (0 = aus)")
    p.add_argument("--bilanz", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    store = Store(a.data)

    if a.bilanz:
        b = bilanz(store)
        if a.json:
            print(json.dumps(b, indent=2, ensure_ascii=False, default=str))
            return 0
        le = b["lehrer"]
        print(f"Rechnungen: {b['rechnungen']}  von Claude bestaetigt: "
              f"{b['von_claude_bestaetigt']}  an Menschen: {b['an_menschen']}  "
              f"Fehler: {b['fehler']}")
        quote = "-" if le["quote"] is None else f"{le['quote'] * 100:.1f} %"
        print(f"Lehrer-Kontrolle (Stichprobe): {le['stichprobe_kontrolliert']} kontrolliert, "
              f"Claude lag {le['claude_lag_falsch']}x falsch ({quote})")
        for f, n in sorted(le["felder"].items(), key=lambda x: -x[1]):
            print(f"   {f}: {n}x")
        st = b["stammdaten_von_claude"]
        if st["bestaetigt"] or st["uebernommen"]:
            print(f"Stammdaten von Claude (vor 'autonom' pruefen): {st['bestaetigt']} "
                  f"Entwuerfe bestaetigt, {st['uebernommen']} in bestehende uebernommen")
        print("Programm (Schattenbetrieb): " + modus_text(b["schueler"]))
        return 0
    if not a.ordner:
        p.error("Ordner angeben (oder --bilanz)")
    if not an():
        print("Lehrer ist aus. Einschalten mit RECHNUNG_CLAUDE_LEHRER=1 "
              "(und RECHNUNG_CLAUDE_API_KEY oder RECHNUNG_CLAUDE_BACKEND=cli setzen).")
        return 1
    grund = bereit()
    if grund:
        print(grund)
        return 1

    def _zeige(r: dict) -> None:
        if a.json:
            return
        extra = "  korrigiert: " + ", ".join(r["korrigiert"]) if r.get("korrigiert") else ""
        if r.get("gruende"):
            extra += "  (" + "; ".join(r["gruende"]) + ")"
        print(f"[{r['ergebnis']:13s}] {r['datei']}{extra}")

    res = anlernen(store, a.ordner, limit=a.limit, fortschritt=_zeige,
                   zeitbudget_s=max(0.0, a.minuten) * 60, erneut=a.erneut)
    if a.json:
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))
        return 0
    n = {e: sum(1 for r in res if r["ergebnis"] == e)
         for e in (ERG_BESTAETIGT, ERG_MENSCH, ERG_UEBERSPRUNGEN, ERG_FEHLER)}
    print(f"\nFertig: {n[ERG_BESTAETIGT]} von Claude bestaetigt, {n[ERG_MENSCH]} an Menschen, "
          f"{n[ERG_UEBERSPRUNGEN]} uebersprungen, {n[ERG_FEHLER]} Fehler.")
    print("Stand pruefen: python -m rechnung.lehrer --bilanz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
