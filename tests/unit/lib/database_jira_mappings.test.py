from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database


def utc_now() -> datetime:
    return datetime(2025, 2, 1, 12, 0, tzinfo=timezone.utc)


class DatabaseJiraMappingsTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.sync_time = utc_now()
        self._seed_project_and_tasks()

    def tearDown(self) -> None:
        self.connection.close()

    def _seed_project_and_tasks(self) -> None:
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("proj_1", "Example", self.sync_time.isoformat()),
        )
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("proj_2", "Secondary", self.sync_time.isoformat()),
        )
        for task_gid in ("task_1", "task_2", "task_extra"):
            self.connection.execute(
                """
                INSERT INTO tasks (gid, name, synced_at)
                VALUES (?, ?, ?)
                """,
                (task_gid, f"Task {task_gid}", self.sync_time.isoformat()),
            )
        self.connection.executemany(
            "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
            [
                ("task_1", "proj_1", self.sync_time.isoformat()),
                ("task_2", "proj_1", self.sync_time.isoformat()),
                ("task_extra", "proj_2", self.sync_time.isoformat()),
            ],
        )
        self.connection.commit()

    def test_upsert_task_jira_mappings_allows_multiple_keys_per_task(self) -> None:
        summary = self.database.upsert_task_jira_mappings(
            self.connection,
            [
                {"task_gid": "task_1", "jira_key": "ENG-100"},
                {"task_gid": "task_1", "jira_key": "ENG-101"},
            ],
            self.sync_time,
        )
        self.assertEqual(summary["written"], 2)

        rows = self.connection.execute(
            "SELECT task_gid, jira_key FROM task_jira_mappings ORDER BY jira_key"
        ).fetchall()
        self.assertEqual([(row["task_gid"], row["jira_key"]) for row in rows], [
            ("task_1", "ENG-100"),
            ("task_1", "ENG-101"),
        ])

        # Re-importing the same key should just update synced_at, not duplicate rows
        summary_second = self.database.upsert_task_jira_mappings(
            self.connection,
            [{"task_gid": "task_1", "jira_key": "ENG-100"}],
            self.sync_time,
        )
        self.assertEqual(summary_second["written"], 1)
        total_rows = self.connection.execute(
            "SELECT COUNT(*) AS count FROM task_jira_mappings"
        ).fetchone()["count"]
        self.assertEqual(total_rows, 2)

    def test_upsert_task_jira_mappings_detects_conflicting_keys(self) -> None:
        self.database.upsert_task_jira_mappings(
            self.connection,
            [{"task_gid": "task_1", "jira_key": "ENG-999"}],
            self.sync_time,
        )

        with self.assertRaises(ValueError):
            self.database.upsert_task_jira_mappings(
                self.connection,
                [{"task_gid": "task_2", "jira_key": "ENG-999"}],
                self.sync_time,
            )

    def test_same_jira_key_is_independent_between_sites(self) -> None:
        self.database.upsert_task_jira_mappings(
            self.connection, [{"task_gid": "task_1", "jira_key": "MIG-1"}],
            self.sync_time, jira_site_key="production_jira",
        )
        self.database.upsert_task_jira_mappings(
            self.connection, [{"task_gid": "task_2", "jira_key": "MIG-1"}],
            self.sync_time, jira_site_key="sandbox_jira",
        )
        rows = self.connection.execute(
            "SELECT jira_site_key, task_gid FROM task_jira_mappings ORDER BY jira_site_key"
        ).fetchall()
        self.assertEqual(
            [(row["jira_site_key"], row["task_gid"]) for row in rows],
            [("production_jira", "task_1"), ("sandbox_jira", "task_2")],
        )

    def test_legacy_mapping_schema_is_preserved_and_attributed(self) -> None:
        self.connection.execute("DROP TABLE task_jira_mappings")
        self.connection.execute(
            "CREATE TABLE task_jira_mappings (task_gid TEXT, jira_key TEXT UNIQUE, synced_at TEXT)"
        )
        self.connection.executemany(
            "INSERT INTO task_jira_mappings VALUES (?, ?, ?)",
            [("task_1", "ATJM-1", "now"), ("task_2", "FACARC-2", "now")],
        )
        self.connection.commit()
        self.database.initialize(self.connection)
        rows = self.connection.execute(
            "SELECT jira_key, jira_site_key FROM task_jira_mappings ORDER BY jira_key"
        ).fetchall()
        self.assertEqual(
            [(row["jira_key"], row["jira_site_key"]) for row in rows],
            [("ATJM-1", "production_jira"), ("FACARC-2", "sandbox_jira")],
        )
