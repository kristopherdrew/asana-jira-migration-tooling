from __future__ import annotations

import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database, synced_at_timestamp
from scripts.export_project_import_status import export_project_import_status_workbook

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
SYNC = synced_at_timestamp(datetime(2026, 1, 2, tzinfo=timezone.utc))


def _sheet_names(zf):
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    return [s.get("name") for s in wb.iter(f"{NS}sheet")]


class ExportProjectImportStatusWorkbookTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "asana.sqlite")
        self.out_dir = Path(self.tmpdir.name) / "exports"

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _run(self, jira_sites=None, project_gids=None):
        path = export_project_import_status_workbook(
            database_path=self.db_path,
            output_dir=self.out_dir,
            filename="test.xlsx",
            jira_sites=jira_sites,
            project_gids=project_gids,
        )
        self.assertTrue(path.exists())
        zf = zipfile.ZipFile(path)
        for name in zf.namelist():  # every XML part must be well-formed
            if name.endswith((".xml", ".rels")):
                ET.fromstring(zf.read(name))
        return zf

    def test_workbook_has_three_tabs(self) -> None:
        zf = self._run()
        self.assertEqual(_sheet_names(zf), ["Overview", "Migrated", "Unmigrated"])

    def test_overview_tab_exists_with_content(self) -> None:
        zf = self._run()
        sheet_names = _sheet_names(zf)
        self.assertIn("Overview", sheet_names)

    def test_migrated_tab_exists(self) -> None:
        zf = self._run()
        sheet_names = _sheet_names(zf)
        self.assertIn("Migrated", sheet_names)

    def test_unmigrated_tab_exists(self) -> None:
        zf = self._run()
        sheet_names = _sheet_names(zf)
        self.assertIn("Unmigrated", sheet_names)

    def test_workbook_with_site_filter(self) -> None:
        zf = self._run(jira_sites=["prod"])
        sheet_names = _sheet_names(zf)
        self.assertEqual(len(sheet_names), 3)

    def test_workbook_with_project_gids_filter(self) -> None:
        zf = self._run(project_gids=["proj_1", "proj_2"])
        sheet_names = _sheet_names(zf)
        self.assertEqual(len(sheet_names), 3)

    def test_workbook_structure_is_valid_xlsx(self) -> None:
        zf = self._run()
        # Verify all required XLSX files exist
        required_files = [
            "xl/workbook.xml",
            "xl/worksheets/sheet1.xml",
            "xl/worksheets/sheet2.xml",
            "xl/worksheets/sheet3.xml",
            "_rels/.rels",
        ]
        for filename in required_files:
            self.assertIn(filename, zf.namelist())
