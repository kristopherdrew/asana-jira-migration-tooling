from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.export_team_memberships import export_team_memberships


class ExportTeamMembershipsTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_exports_view_rows(self) -> None:
        sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.database.upsert_team(
            self.connection,
            {"gid": "team_1", "name": "Team One", "is_public": True},
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "user_1",
                "name": "User One",
                "resource_type": "user",
                "email": "user1@example.com",
                "is_active": True,
            },
            sync_time,
        )
        self.database.upsert_team_members(
            self.connection,
            "team_1",
            [
                {
                    "user": {"gid": "user_1", "is_active": True},
                    "role": "member",
                    "is_admin": True,
                }
            ],
            sync_time,
        )
        self.connection.commit()

        output_path = export_team_memberships(
            str(self.db_path),
            self.workspace / "exports",
            filename="memberships.csv",
            workspace_gid="ws_1",
        )

        self.assertTrue(output_path.exists())
        with output_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["team_gid"], "team_1")
        self.assertEqual(row["team_name"], "Team One")
        self.assertEqual(row["team_url"], "https://app.asana.com/0/team_1/")
        self.assertEqual(row["team_is_public"], "true")
        self.assertEqual(row["user_gid"], "user_1")
        self.assertEqual(row["user_email"], "user1@example.com")
        self.assertEqual(row["user_url"], "https://app.asana.com/1/ws_1/profile/user_1")
        self.assertEqual(row["user_is_active"], "true")
        self.assertEqual(row["role"], "member")
        self.assertEqual(row["is_admin"], "true")
        self.assertEqual(row["membership_is_active"], "true")
        self.assertEqual(row["membership_synced_at"], "2025-01-01")
        self.assertEqual(row["user_synced_at"], "2025-01-01")
        self.assertEqual(row["team_synced_at"], "2025-01-01")

    def test_exports_admin_gid_when_user_snapshot_is_missing(self) -> None:
        sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.database.upsert_team(
            self.connection,
            {"gid": "team_1", "name": "Team One", "is_public": True},
            sync_time,
        )
        self.database.upsert_team_members(
            self.connection,
            "team_1",
            [
                {
                    "user": {"gid": "user_missing", "is_active": True},
                    "role": "member",
                    "is_admin": True,
                }
            ],
            sync_time,
        )
        self.connection.commit()

        output_path = export_team_memberships(
            str(self.db_path),
            self.workspace / "exports",
            filename="memberships.csv",
            workspace_gid="ws_1",
        )

        with output_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["user_gid"], "user_missing")
        self.assertEqual(row["user_name"], "unknown_user user_missing")
        self.assertEqual(row["team_url"], "https://app.asana.com/0/team_1/")
        self.assertEqual(row["user_url"], "https://app.asana.com/1/ws_1/profile/user_missing")
        self.assertEqual(row["is_admin"], "true")
