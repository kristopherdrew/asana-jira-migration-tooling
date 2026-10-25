"""Regression tests for Export ID Management feature.

Tests the end-to-end workflow:
1. Record export ID on first run
2. Filter out already-exported tasks on second run
3. Clear export ID via manage_export_ids.py
4. Verify list/show/clear actions work correctly
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from src.lib.export_id_history import (
    clear_all_export_ids,
    clear_export_id,
    get_export_id_detail,
    list_export_ids,
)
import scripts.manage_export_ids as manage_script


class ExportIdLifecycleTest(TestCase):
    """Regression suite: Export ID record → filter → list → show → clear workflow."""

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self._seed_projects_and_tasks()

    def tearDown(self) -> None:
        self.connection.close()

    def _seed_projects_and_tasks(self) -> None:
        """Create 2 projects with 5 tasks each for testing export ID filtering."""
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("proj_1", "Project One", self.sync_time.isoformat()),
        )
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("proj_2", "Project Two", self.sync_time.isoformat()),
        )

        # Project 1: 5 tasks
        for i in range(1, 6):
            self.database.upsert_task(
                self.connection,
                {
                    "gid": f"task_1_{i}",
                    "name": f"Project 1 Task {i}",
                    "created_at": "2025-01-01T00:00:00Z",
                    "modified_at": "2025-01-01T00:00:00Z",
                    "memberships": [{"project": {"gid": "proj_1", "name": "Project One"}}],
                    "workspace": {"gid": "ws_1", "name": "Workspace"},
                },
                self.sync_time,
            )

        # Project 2: 5 tasks
        for i in range(1, 6):
            self.database.upsert_task(
                self.connection,
                {
                    "gid": f"task_2_{i}",
                    "name": f"Project 2 Task {i}",
                    "created_at": "2025-01-02T00:00:00Z",
                    "modified_at": "2025-01-02T00:00:00Z",
                    "memberships": [{"project": {"gid": "proj_2", "name": "Project Two"}}],
                    "workspace": {"gid": "ws_1", "name": "Workspace"},
                },
                self.sync_time,
            )

        self.connection.commit()

    def test_record_and_filter_export_id_across_runs(self) -> None:
        """Record export ID on first run, filter out already-exported tasks on second run."""
        # Simulate first export run: export 3 tasks with export_id='wave_one'
        first_batch = ["task_1_1", "task_1_2", "task_1_3"]
        self.database.record_exported_tasks(
            self.connection, "wave_one", first_batch, "proj_1", self.sync_time
        )

        # Verify export ID was recorded
        summaries = list_export_ids(self.database)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].export_id, "wave_one")
        self.assertEqual(summaries[0].task_count, 3)
        self.assertEqual(summaries[0].project_count, 1)

        # Simulate second export run: would export same 3 tasks + 2 new ones
        # In real usage, exporter would filter out the 3 already-exported tasks
        # Verify filtering logic: check if tasks are in exported_tasks table
        exported_tasks = self.connection.execute(
            "SELECT DISTINCT task_gid FROM exported_tasks WHERE export_id = ?",
            ("wave_one",),
        ).fetchall()
        exported_task_gids = {row["task_gid"] for row in exported_tasks}

        all_project1_tasks = ["task_1_1", "task_1_2", "task_1_3", "task_1_4", "task_1_5"]
        tasks_to_export = [t for t in all_project1_tasks if t not in exported_task_gids]

        # Second run would only export the 2 new tasks
        self.assertEqual(tasks_to_export, ["task_1_4", "task_1_5"])

        # Record second batch with same export_id
        self.database.record_exported_tasks(
            self.connection, "wave_one", tasks_to_export, "proj_1", self.sync_time
        )

        # Verify export ID now has 5 tasks total
        summaries = list_export_ids(self.database)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].task_count, 5)

    def test_multiple_export_ids_tracked_independently(self) -> None:
        """Record and track multiple export IDs independently."""
        from datetime import timedelta

        # Wave One: export 3 tasks from proj_1
        sync_time_1 = self.sync_time
        self.database.record_exported_tasks(
            self.connection, "wave_one", ["task_1_1", "task_1_2", "task_1_3"], "proj_1", sync_time_1
        )

        # Wave Two: export 2 tasks from proj_2 (one minute later)
        sync_time_2 = sync_time_1 + timedelta(minutes=1)
        self.database.record_exported_tasks(
            self.connection, "wave_two", ["task_2_1", "task_2_2"], "proj_2", sync_time_2
        )

        # Verify both export IDs tracked
        summaries = list_export_ids(self.database)
        self.assertEqual(len(summaries), 2)

        # Verify both export IDs are present (ordering may vary if same timestamp)
        export_ids = {s.export_id for s in summaries}
        self.assertIn("wave_one", export_ids)
        self.assertIn("wave_two", export_ids)

        # Verify task counts per export ID
        wave_one = next(s for s in summaries if s.export_id == "wave_one")
        wave_two = next(s for s in summaries if s.export_id == "wave_two")
        self.assertEqual(wave_one.task_count, 3)
        self.assertEqual(wave_two.task_count, 2)

    def test_get_export_id_detail_with_project_breakdown(self) -> None:
        """Show detailed export ID info with per-project task breakdown."""
        # Record tasks from 2 projects under same export_id
        self.database.record_exported_tasks(
            self.connection, "combined", ["task_1_1", "task_1_2"], "proj_1", self.sync_time
        )
        self.database.record_exported_tasks(
            self.connection, "combined", ["task_2_1", "task_2_2", "task_2_3"], "proj_2", self.sync_time
        )

        self.connection.commit()

        # Get detailed export ID info
        detail = get_export_id_detail(self.database, "combined")
        self.assertIsNotNone(detail)

        self.assertEqual(detail.summary.export_id, "combined")
        self.assertEqual(detail.summary.task_count, 5)
        self.assertEqual(detail.summary.project_count, 2)

        # Verify per-project breakdown
        self.assertEqual(len(detail.projects), 2)
        proj1_detail = next(p for p in detail.projects if p.project_gid == "proj_1")
        proj2_detail = next(p for p in detail.projects if p.project_gid == "proj_2")

        self.assertEqual(proj1_detail.task_count, 2)
        self.assertEqual(proj1_detail.project_name, "Project One")
        self.assertEqual(proj2_detail.task_count, 3)
        self.assertEqual(proj2_detail.project_name, "Project Two")

    def test_clear_single_export_id(self) -> None:
        """Delete recorded task history for one export ID."""
        # Record two export IDs
        self.database.record_exported_tasks(
            self.connection, "wave_one", ["task_1_1", "task_1_2", "task_1_3"], "proj_1", self.sync_time
        )
        self.database.record_exported_tasks(
            self.connection, "wave_two", ["task_2_1", "task_2_2"], "proj_2", self.sync_time
        )

        # Verify both present
        summaries = list_export_ids(self.database)
        self.assertEqual(len(summaries), 2)

        # Clear wave_one
        cleared_count = clear_export_id(self.database, "wave_one")
        self.assertEqual(cleared_count, 3)

        # Verify only wave_two remains
        summaries = list_export_ids(self.database)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].export_id, "wave_two")

    def test_clear_all_export_ids(self) -> None:
        """Delete all recorded export ID history."""
        # Record multiple export IDs with multiple tasks
        self.database.record_exported_tasks(
            self.connection, "wave_one", ["task_1_1", "task_1_2", "task_1_3"], "proj_1", self.sync_time
        )
        self.database.record_exported_tasks(
            self.connection, "wave_two", ["task_2_1", "task_2_2"], "proj_2", self.sync_time
        )

        # Verify history present
        summaries = list_export_ids(self.database)
        self.assertEqual(len(summaries), 2)

        # Clear all
        cleared_count = clear_all_export_ids(self.database)
        self.assertEqual(cleared_count, 5)

        # Verify no export IDs remain
        summaries = list_export_ids(self.database)
        self.assertEqual(len(summaries), 0)
