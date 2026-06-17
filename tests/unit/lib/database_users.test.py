import tempfile
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database, synced_at_timestamp


class DatabaseUsersTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(db_path=str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tmpdir.cleanup()

    def test_upsert_user_persists_core_fields(self) -> None:
        user = {
            "gid": "user_1",
            "name": "User One",
            "resource_type": "user",
            "email": "user.one@example.com",
            "is_active": True,
            "workspaces": [
                {"gid": "ws_1", "name": "Workspace One"},
                {"gid": "ws_2", "name": "Workspace Two"},
            ],
        }

        sync_time = synced_at_timestamp()
        self.database.upsert_user(self.connection, user, sync_time)
        self.connection.commit()

        row = self.connection.execute("SELECT * FROM users WHERE gid = ?", ("user_1",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "User One")
        self.assertEqual(row["resource_type"], "user")
        self.assertEqual(row["email"], "user.one@example.com")
        self.assertEqual(row["is_active"], 1)

        workspace_rows = list(
            self.connection.execute(
                "SELECT workspace_gid, workspace_name FROM user_workspaces WHERE user_gid = ? ORDER BY workspace_gid",
                ("user_1",),
            )
        )
        self.assertEqual(len(workspace_rows), 2)
        self.assertEqual(workspace_rows[0]["workspace_gid"], "ws_1")
        self.assertEqual(workspace_rows[0]["workspace_name"], "Workspace One")
        self.assertEqual(workspace_rows[1]["workspace_gid"], "ws_2")

    def test_upsert_user_replaces_workspace_rows(self) -> None:
        user = {
            "gid": "user_replace",
            "name": "User Replace",
            "resource_type": "user",
            "workspaces": [{"gid": "ws_a", "name": "Workspace A"}],
        }
        sync_time = synced_at_timestamp()
        self.database.upsert_user(self.connection, user, sync_time)
        self.connection.commit()

        updated_user = {
            "gid": "user_replace",
            "name": "User Replace",
            "resource_type": "user",
            "workspaces": [{"gid": "ws_b", "name": "Workspace B"}],
        }
        later_sync = synced_at_timestamp()
        self.database.upsert_user(self.connection, updated_user, later_sync)
        self.connection.commit()

        workspace_rows = list(
            self.connection.execute(
                "SELECT workspace_gid FROM user_workspaces WHERE user_gid = ?", ("user_replace",)
            )
        )
        self.assertEqual(len(workspace_rows), 1)
        self.assertEqual(workspace_rows[0]["workspace_gid"], "ws_b")

    def test_initialize_adds_atlassian_status_column(self) -> None:
        columns = [
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(user_atlassian_accounts)")
        ]
        self.assertIn("atlassian_status", columns)

    def test_load_atlassian_account_map_includes_status(self) -> None:
        sync_time = synced_at_timestamp()
        self.connection.execute(
            """
            INSERT INTO user_atlassian_accounts (
                user_gid, atlassian_account_id, atlassian_email, atlassian_status, synced_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            ("user_status", "atl-status", "status@example.com", "active", sync_time.isoformat()),
        )
        self.connection.commit()

        mapping = self.database.load_atlassian_account_map(self.connection)
        self.assertEqual(mapping["user_status"]["account_id"], "atl-status")
        self.assertEqual(mapping["user_status"]["email"], "status@example.com")
        self.assertEqual(mapping["user_status"]["status"], "active")
