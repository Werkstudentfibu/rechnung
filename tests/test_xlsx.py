"""rechnung/xlsx.py: reiner stdlib-Reader (kein openpyxl, Entscheidung Aaron
15.10.2026). Getestet gegen selbstgebaute Dateien (_xlsx_helpers.py, volle
Kontrolle ueber Layout) UND gegen eine mit echtem LibreOffice erzeugte Datei
(Cross-Check gegen echtes OOXML, nicht nur gegen das eigene Verstaendnis des
Formats)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from rechnung import xlsx

from _xlsx_helpers import write_workbook


def test_single_sheet_header_and_rows():
    data = write_workbook({"Blatt1": [
        ["Name", "Strasse", "PLZ", "Ort"],
        ["Muster GmbH", "Hauptstr. 1", "12345", "Musterstadt"],
        ["Andere AG", "Nebenweg 2", "54321", "Beispielhausen"],
    ]})
    sheets = xlsx.read_workbook(data)
    assert list(sheets.keys()) == ["Blatt1"]
    header, rows = xlsx.header_and_rows(sheets["Blatt1"])
    assert header == ["Name", "Strasse", "PLZ", "Ort"]
    assert rows == [
        ["Muster GmbH", "Hauptstr. 1", "12345", "Musterstadt"],
        ["Andere AG", "Nebenweg 2", "54321", "Beispielhausen"],
    ]


def test_multiple_sheets_in_order():
    data = write_workbook({
        "Deutschland": [["Name"], ["A"]],
        "Schweiz": [["Name"], ["B"]],
        "Mandanten": [["Name"], ["C"]],
    })
    sheets = xlsx.read_workbook(data)
    assert list(sheets.keys()) == ["Deutschland", "Schweiz", "Mandanten"]


def test_messy_layout_leading_blank_rows_and_ragged_columns():
    """Unordentliches Layout: leere Zeilen vor der Kopfzeile, eine Datenzeile
    mit weniger Spalten als die Kopfzeile, leere Zeilen am Ende."""
    data = write_workbook({"Blatt1": [
        [],
        ["", ""],
        ["Name", "UID", "Notiz"],
        ["Firma X", "CHE-123.456.789", ""],
        ["Firma Y", ""],   # fehlende dritte Spalte
        [],
        [],
    ]})
    sheets = xlsx.read_workbook(data)
    header, rows = xlsx.header_and_rows(sheets["Blatt1"])
    assert header == ["Name", "UID", "Notiz"]
    assert rows[0] == ["Firma X", "CHE-123.456.789", ""]
    assert rows[1][0] == "Firma Y"


def test_blank_separator_row_between_data_is_skipped():
    data = write_workbook({"Blatt1": [
        ["Name"],
        ["A"],
        [],
        ["B"],
    ]})
    header, rows = xlsx.header_and_rows(xlsx.read_workbook(data)["Blatt1"])
    assert header == ["Name"]
    assert [r[0] for r in rows] == ["A", "B"]


def test_empty_workbook_raises_xlsxerror_not_crash():
    with pytest.raises(xlsx.XlsxError):
        xlsx.read_workbook(b"nicht wirklich ein zip")


@pytest.mark.skipif(not shutil.which("soffice"), reason="LibreOffice (soffice) nicht installiert")
def test_real_libreoffice_file_is_readable(tmp_path: Path):
    """Cross-Check: eine von echtem LibreOffice erzeugte xlsx-Datei muss sich
    genauso lesen lassen wie eine selbstgebaute -- der eigene Reader darf sich
    nicht nur auf die eigenen (ggf. zu grosszuegigen) Annahmen verlassen."""
    csv_path = tmp_path / "quelle.csv"
    csv_path.write_text("Name,Land,UID\nMuster AG,CH,CHE-123.456.789\n", encoding="utf-8")
    subprocess.run(
        ["soffice", "--headless", "--convert-to", "xlsx", "--outdir", str(tmp_path), str(csv_path)],
        check=True, timeout=60, capture_output=True,
    )
    xlsx_path = tmp_path / "quelle.xlsx"
    assert xlsx_path.exists()
    sheets = xlsx.read_workbook(xlsx_path.read_bytes())
    assert len(sheets) == 1
    rows = next(iter(sheets.values()))
    assert any("Muster AG" in "".join(r) for r in rows)
