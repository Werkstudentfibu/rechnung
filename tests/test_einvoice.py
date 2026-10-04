"""Stufe 0: E-Rechnungen (XRechnung CII/UBL, ZUGFeRD/Factur-X-Hybrid)."""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from rechnung import einvoice
from rechnung.identify import Supplier
from rechnung.pipeline import (AUTO, DEFERRED, EXTRACT_EINVOICE, REVIEW, UNSUPPORTED, finalize,
                               invoice_files, process)
from rechnung.store import Store

CII_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rsm:CrossIndustryInvoice
  xmlns:rsm="urn:un:unece:uncefact:data:standard:CrossIndustryInvoice:100"
  xmlns:ram="urn:un:unece:uncefact:data:standard:ReusableAggregateBusinessInformationEntity:100"
  xmlns:udt="urn:un:unece:uncefact:data:standard:UnqualifiedDataType:100">
  <rsm:ExchangedDocumentContext>
    <ram:GuidelineSpecifiedDocumentContextParameter>
      <ram:ID>urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</ram:ID>
    </ram:GuidelineSpecifiedDocumentContextParameter>
  </rsm:ExchangedDocumentContext>
  <rsm:ExchangedDocument>
    <ram:ID>{nr}</ram:ID>
    <ram:TypeCode>{type_code}</ram:TypeCode>
    <ram:IssueDateTime><udt:DateTimeString format="102">20260915</udt:DateTimeString></ram:IssueDateTime>
  </rsm:ExchangedDocument>
  <rsm:SupplyChainTradeTransaction>
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:AssociatedDocumentLineDocument><ram:LineID>1</ram:LineID></ram:AssociatedDocumentLineDocument>
      <ram:SpecifiedTradeProduct><ram:Name>Druckerpapier A4</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeAgreement>
        <ram:NetPriceProductTradePrice><ram:ChargeAmount>5.00</ram:ChargeAmount></ram:NetPriceProductTradePrice>
      </ram:SpecifiedLineTradeAgreement>
      <ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="H87">20</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>
      <ram:SpecifiedLineTradeSettlement>
        <ram:ApplicableTradeTax><ram:TypeCode>VAT</ram:TypeCode><ram:CategoryCode>S</ram:CategoryCode><ram:RateApplicablePercent>19</ram:RateApplicablePercent></ram:ApplicableTradeTax>
        <ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>100.00</ram:LineTotalAmount></ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>
    <ram:IncludedSupplyChainTradeLineItem>
      <ram:AssociatedDocumentLineDocument><ram:LineID>2</ram:LineID></ram:AssociatedDocumentLineDocument>
      <ram:SpecifiedTradeProduct><ram:Name>Fachbuch</ram:Name></ram:SpecifiedTradeProduct>
      <ram:SpecifiedLineTradeAgreement>
        <ram:NetPriceProductTradePrice><ram:ChargeAmount>50.00</ram:ChargeAmount></ram:NetPriceProductTradePrice>
      </ram:SpecifiedLineTradeAgreement>
      <ram:SpecifiedLineTradeDelivery><ram:BilledQuantity unitCode="H87">1</ram:BilledQuantity></ram:SpecifiedLineTradeDelivery>
      <ram:SpecifiedLineTradeSettlement>
        <ram:ApplicableTradeTax><ram:TypeCode>VAT</ram:TypeCode><ram:CategoryCode>S</ram:CategoryCode><ram:RateApplicablePercent>7</ram:RateApplicablePercent></ram:ApplicableTradeTax>
        <ram:SpecifiedTradeSettlementLineMonetarySummation><ram:LineTotalAmount>50.00</ram:LineTotalAmount></ram:SpecifiedTradeSettlementLineMonetarySummation>
      </ram:SpecifiedLineTradeSettlement>
    </ram:IncludedSupplyChainTradeLineItem>
    <ram:ApplicableHeaderTradeAgreement>
      <ram:BuyerReference>991-12345-67</ram:BuyerReference>
      <ram:SellerTradeParty>
        <ram:Name>Papier &amp; Co GmbH</ram:Name>
        <ram:PostalTradeAddress>
          <ram:PostcodeCode>50667</ram:PostcodeCode>
          <ram:LineOne>Domplatz 1</ram:LineOne>
          <ram:CityName>Koeln</ram:CityName>
          <ram:CountryID>DE</ram:CountryID>
        </ram:PostalTradeAddress>
        <ram:SpecifiedTaxRegistration><ram:ID schemeID="FC">214/5678/9012</ram:ID></ram:SpecifiedTaxRegistration>
        <ram:SpecifiedTaxRegistration><ram:ID schemeID="VA">DE123456789</ram:ID></ram:SpecifiedTaxRegistration>
      </ram:SellerTradeParty>
      <ram:BuyerTradeParty><ram:Name>Musterkunde AG</ram:Name></ram:BuyerTradeParty>
      <ram:BuyerOrderReferencedDocument><ram:IssuerAssignedID>PO-7781</ram:IssuerAssignedID></ram:BuyerOrderReferencedDocument>
    </ram:ApplicableHeaderTradeAgreement>
    <ram:ApplicableHeaderTradeDelivery/>
    <ram:ApplicableHeaderTradeSettlement>
      <ram:InvoiceCurrencyCode>EUR</ram:InvoiceCurrencyCode>
      <ram:SpecifiedTradeSettlementPaymentMeans>
        <ram:TypeCode>58</ram:TypeCode>
        <ram:PayeePartyCreditorFinancialAccount><ram:IBANID>DE89 3704 0044 0532 0130 00</ram:IBANID></ram:PayeePartyCreditorFinancialAccount>
      </ram:SpecifiedTradeSettlementPaymentMeans>
      <ram:ApplicableTradeTax>
        <ram:CalculatedAmount>19.00</ram:CalculatedAmount><ram:TypeCode>VAT</ram:TypeCode>
        <ram:BasisAmount>100.00</ram:BasisAmount><ram:CategoryCode>S</ram:CategoryCode>
        <ram:RateApplicablePercent>19</ram:RateApplicablePercent>
      </ram:ApplicableTradeTax>
      <ram:ApplicableTradeTax>
        <ram:CalculatedAmount>3.50</ram:CalculatedAmount><ram:TypeCode>VAT</ram:TypeCode>
        <ram:BasisAmount>50.00</ram:BasisAmount><ram:CategoryCode>S</ram:CategoryCode>
        <ram:RateApplicablePercent>7</ram:RateApplicablePercent>
      </ram:ApplicableTradeTax>
      <ram:SpecifiedTradePaymentTerms>
        <ram:Description>Zahlbar innerhalb 14 Tagen ohne Abzug</ram:Description>
        <ram:DueDateDateTime><udt:DateTimeString format="102">20260929</udt:DateTimeString></ram:DueDateDateTime>
      </ram:SpecifiedTradePaymentTerms>
      <ram:SpecifiedTradeSettlementHeaderMonetarySummation>
        <ram:LineTotalAmount>150.00</ram:LineTotalAmount>
        <ram:TaxBasisTotalAmount>150.00</ram:TaxBasisTotalAmount>
        <ram:TaxTotalAmount currencyID="EUR">22.50</ram:TaxTotalAmount>
        <ram:GrandTotalAmount>{brutto}</ram:GrandTotalAmount>
        <ram:DuePayableAmount>{brutto}</ram:DuePayableAmount>
      </ram:SpecifiedTradeSettlementHeaderMonetarySummation>
    </ram:ApplicableHeaderTradeSettlement>
  </rsm:SupplyChainTradeTransaction>
