"""Rechnung hinter einem Link holen -- VORBEREITET, NICHT SCHARF.

Aaron 16.11.2026: "Das automatisch Link oeffnen und Rechnungen herunterladen
koennen wir zwar vorbereiten, aber nicht scharf stellen -- ich moechte das
noch mit der IT klaeren, wie weit ich gehen darf und ob das ungefaehrlich
ist." Beschreibung fuer die IT: ``IT-FREIGABE-AENDERUNG-LINKABRUF.md``.

Schalter ``RECHNUNG_LINK_ABRUF``: ``aus`` (Standard) oder ``an``. Solange er
aus ist, oeffnet das Programm keinen einzigen Link -- ``rechnung link-abruf``
zeigt dann nur, WAS es holen WUERDE (Probelauf, ohne Netz).

Es wird nie "pauschal alles" geoeffnet. Ein Link wird nur geholt, wenn ALLES
zutrifft:

1. die Mail liegt offen in "Menschliche Bearbeitung" (kein Rechnungsanhang),
2. der Absender gehoert zu einem BESTAETIGTEN Lieferanten (Mail-Domain aus
   den Stammdaten),
3. fuer genau diesen Lieferanten hat ein Mensch die Link-Domain freigegeben
   (``rechnung link-freigabe``, Datei ``data/link-freigaben.json``),
4. der Link sieht nach direktem PDF-Download aus (``linkmails.link_klasse``),
5. HTTPS auf Port 443, ohne Benutzer/Passwort in der Adresse, die Domain
   ist freigegeben -- auch nach jeder Weiterleitung (hoechstens 3),
6. der Name loest nur auf oeffentliche Adressen auf (nie ins Firmennetz,
   nie auf den eigenen Rechner); verbunden wird genau mit der gepruften
   Adresse, das Zertifikat muss zum Namen passen,
7. die Antwort ist eine PDF (beginnt mit ``%PDF-``), hoechstens 20 MB,
   innerhalb von 20 s.

Kein Browser, kein JavaScript, keine Cookies, keine Anmeldung, nur GET. Die
geholte PDF geht in den Eingang und von dort denselben Weg wie ein Anhang
(Zeitgrenze je Rechnung, guard.py). Jeder Abruf und jeder
Fehlschlag steht im Audit-Protokoll -- nur Domain, Groesse und Pruefsumme,
nie die vollstaendige Adresse (sie kann einen Zugriffsschluessel enthalten).
Die Mail bleibt in "Menschliche Bearbeitung" stehen (mit Vermerk), bis der
Mensch sie erledigt."""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import socket
import ssl
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from . import human, linkmails

MODUS_AUS, MODUS_AN = "aus", "an"
MAX_BYTES = 20 * 1024 * 1024
TIMEOUT_S = 20
MAX_WEITERLEITUNGEN = 3
MAX_JE_LAUF = 20
_FREIGABE_DATEI = "link-freigaben.json"
_PDF_TYPEN = ("application/pdf", "application/octet-stream", "binary/octet-stream",
              "application/x-pdf", "application/download", "application/force-download")


def modus() -> str:
    """``an`` nur, wenn ausdruecklich so gesetzt -- alles andere ist ``aus``."""
    return MODUS_AN if os.environ.get("RECHNUNG_LINK_ABRUF", "").strip().lower() == MODUS_AN \
        else MODUS_AUS


# ---- Freigaben je Lieferant ------------------------------------------------

def _freigabe_pfad(store) -> Path:
    return Path(store.root) / _FREIGABE_DATEI


def freigaben(store) -> dict[str, list[str]]:
    p = _freigabe_pfad(store)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): sorted({str(x) for x in v}) for k, v in d.items() if isinstance(v, list)}


def _norm_domain(d: str) -> str:
    d = str(d or "").strip().lower()
    return linkmails.domain(d if "://" in d else "https://" + d)


def freigeben(store, lieferant_id: str, domain_: str, operator: str = "",
              entfernen: bool = False) -> dict[str, list[str]]:
    """Link-Domain fuer einen Lieferanten freigeben (bzw. entziehen)."""
    if store.supplier(lieferant_id) is None:
        raise ValueError(f"Lieferant {lieferant_id!r} gibt es nicht")
    d = _norm_domain(domain_)
    if "." not in d:
        raise ValueError(f"keine gueltige Domain: {domain_!r}")
    alle = freigaben(store)
    aktuell = set(alle.get(lieferant_id, []))
    (aktuell.discard if entfernen else aktuell.add)(d)
    if aktuell:
        alle[lieferant_id] = sorted(aktuell)
    else:
        alle.pop(lieferant_id, None)
    p = _freigabe_pfad(store)
    p.write_text(json.dumps(alle, indent=2, ensure_ascii=False), encoding="utf-8")
    store.audit("link_freigabe_entzogen" if entfernen else "link_freigabe",
                {"lieferant": lieferant_id, "domain": d}, operator)
    return alle


