from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.export_project_import_status import export_project_import_status


class ExportProjectImportStatusTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"

    def test_exports_empty_database_gracefully(self) -> None:
        db = Database(str(self.db_path))
        with db.connect() as connection:
            db.initialize(connection)

        output_path = export_project_import_status(
            database_path=str(self.db_path),
            output_dir=self.workspace,
        )
        self.assertTrue(output_path.exists())
        with output_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            self.assertEqual(len(rows), 0, "Empty DB should produce no data rows")

    def test_exports_projects_with_no_imports(self) -> None:
        db = Database(str(self.db_path))
        with db.connect() as connection:
            db.initialize(connection)

        output_path = export_project_import_status(
            database_path=str(self.db_path),
            output_dir=self.workspace,
        )
        with output_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            self.assertEqual(len(rows), 0, "Projects without exports should not appear")

    def test_exports_contains_expected_columns(self) -> None:
        output_path = export_project_import_status(
            database_path=str(self.db_path),
            output_dir=self.workspace,
        )
        with output_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            expected_columns = {
                "asana_project_gid",
                "asana_project_name",
                "team_gid",
                "team_name",
                "jira_environment",
                "jira_project_key",
                "jira_project_name",
                "total_tasks",
                "active_tasks",
                "completed_tasks",
                "incomplete_tasks",
                "imported_tasks",
                "unimported_tasks",
                "import_coverage_percent",
                "archived",
                "synced_at",
            }
            self.assertEqual(set(reader.fieldnames or []), expected_columns)