</rsm:CrossIndustryInvoice>
"""

UBL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Invoice xmlns="urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
  xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2"
  xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">
  <cbc:CustomizationID>urn:cen.eu:en16931:2017#compliant#urn:xeinkauf.de:kosit:xrechnung_3.0</cbc:CustomizationID>
  <cbc:ID>UBL-2026-0815</cbc:ID>
  <cbc:IssueDate>2026-09-20</cbc:IssueDate>
  <cbc:DueDate>2026-10-20</cbc:DueDate>
  <cbc:InvoiceTypeCode>380</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>EUR</cbc:DocumentCurrencyCode>
  <cbc:BuyerReference>04011000-12345-34</cbc:BuyerReference>
  <cac:OrderReference><cbc:ID>BEST-42</cbc:ID></cac:OrderReference>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyName><cbc:Name>Hosting Nord</cbc:Name></cac:PartyName>
    <cac:PostalAddress>
      <cbc:StreetName>Hafenstr. 5</cbc:StreetName><cbc:CityName>Hamburg</cbc:CityName>
      <cbc:PostalZone>20457</cbc:PostalZone>
      <cac:Country><cbc:IdentificationCode>DE</cbc:IdentificationCode></cac:Country>
    </cac:PostalAddress>
    <cac:PartyTaxScheme><cbc:CompanyID>DE 987654321</cbc:CompanyID><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:PartyTaxScheme>
    <cac:PartyLegalEntity><cbc:RegistrationName>Hosting Nord GmbH</cbc:RegistrationName></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty><cac:Party>
    <cac:PartyLegalEntity><cbc:RegistrationName>Musterkunde AG</cbc:RegistrationName></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingCustomerParty>
  <cac:PaymentMeans>
    <cbc:PaymentMeansCode>58</cbc:PaymentMeansCode>
    <cac:PayeeFinancialAccount><cbc:ID>DE02120300000000202051</cbc:ID></cac:PayeeFinancialAccount>
  </cac:PaymentMeans>
  <cac:PaymentTerms><cbc:Note>30 Tage netto</cbc:Note></cac:PaymentTerms>
  <cac:TaxTotal>
    <cbc:TaxAmount currencyID="EUR">38.00</cbc:TaxAmount>
    <cac:TaxSubtotal>
      <cbc:TaxableAmount currencyID="EUR">200.00</cbc:TaxableAmount>
      <cbc:TaxAmount currencyID="EUR">38.00</cbc:TaxAmount>
      <cac:TaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>19</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory>
    </cac:TaxSubtotal>
  </cac:TaxTotal>
  <cac:LegalMonetaryTotal>
    <cbc:LineExtensionAmount currencyID="EUR">200.00</cbc:LineExtensionAmount>
    <cbc:TaxExclusiveAmount currencyID="EUR">200.00</cbc:TaxExclusiveAmount>
    <cbc:TaxInclusiveAmount currencyID="EUR">238.00</cbc:TaxInclusiveAmount>
    <cbc:PayableAmount currencyID="EUR">238.00</cbc:PayableAmount>
  </cac:LegalMonetaryTotal>
  <cac:InvoiceLine>
    <cbc:ID>1</cbc:ID>
    <cbc:InvoicedQuantity unitCode="MON">2</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="EUR">200.00</cbc:LineExtensionAmount>
    <cac:Item><cbc:Name>Managed Server</cbc:Name>
      <cac:ClassifiedTaxCategory><cbc:ID>S</cbc:ID><cbc:Percent>19</cbc:Percent><cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:ClassifiedTaxCategory>
    </cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="EUR">100.00</cbc:PriceAmount></cac:Price>
  </cac:InvoiceLine>
</Invoice>
"""


