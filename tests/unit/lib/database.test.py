import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database, synced_at_timestamp


class DatabaseTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(db_path=str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tmpdir.cleanup()

    def test_upsert_team_stores_basic_fields(self) -> None:
        team = {
            "gid": "team_1",
            "name": "Team One",
            "description": "Example team",
            "organization": {"gid": "org_1"},
            "is_public": True,
            "created_at": "2023-01-01T00:00:00.000Z",
            "modified_at": "2023-01-02T00:00:00.000Z",
        }

        sync_time = synced_at_timestamp()
        self.database.upsert_team(self.connection, team, sync_time)
        self.connection.commit()

        row = self.connection.execute("SELECT * FROM teams WHERE gid = ?", ("team_1",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Team One")
        self.assertEqual(row["organization_gid"], "org_1")
        self.assertEqual(row["is_public"], 1)
        self.assertNotIn("raw_json", row.keys())
        self.assertIn("synced_at", row.keys())

    def test_upsert_team_members_persists_records(self) -> None:
        team = {"gid": "team_membership_test", "name": "Team"}
        sync_time = synced_at_timestamp()
        self.database.upsert_team(self.connection, team, sync_time)

        memberships = [
            {
                "user": {"gid": "user_1", "is_active": True},
                "role": "member",
                "is_admin": False,
            },
            {
                "user": {"gid": "user_2", "is_active": False},
                "role": "admin",
                "is_admin": True,
            },
        ]

        self.database.upsert_team_members(self.connection, "team_membership_test", memberships, sync_time)
        self.connection.commit()

        rows = list(
            self.connection.execute(
                "SELECT team_gid, user_gid, role, is_admin, is_active FROM team_members ORDER BY user_gid"
            )
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["user_gid"], "user_1")
        self.assertEqual(rows[0]["is_admin"], 0)
        self.assertEqual(rows[0]["is_active"], 1)
        self.assertEqual(rows[1]["role"], "admin")
        self.assertEqual(rows[1]["is_admin"], 1)
