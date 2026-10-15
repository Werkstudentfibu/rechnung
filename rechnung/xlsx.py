"""Minimaler xlsx-Reader, reine Standardbibliothek (Entscheidung Aaron
15.10.2026): ``openpyxl`` ist weder im Projekt noch im Windows-Laptop-Paket
vorhanden (``.venv/bin/pip list`` zeigt nichts, ``pyproject.toml`` listet es
nicht) -- eine neue Pflicht-Abhaengigkeit nur fuer den Excel-Stammdaten-Import
waere auf dem schwachen Firmen-Laptop ein zusaetzlicher Installationsschritt.
xlsx ist ein ZIP mit XML-Teilen (OOXML SpreadsheetML); Lesen von
``sharedStrings.xml``/``sheetN.xml`` ist mit ``zipfile`` + ``xml.etree`` ohne
fremdes Paket machbar. Geschrieben wird hier nichts -- nur Lesen wird
gebraucht (Stammdaten-Import).

Bewusst NICHT unterstuetzt (fuer eine reine Stammdaten-Liste nicht noetig):
Formeln (nur der zuletzt berechnete Wert steht ohnehin in ``<v>``), Datums-
Formatierung (Stammdaten haben keine relevanten Datumsspalten -- ein
Datums-Serial steht als Rohzahl im Ergebnis), zusammengefasste Zellen
(``mergeCells``, eine Kreditoren-/Mandantenliste hat davon typischerweise
keine)."""

from __future__ import annotations

import io
import re
import zipfile
from xml.etree import ElementTree as ET

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

_COL_RE = re.compile(r"[A-Z]+")


class XlsxError(ValueError):
    """Datei ist kein lesbares xlsx (kaputtes ZIP, fehlende Kernteile)."""


def _col_to_index(ref: str) -> int:
    m = _COL_RE.match(ref)
    if not m:
        return 0
    idx = 0
    for ch in m.group(0):
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    try:
        data = z.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    root = ET.fromstring(data)
    out = []
    for si in root.findall(f"{_NS}si"):
        out.append("".join(t.text or "" for t in si.findall(f".//{_NS}t")))
    return out


def _sheet_list(z: zipfile.ZipFile) -> list[tuple[str, str]]:
    """-> [(Blattname, Pfad der sheetN.xml)], in der Blattreihenfolge des
    Arbeitsmappe-Katalogs (``xl/workbook.xml``), aufgeloest ueber die
    Beziehungen (``xl/_rels/workbook.xml.rels``) -- der Dateiname
    (``sheet1.xml`` ...) muss nicht der Blattreihenfolge entsprechen."""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    try:
        rels_root = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rels = {r.get("Id"): r.get("Target") for r in rels_root.findall(f"{_REL_NS}Relationship")}
    except KeyError:
        rels = {}
    out = []
    for sheet in wb.findall(f"{_NS}sheets/{_NS}sheet"):
        name = sheet.get("name") or ""
        rid = sheet.get(f"{_R_NS}id")
        target = rels.get(rid, "")
        if not target:
            continue
        path = target if target.startswith("xl/") else f"xl/{target.lstrip('/')}"
        out.append((name, path))
    return out


def _cell_value(c: ET.Element, shared: list[str]) -> str:
    t = c.get("t")
    v_el = c.find(f"{_NS}v")
    if t == "s":
        if v_el is None or not v_el.text:
            return ""
        idx = int(v_el.text)
        return shared[idx] if 0 <= idx < len(shared) else ""
    if t == "inlineStr":
        is_el = c.find(f"{_NS}is")
        if is_el is None:
            return ""
        return "".join(x.text or "" for x in is_el.findall(f".//{_NS}t"))
    if t == "b":
        return "1" if (v_el is not None and v_el.text == "1") else "0"
    # "str" (Formel-Textergebnis) oder Zahl/Datum ohne ``t`` -- Rohwert reicht,
    # Formeln/Datumsformatierung werden bewusst nicht aufgeloest (s.o.).
    return v_el.text if v_el is not None and v_el.text is not None else ""


def read_workbook(data: bytes) -> dict[str, list[list[str]]]:
    """xlsx-Bytes -> ``{Blattname: [[Zelle, Zelle, ...], ...]}``. Jede Zeile
    hat so viele Spalten wie die breiteste Zeile des Blatts (fehlende Zellen
    = ``""``); komplett leere Zeilen am Ende eines Blatts werden weggelassen
    (Excel speichert nach Loeschungen oft leere Zeilen mit)."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise XlsxError(f"keine lesbare xlsx-Datei: {e}") from e
    with z:
        try:
            shared = _shared_strings(z)
            sheets = _sheet_list(z)
        except (KeyError, ET.ParseError) as e:
            raise XlsxError(f"xlsx-Struktur nicht lesbar: {e}") from e
        if not sheets:
            raise XlsxError("keine Arbeitsblaetter gefunden")
        out: dict[str, list[list[str]]] = {}
        for name, path in sheets:
            if path not in z.namelist():
                continue
            try:
                root = ET.fromstring(z.read(path))
            except ET.ParseError as e:
                raise XlsxError(f"Blatt '{name}' nicht lesbar: {e}") from e
            raw_rows: list[dict[int, str]] = []
            max_col = 0
            for row_el in root.findall(f"{_NS}sheetData/{_NS}row"):
                cells: dict[int, str] = {}
                next_idx = 0
                for c in row_el.findall(f"{_NS}c"):
                    ref = c.get("r") or ""
                    idx = _col_to_index(ref) if ref else next_idx
                    cells[idx] = _cell_value(c, shared)
                    next_idx = idx + 1
                if cells:
                    max_col = max(max_col, max(cells) + 1)
                raw_rows.append(cells)
            rows = [[cells.get(i, "") for i in range(max_col)] for cells in raw_rows]
            while rows and not any(v.strip() for v in rows[-1]):
                rows.pop()
            out[name] = rows
    return out


def header_and_rows(rows: list[list[str]]) -> tuple[list[str], list[list[str]]]:
    """Erste nicht-leere Zeile = Kopfzeile, Rest = Datenzeilen (komplett
    leere Datenzeilen dazwischen werden uebersprungen -- eine Trennzeile
    zwischen Abschnitten derselben Tabelle ist in Stammdaten-Listen ueblich)."""
    idx = 0
    while idx < len(rows) and not any(v.strip() for v in rows[idx]):
        idx += 1
    if idx >= len(rows):
        return [], []
    header = [h.strip() for h in rows[idx]]
    data = [r for r in rows[idx + 1:] if any(v.strip() for v in r)]
    return header, data