def cii(nr: str = "ZF-2026-001", brutto: str = "172.50", type_code: str = "380") -> bytes:
    return CII_XML.format(nr=nr, brutto=brutto, type_code=type_code).encode()


def make_hybrid_pdf(path: Path, xml: bytes, *, shown_nr: str, shown_brutto: str,
                    att_name: str = "factur-x.xml") -> Path:
    """Sichtbares PDF + eingebettetes XML, so wie ZUGFeRD/Factur-X es liefern."""
    tmp = path.with_suffix(".plain.pdf")
    c = canvas.Canvas(str(tmp), pagesize=A4)
    c.drawString(40, 780, "Papier & Co GmbH")
    c.drawString(40, 740, f"Rechnungsnummer {shown_nr}")
    c.drawString(40, 720, f"Gesamtbetrag {shown_brutto} EUR")
    c.save()
    pdf = pdfium.PdfDocument(str(tmp))
    att = pdf.new_attachment(att_name)
    att.set_data(xml)
    pdf.save(str(path))
    pdf.close()
    tmp.unlink()
    return path


@pytest.fixture()
def store(tmp_path) -> Store:
    return Store(tmp_path / "data")


def _papier(store: Store) -> Supplier:
    s = Supplier(id="papier", name="Papier & Co GmbH", ustids=["DE123456789"],
                 ibans=["DE89370400440532013000"])
    store.save_supplier(s)
    return s


# ---------------------------------------------------------------- Parser

