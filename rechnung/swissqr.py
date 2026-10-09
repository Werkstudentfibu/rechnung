"""Swiss QR-Rechnung: QR-Zahlteil lesen (Postfach-Baustein 7).

Der QR-Zahlteil einer Schweizer Rechnung traegt den SPC ("Swiss Payment
Code") maschinenlesbar als QR-Code -- eine feste Zeilenfolge nach den
Swiss Implementation Guidelines QR-bill: IBAN/QR-IBAN, Zahlungsempfaenger,
Betrag, Waehrung, Referenztyp (QRR/SCOR/NON) + Referenz, Debitor,
Mitteilung.

Genau wie die E-Rechnung (Stufe 0, einvoice.py) ist der SPC eine
STRUKTURIERTE Quelle, kein Rate-Versuch -- die gelesenen Werte werden gegen
den sichtbaren Rechnungstext/die Bibliothek geprueft (IBAN-Abweichung ->
Mensch, wie bei jeder anderen Rechnung).

``zxingcpp`` (lokal, pip-installierbar, auch unter Windows) ist eine
optionale Abhaengigkeit -- fehlt sie oder wird kein QR-Code gefunden,
degradiert ``read()`` sauber auf ``None`` (keine Pruefung, kein Absturz);
die Rechnung laeuft normal (ohne QR-Zahlteil-Pruefungen) weiter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .template import FieldResult
from .textnorm import compact
from .validate import Check

SOURCE = "qr"
TRAILER = "EPD"
# Feste Mindestzeilenzahl bis zum Referenztyp (siehe build_spc/parse_spc)
_MIN_LINES = 31

# QRR-Pruefziffer (wie die ESR-Referenznummer): rekursive Modulo-10-Tabelle
_QRR_TABLE = [
    [0, 9, 4, 6, 8, 2, 7, 1, 3, 5],
    [9, 4, 6, 8, 2, 7, 1, 3, 5, 0],
    [4, 6, 8, 2, 7, 1, 3, 5, 0, 9],
    [6, 8, 2, 7, 1, 3, 5, 0, 9, 4],
    [8, 2, 7, 1, 3, 5, 0, 9, 4, 6],
    [2, 7, 1, 3, 5, 0, 9, 4, 6, 8],
    [7, 1, 3, 5, 0, 9, 4, 6, 8, 2],
    [1, 3, 5, 0, 9, 4, 6, 8, 2, 7],
    [3, 5, 0, 9, 4, 6, 8, 2, 7, 1],
    [5, 0, 9, 4, 6, 8, 2, 7, 1, 3],
]


def qrr_valid(ref: str) -> bool:
    """27-stellige QR-Referenz, Pruefziffer wie bei der ESR-Referenznummer."""
    d = re.sub(r"\D", "", ref or "")
    if len(d) != 27:
        return False
    row = 0
    for c in d[:-1]:
        row = _QRR_TABLE[row][int(c)]
    return (10 - row) % 10 == int(d[-1])


def build_qrr_reference(base: str) -> str:
    """26 Ziffern (rechtsbuendig, linksseitig mit Nullen aufgefuellt) +
    Pruefziffer -> gueltige 27-stellige QR-Referenz. Hilfsfunktion fuer
    synthetische Testrechnungen (synth.py) -- die eigentliche Pruefung ist
    ``qrr_valid``."""
    d = re.sub(r"\D", "", base or "").rjust(26, "0")[-26:]
    row = 0
    for c in d:
        row = _QRR_TABLE[row][int(c)]
    return d + str((10 - row) % 10)


def scor_valid(ref: str) -> bool:
    """ISO-11649 Creditor Reference (SCOR), Pruefziffer wie IBAN (Modulo 97)."""
    ref = re.sub(r"\s+", "", ref or "").upper()
    if not re.fullmatch(r"RF\d{2}[A-Z0-9]{1,21}", ref):
        return False
    rearranged = ref[4:] + ref[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


@dataclass
class SwissQR:
    iban: str
    creditor_name: str
    betrag: float | None
    waehrung: str
    debtor_name: str
    referenz_typ: str
    referenz: str
    nachricht: str

    def to_dict(self) -> dict:
        return {"iban": self.iban, "creditor_name": self.creditor_name,
                "betrag": self.betrag, "waehrung": self.waehrung,
                "debtor_name": self.debtor_name, "referenz_typ": self.referenz_typ,
                "referenz": self.referenz, "nachricht": self.nachricht}


def build_spc(*, iban: str, creditor_name: str, creditor_street: str = "",
             creditor_building: str = "", creditor_zip: str = "", creditor_city: str = "",
             creditor_country: str = "CH", betrag: float | None, waehrung: str = "CHF",
             debtor_name: str = "", debtor_street: str = "", debtor_building: str = "",
             debtor_zip: str = "", debtor_city: str = "", debtor_country: str = "CH",
             referenz_typ: str = "NON", referenz: str = "", nachricht: str = "") -> str:
    """SPC-Inhalt bauen (Swiss Implementation Guidelines QR-bill) -- fuer
    synthetische Testrechnungen (synth.py); zugleich die Referenzimplementierung
    des von ``parse_spc`` erwarteten Zeilenformats."""
    lines = [
        "SPC", "0200", "1",
        iban,
        "S", creditor_name, creditor_street, creditor_building, creditor_zip, creditor_city,
        creditor_country,
        "", "", "", "", "", "", "",   # Ultimate Creditor -- veraltet, immer leer
        f"{betrag:.2f}" if betrag is not None else "", waehrung,
        "S" if debtor_name else "", debtor_name, debtor_street, debtor_building, debtor_zip,
        debtor_city, debtor_country,
        referenz_typ, referenz, nachricht, TRAILER,
    ]
    return "\r\n".join(lines)


def parse_spc(text: str) -> SwissQR | None:
    """Zeilenweise feste Positionen (siehe ``build_spc``) -- kein SPC-Inhalt
    (falscher Barcode-Typ auf der Rechnung, z.B. ein Produkt-QR-Code) ->
    ``None``."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if len(lines) < _MIN_LINES or lines[0].strip() != "SPC":
        return None
    try:
        betrag = float(lines[18]) if lines[18].strip() else None
    except ValueError:
        return None
    return SwissQR(
        iban=compact(lines[3]), creditor_name=lines[5].strip(),
        betrag=betrag, waehrung=lines[19].strip().upper(),
        debtor_name=lines[21].strip(), referenz_typ=lines[27].strip().upper(),
        referenz=re.sub(r"\s+", "", lines[28]), nachricht=lines[29].strip(),
    )


