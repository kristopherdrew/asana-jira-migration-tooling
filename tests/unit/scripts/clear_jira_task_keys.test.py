from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database

import scripts.clear_jira_task_keys as clear_script


def sync_time() -> str:
    return datetime(2025, 2, 1, tzinfo=timezone.utc).isoformat()


class ClearJiraTaskKeysTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self._seed_data()

    def tearDown(self) -> None:
        self.connection.close()

    def _seed_data(self) -> None:
        self.connection.execute(
            "INSERT INTO tasks (gid, name, synced_at) VALUES (?, ?, ?)",
            ("task_1", "Task", sync_time()),
        )
        self.connection.execute(
            "INSERT INTO tasks (gid, name, synced_at) VALUES (?, ?, ?)",
            ("task_2", "Task 2", sync_time()),
        )
        self.connection.executemany(
            "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
            [
                ("task_1", "proj_1", sync_time()),
                ("task_2", "proj_2", sync_time()),
            ],
        )
        self.connection.executemany(
            "INSERT INTO task_jira_mappings (task_gid, jira_key, synced_at) VALUES (?, ?, ?)",
            [
                ("task_1", "ENG-1", sync_time()),
                ("task_2", "ENG-3", sync_time()),
            ],
        )
        self.connection.commit()

    def test_clear_all(self) -> None:
        removed = clear_script.clear_jira_mappings(str(self.db_path))
        self.assertEqual(removed, 2)
        rows = self.connection.execute("SELECT COUNT(*) AS total FROM task_jira_mappings").fetchone()
        self.assertEqual(rows["total"], 0)

    def test_clear_specific_tasks(self) -> None:
        removed = clear_script.clear_jira_mappings(str(self.db_path), ["task_1"])
        self.assertEqual(removed, 1)
        rows = self.connection.execute(
            "SELECT jira_key FROM task_jira_mappings ORDER BY jira_key"
        ).fetchall()
        self.assertEqual([row["jira_key"] for row in rows], ["ENG-3"])

    def test_clear_by_project(self) -> None:
        removed = clear_script.clear_jira_mappings(str(self.db_path), project_gid="proj_1")
        self.assertEqual(removed, 1)
        rows = self.connection.execute(
            "SELECT task_gid, jira_key FROM task_jira_mappings ORDER BY jira_key"
        ).fetchall()
        self.assertEqual([(row["task_gid"], row["jira_key"]) for row in rows], [("task_2", "ENG-3")])