def test_parse_cii_all_fields():
    inv = einvoice.parse(cii())
    assert inv is not None and inv.syntax == "cii"
    assert inv.rechnungsnummer == "ZF-2026-001"
    assert inv.rechnungsdatum == "2026-09-15"
    assert inv.faelligkeit == "2026-09-29"
    assert (inv.netto, inv.ust_betrag, inv.brutto) == (150.0, 22.5, 172.5)
    assert inv.verkaeufer_name == "Papier & Co GmbH"
    assert inv.verkaeufer_ustid == "DE123456789"
    assert inv.verkaeufer_steuernummer == "214/5678/9012"
    assert inv.verkaeufer_iban == ["DE89370400440532013000"]
    assert inv.verkaeufer_adresse == ["Domplatz 1", "50667 Koeln", "DE"]
    assert inv.leitweg_id == "991-12345-67"
    assert inv.bestellnummer == "PO-7781"
    assert {(t["satz"], t["netto"], t["ust"]) for t in inv.steuern} == {
        (19.0, 100.0, 19.0), (7.0, 50.0, 3.5)}
    assert [p["bezeichnung"] for p in inv.positionen] == ["Druckerpapier A4", "Fachbuch"]
    assert inv.positionen[0]["menge"] == 20 and inv.positionen[0]["netto"] == 100.0
    assert "xrechnung" in inv.profile


def test_parse_ubl_all_fields():
    inv = einvoice.parse(UBL_XML.encode())
    assert inv is not None and inv.syntax == "ubl"
    assert inv.rechnungsnummer == "UBL-2026-0815"
    assert inv.rechnungsdatum == "2026-09-20"
    assert (inv.netto, inv.ust_betrag, inv.brutto) == (200.0, 38.0, 238.0)
    assert inv.verkaeufer_name == "Hosting Nord GmbH"
    assert inv.verkaeufer_ustid == "DE987654321"
    assert inv.verkaeufer_iban == ["DE02120300000000202051"]
    assert inv.bestellnummer == "BEST-42"
    assert inv.positionen[0]["menge"] == 2 and inv.positionen[0]["einheit"] == "MON"
    assert not inv.is_credit_note


def test_credit_note_flagged():
    inv = einvoice.parse(cii(type_code="381"))
    assert inv.is_credit_note


@pytest.mark.parametrize("data", [
    b"",
    b"<foo/>",
    b"kein xml",
    # Entity-Expansion ("billion laughs") muss abgelehnt werden, nicht geparst
    b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>'
    b'<rsm:CrossIndustryInvoice xmlns:rsm="urn:un:unece:uncefact:data:standard:'
    b'CrossIndustryInvoice:100">&lol2;</rsm:CrossIndustryInvoice>',
])
def test_parse_rejects_garbage_and_dtd(data):
    assert einvoice.parse(data) is None


def test_plain_pdf_is_not_einvoice(tmp_path):
    p = tmp_path / "plain.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    c.drawString(40, 780, "Rechnung")
    c.save()
    assert einvoice.load(p) is None


# ---------------------------------------------------------------- Pipeline

def test_xrechnung_xml_known_supplier_is_auto(tmp_path, store):
    _papier(store)
    p = tmp_path / "xr.xml"
    p.write_bytes(cii())
    r = process(p, store)
    assert r.extraction == EXTRACT_EINVOICE
    assert r.supplier_id == "papier" and r.template_id is None
    assert r.fields["brutto"].value == 172.5 and r.fields["brutto"].confidence == 1.0
    assert r.fields["rechnungsdatum"].value == "2026-09-15"
    assert {t["satz"] for t in r.tax_breakdown} == {19.0, 7.0}
    assert len(r.line_items) == 2
    assert r.status == AUTO, [c for c in r.checks if c.ok is False]


def test_unknown_supplier_is_deferred_with_seller_name_as_hint(tmp_path, store):
    """Lieferant nur aus der Bibliothek: der Verkaeufername aus dem XML ist
    nur ein Hinweis im Rueckstellgrund, keine Identitaet."""
    p = tmp_path / "ubl.xml"
    p.write_text(UBL_XML)
    r = process(p, store)
    assert r.supplier_id is None and r.supplier_name is None
    assert r.status == DEFERRED
    assert "Hosting Nord GmbH" in r.hold_reasons[0]
    assert r.fields["netto"].value == 200.0


