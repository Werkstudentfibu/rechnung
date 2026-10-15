Alles fertig, auf dem Branch `emmy/lieferant-je-adresse` committet, nicht gepusht.

## Zusammenfassung

**Geänderte Dateien** (13, davon 1 neu):
- `rechnung/identify.py` — `Supplier.addresses`/`external_id` (+ to_dict/from_dict), neue Hilfsfunktion `kandidaten()` (zentrale Filterlogik je Rechnungsadresse), `identify()`/`identify_with_reason()` um `address_id`/`has_addresses`/`address_name` erweitert, einheitliche Rückstellgrund-Formulierung über `_address_scope_reason()`.
- `rechnung/store.py` — `supplier_index(address_id=None)`, jetzt je Kandidatenmenge gecacht; Cache wird auch bei `save_address`/`delete_address` invalidiert.
- `rechnung/importers.py` — `BusinessUnitId` erzeugt interne Id `<Id>@<BU>`, setzt `addresses`/`external_id`; optionales `missing_business_units`-Tracking.
- `rechnung/cli.py` — `import-vendors` gibt jetzt einen Hinweis aus, wenn Lieferanten auf noch nicht importierte Rechnungsadressen verweisen.
- `rechnung/pipeline.py`, `rechnung/queueing.py`, `rechnung/vorlernen.py` — Rechnungsadresse wird zuerst bestimmt, danach nur unter deren Lieferanten gesucht (PDF- **und** E-Rechnungs-Pfad); `Result.supplier_external_id` neu.
- `rechnung/web/server.py` — `/api/invoice` akzeptiert `address`-Parameter (menschliche Übersteuerung); alle `identify(`-Aufrufe adressbewusst; `SupplierPost`/`save_supplier_ep` um `addresses`/`external_id` erweitert.
- `rechnung/web/static/index.html` — Auswahl der Rechnungsadresse löst Neubewertung aus (`adresseUebersteuern()`); Lieferanten-Tab zeigt/editiert Rechnungsadressen (Checkboxen) und Lieferantennummer.
- `rechnung/jobrouter.py` — `payload_from_result` gibt `external_id` als `lieferantennummer` mit, falls vorhanden.
- Tests: neue Datei `tests/test_lieferant_je_adresse.py` (Kandidatenfilter, Zürich/Hamburg-Isolation, gleiche USt-IdNr nicht mehrdeutig, Adresse nicht erkannt → nur global, Index-Scoping, alte JSON lädt, UI-Übersteuerung, Export), Ergänzungen in `test_importers.py` und `test_jobrouter_setup.py`.

**Testergebnis:** beide Läufe grün —
- Standard: `559 passed`
- Mit Laptop-Umgebung (`RECHNUNG_LLM_ENABLED=1 RECHNUNG_OCR_ENABLED=0 RECHNUNG_IMAP_USER=x`): `559 passed`

`.ps1`/`.cmd`-Dateien unverändert (CRLF geprüft, `test_windows.py` grün).

**Commit:** `3a1245f` auf Branch `emmy/lieferant-je-adresse`.

**Offene Punkte/Annahmen:**
- Das Frontend (`#variant`-Übersteuerung, Lieferanten-Checkboxen) habe ich nur per FastAPI-`TestClient` (reale Server-Logik) und durch Durchlesen des erzeugten JS geprüft — **keinen echten Browser** zur Verfügung gehabt, um den Klick-Flow visuell zu bestätigen. Das solltest du einmal im Training-UI gegenprüfen, bevor es auf den Firmenlaptop kommt.
- Die Formulierung der Rückstellgründe ("… unter Rechnungsadresse X nicht gefunden" bzw. "nicht erkannt – nur je Adresse gesucht") ist meine Textwahl innerhalb der von dir vorgegebenen Beispiele, nicht wortwörtlich zitiert.
- Bei der "Ähnliche bekannte Lieferanten"-Vorschlagsbox (Anlern-Starthilfe) habe ich die Adress-Einschränkung bewusst **nicht** ergänzt — das ist nur ein Vorschlag, keine Identifikationsentscheidung, und stand nicht explizit im Auftrag.
