from __future__ import annotations

from datetime import datetime, timezone
from unittest import TestCase

from src.lib.database import Database


class TaskTagsPersistenceTest(TestCase):
    def setUp(self) -> None:
        self.database = Database(":memory:")
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_replace_task_tags_inserts_and_updates(self) -> None:
        sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.database.replace_task_tags(
            self.connection,
            "task_1",
            [
                {"gid": "tag_1", "name": "Alpha"},
                {"gid": "tag_2", "name": "Beta"},
            ],
            sync_time,
        )

        rows = self.connection.execute(
            "SELECT tag_gid, tag_name FROM task_tags ORDER BY tag_gid"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tag_gid"], "tag_1")
        self.assertEqual(rows[0]["tag_name"], "Alpha")

        # Update tags (remove one, rename the other, add a new one)
        self.database.replace_task_tags(
            self.connection,
            "task_1",
            [
                {"gid": "tag_1", "name": "Alpha Updated"},
                {"gid": "tag_3", "name": "Gamma"},
            ],
            sync_time,
        )
        rows = self.connection.execute(
            "SELECT tag_gid, tag_name FROM task_tags ORDER BY tag_gid"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tag_name"], "Alpha Updated")
        self.assertEqual(rows[1]["tag_gid"], "tag_3")

