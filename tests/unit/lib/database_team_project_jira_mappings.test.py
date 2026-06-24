from __future__ import annotations

from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database


class TeamProjectJiraMappingsTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database = Database(str(Path(self.temp_dir.name) / "asana.sqlite"))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.addCleanup(self.connection.close)
        self.sync_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_upsert_and_load_round_trip(self) -> None:
        written = self.database.upsert_team_project_jira_mappings(
            self.connection,
            [
                {"kind": "team", "asana_gid": "team_a", "jira_key": "MIG-1"},
                {"kind": "no_team", "asana_gid": "", "jira_key": "MIG-2"},
                {"kind": "project", "asana_gid": "proj_1", "jira_key": "MIG-3"},
                {"kind": "project", "asana_gid": "", "jira_key": ""},  # ignored (no key)
            ],
            self.sync_time,
        )
        self.assertEqual(written, 3)
        loaded = self.database.load_team_project_jira_mappings(self.connection)
        self.assertEqual(loaded[("team", "team_a")], "MIG-1")
        self.assertEqual(loaded[("no_team", "")], "MIG-2")
        self.assertEqual(loaded[("project", "proj_1")], "MIG-3")

    def test_upsert_updates_existing_key(self) -> None:
        self.database.upsert_team_project_jira_mappings(
            self.connection, [{"kind": "team", "asana_gid": "team_a", "jira_key": "MIG-1"}], self.sync_time
        )
        self.database.upsert_team_project_jira_mappings(
            self.connection, [{"kind": "team", "asana_gid": "team_a", "jira_key": "MIG-9"}], self.sync_time
        )
        loaded = self.database.load_team_project_jira_mappings(self.connection)
        self.assertEqual(loaded[("team", "team_a")], "MIG-9")

    def test_delete_scoped_keys_removes_only_targeted_rows(self) -> None:
        self.database.upsert_team_project_jira_mappings(
            self.connection,
            [
                {"kind": "team", "asana_gid": "team_a", "jira_key": "MIG-1"},
                {"kind": "project", "asana_gid": "proj_1", "jira_key": "MIG-2"},
                {"kind": "team", "asana_gid": "team_b", "jira_key": "MIG-3"},
            ],
            self.sync_time,
        )
        deleted = self.database.delete_team_project_jira_mappings(
            self.connection, [("team", "team_a"), ("project", "proj_1")]
        )
        self.assertEqual(deleted, 2)
        loaded = self.database.load_team_project_jira_mappings(self.connection)
        self.assertEqual(loaded, {("team", "team_b"): "MIG-3"})

    def test_delete_all_clears_table(self) -> None:
        self.database.upsert_team_project_jira_mappings(
            self.connection,
            [
                {"kind": "team", "asana_gid": "team_a", "jira_key": "MIG-1"},
                {"kind": "project", "asana_gid": "proj_1", "jira_key": "MIG-2"},
            ],
            self.sync_time,
        )
        deleted = self.database.delete_team_project_jira_mappings(self.connection)
        self.assertEqual(deleted, 2)
        self.assertEqual(self.database.load_team_project_jira_mappings(self.connection), {})

    def test_duplicate_jira_key_for_different_entity_raises(self) -> None:
        self.database.upsert_team_project_jira_mappings(
            self.connection, [{"kind": "team", "asana_gid": "team_a", "jira_key": "MIG-1"}], self.sync_time
        )
        with self.assertRaises(ValueError):
            self.database.upsert_team_project_jira_mappings(
                self.connection, [{"kind": "project", "asana_gid": "proj_1", "jira_key": "MIG-1"}], self.sync_time
            )
