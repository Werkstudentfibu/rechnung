"""IONOS-Postfach abholen: neue Mails -> Rechnungs-Anhaenge in den Inbox-Ordner.

Nutzt nur die Standardbibliothek (imaplib/email). Standardmaessig passiert
nichts – erst mit gesetzten ``RECHNUNG_IMAP_*``-Variablen (siehe config.py).
Geholt werden PDF/XML sowie Bild-Anhaenge (jpg/png/tiff, siehe
``ImapConfig.extensions``) -- Fotos/Scans ohne Textebene laufen in der
Pipeline automatisch durch die Texterkennung (ocr.py).

Ablauf ``fetch``:
1. Verbinden, ``source_folder`` waehlen.
2. UNSEEN-Mails suchen.
3. Je Mail die passenden Anhaenge speichern (Dateiname entschaerft, Kollision
   -> Zeitstempel-Suffix). Leere (0-Byte) Anhaenge werden uebersprungen.
4. Erfolgreich verarbeitete Mails als ``\\Seen`` markieren und – falls
   ``done_folder`` gesetzt – dorthin kopieren + im Quellordner loeschen.

Der reine Anhang-Parser ``attachments_from_bytes`` ist ohne Netz testbar.
"""

from __future__ import annotations

import email
import email.header
import imaplib
import re
import time
from dataclasses import dataclass
from email.message import Message
from pathlib import Path

from .config import ImapConfig, inbox_dir

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


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


def attachments_from_bytes(raw: bytes, extensions: tuple[str, ...] = (".pdf",)) -> list[Attachment]:
    """RFC822-Rohmail -> Liste passender Anhaenge."""
    msg = email.message_from_bytes(raw)
    frm = _decoded_header(msg, "From")
    subj = _decoded_header(msg, "Subject")
    mid = msg.get("Message-ID", "").strip()
    exts = tuple(e.lower() for e in extensions)
    out: list[Attachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        fname = part.get_filename()
        if fname:
            fname = _decoded_header_value(fname)
        is_att = "attachment" in disp or (fname and "inline" not in disp)
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


def _save(att: Attachment, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / att.filename
    if target.exists():
        stem, suf = target.stem, target.suffix
        target = dest / f"{stem}_{int(time.time())}{suf}"
    target.write_bytes(att.content)
    return target


@dataclass
class FetchResult:
    saved: list[str]
    mails: int
    dry_run: bool
    error: str = ""

    def to_dict(self) -> dict:
        return {"saved": self.saved, "mails": self.mails,
                "dry_run": self.dry_run, "error": self.error}


def fetch(cfg: ImapConfig | None = None, dest: Path | None = None,
          dry_run: bool = False, limit: int = 0) -> FetchResult:
    cfg = cfg or ImapConfig()
    dest = Path(dest) if dest else inbox_dir()
    if not cfg.configured:
        return FetchResult([], 0, dry_run, "IMAP nicht konfiguriert (RECHNUNG_IMAP_*)")

    M = imaplib.IMAP4_SSL(cfg.host, cfg.port) if cfg.ssl else imaplib.IMAP4(cfg.host, cfg.port)
    saved: list[str] = []
    mails = 0
    try:
        M.login(cfg.user, cfg.password)
        M.select(cfg.source_folder)
        typ, data = M.search(None, "UNSEEN")
        ids = data[0].split() if data and data[0] else []
        if limit:
            ids = ids[:limit]
        for num in ids:
            typ, msg_data = M.fetch(num, "(RFC822)")
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            atts = attachments_from_bytes(raw, cfg.extensions)
            if not atts:
                continue
            mails += 1
            for att in atts:
                if dry_run:
                    saved.append(f"(dry) {att.filename} <- {att.from_addr}")
                    continue
                p = _save(att, dest)
                saved.append(str(p))
            if not dry_run:
                M.store(num, "+FLAGS", "\\Seen")
                if cfg.done_folder:
                    M.copy(num, cfg.done_folder)
                    M.store(num, "+FLAGS", "\\Deleted")
        if not dry_run and cfg.done_folder:
            M.expunge()
    except (imaplib.IMAP4.error, OSError) as e:
        return FetchResult(saved, mails, dry_run, f"{type(e).__name__}: {e}")
    finally:
        try:
            M.logout()
        except Exception:
            pass
    return FetchResult(saved, mails, dry_run)
