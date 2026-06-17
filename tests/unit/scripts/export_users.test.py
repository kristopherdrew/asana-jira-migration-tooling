from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.export_users import export_users


class ExportUsersTest(TestCase):
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

    def test_export_users_includes_workspace_memberships(self) -> None:
        sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.database.upsert_user(
            self.connection,
            {
                "gid": "user_1",
                "name": "Alice Example",
                "resource_type": "user",
                "email": "alice@example.com",
                "is_active": True,
                "workspaces": [
                    {"gid": "wrk_1", "name": "Workspace One"},
                    {"gid": "wrk_2", "name": "Workspace Two"},
                ],
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "user_2",
                "name": "Bob Example",
                "resource_type": "user",
                "email": "bob@example.com",
                "is_active": False,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.replace_user_atlassian_accounts(
            self.connection,
            [("user_1", "atl_acct_1", "alice@atlassian.com", "active")],
            sync_time,
        )
        self.connection.commit()

        output_dir = self.workspace / "exports"
        csv_path = export_users(
            str(self.database_path),
            output_dir,
            filename="users.csv",
            workspace_gid="wrk_1",
        )

        self.assertTrue(csv_path.exists())
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            reader = list(csv.DictReader(handle))

        self.assertEqual(len(reader), 2)

        user_one = next(row for row in reader if row["gid"] == "user_1")
        self.assertEqual(user_one["name"], "Alice Example")
        self.assertEqual(user_one["email"], "alice@example.com")
        self.assertEqual(user_one["is_active"], "true")
        self.assertEqual(user_one["atlassian_id"], "atl_acct_1")
        self.assertEqual(user_one["atlassian_status"], "active")
        self.assertEqual(user_one["user_url"], "https://app.asana.com/1/wrk_1/profile/user_1")
        self.assertEqual(user_one["synced_at"], "2025-01-01")
        self.assertEqual(
            user_one["workspaces"],
            "Workspace One; Workspace Two",
        )

        user_two = next(row for row in reader if row["gid"] == "user_2")
        self.assertEqual(user_two["is_active"], "false")
        self.assertEqual(user_two["atlassian_id"], "")
        self.assertEqual(user_two["atlassian_status"], "")
        self.assertEqual(user_two["user_url"], "https://app.asana.com/1/wrk_1/profile/user_2")
        self.assertEqual(user_two["workspaces"], "")
