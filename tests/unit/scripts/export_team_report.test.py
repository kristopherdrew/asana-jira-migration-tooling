from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.export_team_report import export_team_report


class ExportTeamReportTest(TestCase):
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

    def test_exports_team_counts_and_admins(self) -> None:
        sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.database.upsert_team(
            self.connection,
            {"gid": "team_1", "name": "Team One", "is_public": True},
            sync_time,
        )
        self.database.upsert_team(
            self.connection,
            {"gid": "team_2", "name": "Team Two", "is_public": False},
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "user_1",
                "name": "Alice Admin",
                "resource_type": "user",
                "email": "alice@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "user_2",
                "name": "Bob Boss",
                "resource_type": "user",
                "email": "bob@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.upsert_team_members(
            self.connection,
            "team_1",
            [
                {
                    "user": {"gid": "user_1", "name": "Alice Admin", "email": "alice@example.com", "is_active": True},
                    "role": "member",
                    "is_admin": True,
                },
                {
                    "user": {"gid": "user_2", "name": "Bob Boss", "email": "bob@example.com", "is_active": True},
                    "role": "member",
                    "is_admin": True,
                },
            ],
            sync_time,
        )
        self.database.upsert_team_members(
            self.connection,
            "team_2",
            [
                {
                    "user": {"gid": "user_1", "name": "Alice Admin", "email": "alice@example.com", "is_active": True},
                    "role": "member",
                    "is_admin": False,
                }
            ],
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_1",
                "name": "Project One",
                "team": {"gid": "team_1", "name": "Team One"},
                "archived": False,
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_2",
                "name": "Project Two",
                "team": {"gid": "team_1", "name": "Team One"},
                "archived": True,
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_3",
                "name": "Project Three",
                "team": {"gid": "team_1", "name": "Team One"},
                "archived": False,
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_4",
                "name": "Project Four",
                "team": {"gid": "team_2", "name": "Team Two"},
                "archived": False,
            },
            sync_time,
        )
        self.connection.commit()

        output_path = export_team_report(str(self.db_path), self.workspace / "exports", filename="teams.csv")

        self.assertTrue(output_path.exists())
        with output_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual([row["team_gid"] for row in rows], ["team_1", "team_2"])
        team_one = rows[0]
        self.assertEqual(team_one["team_name"], "Team One")
        self.assertEqual(team_one["team_is_public"], "true")
        self.assertEqual(team_one["project_count_total"], "3")
        self.assertEqual(team_one["project_count_active"], "2")
        self.assertEqual(team_one["project_count_archived"], "1")
        self.assertEqual(
            team_one["known_team_admins"],
            "Alice Admin <alice@example.com>; Bob Boss <bob@example.com>",
        )
        self.assertEqual(team_one["team_synced_at"], sync_time.isoformat())

        team_two = rows[1]
        self.assertEqual(team_two["team_name"], "Team Two")
        self.assertEqual(team_two["team_is_public"], "false")
        self.assertEqual(team_two["project_count_total"], "1")
        self.assertEqual(team_two["project_count_active"], "1")
        self.assertEqual(team_two["project_count_archived"], "0")
        self.assertEqual(team_two["known_team_admins"], "")

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

        output_path = export_team_report(str(self.db_path), self.workspace / "exports", filename="teams.csv")

        with output_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(rows[0]["known_team_admins"], "unknown_user user_missing")
