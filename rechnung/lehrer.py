"""Claude als Lehrer in der Anlernphase (``python -m rechnung.lehrer``).

Ersetzt in der Anlernphase den Menschen beim Pruefen/Korrigieren/Bestaetigen
von UEBUNGS-Rechnungen, damit die Vorlagen (Stufe 1, learn.py) schneller
reifen. Ablauf je Rechnung:

1. Das Programm liest die Rechnung wie immer aus (``pipeline.process``).
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
   gefragt: Stammdaten entscheidet immer ein Mensch. Die Rechnung wartet und
   wird automatisch aufgegriffen, sobald der Lieferant bestaetigt ist.

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

_FORMAT = {
    "date": "Datum als JJJJ-MM-TT",
    "amount": "Betrag als Zahl mit Punkt als Dezimaltrenner, ohne Waehrung/Tausenderpunkte",
    "number": "genau wie auf der Rechnung geschrieben",
    "text": "genau wie auf der Rechnung geschrieben",
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
        API_URL, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"content-type": "application/json", "x-api-key": api_key(),
                 "anthropic-version": API_VERSION})
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


def abgleichen(text: str, felder: dict[str, str], programm: dict, antwort: dict) -> Abgleich:
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
        value, grund = _check_value(text, name, wert, vtype)
        if grund:
            out.unsicher[name] = f"Claude-Wert {wert!r}: {grund}"
            continue
        out.werte[name] = value
        if not sicher:
            out.unsicher[name] = "Claude ist unsicher"
            continue
        if not _gleich(prog, value, vtype):
            out.korrekturen[name] = str(wert)
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
                res = process(path, store)
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


def anlernen_datei(store, path: str | Path, *, post=None) -> dict:
    """Eine Uebungsrechnung von Claude pruefen, korrigieren und bestaetigen
    lassen (oder an den Menschen geben). -> Ergebnis-Datensatz."""
    from . import eventlog, human
    from .pipeline import load_document, process

    path = Path(path)
    rec: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "datei": path.name,
                 "weg": backend(), "modell": modell()}

    doc, o = load_document(path, store)
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
    if ERG_MENSCH in frueher:
        rec.update(ergebnis=ERG_UEBERSPRUNGEN, gruende=["liegt beim Menschen"])
        return rec
    if frueher.count(ERG_FEHLER) >= MAX_FEHLVERSUCHE:
        rec.update(ergebnis=ERG_UEBERSPRUNGEN,
                   gruende=[f"{MAX_FEHLVERSUCHE}x gescheitert -- siehe lehrer.jsonl"])
        return rec

    res = process(path, store)
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
    if sup is None or not getattr(sup, "bestaetigt", True):
        rec.update(ergebnis=ERG_UEBERSPRUNGEN, wartet_auf_lieferant=True, gruende=[
            "Lieferant unbekannt -- bitte im Programm anlegen" if sup is None else
            "Lieferant ist nur ein Entwurf -- bitte im Programm bestaetigen"])
        return rec

    felder = _felder()
    programm = {n: (res.fields[n].value if n in res.fields
                    and res.fields[n].source != "missing" else None) for n in felder}
    try:
        antwort = frage_claude(store, path, doc.sha256, felder, programm, post=post)
    except ClaudeNichtErreichbar as e:
        # liegt nicht an der Rechnung: nicht als Fehlversuch zaehlen
        rec.update(ergebnis=ERG_FEHLER, gruende=[str(e)], claude_weg=True)
        _protokoll(store, rec)
        return rec
    except RuntimeError as e:
        rec.update(ergebnis=ERG_FEHLER, gruende=[str(e)])
        _protokoll(store, rec)
        return rec

    ab = abgleichen(doc.text, felder, programm, antwort)
    rec["claude"] = {k: v for k, v in ab.werte.items()}
    rec["korrigiert"] = sorted(ab.korrekturen)
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
        res = process(path, store)

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
             fortschritt=None, zeitbudget_s: float = 0.0) -> list[dict]:
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
        r = anlernen_datei(store, p, post=post)
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
    return {
        "rechnungen": len(letzte),
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
                   zeitbudget_s=max(0.0, a.minuten) * 60)
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
