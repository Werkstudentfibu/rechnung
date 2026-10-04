"""IONOS-Postfach abholen: neue Mails -> Rechnungs-Anhaenge in den Inbox-Ordner.

Nutzt nur die Standardbibliothek (imaplib/email). Standardmaessig passiert
nichts – erst mit gesetzten ``RECHNUNG_IMAP_*``-Variablen (siehe config.py).
Geholt werden PDF/XML sowie Bild-Anhaenge (jpg/png/tiff, siehe
``ImapConfig.extensions``) -- Fotos/Scans ohne Textebene laufen in der
Pipeline automatisch durch die Texterkennung (ocr.py).

Ablauf ``fetch``:
1. Verbinden, ``source_folder`` waehlen.
2. UNSEEN-Mails suchen.
3. Je Mail mit ``BODY.PEEK[]`` holen -- das setzt (anders als ``RFC822``)
   NICHT implizit ``\\Seen``. Frueher wurde eine Mail ohne passenden Anhang
   beim Abholen still als gelesen markiert und danach uebersprungen -- fuer
   die naechste ``UNSEEN``-Suche war sie damit fuer immer verloren.
4. JEDE Mail fuehrt zu einem Ergebnis (``handle_mail``): entweder
   verwertbare Anhaenge im Inbox-Ordner (Dateiname entschaerft, Kollision ->
   Zeitstempel-Suffix, leere Anhaenge zaehlen nicht), oder ein Eintrag im
   Ordner "Menschliche Bearbeitung" mit Grund ("Mail ohne Rechnungsanhang",
   "nur Link im Mailtext", "Rechnung im Mailtext", "Mail verschluesselt")
   samt der Mail selbst als ``.eml`` (eine Rechnung im Mailtext zusaetzlich
   als lesbares PDF).

   Anhaenge sind beschriftet (Postfach-Baustein 8): nur Beleg-Anhaenge
   (Rechnung/Gutschrift/Rechnungskorrektur/Storno/Mahnung/Abschlags-/
   Schlussrechnung, ``belegart.BELEGARTEN``) werden als eigene Rechnung
   verarbeitet -- erst am Dateinamen, dann am Dokumenttitel der ersten
   Seite erkannt. Andere beschriftete Anhaenge derselben Mail (AGB,
   Lieferschein, Auftragsbestaetigung, ..., ``belegart.ANLAGEN``) werden
   NICHT verarbeitet, sondern als Anlage an jeden Beleg dieser Mail
   gehaengt (``anlagen.py``). Kein einziger erkannter Beleg in der Mail
   oder ein gar nicht zuzuordnender Anhang -> "Menschliche Bearbeitung",
   nie stillschweigend verworfen.
5. Erst DANACH ``\\Seen`` setzen bzw. – falls ``done_folder`` gesetzt –
   dorthin kopieren + im Quellordner loeschen. Scheitert Schritt 4 (z.B.
   Platte voll), bleibt die Mail ungelesen und kommt beim naechsten Lauf
   wieder.

Der reine Anhang-Parser ``attachments_from_bytes`` und ``analyse_mail`` sind
ohne Netz testbar.
"""

from __future__ import annotations

import email
import email.header
import hashlib
import html
import imaplib
import io
import re
import time
import zipfile
from dataclasses import dataclass, field
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path

from . import lock
from .config import ImapConfig, data_dir, inbox_dir

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

# Gruende fuer den Ordner "Menschliche Bearbeitung" (wortgleich in Begleit-
# datei, Fehlerbericht und Tests)
MAIL_OHNE_ANHANG = "Mail ohne Rechnungsanhang"
MAIL_OHNE_BELEG_ANHANG = "Mail ohne erkannten Beleg-Anhang"
ANHANG_NICHT_ZUZUORDNEN = "Anhang nicht zuzuordnen (weder Belegart noch bekannte Beschriftung)"
MAIL_NUR_LINK = "nur Link im Mailtext"
MAIL_TEXT_RECHNUNG = "Rechnung im Mailtext (kein Anhang)"
MAIL_VERSCHLUESSELT = "Mail verschluesselt (S/MIME/PGP)"
ANHANG_PDF_PASSWORT = "Anhang verschluesselt (PDF passwortgeschuetzt)"
ANHANG_ZIP_PASSWORT = "Anhang verschluesselt (ZIP mit Passwort)"
ANHANG_ZIP_DEFEKT = "ZIP-Anhang nicht lesbar"
ANHANG_ZIP_GRENZE = "ZIP-Anhang ueberschreitet Sicherheitsgrenze"
ANHANG_ZIP_LEER = "ZIP-Anhang ohne verwertbare Datei"