# ---- Kandidaten (ohne Netz) ------------------------------------------------

@dataclass
class Kandidat:
    sha256: str          # der Mail im Ordner "Menschliche Bearbeitung"
    datei: str
    lieferant_id: str
    lieferant_name: str
    url: str
    domain: str


@dataclass
class Auswahl:
    mails: int = 0
    kandidaten: list[Kandidat] = field(default_factory=list)
    uebersprungen: Counter = field(default_factory=Counter)


GRUND_KEIN_LIEFERANT = "Absender ist kein bestaetigter Lieferant"
GRUND_KEINE_FREIGABE = "Lieferant hat keine Link-Freigabe"
GRUND_KEIN_LINK = "kein direkter Download-Link auf freigegebener Domain"
GRUND_SCHON_GEHOLT = "schon per Link geholt"


def _lieferant_je_domain(store) -> dict[str, object]:
    out: dict[str, object] = {}
    for s in store.suppliers():
        if not getattr(s, "bestaetigt", True):
            continue
        for d in getattr(s, "domains", None) or []:
            out.setdefault(_norm_domain(d), s)
    return out


def auswahl(store) -> Auswahl:
    """Welche Links WUERDEN geholt -- liest nur, oeffnet nichts."""
    a = Auswahl()
    je_domain = _lieferant_je_domain(store)
    frei = freigaben(store)
    for rec in human.offen(store):
        if not human.ist_mail(rec):
            continue
        p = linkmails._eml_pfad(store, rec)
        if p is None:
            continue
        a.mails += 1
        if rec.get("link_geholt"):
            a.uebersprungen[GRUND_SCHON_GEHOLT] += 1
            continue
        absender = human.mail_absender(store, rec)
        lieferant = je_domain.get(_norm_domain(absender.split("@")[-1])) if "@" in absender else None
        if lieferant is None:
            a.uebersprungen[GRUND_KEIN_LIEFERANT] += 1
            continue
        erlaubt = set(frei.get(lieferant.id, []))
        if not erlaubt:
            a.uebersprungen[GRUND_KEINE_FREIGABE] += 1
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        treffer = next(((u, linkmails.domain(u)) for u, t in linkmails.links_aus_mail(raw)
                        if u.lower().startswith("https://")
                        and linkmails.link_klasse(u, t) == "direkt"
                        and linkmails.domain(u) in erlaubt), None)
        if treffer is None:
            a.uebersprungen[GRUND_KEIN_LINK] += 1
            continue
        a.kandidaten.append(Kandidat(sha256=rec.get("sha256", ""), datei=rec.get("datei", ""),
                                     lieferant_id=lieferant.id, lieferant_name=lieferant.name,
                                     url=treffer[0], domain=treffer[1]))
    return a


# ---- Abruf (nur mit Schalter "an") -----------------------------------------

class AbrufFehler(Exception):
    pass


def _pruefe_adresse(url: str, erlaubt: set[str]):
    p = urlsplit(url)
    if p.scheme.lower() != "https":
        raise AbrufFehler("nicht HTTPS")
    if p.username or p.password:
        raise AbrufFehler("Benutzer/Passwort in der Adresse")
    if p.port not in (None, 443):
        raise AbrufFehler("anderer Port als 443")
    if not p.hostname:
        raise AbrufFehler("keine Adresse")
    if linkmails.domain(url) not in erlaubt:
        raise AbrufFehler("Domain nicht freigegeben")
    return p


