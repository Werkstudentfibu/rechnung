"""Prioritaet in 'Menschliche Bearbeitung' (Wunsch Aaron 12.11.2026)."""

from rechnung import human


def _rec(datei, *gruende, art="claude", lieferant=None):
    return {"datei": datei, "lieferant_name": lieferant,
            "gruende": [{"art": a, "grund": g} for a, g in
                        ((art, g) if isinstance(g, str) else g for g in gruende)]}


def test_stufen():
    assert human.prioritaet(_rec("a.pdf", "Stichprobe: Claudes Pruefung bitte kontrollieren"))["stufe"] == 1
    assert human.prioritaet(_rec("m.eml", ("mail", "nur Link im Mailtext")))["stufe"] == 4
    assert human.prioritaet(_rec("i.pdf", ("iban", "IBAN weicht von Stammdaten ab (Betrugsschutz)")))["stufe"] == 2
    assert human.prioritaet(_rec("p.pdf", ("pflichtfeld", "Pflichtfeld fehlt: netto")))["stufe"] == 3
    nur_mandant = _rec("x.pdf", ("zurueckgestellt", "keine Rechnungsadresse aus der Bibliothek passt"),
                       "nach Claudes Pruefung noch offen: keine Rechnungsadresse aus der Bibliothek passt",
                       ("zurueckgestellt", "Lieferant: Rechnungsadresse nicht erkannt -- Lieferant wird nur je Adresse gesucht"))
    assert human.prioritaet(nur_mandant)["stufe"] == 5
    gemischt = _rec("y.pdf", "nach Claudes Pruefung noch offen: keine Rechnungsadresse aus der Bibliothek passt; "
                             "Pruefung rot: positionen-rechnung (Position 1)")
    p = human.prioritaet(gemischt)
    assert p["stufe"] == 3 and "Mandanten" in p["aufgabe"]


def test_sortierung_nach_stufe_dann_lieferant():
    eintraege = [
        _rec("z.pdf", ("zurueckgestellt", "keine Rechnungsadresse aus der Bibliothek passt"), lieferant="B"),
        _rec("b.pdf", ("pflichtfeld", "Pflichtfeld fehlt: netto"), lieferant="Zeta"),
        _rec("a.pdf", ("pflichtfeld", "Pflichtfeld fehlt: netto"), lieferant="Alpha"),
        _rec("s.pdf", "Stichprobe: Claudes Pruefung bitte kontrollieren", lieferant="Q"),
    ]
    reihe = [r["datei"] for r in human.nach_prioritaet(eintraege)]
    assert reihe == ["s.pdf", "a.pdf", "b.pdf", "z.pdf"]


def test_endpunkt_liefert_prioritaet_und_uebersicht(tmp_path):
    from email.message import EmailMessage

    from fastapi.testclient import TestClient

    from rechnung.store import Store
    from rechnung.web import server as server_mod

    store = Store(tmp_path / "data")
    samples = tmp_path / "s"
    samples.mkdir()
    m = EmailMessage(); m["Subject"] = "Rechnung"; m.set_content("https://x.example")
    human.ablegen_datei(store, "mail_1.eml", bytes(m), [{"art": "mail", "grund": "nur Link im Mailtext", "felder": []}])
    d = TestClient(server_mod.create_app(store, samples)).get("/api/menschlich").json()
    assert d["offen"][0]["prioritaet"]["stufe"] == 4
    assert d["uebersicht"] == [{"stufe": 4, "kategorie": "Mail", "aufgabe": human.PRIORITAETEN[4][1], "anzahl": 1}]
