from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database
from src.lib.export_id_history import (
    clear_all_export_ids,
    clear_export_id,
    get_export_id_detail,
    list_export_ids,
)


class ExportIdHistoryTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(db_path=str(self.db_path))
        self.connection = self.database.connect()
        self.addCleanup(self.connection.close)
        self.database.initialize(self.connection)
        self._seed()

    def _seed(self) -> None:
        synced_at = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
        self.connection.executemany(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            [
                ("proj_1", "One Project", synced_at),
                ("proj_2", "Two Project", synced_at),
            ],
        )
        self.connection.executemany(
            """
            INSERT INTO exported_tasks (export_id, task_gid, project_gid, exported_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("wave_old", "task_1", "proj_1", "2025-01-01T00:00:00+00:00"),
                ("wave_new", "task_2", "proj_1", "2025-01-02T00:00:00+00:00"),
                ("wave_new", "task_3", "proj_2", "2025-01-03T00:00:00+00:00"),
                ("wave_new", "task_4", "proj_2", "2025-01-04T00:00:00+00:00"),
            ],
        )
        self.connection.commit()

    def test_list_export_ids_orders_by_last_used(self) -> None:
        summaries = list_export_ids(self.database)

        self.assertEqual([summary.export_id for summary in summaries], ["wave_new", "wave_old"])
        self.assertEqual(summaries[0].task_count, 3)
        self.assertEqual(summaries[0].project_count, 2)
        self.assertEqual(summaries[0].last_used, "2025-01-04T00:00:00+00:00")

    def test_get_export_id_detail_groups_by_project(self) -> None:
        detail = get_export_id_detail(self.database, "wave_new")

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail.summary.task_count, 3)
        self.assertEqual(
            [(project.project_name, project.project_gid, project.task_count) for project in detail.projects],
            [("Two Project", "proj_2", 2), ("One Project", "proj_1", 1)],
        )

    def test_get_export_id_detail_returns_none_for_unknown_id(self) -> None:
        self.assertIsNone(get_export_id_detail(self.database, "missing"))

    def test_clear_export_id_preserves_other_ids(self) -> None:
        cleared = clear_export_id(self.database, "wave_new")

        self.assertEqual(cleared, 3)
        self.assertEqual([summary.export_id for summary in list_export_ids(self.database)], ["wave_old"])

    def test_clear_all_export_ids_removes_all_rows(self) -> None:
        cleared = clear_all_export_ids(self.database)

        self.assertEqual(cleared, 4)
        self.assertEqual(list_export_ids(self.database), [])