# Sicherheitsgrenzen fuer ZIP-Anhaenge (Zip-Bomben): Anzahl Eintraege,
# entpackte Groesse je Datei und gesamt, Kompressionsverhaeltnis. Ein ZIP
# im ZIP wird nicht weiter entpackt (-> Mensch).
ZIP_MAX_FILES = 50
ZIP_MAX_FILE_BYTES = 50 * 1024 * 1024
ZIP_MAX_TOTAL_BYTES = 200 * 1024 * 1024
ZIP_MAX_RATIO = 200


def _safe_name(name: str) -> str:
    name = (name or "anhang.pdf").rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    name = _SAFE.sub("_", name).strip("_.") or "anhang.pdf"
    return name[:120]


@dataclass
class Attachment:
    filename: str
    content: bytes
    from_addr: str = ""
    subject: str = ""
    message_id: str = ""


def _decoded_header(msg: Message, key: str) -> str:
    raw = msg.get(key, "")
    parts = email.header.decode_header(raw)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", "replace"))
        else:
            out.append(text)
    return "".join(out).strip()


def _parts(msg: Message):
    """-> (Teil, Dateiname oder None, ist Anhang?) je Blatt der Mail."""
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        fname = part.get_filename()
        if fname:
            fname = _decoded_header_value(fname)
        is_att = "attachment" in disp or bool(fname and "inline" not in disp)
        yield part, fname, is_att


def attachments_from_bytes(raw: bytes, extensions: tuple[str, ...] = (".pdf",)) -> list[Attachment]:
    """RFC822-Rohmail -> Liste passender Anhaenge."""
    msg = email.message_from_bytes(raw)
    frm = _decoded_header(msg, "From")
    subj = _decoded_header(msg, "Subject")
    mid = msg.get("Message-ID", "").strip()
    exts = tuple(e.lower() for e in extensions)
    out: list[Attachment] = []
    for part, fname, is_att in _parts(msg):
        if not is_att or not fname:
            continue
        if exts and not fname.lower().endswith(exts):
            continue
        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue
        out.append(Attachment(_safe_name(fname), payload, frm, subj, mid))
    return out


def _decoded_header_value(raw: str) -> str:
    parts = email.header.decode_header(raw)
    out = []
    for text, enc in parts:
        out.append(text.decode(enc or "utf-8", "replace") if isinstance(text, bytes) else text)
    return "".join(out)


# ---- Mailtext ------------------------------------------------------------

