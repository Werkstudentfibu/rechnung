"""Mails ohne Rechnungsanhang zaehlen: Lohnt es sich, Rechnungen hinter
Links automatisch zu holen? (Aaron 16.11.2026: "Waere es sinnvoll, wenn das
Programm die Rechnungen selbst herunterlaedt?")

NUR LESEND: liest die im Ordner "Menschliche Bearbeitung" gespeicherten
Mails (``.eml``), oeffnet KEINEN Link, schreibt nichts. Die Einordnung je
Link geschieht allein ueber Linktext und Adresse:

- ``direkt``: sieht nach direktem PDF-Download aus ("Rechnung
  herunterladen", ".pdf", "download"),
- ``unklar``: fuehrt zur Rechnung, aber ob mit Anmeldung, ist nicht zu
  sehen ("Zur Rechnung", ".../invoice/..."),
- ``portal``: Kundenportal/Anmeldung ("Login", "Mein Konto"),
- ``sonstig``: nur andere Links (Webseite, Newsletter),
- ``kein_link``.

Ausgabe nur Zahlen und Link-Domains -- Domains erst ab zwei Mails (eine
einzelne Domain koennte ein Personenname sein)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from . import human

KLASSEN = ("direkt", "unklar", "portal", "sonstig", "kein_link")
KLASSEN_TEXT = {
    "direkt": 'direkter Download (z.B. "Rechnung herunterladen", .pdf)',
    "unklar": "Link zur Rechnung, unklar ob mit Anmeldung",
    "portal": "Kundenportal mit Anmeldung",
    "sonstig": "nur sonstige Links (Webseite, Newsletter)",
    "kein_link": "gar kein Link",
}

_DIREKT = ("download", "herunterladen", "pdf")
_RECHNUNG = ("rechnung", "invoice", "beleg", "faktura", "gutschrift", "document", "dokument",
             "billing", "receipt", "quittung")
_PORTAL = ("login", "anmelden", "anmeldung", "einloggen", "signin", "logon",
           "kundenportal", "kundencenter", "kundenkonto", "meinkonto", "myaccount",
           "account", "portal", "selfservice", "onlineservice")
# Links, die nie zur Rechnung fuehren
_UNWICHTIG = ("unsubscribe", "abmelden", "abbestellen", "newsletter", "datenschutz",
              "privacy", "impressum", "imprint", "agb", "terms", "kontakt", "contact")
_SOZIAL = {"facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
           "xing.com", "youtube.com", "tiktok.com", "pinterest.com"}
_BILD = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp")
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
# zweistufige Endungen (co.uk, com.au ...): dann drei Teile als Domain
_ZWEISTUFIG = ("co", "com", "org", "net", "gov", "ac", "or")


class _LinkSammler(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "a":
            self._href = dict(attrs).get("href") or ""
            self._text = []
        elif tag.lower() == "img" and self._href is not None:
            alt = dict(attrs).get("alt")
            if alt:
                self._text.append(alt)

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.links.append((self._href.strip(), " ".join(" ".join(self._text).split())))
            self._href = None


def links_aus_mail(raw: bytes) -> list[tuple[str, str]]:
    """(Adresse, Linktext) aller http(s)-Links aus HTML- und Textteil."""
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception:  # noqa: BLE001 -- kaputte Mail zaehlt als "kein Link"
        return []
    links: list[tuple[str, str]] = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/html", "text/plain"):
            continue
        try:
            inhalt = part.get_content()
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(inhalt, str):
            continue
        if ctype == "text/html":
            sammler = _LinkSammler()
            try:
                sammler.feed(inhalt)
            except Exception:  # noqa: BLE001
                pass
            links += sammler.links
        else:
            links += [(u, "") for u in _URL_RE.findall(inhalt)]
    return [(u, t) for u, t in links if u.lower().startswith(("http://", "https://"))]


def domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    teile = [t for t in host.split(".") if t]
    if len(teile) >= 3 and teile[-2] in _ZWEISTUFIG and len(teile[-1]) == 2:
        return ".".join(teile[-3:])
    return ".".join(teile[-2:])


def _woerter(s: str) -> list[str]:
    w = [x for x in re.split(r"[^a-z0-9äöüß]+", s.lower()) if x]
    # auch Wortpaare ("mein konto", "sign-in" -> "meinkonto", "signin")
    return w + [a + b for a, b in zip(w, w[1:])]


def _trifft(woerter: list[str], schluessel: tuple[str, ...]) -> bool:
    """Kurze Schluessel (< 5 Zeichen: "pdf", "agb") nur als ganzes Wort --
    sonst traefe "agb" jede zufaellige Zeichenfolge eines Tracking-Links."""
    return any((k in woerter) if len(k) < 5 else any(k in w for w in woerter)
               for k in schluessel)


def link_klasse(url: str, text: str) -> str | None:
    """Klasse eines einzelnen Links, ``None`` = unwichtig (Abmelden,
    Datenschutz, Social Media, Bild)."""
    u = unquote(url)
    pfad = urlsplit(u).path.lower()
    woerter = _woerter(u + " " + (text or ""))
    if (domain(url) in _SOZIAL or pfad.endswith(_BILD)
            or _trifft(_woerter(text or ""), _UNWICHTIG) or _trifft(_woerter(pfad), _UNWICHTIG)):
        return None
    if pfad.endswith(".pdf"):
        return "direkt"
    direkt, portal = _trifft(woerter, _DIREKT), _trifft(woerter, _PORTAL)
    if direkt and not portal:
        return "direkt"
    if _trifft(woerter, _RECHNUNG) and not portal:
        return "unklar"
    if portal:
        return "portal"
    return "sonstig"


def mail_klasse(raw: bytes) -> tuple[str, str]:
    """-> (Klasse, Domain des massgeblichen Links oder "")."""
    beste: tuple[int, str] | None = None
    for url, text in links_aus_mail(raw):
        k = link_klasse(url, text)
        if k is None:
            continue
        rang = KLASSEN.index(k)
        if beste is None or rang < beste[0]:
            beste = (rang, domain(url))
    if beste is None:
        return "kein_link", ""
    return KLASSEN[beste[0]], beste[1]


@dataclass
class Bilanz:
    mails: int = 0
    bekannt: int = 0
    klassen: Counter = field(default_factory=Counter)
    domains: dict[str, Counter] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"mails": self.mails, "bekannt": self.bekannt,
                "klassen": {k: self.klassen.get(k, 0) for k in KLASSEN},
                "domains": {d: dict(c) for d, c in self.domains.items()}}


def _eml_pfad(store, rec: dict) -> Path | None:
    ordner = human.human_dir(store.root)
    for name in [rec.get("datei") or ""] + list(rec.get("beilagen") or []):
        if str(name).lower().endswith(".eml") and (ordner / name).is_file():
            return ordner / name
    return None


def _lieferanten_domains(store) -> set[str]:
    out: set[str] = set()
    for s in store.suppliers():
        for d in getattr(s, "domains", None) or []:
            out.add(domain("https://" + str(d).strip().lower()))
    return out


def bilanz(store) -> Bilanz:
    """Alle offenen Mails ohne Rechnungsanhang in "Menschliche Bearbeitung"
    einordnen."""
    b = Bilanz()
    bekannte = _lieferanten_domains(store)
    for rec in human.offen(store):
        if not human.ist_mail(rec):
            continue
        p = _eml_pfad(store, rec)
        if p is None:
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        b.mails += 1
        absender = human.mail_absender(store, rec)
        ist_bekannt = bool(absender) and domain("https://" + absender.split("@")[-1]) in bekannte
        b.bekannt += ist_bekannt
        klasse, dom = mail_klasse(raw)
        b.klassen[klasse] += 1
        if dom:
            c = b.domains.setdefault(dom, Counter())
            c["mails"] += 1
            c[klasse] += 1
            c["bekannt"] += ist_bekannt
    return b


def bericht(b: Bilanz, min_mails: int = 2) -> str:
    zeilen = [f'Mails ohne Rechnungsanhang in "Menschliche Bearbeitung": {b.mails}',
              f"  davon von bekannten Lieferanten (Absender-Domain in den Stammdaten): {b.bekannt}",
              "", "Link-Art (je Mail der beste Link):"]
    for k in KLASSEN:
        zeilen.append(f"  {KLASSEN_TEXT[k]:58s} {b.klassen.get(k, 0):5d}")
    haeufig = sorted(((d, c) for d, c in b.domains.items() if c["mails"] >= min_mails),
                     key=lambda dc: (-dc[1]["mails"], dc[0]))
    selten = [d for d, c in b.domains.items() if c["mails"] < min_mails]
    zeilen += ["", f"Link-Domains (ab {min_mails} Mails):"]
    if haeufig:
        zeilen.append(f"  {'Domain':32s} {'Mails':>5s} {'direkt':>7s} {'unklar':>7s} "
                      f"{'Portal':>7s} {'bekannt':>8s}")
        for d, c in haeufig:
            zeilen.append(f"  {d:32s} {c['mails']:5d} {c['direkt']:7d} {c['unklar']:7d} "
                          f"{c['portal']:7d} {c['bekannt']:8d}")
    else:
        zeilen.append("  (keine)")
    if selten:
        zeilen.append(f"  weitere: {len(selten)} Domains mit weniger als {min_mails} Mails "
                      "(nicht einzeln gezeigt)")
    zeilen += ["", "Nur gezaehlt -- kein Link wurde geoeffnet, nichts veraendert."]
    return "\n".join(zeilen)
