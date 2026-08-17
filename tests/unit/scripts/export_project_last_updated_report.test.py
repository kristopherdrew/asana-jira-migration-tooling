from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.export_project_last_updated_report import export_project_last_updated_report


class ExportProjectLastUpdatedReportTest(TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.workspace = Path(self.tmp_dir.name)
        self.database_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.database_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def _seed(self) -> None:
        sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        # Project on team_a with two tasks (task_2 modified most recently).
        self.database.upsert_project(
            self.connection,
            {"gid": "proj_1", "name": "Website Redesign", "team": {"gid": "team_a"}},
            sync_time,
        )
        # Project on team_b with a single task.
        self.database.upsert_project(
            self.connection,
            {"gid": "proj_2", "name": "Mobile App", "team": {"gid": "team_b"}},
            sync_time,
        )
        # Empty project (no tasks) on team_a.
        self.database.upsert_project(
            self.connection,
            {"gid": "proj_3", "name": "Archived Ideas", "team": {"gid": "team_a"}},
            sync_time,
        )

        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_1",
                "name": "Design spec",
                "modified_at": "2026-01-02T09:00:00.000Z",
                "memberships": [{"project": {"gid": "proj_1"}}],
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_2",
                "name": "Fix login bug",
                "modified_at": "2026-07-10T15:30:00.000Z",
                "memberships": [{"project": {"gid": "proj_1"}}],
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_3",
                "name": "Ship release",
                "modified_at": "2026-05-02T12:00:00.000Z",
                "memberships": [{"project": {"gid": "proj_2"}}],
            },
            sync_time,
        )
        self.connection.commit()

    def _read(self, csv_path: Path) -> dict[str, dict[str, str]]:
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            return {row["project_gid"]: row for row in csv.DictReader(handle)}

    def test_report_rolls_up_latest_task_update_per_project(self) -> None:
        self._seed()
        output_dir = self.workspace / "exports"

        csv_path = export_project_last_updated_report(
            str(self.database_path),
            output_dir,
            filename="report.csv",
        )

        self.assertTrue(csv_path.exists())
        rows = self._read(csv_path)
        self.assertEqual(len(rows), 3)

        proj_1 = rows["proj_1"]
        self.assertEqual(proj_1["project_name"], "Website Redesign")
        self.assertEqual(proj_1["task_count"], "2")
        self.assertEqual(proj_1["latest_task_update"], "2026-07-10")
        self.assertEqual(proj_1["latest_updated_task_gid"], "task_2")
        self.assertEqual(proj_1["latest_updated_task_name"], "Fix login bug")

        proj_2 = rows["proj_2"]
        self.assertEqual(proj_2["task_count"], "1")
        self.assertEqual(proj_2["latest_task_update"], "2026-05-02")
        self.assertEqual(proj_2["latest_updated_task_gid"], "task_3")

    def test_report_includes_projects_without_tasks(self) -> None:
        self._seed()
        output_dir = self.workspace / "exports"

        csv_path = export_project_last_updated_report(
            str(self.database_path),
            output_dir,
            filename="report.csv",
        )
        rows = self._read(csv_path)

        empty = rows["proj_3"]
        self.assertEqual(empty["task_count"], "0")
        self.assertEqual(empty["latest_task_update"], "")
        self.assertEqual(empty["latest_updated_task_gid"], "")
        self.assertEqual(empty["latest_updated_task_name"], "")

    def test_team_gid_scopes_to_a_single_team(self) -> None:
        self._seed()
        output_dir = self.workspace / "exports"

        csv_path = export_project_last_updated_report(
            str(self.database_path),
            output_dir,
            filename="report.csv",
            team_gid="team_a",
        )
        rows = self._read(csv_path)

        self.assertEqual(set(rows), {"proj_1", "proj_3"})
        self.assertNotIn("proj_2", rows)

    def test_project_gids_scope_to_the_explicit_list(self) -> None:
        self._seed()
        csv_path = export_project_last_updated_report(
            str(self.database_path), self.workspace / "exports", filename="selected.csv",
            project_gids=["proj_2"],
        )
        self.assertEqual(set(self._read(csv_path)), {"proj_2"})
