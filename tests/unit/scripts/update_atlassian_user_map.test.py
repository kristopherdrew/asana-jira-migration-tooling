from __future__ import annotations

import csv
from datetime import datetime, timezone
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

        cleared, updated = update_mapping(str(self.db_path), csv_path)
        self.assertEqual(cleared, 0)
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

    def _write_mapping(self, *records: tuple[str, str, str, str]) -> Path:
        csv_path = self.workspace / "mapping.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["gid", "atlassianAccountId", "atlassianEmail", "atlassianStatus"])
            for record in records:
                writer.writerow(record)
        return csv_path

    def _count_rows(self) -> int:
        connection = self.database.connect()
        try:
            return connection.execute(
                "SELECT COUNT(*) FROM user_atlassian_accounts"
            ).fetchone()[0]
        finally:
            connection.close()

    def _seed_users(self, *users: tuple[str, str]) -> None:
        connection = self.database.connect()
        try:
            synced_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
            for gid, email in users:
                self.database.upsert_user(
                    connection,
                    {"gid": gid, "name": gid, "email": email, "is_active": True},
                    synced_at,
                )
            connection.commit()
        finally:
            connection.close()

    def _write_atlassian_export(self, *records: tuple[str, str, str]) -> Path:
        export_path = self.workspace / "export-users.csv"
        with export_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["User id", "User name", "email", "User status", "Org role"])
            for account_id, email, status in records:
                writer.writerow([account_id, "Ignored Name", email, status, "None"])
        return export_path

    def test_atlassian_export_matches_users_by_email(self) -> None:
        self._seed_users(("user_1", "Alice@Example.com"), ("user_2", "bob@example.com"))
        export_path = self._write_atlassian_export(
            ("atl-alice", "alice@example.com", "Active"),
            ("atl-bob", "BOB@EXAMPLE.COM", "Deactivated"),
            ("atl-nobody", "stranger@example.com", "Suspended"),
        )

        cleared, updated = update_mapping(
            str(self.db_path), atlassian_export_path=export_path
        )
        self.assertEqual(cleared, 0)
        self.assertEqual(updated, 2)  # stranger has no Asana user

        connection = self.database.connect()
        try:
            rows = connection.execute(
                """
                SELECT user_gid, atlassian_account_id, atlassian_email, atlassian_status
                FROM user_atlassian_accounts
                ORDER BY user_gid
                """
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["user_gid"], "user_1")
        self.assertEqual(rows[0]["atlassian_account_id"], "atl-alice")
        self.assertEqual(rows[0]["atlassian_status"], "Active")
        self.assertEqual(rows[1]["user_gid"], "user_2")
        self.assertEqual(rows[1]["atlassian_account_id"], "atl-bob")
        self.assertEqual(rows[1]["atlassian_status"], "Deactivated")

    def test_atlassian_export_with_clear_replaces_table(self) -> None:
        self._seed_users(("user_1", "alice@example.com"))
        update_mapping(str(self.db_path), self._write_mapping(("stale", "atl-stale", "", "")))
        self.assertEqual(self._count_rows(), 1)

        export_path = self._write_atlassian_export(("atl-alice", "alice@example.com", "Active"))
        cleared, updated = update_mapping(
            str(self.db_path), clear=True, atlassian_export_path=export_path
        )
        self.assertEqual(cleared, 1)
        self.assertEqual(updated, 1)

        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT user_gid FROM user_atlassian_accounts"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual([row["user_gid"] for row in rows], ["user_1"])

    def test_clear_without_csv_wipes_all_rows(self) -> None:
        csv_path = self._write_mapping(("user_1", "atl-alice", "", "active"))
        update_mapping(str(self.db_path), csv_path)
        self.assertEqual(self._count_rows(), 1)

        cleared, updated = update_mapping(str(self.db_path), clear=True)
        self.assertEqual(cleared, 1)
        self.assertEqual(updated, 0)
        self.assertEqual(self._count_rows(), 0)

    def test_clear_then_load_replaces_rows(self) -> None:
        first = self._write_mapping(
            ("user_1", "atl-alice", "", "active"),
            ("user_2", "atl-bob", "", "active"),
        )
        update_mapping(str(self.db_path), first)
        self.assertEqual(self._count_rows(), 2)

        replacement = self._write_mapping(("user_3", "atl-carol", "", "active"))
        cleared, updated = update_mapping(str(self.db_path), replacement, clear=True)
        self.assertEqual(cleared, 2)
        self.assertEqual(updated, 1)

        connection = self.database.connect()
        try:
            rows = connection.execute(
                "SELECT user_gid FROM user_atlassian_accounts ORDER BY user_gid"
            ).fetchall()
            self.assertEqual([row["user_gid"] for row in rows], ["user_3"])
        finally:
            connection.close()
