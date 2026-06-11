import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database


def utc_now() -> datetime:
    return datetime(2025, 10, 31, tzinfo=timezone.utc)


class DatabaseTaskExtrasTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tmpdir.cleanup()

    def test_replace_task_comments_persists_rows(self) -> None:
        sync_time = utc_now()
        result = self.database.replace_task_comments(
            self.connection,
            "task_1",
            [
                {
                    "gid": "comment_1",
                    "created_at": "2025-10-31T10:00:00.000Z",
                    "created_by": {"gid": "user_a"},
                    "text": "Plain text",
                    "html_text": "<body>Plain text</body>",
                    "is_edited": True,
                }
            ],
            sync_time,
        )
        self.connection.commit()

        row = self.connection.execute(
            "SELECT gid, created_by_gid, is_edited, is_deleted FROM task_comments WHERE gid = ?",
            ("comment_1",),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["created_by_gid"], "user_a")
        self.assertEqual(row["is_edited"], 1)
        self.assertEqual(row["is_deleted"], 0)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["deleted"], 0)

        second_result = self.database.replace_task_comments(
            self.connection,
            "task_1",
            [],
            sync_time,
        )
        self.connection.commit()
        deleted_row = self.connection.execute(
            "SELECT is_deleted FROM task_comments WHERE gid = ?",
            ("comment_1",),
        ).fetchone()
        self.assertEqual(deleted_row["is_deleted"], 1)
        self.assertEqual(second_result["created"], 0)
        self.assertEqual(second_result["updated"], 0)
        self.assertEqual(second_result["deleted"], 1)

    def test_replace_task_attachments_persists_rows(self) -> None:
        sync_time = utc_now()
        result = self.database.replace_task_attachments(
            self.connection,
            "task_2",
            [
                {
                    "gid": "attach_1",
                    "parent": {"gid": "comment_1", "resource_type": "story"},
                    "name": "screenshot.png",
                    "resource_subtype": "external",
                    "host": "asana",
                    "service": None,
                    "size": 12345,
                    "created_at": "2025-10-31T11:00:00.000Z",
                    "created_by": {"gid": "user_b"},
                    "permanent_url": "https://app.asana.com/attachment/attach_1",
                    "view_url": "https://app.asana.com/attachment/attach_1/view",
                }
            ],
            sync_time,
        )
        self.connection.commit()

        row = self.connection.execute(
            "SELECT parent_gid, created_by_gid, size, is_deleted FROM task_attachments WHERE gid = ?",
            ("attach_1",),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["parent_gid"], "comment_1")
        self.assertEqual(row["created_by_gid"], "user_b")
        self.assertEqual(row["size"], 12345)
        self.assertEqual(row["is_deleted"], 0)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["deleted"], 0)

        second_result = self.database.replace_task_attachments(
            self.connection,
            "task_2",
            [],
            sync_time,
        )
        self.connection.commit()
        deleted_row = self.connection.execute(
            "SELECT is_deleted FROM task_attachments WHERE gid = ?",
            ("attach_1",),
        ).fetchone()
        self.assertEqual(deleted_row["is_deleted"], 1)
        self.assertEqual(second_result["created"], 0)
        self.assertEqual(second_result["updated"], 0)
        self.assertEqual(second_result["deleted"], 1)