def _oeffentliche_ip(host: str) -> str:
    """Name -> EINE gepruefte Adresse. Jede aufgeloeste Adresse muss
    oeffentlich sein -- sonst koennte ein Link ins Firmennetz zeigen."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError as e:
        raise AbrufFehler("Name nicht aufloesbar") from e
    ips = sorted({i[4][0] for i in infos})
    if not ips:
        raise AbrufFehler("Name nicht aufloesbar")
    for ip in ips:
        if not ipaddress.ip_address(ip.split("%")[0]).is_global:
            raise AbrufFehler("Adresse im internen Netz")
    return ips[0]


class _GepinnteVerbindung(http.client.HTTPSConnection):
    """HTTPS genau zur gepruften Adresse; Zertifikat wird gegen den NAMEN
    geprueft (SNI) -- ein zweites Aufloesen kann nicht umlenken."""

    def __init__(self, host: str, ip: str, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._gepruefte_ip = ip

    def connect(self):
        sock = socket.create_connection((self._gepruefte_ip, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _verbindung(host: str, ip: str, timeout: float):
    return _GepinnteVerbindung(host, ip, timeout)


def abrufen(url: str, erlaubt: set[str], *, max_bytes: int = MAX_BYTES,
            timeout: float = TIMEOUT_S) -> bytes:
    for _ in range(MAX_WEITERLEITUNGEN + 1):
        p = _pruefe_adresse(url, erlaubt)
        ip = _oeffentliche_ip(p.hostname)
        conn = _verbindung(p.hostname, ip, timeout)
        try:
            pfad = (p.path or "/") + (f"?{p.query}" if p.query else "")
            conn.request("GET", pfad, headers={
                "User-Agent": "Rechnungsabruf/1.0", "Accept": "application/pdf",
                "Connection": "close"})
            r = conn.getresponse()
            if r.status in (301, 302, 303, 307, 308):
                ziel = r.getheader("Location") or ""
                if not ziel:
                    raise AbrufFehler("Weiterleitung ohne Ziel")
                url = urljoin(url, ziel)
                continue
            if r.status != 200:
                raise AbrufFehler(f"HTTP {r.status}")
            typ = (r.getheader("Content-Type") or "").split(";")[0].strip().lower()
            if typ not in _PDF_TYPEN:
                raise AbrufFehler("keine PDF (Inhaltstyp)")
            laenge = r.getheader("Content-Length")
            if laenge and laenge.isdigit() and int(laenge) > max_bytes:
                raise AbrufFehler("zu gross")
            daten = r.read(max_bytes + 1)
            if len(daten) > max_bytes:
                raise AbrufFehler("zu gross")
            if not daten.startswith(b"%PDF-"):
                raise AbrufFehler("keine PDF (Inhalt)")
            return daten
        except (OSError, http.client.HTTPException, ssl.SSLError) as e:
            raise AbrufFehler(f"Verbindungsfehler ({type(e).__name__})") from e
        finally:
            conn.close()
    raise AbrufFehler("zu viele Weiterleitungen")


def lauf(store, eingang: Path, *, max_je_lauf: int = MAX_JE_LAUF, operator: str = "") -> dict:
    """Mit Schalter ``aus`` (Standard): nur die Auswahl, kein Netz. Mit ``an``:
    die ausgewaehlten Links holen und die PDFs in den Eingang legen."""
    from .ingest import Attachment, _safe_name, _save

    a = auswahl(store)
    out = {"modus": modus(), "mails": a.mails, "kandidaten": len(a.kandidaten),
           "uebersprungen": dict(a.uebersprungen), "geholt": 0, "schon_da": 0,
           "fehler": {}, "liste": [{"lieferant": k.lieferant_name, "domain": k.domain,
                                    "datei": k.datei} for k in a.kandidaten]}
    if out["modus"] != MODUS_AN:
        return out
    fehler: Counter = Counter()
    for k in a.kandidaten[:max_je_lauf]:
        try:
            daten = abrufen(k.url, {k.domain})
        except AbrufFehler as e:
            fehler[str(e)] += 1
            store.audit("link_abruf_fehler", {"mail_sha256": k.sha256, "lieferant": k.lieferant_id,
                                              "domain": k.domain, "grund": str(e)}, operator)
            continue
        sha = hashlib.sha256(daten).hexdigest()
        name = _safe_name(f"{k.lieferant_id}_link_{sha[:8]}.pdf")
        gespeichert = _save(Attachment(filename=name, content=daten), Path(eingang))
        if gespeichert is None:
            out["schon_da"] += 1
        else:
            out["geholt"] += 1
        store.audit("link_abruf", {"mail_sha256": k.sha256, "lieferant": k.lieferant_id,
                                   "domain": k.domain, "sha256": sha, "bytes": len(daten),
                                   "datei": gespeichert.name if gespeichert else ""}, operator)
        _vermerk(store, k.sha256, gespeichert.name if gespeichert else name)
    out["fehler"] = dict(fehler)
    return out


def _vermerk(store, mail_sha: str, datei: str) -> None:
    gefunden = human.finde(store, mail_sha)
    if gefunden is None:
        return
    side, daten = gefunden
    hinweise = list(daten.get("hinweise") or [])
    text = f"Rechnung per Link geholt und in den Eingang gelegt: {datei}"
    if text not in hinweise:
        hinweise.append(text)
    human._write(side, dict(daten, hinweise=hinweise, link_geholt=datei))
