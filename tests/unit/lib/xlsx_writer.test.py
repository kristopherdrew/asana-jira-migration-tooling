from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import TestCase

from src.lib.xlsx_writer import write_workbook

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _sheet_names(zf: zipfile.ZipFile):
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    return [s.get("name") for s in wb.iter(f"{NS}sheet")]


def _read_sheet(zf: zipfile.ZipFile, index: int):
    """Return rows of (cell_type, text) for sheet{index}.xml. type: 'inlineStr', 'n', or None."""
    ws = ET.fromstring(zf.read(f"xl/worksheets/sheet{index}.xml"))
    rows = []
    for row in ws.iter(f"{NS}row"):
        cells = []
        for c in row.findall(f"{NS}c"):
            ctype = c.get("t")
            if ctype == "inlineStr":
                node = c.find(f"{NS}is/{NS}t")
                cells.append(("inlineStr", node.text if node is not None else ""))
            else:
                v = c.find(f"{NS}v")
                cells.append(("n" if v is not None else None, v.text if v is not None else None))
        rows.append(cells)
    return rows


def _assert_well_formed(path: Path) -> zipfile.ZipFile:
    zf = zipfile.ZipFile(path)
    for name in zf.namelist():
        if name.endswith((".xml", ".rels")):
            ET.fromstring(zf.read(name))  # raises on malformed XML
    return zf


class XlsxWriterTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tmpdir.name) / "out.xlsx"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def test_multiple_sheets_in_order(self) -> None:
        write_workbook(self.path, [("First", [["a"]]), ("Second", [["b"]])])
        zf = _assert_well_formed(self.path)
        self.assertEqual(_sheet_names(zf), ["First", "Second"])

    def test_numbers_vs_text_typing(self) -> None:
        write_workbook(self.path, [("S", [["header"], [42, 3.5, "1209238528105063", "00123"]])])
        zf = _assert_well_formed(self.path)
        body = _read_sheet(zf, 1)[1]
        self.assertEqual(body[0], ("n", "42"))          # int → numeric cell
        self.assertEqual(body[1], ("n", "3.5"))          # float → numeric cell
        self.assertEqual(body[2], ("inlineStr", "1209238528105063"))  # str stays text
        self.assertEqual(body[3], ("inlineStr", "00123"))

    def test_bold_frozen_header(self) -> None:
        write_workbook(self.path, [("S", [["H1", "H2"], ["v1", "v2"]])])
        zf = _assert_well_formed(self.path)
        sheet_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        self.assertIn('state="frozen"', sheet_xml)
        ws = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        first_row = next(ws.iter(f"{NS}row"))
        for cell in first_row.findall(f"{NS}c"):
            self.assertEqual(cell.get("s"), "1")  # bold style index

    def test_xml_special_characters_round_trip(self) -> None:
        value = 'a & b < c > d " e'
        write_workbook(self.path, [("S", [["h"], [value]])])
        zf = _assert_well_formed(self.path)
        self.assertEqual(_read_sheet(zf, 1)[1][0], ("inlineStr", value))

    def test_illegal_control_characters_stripped(self) -> None:
        write_workbook(self.path, [("S", [["h"], ["a\x00b\x07c"]])])
        zf = _assert_well_formed(self.path)
        self.assertEqual(_read_sheet(zf, 1)[1][0], ("inlineStr", "abc"))

    def test_cell_truncated_to_excel_limit(self) -> None:
        write_workbook(self.path, [("S", [["h"], ["x" * 40000]])])
        zf = _assert_well_formed(self.path)
        _, text = _read_sheet(zf, 1)[1][0]
        self.assertEqual(len(text), 32767)

    def test_sheet_title_sanitized_and_deduped(self) -> None:
        write_workbook(
            self.path,
            [("a/b:c*long" + "x" * 40, [["h"]]), ("dup", [["h"]]), ("dup", [["h"]]), ("///", [["h"]])],
        )
        zf = _assert_well_formed(self.path)
        names = _sheet_names(zf)
        self.assertTrue(all(len(n) <= 31 for n in names))
        self.assertNotIn("/", names[0])
        self.assertNotIn(":", names[0])
        self.assertEqual(names[1], "dup")
        self.assertEqual(names[2], "dup 2")          # duplicate deduped
        self.assertEqual(names[3], "Sheet")          # blank-after-sanitize fallback

    def test_blank_cells(self) -> None:
        write_workbook(self.path, [("S", [["h"], [None, ""]])])
        zf = _assert_well_formed(self.path)
        self.assertEqual(_read_sheet(zf, 1)[1], [(None, None), (None, None)])
