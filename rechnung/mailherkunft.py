"""Herkunft eines Belegs aus dem Postfach: Absender der Mail als Lieferanten-
Hinweis (Effizienz-Baustein 1, Entscheidung Aaron 10.10.2026).

Beim Abholen (``ingest.handle_mail``) wird je gespeichertem Anhang
festgehalten, aus welcher Mail er kam -- Registry nach sha256 der
Originaldatei wie ``anlagen.py``: ``data/herkunft/<sha>.json`` mit
``von``, ``domain``, ``betreff``, ``message_id``, ``datum`` und ggf.
``weitergeleitet_von`` (die eigene Adresse, die weitergeleitet hat).

Die Domain ist **nie allein die Identitaet** eines Lieferanten, nur ein
Hinweis/eine Eingrenzung (``pipeline``): sie zaehlt im Abgleich wie eine auf
der Rechnung gedruckte Mail-Domain, ein Treffer braucht also noch ein
weiteres Indiz (Name, Kopf-Layout, Kennung). Keine Identitaet sind:

- eigene Domains (``RECHNUNG_EIGENE_DOMAINS``, Standard ``althoffhotels.com``)
  -- Aaron leitet Rechnungen aus dem Firmen-Outlook weiter; dann gilt der
  urspruengliche Absender aus dem zitierten Kopf ("Von:"/"From:") bzw. aus
  einer angehaengten Mail (message/rfc822), aber nur, wenn er eindeutig ist;
- Freemail-Anbieter (gmail, gmx, web.de, outlook, ...);
- Rechnungsportale/Versanddienste (``PORTALE``, erweiterbar ueber
  ``RECHNUNG_PORTAL_DOMAINS``) -- dort schicken viele Firmen ueber dieselbe
  Domain.
"""

from __future__ import annotations

import email.utils
import json
import os
import re
from email.message import Message
from pathlib import Path

HERKUNFT_DIR = "herkunft"

_EIGENE_STANDARD = "althoffhotels.com"

FREEMAIL = frozenset({
    "gmail.com", "googlemail.com", "gmx.de", "gmx.net", "gmx.at", "gmx.ch", "gmx.com",
    "web.de", "outlook.com", "outlook.de", "hotmail.com", "hotmail.de", "live.com",
    "live.de", "msn.com", "t-online.de", "icloud.com", "me.com", "mac.com",
    "yahoo.com", "yahoo.de", "aol.com", "aol.de", "freenet.de", "arcor.de",
    "posteo.de", "mailbox.org", "proton.me", "protonmail.com", "gmx.li",
    "bluewin.ch", "hispeed.ch", "1und1.de", "online.de", "vodafone.de", "mail.de",
    "email.de", "kabelmail.de", "ewe.net", "o2online.de", "googlemail.de",
})

# Rechnungsportale, Buchhaltungs-Software und Versanddienste: viele
# verschiedene Absender teilen sich dieselbe Domain -> nie ein Lieferant.
PORTALE = frozenset({
    "sevdesk.de", "sevdesk.com", "lexoffice.de", "lexware.de", "billomat.net",
    "billomat.com", "easybill.de", "fastbill.com", "debitoor.com", "zervant.com",
    "papierkram.de", "invoiz.de", "orgamax.de", "datev.de", "candis.io",
    "getmoss.com", "pliant.io", "billbee.io", "sumup.com", "stripe.com",
    "paypal.com", "paypal.de", "invoice.paypal.com", "ariba.com", "coupa.com",
    "tungsten-network.com", "basware.com", "pagero.com", "tradeshift.com",
    "docusign.net", "xero.com", "intuit.com", "freshbooks.com", "zoho.com",
    "zohoinvoice.com", "sendgrid.net", "amazonses.com", "mailgun.org",
    "mailchimp.com", "mandrillapp.com", "sparkpostmail.com", "postmarkapp.com",
    "rechnung.de", "e-rechnung.de", "peppol.eu", "bexio.com", "smallinvoice.ch",
    "klara.ch", "abacus.ch", "zahls.ch", "weclapp.com", "billwerk.com",
})