def test_wrong_totals_go_to_review(tmp_path, store):
    _papier(store)
    p = tmp_path / "bad.xml"
    p.write_bytes(cii(brutto="999.99"))
    r = process(p, store)
    assert r.status == REVIEW
    assert any(c.name == "netto+ust=brutto" and c.ok is False for c in r.checks)


def test_iban_mismatch_vs_master_data_goes_to_review(tmp_path, store):
    """Gleiche USt-IdNr, aber andere IBAN im XML als im Stammdatensatz --
    klassisches Betrugsmuster (geaenderte Bankverbindung)."""
    store.save_supplier(Supplier(id="papier", name="Papier & Co GmbH",
                                 ustids=["DE123456789"], ibans=["DE02120300000000202051"]))
    p = tmp_path / "xr.xml"
    p.write_bytes(cii())
    r = process(p, store)
    assert r.supplier_id == "papier"
    chk = next(c for c in r.checks if c.name == "iban-stammdaten")
    assert chk.ok is False and "IBAN weicht von Stammdaten ab (Betrugsschutz)" in chk.detail
    assert r.status == DEFERRED
    # Stammdaten haben Vorrang: die IBAN aus dem XML wird nicht uebernommen
    assert r.fields["iban"].value == "DE02120300000000202051"
    assert r.fields["iban"].source == "library"
    assert r.shadow["would_pass"] is False


def test_hybrid_pdf_matching(tmp_path, store):
    _papier(store)
    p = make_hybrid_pdf(tmp_path / "zf.pdf", cii(), shown_nr="ZF-2026-001",
                        shown_brutto="172,50")
    r = process(p, store)
    assert r.extraction == EXTRACT_EINVOICE
    assert r.einvoice["source"] == "pdf:factur-x.xml"
    assert any(c.name == "pdf-xml-abgleich" and c.ok is True for c in r.checks)
    assert r.status == AUTO


def test_hybrid_pdf_mismatch_goes_to_review(tmp_path, store):
    """Sichtbares PDF zeigt einen anderen Betrag als das XML -> Mensch."""
    _papier(store)
    p = make_hybrid_pdf(tmp_path / "zf.pdf", cii(), shown_nr="ZF-2026-001",
                        shown_brutto="1.172,50")
    r = process(p, store)
    chk = next(c for c in r.checks if c.name == "pdf-xml-abgleich")
    assert chk.ok is False and "Brutto" in chk.detail
    assert r.status == REVIEW


def test_foreign_xml_is_unsupported(tmp_path, store):
    p = tmp_path / "bestellung.xml"
    p.write_text("<Order/>")
    assert process(p, store).status == UNSUPPORTED


def test_invoice_files_includes_xml(tmp_path):
    for n in ("a.pdf", "b.xml", "c.txt", "D.XML"):
        (tmp_path / n).write_text("x")
    assert [p.name for p in invoice_files(tmp_path)] == ["D.XML", "a.pdf", "b.xml"]


def test_finalize_archives_xml_original(tmp_path, store):
    _papier(store)
    p = tmp_path / "xr.xml"
    p.write_bytes(cii())
    r = process(p, store)
    assert finalize(r, store, to_jobrouter=False)["error"] == "not_confirmed"
    store.mark_confirmed(r.sha256, "test")
    out = finalize(r, store, to_jobrouter=False)
    assert out["finalized"] is True
    assert (store.root / "archive" / f"{r.sha256}.xml").read_bytes() == cii()
    # Dublette: dieselbe Rechnungsnummer erneut -> nicht mehr AUTO
    p2 = tmp_path / "xr-kopie.xml"
    p2.write_bytes(cii().replace(b"<ram:LineOne>", b"<ram:LineOne>Hof "))
    r2 = process(p2, store)
    assert any(c.name == "keine-dublette" and c.ok is False for c in r2.checks)
    assert r2.status == REVIEW


def test_parse_date_iso_not_misread_as_dayfirst():
    from datetime import date
    from rechnung.textnorm import parse_date
    assert parse_date("2026-09-15") == date(2026, 9, 15)
    assert parse_date("Datum: 2026-09-15") == date(2026, 9, 15)
    assert parse_date("15.09.2026") == date(2026, 9, 15)
