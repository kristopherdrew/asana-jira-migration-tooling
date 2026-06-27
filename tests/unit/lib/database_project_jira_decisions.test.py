from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database


class ProjectJiraDecisionsTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database = Database(str(Path(self.temp_dir.name) / "asana.sqlite"))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.addCleanup(self.connection.close)
        self.sync_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def _decisions(self):
        return {
            row["project_gid"]: (row["jira_key"], row["decision"])
            for row in self.connection.execute(
                "SELECT project_gid, jira_key, decision FROM project_jira_decisions"
            ).fetchall()
        }

    def test_upsert_writes_rows_and_skips_incomplete(self) -> None:
        result = self.database.upsert_project_jira_decisions(
            self.connection,
            [
                {"project_gid": "proj_1", "jira_key": "MIG-1", "decision": "Migrate"},
                {"project_gid": "proj_2", "jira_key": "MIG-2", "decision": ""},  # empty -> NULL
                {"project_gid": "", "jira_key": "MIG-3", "decision": "Archive"},  # ignored (no project)
                {"project_gid": "proj_4", "jira_key": "", "decision": "Archive"},  # ignored (no key)
            ],
            self.sync_time,
        )
        self.assertEqual(result["written"], 2)
        decisions = self._decisions()
        self.assertEqual(decisions["proj_1"], ("MIG-1", "Migrate"))
        self.assertEqual(decisions["proj_2"], ("MIG-2", None))
        self.assertNotIn("", decisions)

    def test_upsert_updates_existing_project(self) -> None:
        self.database.upsert_project_jira_decisions(
            self.connection,
            [{"project_gid": "proj_1", "jira_key": "MIG-1", "decision": "Migrate"}],
            self.sync_time,
        )
        self.database.upsert_project_jira_decisions(
            self.connection,
            [{"project_gid": "proj_1", "jira_key": "MIG-1", "decision": "Do not migrate"}],
            self.sync_time,
        )
        self.assertEqual(self._decisions()["proj_1"], ("MIG-1", "Do not migrate"))

    def test_duplicate_in_batch_keeps_first(self) -> None:
        result = self.database.upsert_project_jira_decisions(
            self.connection,
            [
                {"project_gid": "proj_1", "jira_key": "MIG-1", "decision": "Migrate"},
                {"project_gid": "proj_1", "jira_key": "MIG-9", "decision": "Archive"},
            ],
            self.sync_time,
        )
        self.assertEqual(result["written"], 1)
        self.assertEqual(self._decisions()["proj_1"], ("MIG-1", "Migrate"))

    def test_conflicting_jira_key_raises(self) -> None:
        self.database.upsert_project_jira_decisions(
            self.connection,
            [{"project_gid": "proj_1", "jira_key": "MIG-1", "decision": "Migrate"}],
            self.sync_time,
        )
        with self.assertRaises(ValueError):
            self.database.upsert_project_jira_decisions(
                self.connection,
                [{"project_gid": "proj_2", "jira_key": "MIG-1", "decision": "Migrate"}],
                self.sync_time,
            )

    def test_chunking_writes_all_rows(self) -> None:
        rows = [
            {"project_gid": f"proj_{i}", "jira_key": f"MIG-{i}", "decision": "Migrate"}
            for i in range(900)
        ]
        result = self.database.upsert_project_jira_decisions(self.connection, rows, self.sync_time)
        self.assertEqual(result["written"], 900)
        count = self.connection.execute(
            "SELECT COUNT(*) FROM project_jira_decisions"
        ).fetchone()[0]
        self.assertEqual(count, 900)


if __name__ == "__main__":
    import unittest

    unittest.main()