def _liste(name: str, default: str = "") -> frozenset[str]:
    raw = os.environ.get(name, default)
    return frozenset(x.strip().lower().lstrip("@") for x in raw.split(",") if x.strip())


def eigene_domains() -> frozenset[str]:
    """``RECHNUNG_EIGENE_DOMAINS`` (kommagetrennt, Standard
    ``althoffhotels.com``) -- Absender aus diesen Domains sind wir selbst
    (Weiterleitung), nie ein Lieferant."""
    return _liste("RECHNUNG_EIGENE_DOMAINS", _EIGENE_STANDARD)


def portal_domains() -> frozenset[str]:
    return PORTALE | _liste("RECHNUNG_PORTAL_DOMAINS")


def an() -> bool:
    """Schalter ``RECHNUNG_ABSENDER_HINWEIS`` (Standard an): Absender-Domain im
    Lieferanten-Abgleich/als Vorschlag/beim Lernen verwenden. Aus: die
    Herkunft wird trotzdem festgehalten und angezeigt, wirkt aber nicht."""
    return os.environ.get("RECHNUNG_ABSENDER_HINWEIS", "1").strip().lower() not in (
        "0", "false", "no", "off", "nein")


def _unter(domain: str, menge: frozenset[str]) -> bool:
    """``rechnung.firma.de`` gehoert zu ``firma.de``."""
    return any(domain == d or domain.endswith("." + d) for d in menge)


def domain_von(adresse: str) -> str:
    """``"Firma <rechnung@Firma.de>"`` -> ``"firma.de"``, sonst ''."""
    _name, addr = email.utils.parseaddr(adresse or "")
    if "@" not in addr:
        m = re.search(r"[\w.+-]+@([\w-]+(?:\.[\w-]+)+)", adresse or "")
        return m.group(1).lower() if m else ""
    return addr.rsplit("@", 1)[1].strip().strip(">").lower()


def eigen(domain: str) -> bool:
    return bool(domain) and _unter(domain, eigene_domains())


def verwendbar(domain: str) -> bool:
    """Taugt die Domain als Lieferanten-Hinweis? Nicht eigen, kein Freemail,
    kein Rechnungsportal."""
    if not domain or "." not in domain:
        return False
    return not (eigen(domain) or _unter(domain, FREEMAIL) or _unter(domain, portal_domains()))


# ---- Weitergeleitete Mails: urspruenglichen Absender finden ----------------

# Zitierter Kopf einer Weiterleitung (Outlook deutsch/englisch, Thunderbird,
# Apple Mail, Gmail): "Von: Name <a@b.de>", "From: a@b.de",
# "Von: Name [mailto:a@b.de]", "*From:* ...".
_ZITAT_VON = re.compile(r"^[>\s*]*(?:Von|From|Absender)\s*:\s*\**\s*(.+)$", re.I | re.M)
_ADRESSE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def _zitierte_absender(text: str) -> list[str]:
    out = []
    for m in _ZITAT_VON.finditer(text or ""):
        a = _ADRESSE.search(m.group(1))
        if a:
            out.append(a.group(0).lower())
    return out


def _angehaengte_absender(msg: Message) -> list[str]:
    """Absender angehaengter Mails (Weiterleitung "als Anlage", .eml/
    message/rfc822)."""
    out = []
    for part in msg.walk():
        if part.get_content_type() != "message/rfc822":
            continue
        payload = part.get_payload()
        inner = payload[0] if isinstance(payload, list) and payload else None
        if isinstance(inner, Message):
            _n, addr = email.utils.parseaddr(str(inner.get("From", "")))
            if "@" in addr:
                out.append(addr.lower())
    return out


