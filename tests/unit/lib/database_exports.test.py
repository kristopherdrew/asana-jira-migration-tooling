import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database


class DatabaseExportHistoryTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(db_path=str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.export_time = datetime(2025, 1, 2, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.connection.close()
        self.tmpdir.cleanup()

    def test_record_and_fetch_exported_tasks(self) -> None:
        created = self.database.record_exported_tasks(
            self.connection,
            "batch_one",
            ["task_a", "task_b"],
            "project_1",
            self.export_time,
        )
        self.assertEqual(created, 2)

        second_created = self.database.record_exported_tasks(
            self.connection,
            "batch_one",
            ["task_a", "task_c"],
            "project_1",
            self.export_time,
        )
        self.assertEqual(second_created, 1)

        recorded = self.database.get_exported_task_gids(self.connection, "batch_one")
        self.assertEqual(recorded, {"task_a", "task_b", "task_c"})

        other_batch = self.database.record_exported_tasks(
            self.connection,
            "batch_two",
            ["task_a"],
            "project_2",
            self.export_time,
        )
        self.assertEqual(other_batch, 1)
        self.assertEqual(self.database.get_exported_task_gids(self.connection, "batch_two"), {"task_a"})

    def test_clear_export_history_removes_all_rows(self) -> None:
        self.database.record_exported_tasks(
            self.connection,
            "batch_one",
            ["task_a", "task_b"],
            "project_1",
            self.export_time,
        )
        self.database.record_exported_tasks(
            self.connection,
            "batch_two",
            ["task_c"],
            "project_2",
            self.export_time,
        )

        deleted = self.database.clear_export_history(self.connection)
        self.assertEqual(deleted, 3)
        self.assertEqual(self.database.get_exported_task_gids(self.connection, "batch_one"), set())
        self.assertEqual(self.database.get_exported_task_gids(self.connection, "batch_two"), set())
