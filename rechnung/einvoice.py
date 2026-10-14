"""E-Rechnungen (XRechnung / ZUGFeRD / Factur-X) direkt auslesen.

Eine E-Rechnung traegt ihre Daten maschinenlesbar als XML -- entweder als
eigene Datei (XRechnung, meist ``*.xml``) oder eingebettet in ein PDF/A-3
(ZUGFeRD / Factur-X, Anhang ``factur-x.xml`` / ``zugferd-invoice.xml`` /
``xrechnung.xml``). Dann muss nichts per Layout-Template oder Modell geraten
werden: die Werte stehen eindeutig im XML. Die Pipeline prueft deshalb
*zuerst* hierauf (Stufe 0), vor Template und KI.

Unterstuetzte Syntaxen (EN 16931):
- **UN/CEFACT CII** (``CrossIndustryInvoice``) -- ZUGFeRD 2.x, Factur-X,
  XRechnung-CII
- **UBL 2.1** (``Invoice`` / ``CreditNote``) -- XRechnung-UBL, Peppol

Nur Standardbibliothek. Sicherheit: XML stammt von Fremden (Mail-Anhang).
Dokumente mit ``<!DOCTYPE`` werden abgelehnt -- EN-16931-Rechnungen haben
keine DTD, und ohne DTD gibt es weder Entity-Expansion ("billion laughs")
noch externe Entities. Zusaetzlich eine Groessengrenze.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

MAX_XML_BYTES = 100 * 1024 * 1024   # Peppol-"Large Invoice" mit eingebetteten Anhaengen ~25 MB

# Nur fuer die Korpus-Messung (tools/korpus_bericht.py, Baustein 2): Stufe 0
# abschalten, um zu messen, wie gut reines PDF-Lesen (Text/Tabelle/KI) ohne
# das eingebettete XML waere -- wie bei einem Scan/Ausdruck ohne E-Rechnung.
# Im echten Betrieb nie gesetzt.
IGNORE_ENV = "RECHNUNG_IGNORE_EINVOICE"

# Dateinamen eingebetteter Rechnungs-XML laut ZUGFeRD-/Factur-X-Spezifikation
# (Gross-/Kleinschreibung variiert in freier Wildbahn).
_EMBEDDED_NAMES = ("factur-x.xml", "zugferd-invoice.xml",
                   "xrechnung.xml", "order-x.xml")

NS = {
    "rsm": "urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100",
    "ram": "urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100",
    "udt": "urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100",
    "ubl": "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2",
    "ublcn": "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2",
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
}

CII = "cii"
UBL = "ubl"


@dataclass
class EInvoice:
    syntax: str                       # "cii" | "ubl"
    profile: str = ""                 # GuidelineSpecifiedDocumentContextParameter / CustomizationID
    source: str = ""                  # "xml" | "pdf:<anhangname>"
    type_code: str = ""               # 380 Rechnung, 381 Gutschrift, 384 Korrektur ...
    rechnungsnummer: str = ""
    rechnungsdatum: str = ""          # ISO yyyy-mm-dd
    faelligkeit: str = ""             # ISO yyyy-mm-dd
    waehrung: str = ""
    netto: float | None = None        # Summe Nettobetraege (TaxBasisTotal / TaxExclusiveAmount)
    ust_betrag: float | None = None
    brutto: float | None = None       # GrandTotal / TaxInclusiveAmount
    zahlbetrag: float | None = None   # DuePayable / PayableAmount
    summe_positionen: float | None = None  # BT-106: Summe der Positionsnettobetraege
    bestellnummer: str = ""
    leitweg_id: str = ""              # BuyerReference (BT-10)
    verkaeufer_name: str = ""
    verkaeufer_ustid: str = ""
    verkaeufer_steuernummer: str = ""
    verkaeufer_adresse: list[str] = field(default_factory=list)
    verkaeufer_iban: list[str] = field(default_factory=list)
    verkaeufer_bic: str = ""
    kaeufer_name: str = ""
    zahlungsbedingungen: str = ""
    steuern: list[dict] = field(default_factory=list)       # {satz, netto, ust, kategorie}
    positionen: list[dict] = field(default_factory=list)    # {pos, bezeichnung, menge, einheit, einzelpreis, netto, ust_satz}

    @property
    def is_credit_note(self) -> bool:
        return self.type_code in ("381", "261", "262", "296", "308", "396", "420", "458", "532")

    @property
    def is_correction(self) -> bool:
        """Gutschrift oder Rechnungskorrektur (384) -- dort sind negative
        Betraege zulaessig."""
        return self.is_credit_note or self.type_code == "384"

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


# ---------------------------------------------------------------- Laden

def _safe_parse(data: bytes) -> ET.Element | None:
    if not data or len(data) > MAX_XML_BYTES:
        return None
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        return None
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def _syntax(root: ET.Element) -> str | None:
    tag = root.tag
    if tag == f"{{{NS['rsm']}}}CrossIndustryInvoice":
        return CII
    if tag in (f"{{{NS['ubl']}}}Invoice", f"{{{NS['ublcn']}}}CreditNote"):
        return UBL
    return None


def embedded_xml(path: str | Path) -> tuple[str, bytes] | None:
    """Rechnungs-XML aus einem PDF-Anhang holen -> (anhangname, bytes)."""
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception:
        return None
    try:
        candidates: list[tuple[int, str, bytes]] = []
        for i in range(pdf.count_attachments()):
            att = pdf.get_attachment(i)
            name = att.get_name() or ""
            if not name.lower().endswith(".xml"):
                continue
            try:
                data = bytes(att.get_data())
            except Exception:
                continue
            prio = 0 if name.lower() in _EMBEDDED_NAMES else 1
            candidates.append((prio, name, data))
        for _, name, data in sorted(candidates, key=lambda c: c[0]):
            root = _safe_parse(data)
            if root is not None and _syntax(root):
                return name, data
        return None
    finally:
        pdf.close()


def load(path: str | Path) -> EInvoice | None:
    """Datei (``.xml`` oder ``.pdf``) -> EInvoice, sonst None."""
    if os.environ.get(IGNORE_ENV) == "1":
        return None
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".xml":
        data = path.read_bytes()
        source = "xml"
    elif suffix == ".pdf":
        hit = embedded_xml(path)
        if hit is None:
            return None
        source, data = f"pdf:{hit[0]}", hit[1]
    else:
        return None
    inv = parse(data)
    if inv is not None:
        inv.source = source
    return inv


def parse(data: bytes) -> EInvoice | None:
    root = _safe_parse(data)
    if root is None:
        return None
    syn = _syntax(root)
    if syn == CII:
        return _parse_cii(root)
    if syn == UBL:
        return _parse_ubl(root)
    return None


# ---------------------------------------------------------------- Helfer

def _t(el: ET.Element | None, path: str) -> str:
    if el is None:
        return ""
    x = el.find(path, NS)
    return (x.text or "").strip() if x is not None and x.text else ""


def _all(el: ET.Element | None, path: str) -> list[ET.Element]:
    return el.findall(path, NS) if el is not None else []


def _num(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _num_raw(s: str) -> float | None:
    try:
        return float(s.strip()) if s and s.strip() else None
    except ValueError:
        return None


def _cii_date(s: str) -> str:
    """CII udt:DateTimeString format 102 = YYYYMMDD -> ISO."""
    s = (s or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _iban(s: str) -> str:
    return (s or "").replace(" ", "").upper()


def _addr(parts: list[str]) -> list[str]:
    return [p for p in (x.strip() for x in parts) if p]


# ---------------------------------------------------------------- CII

def _parse_cii(root: ET.Element) -> EInvoice:
    ctx = root.find("rsm:ExchangedDocumentContext", NS)
    doc = root.find("rsm:ExchangedDocument", NS)
    tx = root.find("rsm:SupplyChainTradeTransaction", NS)
    agr = tx.find("ram:ApplicableHeaderTradeAgreement", NS) if tx is not None else None
    stl = tx.find("ram:ApplicableHeaderTradeSettlement", NS) if tx is not None else None
    seller = agr.find("ram:SellerTradeParty", NS) if agr is not None else None
    buyer = agr.find("ram:BuyerTradeParty", NS) if agr is not None else None
    summ = stl.find("ram:SpecifiedTradeSettlementHeaderMonetarySummation", NS) if stl is not None else None

    inv = EInvoice(syntax=CII)
    inv.profile = _t(ctx, "ram:GuidelineSpecifiedDocumentContextParameter/ram:ID")
    inv.rechnungsnummer = _t(doc, "ram:ID")
    inv.type_code = _t(doc, "ram:TypeCode")
    inv.rechnungsdatum = _cii_date(_t(doc, "ram:IssueDateTime/udt:DateTimeString"))
    inv.leitweg_id = _t(agr, "ram:BuyerReference")
    inv.bestellnummer = _t(agr, "ram:BuyerOrderReferencedDocument/ram:IssuerAssignedID")

    inv.verkaeufer_name = _t(seller, "ram:Name")
    for reg in _all(seller, "ram:SpecifiedTaxRegistration/ram:ID"):
        scheme = reg.get("schemeID", "")
        val = (reg.text or "").strip()
        if scheme == "VA" and not inv.verkaeufer_ustid:
            inv.verkaeufer_ustid = val.replace(" ", "").upper()
        elif scheme == "FC" and not inv.verkaeufer_steuernummer:
            inv.verkaeufer_steuernummer = val
    pa = seller.find("ram:PostalTradeAddress", NS) if seller is not None else None
    if pa is not None:
        inv.verkaeufer_adresse = _addr([
            _t(pa, "ram:LineOne"), _t(pa, "ram:LineTwo"), _t(pa, "ram:LineThree"),
            f"{_t(pa, 'ram:PostcodeCode')} {_t(pa, 'ram:CityName')}",
            _t(pa, "ram:CountryID"),
        ])
    inv.kaeufer_name = _t(buyer, "ram:Name")

    inv.waehrung = _t(stl, "ram:InvoiceCurrencyCode")
    for pm in _all(stl, "ram:SpecifiedTradeSettlementPaymentMeans"):
        iban = _iban(_t(pm, "ram:PayeePartyCreditorFinancialAccount/ram:IBANID"))
        if iban and iban not in inv.verkaeufer_iban:
            inv.verkaeufer_iban.append(iban)
        inv.verkaeufer_bic = inv.verkaeufer_bic or _t(
            pm, "ram:PayeeSpecifiedCreditorFinancialInstitution/ram:BICID")
    terms = stl.find("ram:SpecifiedTradePaymentTerms", NS) if stl is not None else None
    inv.zahlungsbedingungen = _t(terms, "ram:Description")
    inv.faelligkeit = _cii_date(_t(terms, "ram:DueDateDateTime/udt:DateTimeString"))

    for tax in _all(stl, "ram:ApplicableTradeTax"):
        inv.steuern.append({
            "satz": _num_raw(_t(tax, "ram:RateApplicablePercent")),
            "netto": _num(_t(tax, "ram:BasisAmount")),
            "ust": _num(_t(tax, "ram:CalculatedAmount")),
            "kategorie": _t(tax, "ram:CategoryCode"),
        })

    inv.netto = _num(_t(summ, "ram:TaxBasisTotalAmount"))
    # TaxTotalAmount darf mehrfach vorkommen (je Waehrung) -- die in
    # Rechnungswaehrung nehmen.
    for tt in _all(summ, "ram:TaxTotalAmount"):
        if not inv.waehrung or tt.get("currencyID", inv.waehrung) == inv.waehrung:
            inv.ust_betrag = _num(tt.text or "")
            break
    inv.brutto = _num(_t(summ, "ram:GrandTotalAmount"))
    inv.summe_positionen = _num(_t(summ, "ram:LineTotalAmount"))
    inv.zahlbetrag = _num(_t(summ, "ram:DuePayableAmount"))

    for li in _all(tx, "ram:IncludedSupplyChainTradeLineItem"):
        qty_el = li.find("ram:SpecifiedLineTradeDelivery/ram:BilledQuantity", NS)
        inv.positionen.append({
            "pos": _t(li, "ram:AssociatedDocumentLineDocument/ram:LineID"),
            "bezeichnung": _t(li, "ram:SpecifiedTradeProduct/ram:Name"),
            "menge": _num_raw(qty_el.text or "") if qty_el is not None else None,
            "einheit": qty_el.get("unitCode", "") if qty_el is not None else "",
            "einzelpreis": _num_raw(_t(li, "ram:SpecifiedLineTradeAgreement/"
                                           "ram:NetPriceProductTradePrice/ram:ChargeAmount")),
            "netto": _num(_t(li, "ram:SpecifiedLineTradeSettlement/"
                                 "ram:SpecifiedTradeSettlementLineMonetarySummation/"
                                 "ram:LineTotalAmount")),
            "ust_satz": _num_raw(_t(li, "ram:SpecifiedLineTradeSettlement/"
                                        "ram:ApplicableTradeTax/ram:RateApplicablePercent")),
        })
    return inv


# ---------------------------------------------------------------- UBL

def _parse_ubl(root: ET.Element) -> EInvoice:
    is_cn = root.tag.endswith("CreditNote")
    line_tag = "cac:CreditNoteLine" if is_cn else "cac:InvoiceLine"
    qty_tag = "cbc:CreditedQuantity" if is_cn else "cbc:InvoicedQuantity"

    inv = EInvoice(syntax=UBL)
    inv.profile = _t(root, "cbc:CustomizationID")
    inv.rechnungsnummer = _t(root, "cbc:ID")
    inv.type_code = _t(root, "cbc:CreditNoteTypeCode" if is_cn else "cbc:InvoiceTypeCode") \
        or ("381" if is_cn else "380")
    inv.rechnungsdatum = _t(root, "cbc:IssueDate")
    inv.faelligkeit = _t(root, "cbc:DueDate") or _t(root, "cac:PaymentMeans/cbc:PaymentDueDate")
    inv.waehrung = _t(root, "cbc:DocumentCurrencyCode")
    inv.leitweg_id = _t(root, "cbc:BuyerReference")
    inv.bestellnummer = _t(root, "cac:OrderReference/cbc:ID")

    seller = root.find("cac:AccountingSupplierParty/cac:Party", NS)
    inv.verkaeufer_name = (_t(seller, "cac:PartyLegalEntity/cbc:RegistrationName")
                           or _t(seller, "cac:PartyName/cbc:Name"))
    for pts in _all(seller, "cac:PartyTaxScheme"):
        cid = _t(pts, "cbc:CompanyID")
        scheme = _t(pts, "cac:TaxScheme/cbc:ID").upper()
        if scheme == "VAT" and not inv.verkaeufer_ustid:
            inv.verkaeufer_ustid = cid.replace(" ", "").upper()
        elif scheme != "VAT" and not inv.verkaeufer_steuernummer:
            inv.verkaeufer_steuernummer = cid
    pa = seller.find("cac:PostalAddress", NS) if seller is not None else None
    if pa is not None:
        inv.verkaeufer_adresse = _addr([
            _t(pa, "cbc:StreetName"), _t(pa, "cbc:AdditionalStreetName"),
            _t(pa, "cac:AddressLine/cbc:Line"),
            f"{_t(pa, 'cbc:PostalZone')} {_t(pa, 'cbc:CityName')}",
            _t(pa, "cac:Country/cbc:IdentificationCode"),
        ])
    buyer = root.find("cac:AccountingCustomerParty/cac:Party", NS)
    inv.kaeufer_name = (_t(buyer, "cac:PartyLegalEntity/cbc:RegistrationName")
                        or _t(buyer, "cac:PartyName/cbc:Name"))

    for pm in _all(root, "cac:PaymentMeans"):
        iban = _iban(_t(pm, "cac:PayeeFinancialAccount/cbc:ID"))
        if iban and iban not in inv.verkaeufer_iban:
            inv.verkaeufer_iban.append(iban)
        inv.verkaeufer_bic = inv.verkaeufer_bic or _t(
            pm, "cac:PayeeFinancialAccount/cac:FinancialInstitutionBranch/cbc:ID")
    inv.zahlungsbedingungen = _t(root, "cac:PaymentTerms/cbc:Note")

    for tt in _all(root, "cac:TaxTotal"):
        amt = tt.find("cbc:TaxAmount", NS)
        if amt is None:
            continue
        if inv.waehrung and amt.get("currencyID", inv.waehrung) != inv.waehrung:
            continue
        inv.ust_betrag = _num(amt.text or "")
        for sub in _all(tt, "cac:TaxSubtotal"):
            inv.steuern.append({
                "satz": _num_raw(_t(sub, "cac:TaxCategory/cbc:Percent")),
                "netto": _num(_t(sub, "cbc:TaxableAmount")),
                "ust": _num(_t(sub, "cbc:TaxAmount")),
                "kategorie": _t(sub, "cac:TaxCategory/cbc:ID"),
            })
        break

    lmt = root.find("cac:LegalMonetaryTotal", NS)
    inv.netto = _num(_t(lmt, "cbc:TaxExclusiveAmount"))
    inv.summe_positionen = _num(_t(lmt, "cbc:LineExtensionAmount"))
    inv.brutto = _num(_t(lmt, "cbc:TaxInclusiveAmount"))
    inv.zahlbetrag = _num(_t(lmt, "cbc:PayableAmount"))

    for li in _all(root, line_tag):
        qty_el = li.find(qty_tag, NS)
        inv.positionen.append({
            "pos": _t(li, "cbc:ID"),
            "bezeichnung": _t(li, "cac:Item/cbc:Name"),
            "menge": _num_raw(qty_el.text or "") if qty_el is not None else None,
            "einheit": qty_el.get("unitCode", "") if qty_el is not None else "",
            "einzelpreis": _num_raw(_t(li, "cac:Price/cbc:PriceAmount")),
            "netto": _num(_t(li, "cbc:LineExtensionAmount")),
            "ust_satz": _num_raw(_t(li, "cac:Item/cac:ClassifiedTaxCategory/cbc:Percent")),
        })
    return inv
