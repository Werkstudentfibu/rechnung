"""Import der Abbyy-FlexiCapture-Stammdaten ('Vendors'/'BusinessUnits' Data
Sets, siehe rechnung/importers.py) -- getestet gegen das offiziell dokumentierte
Abbyy-Spaltenschema mit synthetischen Daten, da das Programm noch nicht im
Unternehmen im Einsatz ist und es noch keinen echten Export gibt
(Konversation vom 2026-09-18)."""

from pathlib import Path

from rechnung.importers import import_business_units_csv, import_vendors_csv
from rechnung.store import Store

VENDORS_CSV = (
    "Id,Name,VATID,NationalVATID,IBAN,BankAccount,BankCode,Street,ZIP,City,State,CountryCode,GLCode\n"
    "muster-gmbh,Muster GmbH,DE123456789,,DE89370400440532013000,,,Musterstr. 1,50667,Koeln,,DE,4400\n"
    "swiss-ag,Swiss AG,CHE-123.456.789,,CH9300762011623852957,,,Bahnhofstr. 5,8001,Zuerich,,CH,\n"
)

BUSINESS_UNITS_CSV = (
    "Id,Name,VATID,Street,ZIP,City,State,CountryCode\n"
    "werk-sued,Werk Sued,DE999999999,Industriering 9,70565,Stuttgart,,DE\n"
)


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, "utf-8")
    return p


def test_import_vendors_creates_suppliers_with_full_mapping(tmp_path):
    store = Store(tmp_path / "data")
    csv_path = _write(tmp_path, "vendors.csv", VENDORS_CSV)

    n = import_vendors_csv(csv_path, store)
    assert n == 2

    muster = store.supplier("muster-gmbh")
    assert muster.name == "Muster GmbH"
    assert muster.ustids == ["DE123456789"]
    assert muster.ibans == ["DE89370400440532013000"]
    assert muster.address_lines == ["Musterstr. 1", "50667 Koeln"]
    assert muster.currency == "EUR"

    swiss = store.supplier("swiss-ag")
    assert swiss.currency == "CHF"
    assert swiss.ustids == ["CHE-123.456.789"]


def test_import_vendors_is_idempotent_upsert(tmp_path):
    store = Store(tmp_path / "data")
    csv_path = _write(tmp_path, "vendors.csv", VENDORS_CSV)

    import_vendors_csv(csv_path, store)
    n2 = import_vendors_csv(csv_path, store)
    assert n2 == 2
    assert len(store.suppliers()) == 2  # keine Dubletten bei erneutem Einspielen


def test_import_vendors_preserves_manually_added_ibans(tmp_path):
    """Ein zweiter Import (z.B. aktualisierte Abbyy-Liste) darf eine im
    Programm zusaetzlich erfasste IBAN nicht verschlucken -- IBANs werden
    gemergt, nicht ersetzt."""
    store = Store(tmp_path / "data")
    csv_path = _write(tmp_path, "vendors.csv", VENDORS_CSV)
    import_vendors_csv(csv_path, store)

    sup = store.supplier("muster-gmbh")
    sup.ibans = sorted(set(sup.ibans) | {"DE00000000000000000000"})
    store.save_supplier(sup)

    import_vendors_csv(csv_path, store)
    sup = store.supplier("muster-gmbh")
    assert set(sup.ibans) == {"DE89370400440532013000", "DE00000000000000000000"}


def test_import_business_units_creates_billing_addresses(tmp_path):
    store = Store(tmp_path / "data")
    csv_path = _write(tmp_path, "bu.csv", BUSINESS_UNITS_CSV)

    n = import_business_units_csv(csv_path, store)
    assert n == 1

    addr = store.address("werk-sued")
    assert addr.name == "Werk Sued"
    assert addr.lines == ["Industriering 9", "70565 Stuttgart"]
    assert "Werk Sued" in addr.match_terms
    assert "Industriering 9" in addr.match_terms
    assert "USt-IdNr: DE999999999" in addr.notes


def test_import_vendors_handles_semicolon_delimiter_and_cp1252(tmp_path):
    """Deutsche Windows-Exporte liefern haeufig ';'-getrennte cp1252-Dateien
    statt UTF-8/Komma -- muss ohne kaputte Umlaute durchlaufen."""
    store = Store(tmp_path / "data")
    content = (
        "Id;Name;VATID;NationalVATID;IBAN;BankAccount;BankCode;Street;ZIP;City;State;CountryCode;GLCode\n"
        "muellermeier;Müller & Meier GmbH;DE111111111;;DE12500105170648489890;;;Königsallee 1;40212;Düsseldorf;;DE;\n"
    )
    p = tmp_path / "vendors_cp1252.csv"
    p.write_bytes(content.encode("cp1252"))

    n = import_vendors_csv(p, store)
    assert n == 1
    sup = store.supplier("muellermeier")
    assert sup.name == "Müller & Meier GmbH"
    assert sup.address_lines[0] == "Königsallee 1"