def ursprung(msg: Message, von: str, body_text: str) -> dict:
    """-> ``{von, domain, weitergeleitet_von, grund}``. Kommt die Mail von
    einer eigenen Domain (Weiterleitung), wird der urspruengliche Absender
    aus einer angehaengten Mail bzw. dem zitierten Kopf genommen -- nur,
    wenn genau EIN fremder Absender vorkommt. Sonst bleibt ``domain`` leer
    (kein Hinweis statt eines geratenen)."""
    dom = domain_von(von)
    if not eigen(dom):
        return {"von": von, "domain": dom, "weitergeleitet_von": "", "grund": ""}
    kand = _angehaengte_absender(msg) or _zitierte_absender(body_text)
    fremd = sorted({a for a in kand if not eigen(domain_von(a))})
    if len(fremd) == 1:
        return {"von": fremd[0], "domain": domain_von(fremd[0]), "weitergeleitet_von": von,
                "grund": ""}
    grund = ("Weiterleitung ohne erkennbaren urspruenglichen Absender" if not fremd
             else "Weiterleitung mit mehreren moeglichen Absendern")
    return {"von": von, "domain": "", "weitergeleitet_von": von, "grund": grund}


# ---- Registry -------------------------------------------------------------

def _pfad(root, sha: str) -> Path:
    return Path(root) / HERKUNFT_DIR / f"{sha}.json"


def merken(root, sha: str, rec: dict) -> bool:
    """Herkunft fuer den Beleg ``sha`` ablegen -- nur, wenn noch keine da ist
    (die erste Mail gewinnt; ein spaeteres Duplikat ueberschreibt nichts).
    -> True, wenn geschrieben."""
    if not sha:
        return False
    p = _pfad(root, sha)
    if p.exists():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    tmp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(p)
    return True


def lesen(root, sha: str) -> dict | None:
    if not sha:
        return None
    try:
        data = json.loads(_pfad(root, sha).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def fuer_beleg(store, sha: str, sammel: dict | None = None) -> dict | None:
    """Herkunft eines Belegs; ein Teil eines Sammel-PDFs (split.py) erbt die
    Herkunft seines Originals."""
    rec = lesen(store.root, sha)
    if rec is None and sammel:
        rec = lesen(store.root, sammel.get("sha256") or sammel.get("original_sha256") or "")
    return rec


def hinweis_domain(rec: dict | None) -> str:
    """Domain, die als Lieferanten-Hinweis taugt (sonst '')."""
    if not rec or not an():
        return ""
    dom = (rec.get("domain") or "").lower()
    return dom if verwendbar(dom) else ""


def anzeige(rec: dict | None) -> dict | None:
    """Fuer Ergebnis/Pruefmaske: ``{von, domain, betreff, datum,
    weitergeleitet_von, hinweis}`` (``hinweis`` = taugt als Lieferanten-
    Hinweis)."""
    if not rec:
        return None
    return {"von": rec.get("von") or "", "domain": rec.get("domain") or "",
            "betreff": rec.get("betreff") or "", "datum": rec.get("datum") or "",
            "weitergeleitet_von": rec.get("weitergeleitet_von") or "",
            "hinweis": bool(hinweis_domain(rec))}


def passt(domain: str, supplier) -> bool:
    """Gehoert die Absender-Domain zu diesem Lieferanten (auch Subdomain:
    ``rechnung.firma.de`` zu ``firma.de``)?"""
    doms = frozenset(d.lower() for d in (supplier.domains or []))
    return bool(domain) and _unter(domain, doms)


# ---- Lernen ---------------------------------------------------------------

def lerne_domain(store, supplier_id: str | None, sha: str, operator: str = "",
                 sammel: dict | None = None) -> str:
    """Beim Bestaetigen: Absender-Domain dieses Belegs zu ``Supplier.domains``
    hinzufuegen -- nur eine verwendbare (nicht eigen/Freemail/Portal) und nur,
    wenn sie noch keinem ANDEREN Lieferanten gehoert (eine Domain soll auf
    genau einen Lieferanten zeigen, sonst ist sie kein Hinweis mehr). ->
    gelernte Domain oder ''."""
    if not supplier_id:
        return ""
    dom = hinweis_domain(fuer_beleg(store, sha, sammel))
    if not dom:
        return ""
    sup = store.supplier(supplier_id)
    if sup is None or passt(dom, sup):
        return ""
    if any(passt(dom, s) for s in store.suppliers() if s.id != supplier_id):
        return ""
    sup.domains = sorted(set(sup.domains or []) | {dom})
    store.save_supplier(sup)
    store.audit("lieferant_domain_gelernt", {"id": supplier_id, "domain": dom,
                                             "sha256": sha}, operator or "?")
    return dom
