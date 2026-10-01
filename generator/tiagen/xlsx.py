"""Read and write .xlsx with the standard library only.

The project rule is standard library plus PyYAML, and an engineering PC is exactly
where adding a package is hardest. An .xlsx is a zip of XML parts, and the subset a
machine workbook needs - text, numbers, yes/no, a header row, drop-down lists - is
small enough to handle directly.

Reading is deliberately tolerant, because the files come from Excel, LibreOffice,
Google Sheets and whatever exported the customer's IO list: shared or inline
strings, rich-text runs, sparse rows with missing cells, booleans, cached formula
results, and both the Transitional and Strict OOXML namespaces. Elements are matched
by local name so the namespace variant does not matter.

Writing is deliberately narrow: what this tool needs and nothing else, in the element
order the schema requires - Excel offers to "repair" a file whose worksheet children
are out of order, and that prompt is the first thing an engineer would see.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple
from xml.etree import ElementTree
from xml.sax.saxutils import escape

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")
# XML 1.0 forbids most control characters; a stray one from a pasted cell would
# make the whole workbook unreadable.
_ILLEGAL_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Sheet names Excel accepts: 31 characters, none of []:*?/\
_BAD_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


class XlsxError(Exception):
    """A file that is not a readable workbook, said in terms a user can act on."""


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
Row = Tuple[int, List[str]]          # (1-based row number as Excel shows it, cells)


def read(path: str) -> Dict[str, List[Row]]:
    """Every sheet, as rows of cell text. Empty cells are ''; empty rows are omitted.

    Values come back as text exactly as stored: numbers in Excel's own notation
    ("30", "2.5"), booleans as "TRUE"/"FALSE". Interpreting them is the caller's
    job, because only the caller knows whether "1" in a column means one or yes.
    """
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise XlsxError(
            f"{path} is not an .xlsx workbook. If it is an old .xls file, open it in "
            "Excel and save it as 'Excel Workbook (*.xlsx)'."
        )

    with archive:
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names:
            raise XlsxError(f"{path} has no xl/workbook.xml - it is not an Excel workbook")
        shared = _shared_strings(archive) if "xl/sharedStrings.xml" in names else []
        sheets: Dict[str, List[Row]] = {}
        for sheet_name, part in _sheet_parts(archive):
            if part not in names:
                continue
            sheets[sheet_name] = _sheet_rows(archive.read(part), shared)
        return sheets


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(element, name: str):
    return [c for c in element if _local(c.tag) == name]


def _text_of(element) -> str:
    """All <t> text under an element - handles plain and rich-text (run) strings."""
    return "".join(node.text or "" for node in element.iter() if _local(node.tag) == "t")


def _shared_strings(archive) -> List[str]:
    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    return [_text_of(si) for si in root if _local(si.tag) == "si"]


def _sheet_parts(archive) -> List[Tuple[str, str]]:
    """(sheet name, zip part) in workbook order, resolved through the relationships."""
    workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    rels: Dict[str, str] = {}
    if "xl/_rels/workbook.xml.rels" in archive.namelist():
        for rel in ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels")):
            target = rel.get("Target", "")
            # Targets are relative to xl/, or absolute from the package root.
            part = target.lstrip("/") if target.startswith("/") else "xl/" + target
            rels[rel.get("Id", "")] = part
    result = []
    for sheets in (e for e in workbook if _local(e.tag) == "sheets"):
        for sheet in sheets:
            rid = next((v for k, v in sheet.attrib.items() if _local(k) == "id"), "")
            result.append((sheet.get("name", ""), rels.get(rid, "")))
    return result


def _sheet_rows(xml: bytes, shared: List[str]) -> List[Row]:
    root = ElementTree.fromstring(xml)
    rows: List[Row] = []
    for data in (e for e in root if _local(e.tag) == "sheetData"):
        for row_el in _children(data, "row"):
            row_number = int(row_el.get("r", len(rows) + 1))
            cells: Dict[int, str] = {}
            next_col = 0
            for cell in _children(row_el, "c"):
                ref = cell.get("r")
                col = _column_index(ref) if ref else next_col
                next_col = col + 1
                cells[col] = _cell_value(cell, shared)
            if not any(v.strip() for v in cells.values()):
                continue
            width = max(cells) + 1
            rows.append((row_number, [cells.get(i, "") for i in range(width)]))
    return rows


def _cell_value(cell, shared: List[str]) -> str:
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        inline = _children(cell, "is")
        return _text_of(inline[0]) if inline else ""
    value = next((c.text or "" for c in cell if _local(c.tag) == "v"), "")
    if kind == "s":
        try:
            return shared[int(value)]
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return "TRUE" if value.strip() == "1" else "FALSE"
    if kind == "e":
        return ""                    # #N/A, #REF! - an error is not a value
    return value                     # n, str (cached formula text), or untyped


def _column_index(ref: str) -> int:
    match = _CELL_REF.match(ref.upper())
    letters = match.group(1) if match else re.sub(r"[^A-Z]", "", ref.upper())
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------
Cell = Optional[object]              # str, int, float, bool or None


@dataclass
class Sheet:
    name: str
    rows: List[Sequence[Cell]]
    widths: List[float] = field(default_factory=list)     # per column, in characters
    header: bool = True                                    # style and freeze row 1
    lists: Dict[int, Sequence[str]] = field(default_factory=dict)  # col -> drop-down values
    wrap: bool = False                                     # wrap text in data rows


STYLE_PLAIN, STYLE_HEADER, STYLE_WRAP = 0, 1, 2


def write(path: str, sheets: List[Sheet]) -> None:
    for sheet in sheets:
        if len(sheet.name) > 31 or _BAD_SHEET_CHARS.search(sheet.name):
            raise XlsxError(f"'{sheet.name}' is not a valid Excel sheet name")

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types(len(sheets)))
        zf.writestr("_rels/.rels", (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{PKG_REL_NS}">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>'))
        zf.writestr("xl/workbook.xml", _workbook(sheets))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheets)))
        zf.writestr("xl/styles.xml", _STYLES)
        for index, sheet in enumerate(sheets, start=1):
            zf.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet(sheet))


def _content_types(count: int) -> str:
    sheets = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, count + 1))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/'
        'vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/'
        'vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f'{sheets}</Types>')


def _workbook(sheets: List[Sheet]) -> str:
    entries = "".join(
        f'<sheet name="{escape(s.name, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
        for i, s in enumerate(sheets, start=1))
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">'
        f'<sheets>{entries}</sheets></workbook>')


def _workbook_rels(count: int) -> str:
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/'
        f'2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, count + 1))
    rels += (f'<Relationship Id="rId{count + 1}" Type="http://schemas.openxmlformats.org/'
             'officeDocument/2006/relationships/styles" Target="styles.xml"/>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            f'<Relationships xmlns="{PKG_REL_NS}">{rels}</Relationships>')


def _worksheet(sheet: Sheet) -> str:
    # Child order is fixed by the schema: sheetViews, cols, sheetData, dataValidations.
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n',
             f'<worksheet xmlns="{MAIN_NS}" xmlns:r="{REL_NS}">']
    if sheet.header and sheet.rows:
        parts.append('<sheetViews><sheetView workbookViewId="0">'
                     '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                     '</sheetView></sheetViews>')
    if sheet.widths:
        cols = "".join(
            f'<col min="{i}" max="{i}" width="{w:.1f}" customWidth="1"/>'
            for i, w in enumerate(sheet.widths, start=1))
        parts.append(f"<cols>{cols}</cols>")

    parts.append("<sheetData>")
    for r, row in enumerate(sheet.rows, start=1):
        style = STYLE_HEADER if (sheet.header and r == 1) else (
            STYLE_WRAP if sheet.wrap else STYLE_PLAIN)
        cells = "".join(_cell_xml(f"{column_letter(c)}{r}", v, style)
                        for c, v in enumerate(row) if v is not None and v != "")
        parts.append(f'<row r="{r}">{cells}</row>')
    parts.append("</sheetData>")

    if sheet.lists:
        last = max(len(sheet.rows), 1) + 500         # room to add rows below the data
        validations = []
        for col, values in sorted(sheet.lists.items()):
            formula = '"' + ",".join(values) + '"'
            if len(formula) > 255:
                raise XlsxError(f"drop-down list for {sheet.name} column {col} is too long")
            letter = column_letter(col)
            validations.append(
                f'<dataValidation type="list" allowBlank="1" showErrorMessage="1" '
                f'sqref="{letter}2:{letter}{last}"><formula1>{escape(formula)}</formula1>'
                '</dataValidation>')
        parts.append(f'<dataValidations count="{len(validations)}">'
                     f'{"".join(validations)}</dataValidations>')
    parts.append("</worksheet>")
    return "".join(parts)


def _cell_xml(ref: str, value, style: int) -> str:
    s = f' s="{style}"' if style else ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"{s}><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{s}><v>{value!r}</v></c>'
    text = _ILLEGAL_XML.sub("", str(value))
    return (f'<c r="{ref}" t="inlineStr"{s}><is><t xml:space="preserve">'
            f'{escape(text)}</t></is></c>')


# Minimal stylesheet: default, bold-on-blue header, and wrapped text. The first two
# fills (none, gray125) are mandatory placeholders Excel expects in that position.
_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    f'<styleSheet xmlns="{MAIN_NS}">'
    '<fonts count="2">'
    '<font><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
    '<font><b/><sz val="11"/><name val="Calibri"/><family val="2"/></font>'
    '</fonts>'
    '<fills count="3">'
    '<fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFDDE6EF"/>'
    '<bgColor indexed="64"/></patternFill></fill>'
    '</fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="3">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1">'
    '<alignment vertical="top" wrapText="1"/></xf>'
    '</cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    '</styleSheet>'
)
