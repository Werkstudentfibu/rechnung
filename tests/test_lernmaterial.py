"""Lernmaterial gezielt holen (80/15-Regel) -- mit erfundenem Postfach."""

from __future__ import annotations

import json
from email.message import EmailMessage
from pathlib import Path

import pytest

from rechnung import lernmaterial
from rechnung.config import ImapConfig
from rechnung.store import Store

pytest.importorskip("reportlab")
from rechnung.synth import make_invoice  # noqa: E402


def _zeile(von, mid, datum="2024-01-01", anhang=True, ordner="Done", uid=1):
    return {"postfach": "ap@x.example", "mandant": "", "ordner": ordner, "uid": uid, "mid": mid,
            "von": von, "datum": datum, "betreff": "", "anhang": anhang}


def test_auswahl_80_15():
    scan = []
    n = 0
    for von, anzahl in (("a@lief.example", 10), ("b@lief.example", 5), ("c@lief.example", 3),
                        ("d@lief.example", 1), ("e@lief.example", 1)):
        for k in range(anzahl):
            n += 1
            scan.append(_zeile(von, f"<{n}>", datum=f"2024-01-{k + 1:02d}", uid=n))
    # Rauschen: eigene Hotels, ohne Anhang, doppelt in INBOX
    scan.append(_zeile("rechnung@althoffhotels.com", "<x1>", uid=900))
    scan.append(_zeile("a@lief.example", "<x2>", anhang=False, uid=901))
    scan.append(dict(scan[0], ordner="INBOX"))
    auswahl, kz = lernmaterial.auswaehlen(scan, bekannte={"<1>"})
    assert kz["mails_mit_anhang"] == 20 and kz["absender_80"] == 3 and kz["absender_95"] == 4
    je = {}
    for m in auswahl:
        je[m["von"]] = je.get(m["von"], 0) + 1
    assert je == {"a@lief.example": 2, "b@lief.example": 3, "c@lief.example": 3, "d@lief.example": 1}
    a = [m for m in auswahl if m["von"] == "a@lief.example"]
    assert [m["datum"] for m in a] == ["2024-01-10", "2024-01-09"]      # neueste zuerst
    assert kz["schon_geholt"] == 1 and kz["holen_80"] == 8 and kz["holen_95"] == 1


def _mail(von: str, mid: str, pdf: bytes) -> bytes:
    m = EmailMessage()
    m["From"] = von
    m["Subject"] = "Rechnung"
    m["Message-ID"] = mid
    m["Date"] = "Mon, 01 Jan 2024 10:00:00 +0100"
    m.set_content("Anbei die Rechnung.")
    m.add_attachment(pdf, maintype="application", subtype="pdf", filename=f"re_{mid.strip('<>')}.pdf")
    return bytes(m)


class _FakeImap:
    """Postfach mit einem Ordner 'Done' -- nur lesende Befehle."""

    def __init__(self, mails: list[bytes]):
        self.mails = mails
        self.gelesen_readonly = []

    def login(self, user, pw):
        return "OK", []

    def select(self, ordner, readonly=False):
        self.gelesen_readonly.append(readonly)
        return ("OK", [str(len(self.mails)).encode()]) if ordner == '"Done"' else ("NO", [])

    def fetch(self, bereich, was):
        start, ende = (int(x) for x in bereich.split(":"))
        out = []
        for i in range(start, ende + 1):
            raw = self.mails[i - 1]
            kopf = raw.split(b"\n\n", 1)[0]
            meta = f'{i} (UID {100 + i} BODYSTRUCTURE (("application" "pdf")) BODY[HEADER.FIELDS] {{{len(kopf)}}}'.encode()
            out += [(meta, kopf), b")"]
        return "OK", out

    def uid(self, befehl, uid, was):
        raw = self.mails[int(uid) - 101]
        return "OK", [(f"1 (UID {uid} BODY[] {{{len(raw)}}}".encode(), raw), b")"]

    def logout(self):
        return "OK", []


def test_lauf_holt_ins_eingang_und_merkt_sich(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("RECHNUNG_DATA", str(data))
    store = Store(data)
    ziel = tmp_path / "eingang"
    ziel.mkdir()
    pdf = make_invoice(tmp_path / "q.pdf").read_bytes()
    mails = [_mail("a@lief.example", f"<m{i}@x>", pdf + str(i).encode()) for i in range(4)]
    mails.append(_mail("b@lief.example", "<n1@x>", pdf + b"b"))
    imap = _FakeImap(mails)
    cfg = ImapConfig(host="x", user="ap@x.example", password="", source_folder="Done", nur_lesen=True)
    konten = [("Testpostfach", cfg, None)]
    r = lernmaterial.lauf(store, ziel, konten=konten, verbinden=lambda c: imap, melde=lambda s: None)
    assert r["absender"] == 2 and r["ausgewaehlt"] == 4 and r["geholt"] == 4    # a: 3 neueste, b: 1
    assert len(list(ziel.iterdir())) == 4
    assert all(imap.gelesen_readonly)                                           # nur lesend geoeffnet
    assert (data / lernmaterial.SCAN_DATEI).exists() and (data / lernmaterial.AUSWAHL_DATEI).exists()
    # zweiter Lauf: Scan wird wiederverwendet, nichts Neues
    r2 = lernmaterial.lauf(store, ziel, konten=konten, verbinden=lambda c: imap, melde=lambda s: None)
    assert r2["ausgewaehlt"] == 0 and r2["schon_geholt"] == 4


def test_nur_anzeigen_holt_nichts(tmp_path: Path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("RECHNUNG_DATA", str(data))
    store = Store(data)
    scan = [_zeile("a@lief.example", "<1>", uid=1)]
    data.mkdir(parents=True, exist_ok=True)
    (data / lernmaterial.SCAN_DATEI).write_text(json.dumps(scan), "utf-8")
    r = lernmaterial.lauf(store, tmp_path / "eingang", nur_anzeigen=True, konten=[],
                          verbinden=lambda c: None, melde=lambda s: None)
    assert r["ausgewaehlt"] == 1 and "geholt" not in r


def test_befehl_ist_eingetragen(capsys):
    from rechnung import cli

    with pytest.raises(SystemExit):
        cli.main(["lernmaterial", "--help"])
    assert "--nur-anzeigen" in capsys.readouterr().out
