from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from src.lib.database import Database
from src.lib.user_backfill import UserBackfill
from src.tasks.personal_tasks import sync_personal_tasks


class PersonalTasksSyncTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(db_path=str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        self.database.upsert_user(
            self.connection,
            {
                "gid": "user_1",
                "name": "User One",
                "resource_type": "user",
                "email": "user@example.com",
                "is_active": True,
                "workspaces": [],
            },
            self.sync_time,
        )
        self.connection.commit()

        self.user_backfill = UserBackfill(client=object(), database=self.database)
        self.user_backfill.prime(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    @patch("src.tasks.personal_tasks.fetch_task_detail")
    @patch("src.tasks.personal_tasks.fetch_workspace_tasks")
    def test_sync_personal_tasks_persists_pseudo_project(self, mock_fetch_workspace, mock_fetch_detail) -> None:
        personal_summary = {
            "gid": "task_personal",
            "memberships": [],
            "assignee": {"gid": "user_1", "name": "User One"},
            "workspace": {"gid": "ws_1", "name": "Workspace One"},
            "modified_at": "2025-01-02T10:00:00Z",
        }
        project_summary = {
            "gid": "task_project",
            "memberships": [{"project": {"gid": "proj_real"}}],
            "assignee": {"gid": "user_1", "name": "User One"},
            "workspace": {"gid": "ws_1", "name": "Workspace One"},
            "modified_at": "2025-01-02T11:00:00Z",
        }
        mock_fetch_workspace.return_value = iter([personal_summary, project_summary])
        mock_fetch_detail.return_value = {
            "gid": "task_personal",
            "memberships": [],
            "workspace": {"gid": "ws_1", "name": "Workspace One"},
            "assignee": {"gid": "user_1", "name": "User One"},
            "modified_at": "2025-01-02T10:00:00Z",
        }

        result = sync_personal_tasks(
            connection=self.connection,
            database=self.database,
            client=object(),
            user_backfill=self.user_backfill,
            known_custom_fields=set(),
            workspace_gid="ws_1",
            assignee="me",
            page_size=50,
            workers=1,
            sync_time=self.sync_time,
        )
        self.connection.commit()

        self.assertEqual(result.processed, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(result.max_modified_at, "2025-01-02T10:00:00Z")

        stored_task = self.connection.execute(
            "SELECT is_personal FROM tasks WHERE gid = ?",
            ("task_personal",),
        ).fetchone()
        self.assertIsNotNone(stored_task)
        self.assertEqual(stored_task["is_personal"], 1)

        membership = self.connection.execute(
            "SELECT project_gid FROM task_projects WHERE task_gid = ?",
            ("task_personal",),
        ).fetchone()
        self.assertEqual(membership["project_gid"], "personal_user_1")

        project_row = self.connection.execute(
            "SELECT is_personal_project FROM projects WHERE gid = ?",
            ("personal_user_1",),
        ).fetchone()
        self.assertEqual(project_row["is_personal_project"], 1)
