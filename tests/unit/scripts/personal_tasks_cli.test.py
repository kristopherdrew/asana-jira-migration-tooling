from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase

from scripts.personal_tasks_cli import AssigneeTarget, resolve_assignees
from src.lib.database import Database


class PersonalTasksCliHelperTest(TestCase):
    def setUp(self) -> None:
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.tmpdir = tmpdir
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(db_path=str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_resolve_assignees_defaults_to_me(self) -> None:
        targets = resolve_assignees(self.connection, assignee=None, all_users=False)
        self.assertEqual(targets, [AssigneeTarget(selector="me", label="me")])

    def test_resolve_assignees_all_users_requires_seed_data(self) -> None:
        with self.assertRaises(SystemExit):
            resolve_assignees(self.connection, assignee=None, all_users=True)

    def test_resolve_assignees_all_users_returns_sorted_users(self) -> None:
        self.database.upsert_user(
            self.connection,
            {"gid": "user_b", "name": "Bravo", "resource_type": "user", "email": "b@example.com", "is_active": True, "workspaces": []},
            synced_at_timestamp(),
        )
        self.database.upsert_user(
            self.connection,
            {"gid": "user_a", "name": "Alpha", "resource_type": "user", "email": "a@example.com", "is_active": True, "workspaces": []},
            synced_at_timestamp(),
        )
        self.connection.commit()

        targets = resolve_assignees(self.connection, assignee=None, all_users=True)
        self.assertEqual(
            targets,
            [
                AssigneeTarget(selector="user_a", label="Alpha"),
                AssigneeTarget(selector="user_b", label="Bravo"),
            ],
        )


def synced_at_timestamp():
    from datetime import datetime, timezone

    return datetime(2025, 1, 1, tzinfo=timezone.utc)
