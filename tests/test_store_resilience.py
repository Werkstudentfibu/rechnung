import json

from rechnung.identify import Supplier
from rechnung.store import Store


def test_save_supplier_creates_backup_on_second_write(tmp_path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="a", name="A GmbH"))
    bak = tmp_path / "data" / "suppliers.json.bak"
    assert not bak.exists()  # erster Write: nichts zu sichern

    store.save_supplier(Supplier(id="b", name="B GmbH"))
    assert bak.exists()  # zweiter Write: alte Version gesichert
    assert json.loads(bak.read_text("utf-8"))[0]["id"] == "a"
    assert {s.id for s in store.suppliers()} == {"a", "b"}


def test_corrupt_suppliers_file_recovers_from_backup(tmp_path):
    store = Store(tmp_path / "data")
    store.save_supplier(Supplier(id="a", name="A GmbH"))
    store.save_supplier(Supplier(id="b", name="B GmbH"))  # legt .bak mit "a" an

    suppliers_json = tmp_path / "data" / "suppliers.json"
    suppliers_json.write_text("{ das ist kein json", "utf-8")

    # keine Exception, kein Datenverlust: faellt auf die Sicherung zurueck.
    result = store.suppliers()
    assert [s.id for s in result] == ["a"]


def test_corrupt_suppliers_file_without_backup_quarantines_and_degrades(tmp_path):
    store = Store(tmp_path / "data")
    suppliers_json = tmp_path / "data" / "suppliers.json"
    suppliers_json.write_text("{ kaputt", "utf-8")

    result = store.suppliers()
    assert result == []
    quarantined = list((tmp_path / "data").glob("suppliers.json.corrupt-*"))
    assert len(quarantined) == 1
    assert not suppliers_json.exists()


def test_corrupt_line_in_jsonl_log_is_skipped_not_fatal(tmp_path):
    store = Store(tmp_path / "data")
    store.mark_processed({"sha256": "abc", "rechnungsnummer": "RE-1"})
    processed_log = tmp_path / "data" / "processed.jsonl"
    with processed_log.open("a", encoding="utf-8") as fh:
        fh.write("{ kaputte zeile\n")
    store.mark_processed({"sha256": "def", "rechnungsnummer": "RE-2"})

    rows = store.processed()
    assert [r["sha256"] for r in rows] == ["abc", "def"]


def test_processed_is_cached_and_invalidated_by_mark_processed(tmp_path):
    """process() fragt bei jeder Datei mehrfach store.processed() ab (Dubletten-
    Check, Bestaetigungszaehler) -- ohne Cache liest/parst das bei jedem Scan
    ueber alle offenen Rechnungen das komplette, stetig wachsende Log neu.
    Siehe Konversation vom 2026-09-18 (Effizienz auf lange Sicht)."""
    store = Store(tmp_path / "data")
    store.mark_processed({"sha256": "abc", "rechnungsnummer": "RE-1"})
    assert len(store.processed()) == 1

    # Direkt an der Store-API vorbei geschrieben -- der Cache darf das
    # (richtigerweise) nicht sehen, solange niemand ueber mark_processed()
    # invalidiert hat.
    processed_log = tmp_path / "data" / "processed.jsonl"
    with processed_log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"sha256": "def", "rechnungsnummer": "RE-2"}) + "\n")
    assert len(store.processed()) == 1

    store.mark_processed({"sha256": "ghi", "rechnungsnummer": "RE-3"})
    rows = store.processed()
    assert [r["sha256"] for r in rows] == ["abc", "def", "ghi"]


def test_delete_category_example(tmp_path):
    store = Store(tmp_path / "data")
    store.save_category_example("Reisekosten Bahn", "Reisekosten")
    key = next(iter(store.category_examples()))
    store.delete_category_example(key)
    assert store.category_examples() == {}


def test_delete_correction_falls_back_to_original(tmp_path):
    store = Store(tmp_path / "data")
    store.save_correction("sha123", "rechnungsnummer", "RE-FALSCH")
    assert store.corrections("sha123") == {"rechnungsnummer": "RE-FALSCH"}

    store.delete_correction("sha123", "rechnungsnummer")
    assert store.corrections("sha123") == {}


def test_delete_correction_on_unknown_field_is_a_no_op(tmp_path):
    store = Store(tmp_path / "data")
    store.save_correction("sha123", "netto", "1000.00")
    store.delete_correction("sha123", "nicht_vorhanden")
    assert store.corrections("sha123") == {"netto": "1000.00"}
