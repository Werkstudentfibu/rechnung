"""Minimaler xlsx-WRITER nur fuer Tests (Gegenstueck zu rechnung/xlsx.py, das
nur LIEST). Baut ein valides, minimales xlsx (ZIP + OOXML-XML) direkt aus
Zeilenlisten -- ohne openpyxl, damit synthetische Testdateien mit beliebigen
(auch unordentlichen) Layouts entstehen koennen, ohne eine neue Abhaengigkeit
einzufuehren. Mehrere Blaetter werden unterstuetzt."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
{sheet_overrides}
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""


def _col_letter(idx: int) -> str:
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def write_workbook(sheets: dict[str, list[list[str]]]) -> bytes:
    """``{Blattname: [[Zelle, ...], ...]}`` (alle Zellen als Text) -> xlsx-Bytes."""
    names = list(sheets.keys())
    shared: list[str] = []
    shared_idx: dict[str, int] = {}

    def sidx(text: str) -> int:
        if text not in shared_idx:
            shared_idx[text] = len(shared)
            shared.append(text)
        return shared_idx[text]

    sheet_xml = {}
    for name in names:
        rows_xml = []
        for r, row in enumerate(sheets[name], start=1):
            cells_xml = []
            for c, val in enumerate(row):
                if val == "" or val is None:
                    continue
                ref = f"{_col_letter(c)}{r}"
                i = sidx(str(val))
                cells_xml.append(f'<c r="{ref}" t="s"><v>{i}</v></c>')
            rows_xml.append(f'<row r="{r}">{"".join(cells_xml)}</row>')
        sheet_xml[name] = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'<sheetData>{"".join(rows_xml)}</sheetData></worksheet>'
        )

    sheet_entries = []
    sheet_rels = []
    sheet_overrides = []
    for i, name in enumerate(names, start=1):
        rid = f"rId{i}"
        sheet_entries.append(f'<sheet name="{escape(name)}" sheetId="{i}" r:id="{rid}"/>')
        sheet_rels.append(
            f'<Relationship Id="{rid}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
        )
        sheet_overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    shared_rid = f"rId{len(names) + 1}"
    sheet_rels.append(
        f'<Relationship Id="{shared_rid}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(sheet_entries)}</sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(sheet_rels)}</Relationships>'
    )
    shared_strings_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        f'count="{len(shared)}" uniqueCount="{len(shared)}">'
        + "".join(f"<si><t xml:space=\"preserve\">{escape(s)}</t></si>" for s in shared)
        + "</sst>"
    )
    content_types = _CONTENT_TYPES.format(sheet_overrides="\n".join(sheet_overrides))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", _ROOT_RELS)
        z.writestr("xl/workbook.xml", workbook_xml)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/sharedStrings.xml", shared_strings_xml)
        for i, name in enumerate(names, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", sheet_xml[name])
    return buf.getvalue()
