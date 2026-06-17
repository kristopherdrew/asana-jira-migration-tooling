from __future__ import annotations

import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.update_atlassian_user_map import update_mapping


class UpdateAtlassianUserMapTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.connection.close()

    def test_update_mapping_inserts_rows(self) -> None:
        csv_path = self.workspace / "mapping.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["gid", "atlassianAccountId", "atlassianEmail", "atlassianStatus"])
            writer.writerow(["user_1", "atl-alice", "alice.atl@example.com", "active"])
            writer.writerow(["user_2", "atl-bob", "", "deactivated"])

        updated = update_mapping(str(self.db_path), csv_path)
        self.assertEqual(updated, 2)

        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT user_gid, atlassian_account_id, atlassian_email, atlassian_status
                FROM user_atlassian_accounts
                ORDER BY user_gid
                """
            ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["user_gid"], "user_1")
            self.assertEqual(rows[0]["atlassian_account_id"], "atl-alice")
            self.assertEqual(rows[0]["atlassian_email"], "alice.atl@example.com")
            self.assertEqual(rows[0]["atlassian_status"], "active")
            self.assertEqual(rows[1]["user_gid"], "user_2")
            self.assertEqual(rows[1]["atlassian_account_id"], "atl-bob")
            self.assertIsNone(rows[1]["atlassian_email"])
            self.assertEqual(rows[1]["atlassian_status"], "deactivated")
        finally:
            connection.close()
