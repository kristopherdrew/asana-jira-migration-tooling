"""Minimal, dependency-free `.xlsx` (OOXML) writer for plain tabular data.

This exists so the toolchain stays standard-library-only: an `.xlsx` file is just
a ZIP of XML parts, which ``zipfile`` + string building can produce without any
third-party package. It deliberately supports only what the reports need — multiple
sheets of text/number cells, a bold + frozen header row, and auto-sized columns.
No formulas, styles beyond bold, merged cells, or shared-string table.

Robustness guards (the realistic sources of "Excel won't open the file"):
  * XML-escape ``& < >`` in text and ``"`` in attributes.
  * Strip XML-1.0-illegal control characters that Excel rejects.
  * Truncate any cell to Excel's 32,767-character limit.
  * Sanitize sheet titles (<=31 chars, no ``[]:*?/\\``, non-blank, unique).
  * Always UTF-8.

Public API: :func:`write_workbook`.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

CellValue = Union[str, int, float, None]
Sheet = Tuple[str, Sequence[Sequence[CellValue]]]

MAX_CELL_CHARS = 32_767
MAX_SHEET_TITLE = 31
MAX_COLUMN_WIDTH = 60
_COLUMN_WIDTH_SAMPLE = 200

# XML 1.0 forbids these control chars; Excel refuses files that contain them.
_ILLEGAL_XML_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean(text: str) -> str:
    text = _ILLEGAL_XML_CHARS.sub("", text)
    if len(text) > MAX_CELL_CHARS:
        text = text[:MAX_CELL_CHARS]
    return text


def _escape_text(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(text: str) -> str:
    return _escape_text(text).replace('"', "&quot;")


def _col_letter(index: int) -> str:
    """1-based column index → spreadsheet column letters (1→A, 27→AA)."""
    letters = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def _safe_title(title: str, used: set) -> str:
    cleaned = re.sub(r"[\[\]:*?/\\]", " ", str(title)).strip()
    cleaned = cleaned[:MAX_SHEET_TITLE] or "Sheet"
    candidate = cleaned
    suffix = 2
    while candidate.casefold() in used:
        trimmed = cleaned[: MAX_SHEET_TITLE - len(f" {suffix}")]
        candidate = f"{trimmed} {suffix}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def _is_number(value: CellValue) -> bool:
    # bool is a subclass of int but should render as text, not 0/1.
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _cell_xml(ref: str, value: CellValue, bold: bool) -> str:
    style = ' s="1"' if bold else ""
    if value is None or value == "":
        return f'<c r="{ref}"{style}/>'
    if _is_number(value):
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    text = _escape_text(_clean(str(value)))
    return f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _column_widths(rows: Sequence[Sequence[CellValue]]) -> List[int]:
    widths: List[int] = []
    for record in list(rows)[:_COLUMN_WIDTH_SAMPLE]:
        for idx, value in enumerate(record):
            length = len("" if value is None else str(value))
            if idx < len(widths):
                widths[idx] = max(widths[idx], length)
            else:
                widths.append(length)
    return [min(width + 2, MAX_COLUMN_WIDTH) for width in widths]


def _sheet_xml(rows: Sequence[Sequence[CellValue]], freeze_header: bool, bold_header: bool) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
    ]
    if freeze_header and rows:
        parts.append(
            '<sheetViews><sheetView workbookViewId="0">'
            '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            "</sheetView></sheetViews>"
        )
    widths = _column_widths(rows)
    if widths:
        cols = "".join(
            f'<col min="{i}" max="{i}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths, start=1)
        )
        parts.append(f"<cols>{cols}</cols>")
    parts.append("<sheetData>")
    for row_index, record in enumerate(rows, start=1):
        bold = bold_header and row_index == 1
        cells = "".join(
            _cell_xml(f"{_col_letter(col_index)}{row_index}", value, bold)
            for col_index, value in enumerate(record, start=1)
        )
        parts.append(f'<row r="{row_index}">{cells}</row>')
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


_CONTENT_TYPES_HEAD = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
)

_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    "</Relationships>"
)

_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<fonts count="2"><font/><font><b/></font></fonts>'
    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
    '<fill><patternFill patternType="gray125"/></fill></fills>'
    '<borders count="1"><border/></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="2">'
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
    "</cellXfs>"
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
    "</styleSheet>"
)


def write_workbook(
    output_path: Union[str, Path],
    sheets: Sequence[Sheet],
    freeze_header: bool = True,
    bold_header: bool = True,
) -> Path:
    """Write ``sheets`` to an .xlsx file at ``output_path``.

    ``sheets`` is a sequence of ``(title, rows)`` where ``rows`` is a sequence of
    row sequences; the first row is treated as the header. Cell values may be
    ``str``, ``int``, ``float`` or ``None`` (blank). Returns the output path.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    used_titles: set = set()
    resolved = [(_safe_title(title, used_titles), rows) for title, rows in sheets]

    content_types = [_CONTENT_TYPES_HEAD]
    workbook_sheets = []
    workbook_rels = []
    for index, (title, _rows) in enumerate(resolved, start=1):
        content_types.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
        workbook_sheets.append(
            f'<sheet name="{_escape_attr(title)}" sheetId="{index}" r:id="rId{index}"/>'
        )
        workbook_rels.append(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
    content_types.append("</Types>")

    styles_rid = f"rId{len(resolved) + 1}"
    workbook_rels.append(
        f'<Relationship Id="{styles_rid}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
    )

    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets>{"".join(workbook_sheets)}</sheets></workbook>'
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'{"".join(workbook_rels)}</Relationships>'
    )

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "".join(content_types))
        archive.writestr("_rels/.rels", _ROOT_RELS)
        archive.writestr("xl/workbook.xml", workbook_xml)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        archive.writestr("xl/styles.xml", _STYLES)
        for index, (_title, rows) in enumerate(resolved, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _sheet_xml(rows, freeze_header, bold_header),
            )

    return output_path
