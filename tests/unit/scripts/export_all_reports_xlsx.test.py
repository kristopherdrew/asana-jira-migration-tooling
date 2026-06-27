from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database, synced_at_timestamp
from scripts.export_all_reports_xlsx import _coerce, export_all_reports_xlsx

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
SYNC = synced_at_timestamp(datetime(2026, 1, 2, tzinfo=timezone.utc))

EXPECTED_TABS = [
    "Overview",
    "Projects",
    "Users",
    "Team Memberships",
    "Teams",
    "Custom Field Usage",
    "Portfolios",
    "Portfolio Items",
]


def _sheet_names(zf):
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    return [s.get("name") for s in wb.iter(f"{NS}sheet")]


def _read_sheet(zf, index):
    ws = ET.fromstring(zf.read(f"xl/worksheets/sheet{index}.xml"))
    rows = []
    for row in ws.iter(f"{NS}row"):
        cells = []
        for c in row.findall(f"{NS}c"):
            if c.get("t") == "inlineStr":
                node = c.find(f"{NS}is/{NS}t")
                cells.append(("inlineStr", node.text if node is not None else ""))
            else:
                v = c.find(f"{NS}v")
                cells.append(("n" if v is not None else None, v.text if v is not None else None))
        rows.append(cells)
    return rows


class CoerceTest(TestCase):
    def test_blank_becomes_none(self) -> None:
        self.assertIsNone(_coerce(""))
        self.assertIsNone(_coerce(None))

    def test_small_integers_and_floats(self) -> None:
        self.assertEqual(_coerce("42"), 42)
        self.assertIsInstance(_coerce("42"), int)
        self.assertEqual(_coerce("-7"), -7)
        self.assertEqual(_coerce("0.42"), 0.42)
        self.assertIsInstance(_coerce("0.42"), float)

    def test_long_gid_and_leading_zero_stay_text(self) -> None:
        self.assertEqual(_coerce("1209238528105063"), "1209238528105063")
        self.assertIsInstance(_coerce("1209238528105063"), str)
        self.assertEqual(_coerce("007"), "007")

    def test_plain_text_passthrough(self) -> None:
        self.assertEqual(_coerce("on_track"), "on_track")
        self.assertEqual(_coerce("2026-01-02"), "2026-01-02")


class ExportAllReportsXlsxTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "asana.sqlite")
        self.out_dir = Path(self.tmpdir.name) / "exports"
        db = Database(db_path=self.db_path)
        conn = db.connect()
        db.initialize(conn)
        db.upsert_user(conn, {"gid": "u_1", "name": "Olive", "email": "olive@x.com"}, SYNC)
        db.upsert_portfolio(
            conn,
            {"gid": "1209238528105063", "name": "Roadmap", "owner": {"gid": "u_1", "name": "Olive"}},
            SYNC,
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _run(self):
        path = export_all_reports_xlsx(
            database=self.db_path, output_dir=self.out_dir, workspace_gid="ws_1", filename="all.xlsx"
        )
        self.assertTrue(path.exists())
        zf = zipfile.ZipFile(path)
        for name in zf.namelist():  # every XML part must be well-formed
            if name.endswith((".xml", ".rels")):
                ET.fromstring(zf.read(name))
        return zf

    def test_workbook_has_all_tabs_in_order(self) -> None:
        zf = self._run()
        self.assertEqual(_sheet_names(zf), EXPECTED_TABS)

    def test_overview_lists_row_counts(self) -> None:
        zf = self._run()
        overview = _read_sheet(zf, 1)
        counts = {}
        for cells in overview:
            if len(cells) >= 2 and cells[0][1] in {"Portfolios", "Users"}:
                counts[cells[0][1]] = cells[1][1]
        self.assertEqual(counts.get("Portfolios"), "1")
        self.assertEqual(counts.get("Users"), "1")

    def test_gid_kept_as_text_and_count_numeric(self) -> None:
        zf = self._run()
        names = _sheet_names(zf)
        pf_index = names.index("Portfolios") + 1
        rows = _read_sheet(zf, pf_index)
        header = [text for _t, text in rows[0]]
        gid_col = header.index("portfolio_gid")
        num_col = header.index("num_projects")
        self.assertEqual(rows[1][gid_col], ("inlineStr", "1209238528105063"))
        self.assertEqual(rows[1][num_col], ("n", "0"))
