from rechnung.identify import Supplier
from rechnung.suggest import similar_suppliers, suggest_category
from rechnung.store import Store


def test_suggest_category_exact_and_fuzzy_and_none():
    examples = {
        "buerobedarf-portokosten": {"text": "Bürobedarf Portokosten", "kategorie": "Büro"},
    }
    exact = suggest_category("Bürobedarf Portokosten", examples)
    assert exact == ("Büro", 0.95)

    fuzzy = suggest_category("Buerobedarf Portokosten Q3", examples)
    assert fuzzy is not None
    assert fuzzy[0] == "Büro"
    assert 0.5 < fuzzy[1] < 0.95

    assert suggest_category("Voellig anderer Text", examples) is None
    assert suggest_category("", examples) is None
    assert suggest_category("irrelevant", {}) is None


def test_similar_suppliers_ranks_by_fingerprint_overlap():
    a = Supplier(id="a", name="A GmbH", fingerprint=["muster", "software", "gmbh"])
    b = Supplier(id="b", name="B GmbH", fingerprint=["muster", "software", "ag"])
    c = Supplier(id="c", name="C GmbH", fingerprint=["voellig", "anders"])
    result = similar_suppliers({"muster", "software", "gmbh"}, [a, b, c])
    ids = [s.id for s, _ in result]
    assert ids[0] == "a"
    assert "c" not in ids


def test_store_category_examples_roundtrip(tmp_path):
    store = Store(tmp_path / "data")
    assert store.category_examples() == {}
    store.save_category_example("Reisekosten Bahn", "Reisekosten")
    ex = store.category_examples()
    assert len(ex) == 1
    key = next(iter(ex))
    assert ex[key]["kategorie"] == "Reisekosten"
    # leere Kategorie wird ignoriert
    store.save_category_example("Sonstiges", "  ")
    assert len(store.category_examples()) == 1
