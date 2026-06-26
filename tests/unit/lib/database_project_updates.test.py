import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database


def utc_now() -> datetime:
    return datetime(2025, 10, 31, tzinfo=timezone.utc)


class DatabaseProjectUpdatesTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tmpdir.cleanup()

    def _row(self, gid: str):
        return self.connection.execute(
            "SELECT * FROM project_updates WHERE gid = ?",
            (gid,),
        ).fetchone()

    def test_replace_project_updates_persists_rows(self) -> None:
        sync_time = utc_now()
        result = self.database.replace_project_updates(
            self.connection,
            "project_1",
            [
                {
                    "gid": "su_1",
                    "kind": "status_update",
                    "title": "Status update - May 16",
                    "status_type": "on_track",
                    "text": "All good",
                    "html_text": "<body>All good</body>",
                    "created_at": "2025-05-16T23:56:22.076Z",
                    "created_by_gid": "user_a",
                    "modified_at": "2025-05-16T23:56:22.667Z",
                    "resource_subtype": "project_status_update",
                    "is_edited": None,
                },
                {
                    "gid": "comment_1",
                    "kind": "comment",
                    "title": None,
                    "status_type": None,
                    "text": "a real comment",
                    "html_text": None,
                    "created_at": "2025-01-01T00:00:00.000Z",
                    "created_by_gid": "user_b",
                    "modified_at": None,
                    "resource_subtype": "comment_added",
                    "is_edited": True,
                },
            ],
            sync_time,
        )
        self.connection.commit()

        su_row = self._row("su_1")
        self.assertEqual(su_row["kind"], "status_update")
        self.assertEqual(su_row["status_type"], "on_track")
        self.assertEqual(su_row["title"], "Status update - May 16")
        self.assertEqual(su_row["created_by_gid"], "user_a")
        self.assertEqual(su_row["is_deleted"], 0)

        comment_row = self._row("comment_1")
        self.assertEqual(comment_row["kind"], "comment")
        self.assertEqual(comment_row["is_edited"], 1)

        self.assertEqual(result["created"], 2)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["deleted"], 0)

    def test_replace_project_updates_soft_deletes_missing(self) -> None:
        sync_time = utc_now()
        self.database.replace_project_updates(
            self.connection,
            "project_1",
            [{"gid": "su_1", "kind": "status_update", "created_at": "2025-01-01T00:00:00Z"}],
            sync_time,
        )
        self.connection.commit()

        # Re-run with the same row plus a re-confirm => updated, not duplicated.
        second = self.database.replace_project_updates(
            self.connection,
            "project_1",
            [{"gid": "su_1", "kind": "status_update", "created_at": "2025-01-01T00:00:00Z"}],
            sync_time,
        )
        self.connection.commit()
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["updated"], 1)
        self.assertEqual(self._row("su_1")["is_deleted"], 0)

        # Now sync an empty set => the previously stored row is soft-deleted.
        third = self.database.replace_project_updates(
            self.connection,
            "project_1",
            [],
            sync_time,
        )
        self.connection.commit()
        self.assertEqual(third["deleted"], 1)
        self.assertEqual(self._row("su_1")["is_deleted"], 1)

    def test_replace_project_updates_handles_missing_project_gid(self) -> None:
        result = self.database.replace_project_updates(self.connection, None, [], utc_now())
        self.assertEqual(result, {"analyzed": 0, "created": 0, "updated": 0, "deleted": 0})