class _HtmlText(HTMLParser):
    """HTML -> Klartext (nur stdlib); Links werden mitgezaehlt."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.links: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "table"):
            self.out.append("\n")
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.lower().startswith(("http://", "https://")):
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1
        if tag in ("td", "th"):
            self.out.append("  ")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def mail_body(msg: Message) -> tuple[str, list[str]]:
    """-> (Klartext des Mailtexts, Links). Text-Teil bevorzugt, sonst HTML."""
    plain, htm = [], []
    for part, fname, is_att in _parts(msg):
        if is_att:
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        payload = part.get_payload(decode=True) or b""
        text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        (plain if ctype == "text/plain" else htm).append(text)
    links: list[str] = []
    if htm:
        p = _HtmlText()
        p.feed("\n".join(htm))
        links = p.links
    text = "\n".join(plain) if plain else (html.unescape("".join(p.out)) if htm else "")
    links += [u for u in re.findall(r"https?://[^\s<>\"')]+", text) if u not in links]
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip(), links


_INVOICE_WORDS = re.compile(r"(?i)\b(rechnung|invoice|faktura|gutschrift|rechnungsbetrag|"
                            r"zahlbetrag|gesamtbetrag)\w*")
_AMOUNT_IN_TEXT = re.compile(r"\d{1,3}(?:[.'’ ]\d{3})*[.,]\d{2}\s*(?:€|EUR|CHF|Fr\.)|"
                             r"(?:€|EUR|CHF)\s*\d{1,3}(?:[.'’ ]\d{3})*[.,]\d{2}")


def _is_encrypted(msg: Message) -> bool:
    ctype = msg.get_content_type()
    if ctype == "multipart/encrypted":
        return True
    for part in msg.walk():
        t = part.get_content_type()
        if t in ("application/pgp-encrypted",):
            return True
        if t in ("application/pkcs7-mime", "application/x-pkcs7-mime") and \
                "signed-data" not in (part.get("Content-Type") or "").lower():
            return True
    return False


@dataclass
class MailOutcome:
    """Ergebnis einer Mail: verwertbare Anhaenge ODER ein Grund fuer den
    Menschen (nie beides leer)."""
    attachments: list[Attachment] = field(default_factory=list)
    human_reason: str = ""
    body_text: str = ""                       # Mailtext (fuer "Rechnung im Mailtext")
    ignored: list[str] = field(default_factory=list)  # nicht verwertbare Anhaenge
    # Einzelne Anhaenge fuer den Menschen (Name, Inhalt, Grund) -- z.B.
    # passwortgeschuetztes PDF, ZIP ueber der Sicherheitsgrenze. Die Mail
    # selbst kann daneben trotzdem verwertbare Anhaenge haben.
    human_items: list[tuple[str, bytes, str]] = field(default_factory=list)
    from_addr: str = ""
    subject: str = ""
    message_id: str = ""
    # Andere beschriftete Anhaenge DERSELBEN Mail (AGB, Lieferschein, ...) --
    # kein eigener Beleg, sondern an jeden Beleg dieser Mail gehaengt
    # (Postfach-Baustein 8): (Dateiname, Inhalt, Anlagenart)
    anlagen: list[tuple[str, bytes, str]] = field(default_factory=list)


def _classify_attachment(att: Attachment) -> tuple[str, str | None, str]:
    """-> ("beleg"|"anlage"|"unklar", Art, Grund). Erst der Dateiname
    (``belegart.aus_dateiname``), dann -- nur bei PDFs -- der Dokumenttitel
    der ersten Seite (Postfach-Baustein 8). Nur woertliche Treffer, keine
    KI-Klassifikation."""
    from . import belegart, dubletten

    art, anlage_art, grund = belegart.aus_dateiname(att.filename)
    if art:
        return "beleg", art, ""
    if anlage_art:
        return "anlage", anlage_art, ""
    if not grund and att.filename.lower().endswith(".pdf"):
        try:
            from .pdf import load_bytes
            title = dubletten.title_text(load_bytes(att.content))
        except Exception:  # noqa: BLE001 -- kaputtes PDF faellt der Pipeline auf
            title = ""
        if title:
            art2, why2 = belegart.aus_titel(title)
            if art2:
                return "beleg", art2, ""
            anlage2 = belegart.anlage_aus_titel(title)
            if anlage2:
                return "anlage", anlage2, ""
    return "unklar", None, f"{ANHANG_NICHT_ZUZUORDNEN}: {att.filename}"


def analyse_mail(raw: bytes, extensions: tuple[str, ...] = (".pdf",)) -> MailOutcome:
    """Was ist mit dieser Mail zu tun? Rein lokal, ohne Netz/Dateisystem."""
    msg = email.message_from_bytes(raw)
    out = MailOutcome(from_addr=_decoded_header(msg, "From"),
                      subject=_decoded_header(msg, "Subject"),
                      message_id=msg.get("Message-ID", "").strip())
    if _is_encrypted(msg):
        out.human_reason = MAIL_VERSCHLUESSELT
        return out
    exts = tuple(e.lower() for e in extensions)
    candidates = attachments_from_bytes(raw, tuple(exts) + (".zip",))
    for att in candidates:
        if att.filename.lower().endswith(".zip") and ".zip" not in exts:
            inner, problems = unpack_zip(att, exts)
            out.attachments += inner
            out.human_items += problems
        else:
            out.attachments.append(att)
    for att in list(out.attachments):
        if att.filename.lower().endswith(".pdf") and _pdf_locked(att.content):
            out.attachments.remove(att)
            out.human_items.append((att.filename, att.content, ANHANG_PDF_PASSWORT))

    if out.attachments:
        # Beschriftung auswaehlen (Postfach-Baustein 8): nur Beleg-Anhaenge
        # (Rechnung/Gutschrift/...) werden als eigene Rechnung verarbeitet;
        # andere beschriftete Anhaenge (AGB, Lieferschein, ...) haengen als
        # Anlage am Beleg dieser Mail; unzuordenbare gehen einzeln an den
        # Menschen. Kein einziger erkannter Beleg -> die ganze Mail.
        beleg, anlage, unklar = [], [], []
        for att in out.attachments:
            kind, art, grund = _classify_attachment(att)
            if kind == "beleg":
                beleg.append(att)
            elif kind == "anlage":
                anlage.append((att.filename, att.content, art))
            else:
                unklar.append((att, grund))
        if not beleg:
            out.attachments = []
            teile = sorted({a for _n, _c, a in anlage})
            zusatz = f" (nur: {', '.join(teile)})" if teile else \
                " (nicht zuzuordnen)" if unklar else ""
            out.human_reason = MAIL_OHNE_BELEG_ANHANG + zusatz
            return out
        out.attachments = beleg
        out.anlagen = anlage
        for att, grund in unklar:
            out.human_items.append((att.filename, att.content, grund))

    if out.attachments or out.human_items:
        return out
    out.ignored = [_safe_name(f) for _p, f, is_att in _parts(msg)
                   if is_att and f and not f.lower().endswith(exts)]
    body, links = mail_body(msg)
    looks_invoice = bool(_INVOICE_WORDS.search(body) or _INVOICE_WORDS.search(out.subject))
    if links and looks_invoice:
        out.human_reason = MAIL_NUR_LINK
    elif looks_invoice and _AMOUNT_IN_TEXT.search(body):
        out.human_reason = MAIL_TEXT_RECHNUNG
        out.body_text = body
    else:
        out.human_reason = MAIL_OHNE_ANHANG
    if out.ignored:
        out.human_reason += " (nicht verwertbare Anhaenge: " + ", ".join(out.ignored) + ")"
    return out


def _pdf_locked(content: bytes) -> bool:
    from .pdf import password_protected

    try:
        return password_protected(content)
    except Exception:  # noqa: BLE001 -- kaputtes PDF faellt der Pipeline auf
        return False


def unpack_zip(att: Attachment, exts: tuple[str, ...]
               ) -> tuple[list[Attachment], list[tuple[str, bytes, str]]]:
    """ZIP-Anhang im Speicher entpacken -> (verwertbare Dateien, Faelle fuer
    den Menschen). Nie auf die Platte entpacken: Pfadanteile der Eintraege
    werden verworfen (``../../x.pdf`` -> ``x.pdf``, kein Pfad-Traversal).
    Sicherheitsgrenzen (``ZIP_MAX_*``) gegen Zip-Bomben werden VOR dem
    Entpacken aus dem Inhaltsverzeichnis geprueft und beim Lesen noch einmal
    hart begrenzt. Passwortgeschuetzte Eintraege, verschachtelte ZIPs und
    Grenzueberschreitungen gehen mit dem ganzen ZIP an den Menschen."""
    def mk(name: str, content: bytes) -> Attachment:
        return Attachment(_safe_name(name), content, att.from_addr, att.subject, att.message_id)

    try:
        zf = zipfile.ZipFile(io.BytesIO(att.content))
        infos = [i for i in zf.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, OSError, ValueError):
        return [], [(att.filename, att.content, ANHANG_ZIP_DEFEKT + f": {att.filename}")]
    if len(infos) > ZIP_MAX_FILES:
        return [], [(att.filename, att.content,
                     f"{ANHANG_ZIP_GRENZE}: {len(infos)} Dateien (max. {ZIP_MAX_FILES})")]
    total = sum(i.file_size for i in infos)
    for i in infos:
        ratio = i.file_size / max(i.compress_size, 1)
        if i.file_size > ZIP_MAX_FILE_BYTES or total > ZIP_MAX_TOTAL_BYTES or (
                i.file_size > 1024 * 1024 and ratio > ZIP_MAX_RATIO):
            return [], [(att.filename, att.content,
                         f"{ANHANG_ZIP_GRENZE}: {att.filename} ({total} Bytes entpackt)")]
    if any(i.flag_bits & 0x1 for i in infos):
        return [], [(att.filename, att.content, f"{ANHANG_ZIP_PASSWORT}: {att.filename}")]
    out: list[Attachment] = []
    problems: list[tuple[str, bytes, str]] = []
    for i in infos:
        base = re.split(r"[\\/]", i.filename)[-1]
        if not base or base.startswith("."):
            continue
        low = base.lower()
        if low.endswith(".zip"):
            problems.append((att.filename, att.content,
                             f"{ANHANG_ZIP_GRENZE}: ZIP im ZIP ({base}) wird nicht entpackt"))
            return [], problems
        if not low.endswith(exts):
            continue
        with zf.open(i) as fh:
            data = fh.read(ZIP_MAX_FILE_BYTES + 1)   # harte Grenze, falls Header luegt
        if len(data) > ZIP_MAX_FILE_BYTES:
            return [], [(att.filename, att.content, f"{ANHANG_ZIP_GRENZE}: {base}")]
        if data:
            out.append(mk(base, data))
    if not out:
        problems.append((att.filename, att.content, f"{ANHANG_ZIP_LEER}: {att.filename}"))
    return out, problems


def text_pdf(title: str, text: str) -> bytes:
    """Mailtext als schlichtes PDF (reportlab, ohnehin Abhaengigkeit) --
    damit der Mensch eine im Mailtext stehende Rechnung wie jede andere
    oeffnen und ablegen kann."""
    import io
    import textwrap

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    y = h - 50
    c.setFont("Helvetica-Bold", 11)
    c.drawString(40, y, title[:100])
    c.setFont("Helvetica", 9)
    y -= 22
    for para in text.splitlines() or [""]:
        for line in textwrap.wrap(para, 105) or [""]:
            if y < 50:
                c.showPage()
                c.setFont("Helvetica", 9)
                y = h - 50
            c.drawString(40, y, line)
            y -= 12
    c.showPage()
    c.save()
    return buf.getvalue()


def _save(att: Attachment, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / att.filename
    if target.exists():
        stem, suf = target.stem, target.suffix
        target = dest / f"{stem}_{int(time.time())}{suf}"
    target.write_bytes(att.content)
    return target


def _mail_name(out: MailOutcome) -> str:
    subj = _safe_name(out.subject or "ohne_betreff")[:60]
    return f"mail_{time.strftime('%Y%m%d')}_{subj}"


def handle_mail(raw: bytes, cfg: ImapConfig, dest: Path, store=None,
                dry_run: bool = False) -> dict:
    """Eine Mail verarbeiten -> ``{"saved": [...], "human": [...]}``. Wirft
    nur bei echten Fehlern (Platte voll ...) -- dann darf die Mail NICHT als
    gelesen markiert werden."""
    from . import anlagen as anlagen_mod
    from . import human
    from .store import Store

    out = analyse_mail(raw, cfg.extensions)
    saved: list[str] = []
    to_human: list[str] = []
    herkunft = {"von": out.from_addr, "betreff": out.subject,
                "message_id": out.message_id, "original": "postfach"}
    for att in out.attachments:
        if dry_run:
            saved.append(f"(dry) {att.filename} <- {att.from_addr}"
                        + (f" (+{len(out.anlagen)} Anlage(n))" if out.anlagen else ""))
            continue
        saved.append(str(_save(att, dest)))
        if out.anlagen:
            store = store or Store(data_dir())
            anlagen_mod.save(store, hashlib.sha256(att.content).hexdigest(), out.anlagen)
    for name, content, why in out.human_items:
        if dry_run:
            to_human.append(f"(dry) {name}: {why}")
            continue
        store = store or Store(data_dir())
        rec = human.ablegen_datei(store, name, content,
                                  [{"art": human.ART_ANHANG, "grund": why, "felder": []}],
                                  beilagen={".eml": raw}, herkunft=herkunft)
        to_human.append(f"{rec['datei'] if rec else name}: {why}")
    if out.human_reason:
        if dry_run:
            to_human.append(f"(dry) {out.human_reason} <- {out.from_addr}")
        else:
            store = store or Store(data_dir())
            grund = [{"art": human.ART_MAIL, "grund": out.human_reason, "felder": []}]
            name = _mail_name(out)
            if out.body_text:
                try:
                    datei, inhalt = name + ".pdf", text_pdf(
                        f"Mailtext: {out.subject}  (von {out.from_addr})", out.body_text)
                except ImportError:  # reportlab fehlt -> Mailtext als .txt
                    datei, inhalt = name + ".txt", out.body_text.encode("utf-8")
                rec = human.ablegen_datei(store, datei, inhalt, grund,
                                          beilagen={".eml": raw}, herkunft=herkunft)
            else:
                rec = human.ablegen_datei(store, name + ".eml", raw, grund, herkunft=herkunft)
            to_human.append(f"{rec['datei'] if rec else name}: {out.human_reason}")
    return {"saved": saved, "human": to_human}


@dataclass
class FetchResult:
    saved: list[str]
    mails: int
    dry_run: bool
    error: str = ""
    # Mails, die (ganz oder teilweise) an den Menschen gingen -- je Eintrag
    # "<datei>: <grund>"
    human: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"saved": self.saved, "mails": self.mails, "human": self.human,
                "dry_run": self.dry_run, "error": self.error}


def _raw_from_fetch(msg_data) -> bytes | None:
    """IMAP-FETCH-Antwort -> Rohmail (erstes (Kopf, Inhalt)-Tupel)."""
    for item in msg_data or []:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def fetch(cfg: ImapConfig | None = None, dest: Path | None = None,
          dry_run: bool = False, limit: int = 0, store=None) -> FetchResult:
    cfg = cfg or ImapConfig()
    dest = Path(dest) if dest else inbox_dir()
    if not cfg.configured:
        return FetchResult([], 0, dry_run, "IMAP nicht konfiguriert (RECHNUNG_IMAP_*)")

    # Nachtlauf-Lock (lock.py, SPEC §7r): laeuft gerade ``rechnung nacht``,
    # pausiert die Abholung, statt parallel auf denselben Daten zu schreiben.
    lock.wait_until_unlocked(data_dir())
    M = imaplib.IMAP4_SSL(cfg.host, cfg.port) if cfg.ssl else imaplib.IMAP4(cfg.host, cfg.port)
    saved: list[str] = []
    to_human: list[str] = []
    mails = 0
    errors: list[str] = []
    try:
        M.login(cfg.user, cfg.password)
        M.select(cfg.source_folder)
        typ, data = M.search(None, "UNSEEN")
        ids = data[0].split() if data and data[0] else []
        if limit:
            ids = ids[:limit]
        for num in ids:
            # BODY.PEEK[] statt RFC822: Abholen allein markiert NICHT als gelesen
            typ, msg_data = M.fetch(num, "(BODY.PEEK[])")
            raw = _raw_from_fetch(msg_data) if typ == "OK" else None
            if raw is None:
                errors.append(f"Mail {num!r}: nicht abrufbar")
                continue
            try:
                r = handle_mail(raw, cfg, dest, store, dry_run)
            except OSError as e:
                # Ergebnis nicht gesichert -> Mail bleibt ungelesen, naechster Lauf
                errors.append(f"Mail {num!r}: {type(e).__name__}: {e}")
                continue
            mails += 1
            saved += r["saved"]
            to_human += r["human"]
            if not dry_run:
                M.store(num, "+FLAGS", "\\Seen")
                if cfg.done_folder:
                    M.copy(num, cfg.done_folder)
                    M.store(num, "+FLAGS", "\\Deleted")
        if not dry_run and cfg.done_folder:
            M.expunge()
    except (imaplib.IMAP4.error, OSError) as e:
        return FetchResult(saved, mails, dry_run, f"{type(e).__name__}: {e}", to_human)
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return FetchResult(saved, mails, dry_run, "; ".join(errors), to_human)


def check_connection(cfg: ImapConfig | None = None) -> dict:
    """Anmeldung + Ordnerliste + Anzahl ungelesener Mails pruefen, OHNE
    irgendetwas abzuholen oder als gelesen zu markieren (``rechnung
    imap-test``). ``SELECT ... readonly`` verhindert zusaetzlich jede
    Status-Aenderung durch den Check selbst."""
    cfg = cfg or ImapConfig()
    if not cfg.configured:
        return {"ok": None, "detail": "IMAP nicht konfiguriert (RECHNUNG_IMAP_*)", "folders": []}
    try:
        M = imaplib.IMAP4_SSL(cfg.host, cfg.port) if cfg.ssl else imaplib.IMAP4(cfg.host, cfg.port)
    except OSError as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "folders": []}
    try:
        M.login(cfg.user, cfg.password)
        typ, raw = M.list()
        folders = ([f.decode("utf-8", "replace") for f in raw]
                  if typ == "OK" and raw and raw[0] else [])
        M.select(cfg.source_folder, readonly=True)
        typ2, data = M.search(None, "UNSEEN")
        unseen = len(data[0].split()) if typ2 == "OK" and data and data[0] else 0
        return {"ok": True, "detail": f"{len(folders)} Ordner, {unseen} ungelesen in "
               f"'{cfg.source_folder}'", "folders": folders, "unseen": unseen}
    except (imaplib.IMAP4.error, OSError) as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {e}", "folders": []}
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001 -- Check darf nicht abstuerzen
            pass