_HINT_RE = re.compile(r"(?i)zahlteil|empfangsschein|section\s+paiement|"
                      r"sezione\s+di\s+pagamento|qr-iban")


def likely(text: str) -> bool:
    """Schneller Text-Hinweis, ob das Rendern+Dekodieren ueberhaupt lohnt --
    jede Schweizer QR-Rechnung druckt eine dieser Beschriftungen als
    lesbaren Text neben dem QR-Code. Vermeidet das teure Seiten-Rendern bei
    jeder gewoehnlichen (Nicht-QR-)Rechnung."""
    return bool(_HINT_RE.search(text or ""))


def read(path: str | Path, pages: int = 2) -> SwissQR | None:
    """QR-Zahlteil aus den letzten ``pages`` Seiten lesen (dort liegt er
    nach den Swiss Implementation Guidelines immer). Fehlt ``zxingcpp``
    (optionale Abhaengigkeit) oder enthaelt keine der Seiten einen
    SPC-QR-Code -> ``None``, ohne Fehler."""
    try:
        import zxingcpp
    except ImportError:
        return None
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        return None
    try:
        doc = pdfium.PdfDocument(str(path))
    except Exception:
        return None
    try:
        n = len(doc)
        for i in range(max(0, n - pages), n):
            img = doc[i].render(scale=300 / 72).to_pil().convert("L")
            for barcode in zxingcpp.read_barcodes(img, formats=zxingcpp.QRCode):
                spc = parse_spc(barcode.text)
                if spc is not None:
                    return spc
    except Exception:
        return None
    finally:
        doc.close()
    return None


def apply(fields: dict, qr: SwissQR | None, hinweise: list[str] | None = None) -> None:
    """Zahlungsreferenz (QRR/SCOR) und -- falls aus dem Text noch nichts
    bekannt ist -- die Waehrung ergaenzen. Der sichtbare Rechnungstext
    (waehrung.py) und jede Korrektur haben Vorrang; der QR-Zahlteil ergaenzt,
    ersetzt aber keinen schon gesetzten Wert."""
    if qr is None:
        return
    if qr.referenz and qr.referenz_typ in ("QRR", "SCOR"):
        cur = fields.get("zahlungsreferenz")
        if cur is None or cur.source == "missing" or not cur.value:
            fields["zahlungsreferenz"] = FieldResult(
                "zahlungsreferenz", qr.referenz, f"{qr.referenz_typ} {qr.referenz}", 0.95, SOURCE)
    # Waehrung und Betrag stehen im QR-Zahlteil strukturiert vom Aussteller --
    # sie gewinnen gegen Textregel/KI (Befund Aaron 09.10.2026: Zuercher
    # QR-Rechnung lief als EUR mit deutscher MwSt, Brutto = Netto). Nur eine
    # Korrektur des Menschen ("manual") hat Vorrang.
    cur_w = fields.get("waehrung")
    if qr.waehrung and (cur_w is None or cur_w.source != "manual"):
        fields["waehrung"] = FieldResult("waehrung", qr.waehrung, qr.waehrung, 0.95, SOURCE)
    cur_b = fields.get("brutto")
    if qr.betrag is not None and (cur_b is None or cur_b.source != "manual"):
        if cur_b is not None and cur_b.source != "missing" and cur_b.value not in (None, "") \
                and hinweise is not None:
            try:
                abw = abs(float(cur_b.value) - qr.betrag) > 0.005
            except (TypeError, ValueError):
                abw = True
            if abw:
                hinweise.append(f"Brutto aus dem QR-Zahlteil ({qr.betrag:.2f}) statt "
                                f"gelesenem Wert {cur_b.value}")
        fields["brutto"] = FieldResult("brutto", qr.betrag, f"QR-Zahlteil {qr.betrag:.2f}", 0.95, SOURCE)


def parteien(ki_lieferant: dict, ki_empfaenger: dict, qr: "SwissQR | None") -> None:
    """Zahlungsempfaenger (= Rechnungssteller) und Zahlungspflichtiger
    (= Rechnungsempfaenger) aus dem QR-Zahlteil -- strukturiert vom Aussteller,
    gewinnen gegen die KI-Lesung (die auf Scans gern Absender und Empfaenger
    verwechselt, Befund 09.10.2026)."""
    if qr is None:
        return
    if qr.creditor_name:
        ki_lieferant["name"] = qr.creditor_name
    if qr.iban:
        ki_lieferant["iban"] = qr.iban
    if qr.debtor_name:
        ki_empfaenger["name"] = qr.debtor_name


def checks(fields: dict, qr: SwissQR | None, supplier=None) -> list[Check]:
    """IBAN/Betrag aus dem QR-Zahlteil gegen die Rechnung pruefen -- wie der
    PDF/XML-Abgleich einer Hybrid-E-Rechnung (``pipeline._check_pdf_matches_xml``).
    Referenz-Pruefziffer als eigene Pruefung."""
    if qr is None:
        return []
    out = []
    if qr.referenz_typ == "QRR":
        out.append(Check("qr-referenz", qrr_valid(qr.referenz), qr.referenz))
    elif qr.referenz_typ == "SCOR":
        out.append(Check("qr-referenz", scor_valid(qr.referenz), qr.referenz))
    betrag_fr = fields.get("brutto")
    if betrag_fr is not None and betrag_fr.source != "missing" and betrag_fr.value not in (None, "") \
            and qr.betrag is not None:
        try:
            diff = abs(float(betrag_fr.value) - qr.betrag)
            out.append(Check("qr-betrag", diff <= 0.02,
                             f"{qr.betrag:.2f} vs {float(betrag_fr.value):.2f}"))
        except (TypeError, ValueError):
            pass
    if supplier is not None and getattr(supplier, "ibans", None) and qr.iban:
        out.append(Check("qr-iban", qr.iban in set(supplier.ibans), qr.iban))
    return out
