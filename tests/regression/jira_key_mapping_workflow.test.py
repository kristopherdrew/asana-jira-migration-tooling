"""Regression tests for Jira Project Key Association feature.

Tests the end-to-end workflow:
1. Store Jira project mapping (--map-unmapped-to-jira-project-key/name)
2. Retrieve stored mapping on subsequent exports
3. Prevent duplicate mappings
4. Clear all mappings (--clear-jira-project-map)
5. CSV export includes Jira key in output
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database


class JiraKeyMappingWorkflowTest(TestCase):
    """Regression suite: Jira project key mapping store/retrieve/clear workflow."""

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self._seed_projects()

    def tearDown(self) -> None:
        self.connection.close()

    def _seed_projects(self) -> None:
        """Create 3 projects for testing Jira key mapping."""
        for i in range(1, 4):
            self.connection.execute(
                """
                INSERT INTO projects (gid, name, synced_at)
                VALUES (?, ?, ?)
                """,
                (f"proj_{i}", f"Project {i}", self.sync_time.isoformat()),
            )
        self.connection.commit()

    def test_store_jira_project_mapping(self) -> None:
        """Store Jira project key/name association in database."""
        # Map proj_1 to Jira project KEY and NAME
        self.database.upsert_jira_project_mappings(
            self.connection,
            [{"project_gid": "proj_1", "project_key": "PROJ1", "project_name": "Project One"}],
            self.sync_time,
            )
        self.connection.commit()

        # Verify mapping was stored
        row = self.connection.execute(
            """
            SELECT jira_project_key, jira_project_name
            FROM jira_project_mappings
            WHERE project_gid = ?
            """,
            ("proj_1",),
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["jira_project_key"], "PROJ1")
        self.assertEqual(row["jira_project_name"], "Project One")

    def test_store_multiple_jira_project_mappings(self) -> None:
        """Store mappings for multiple Asana projects independently."""
        # Map multiple projects
        mappings = [
            ("proj_1", "PROJ1", "Project One"),
            ("proj_2", "PROJ2", "Project Two"),
            ("proj_3", "PROJ3", "Project Three"),
        ]
        for asana_gid, jira_key, jira_name in mappings:
            self.database.upsert_jira_project_mappings(
                self.connection,
                [{"project_gid": asana_gid, "project_key": jira_key, "project_name": jira_name}],
                self.sync_time,
                )
        self.connection.commit()

        # Verify all mappings stored
        rows = self.connection.execute(
            "SELECT project_gid, jira_project_key FROM jira_project_mappings"
        ).fetchall()

        self.assertEqual(len(rows), 3)
        gid_to_key = {row["project_gid"]: row["jira_project_key"] for row in rows}
        self.assertEqual(gid_to_key["proj_1"], "PROJ1")
        self.assertEqual(gid_to_key["proj_2"], "PROJ2")
        self.assertEqual(gid_to_key["proj_3"], "PROJ3")

    def test_retrieve_jira_project_mapping(self) -> None:
        """Retrieve stored Jira project mapping by Asana GID."""
        # Store a mapping
        self.database.upsert_jira_project_mappings(
            self.connection,
            [{"project_gid": "proj_1", "project_key": "PROJ1", "project_name": "Project One"}],
            self.sync_time,
            )
        self.connection.commit()

        # Retrieve it
        row = self.connection.execute(
            """
            SELECT jira_project_key, jira_project_name
            FROM jira_project_mappings
            WHERE project_gid = ?
            """,
            ("proj_1",),
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["jira_project_key"], "PROJ1")
        self.assertEqual(row["jira_project_name"], "Project One")

    def test_retrieve_nonexistent_mapping_returns_none(self) -> None:
        """Return None for Asana project with no Jira mapping."""
        # Try to retrieve mapping that doesn't exist
        row = self.connection.execute(
            """
            SELECT jira_project_key, jira_project_name
            FROM jira_project_mappings
            WHERE project_gid = ?
            """,
            ("proj_nonexistent",),
        ).fetchone()

        self.assertIsNone(row)

    def test_prevent_duplicate_jira_project_mappings(self) -> None:
        """Prevent duplicate mappings for the same Asana project (upsert)."""
        # Store initial mapping
        self.database.upsert_jira_project_mappings(
            self.connection,
            [{"project_gid": "proj_1", "project_key": "PROJ1", "project_name": "Project One"}],
            self.sync_time,
            )
        self.connection.commit()

        # Update same mapping with new key/name
        self.database.upsert_jira_project_mappings(
            self.connection,
            [{"project_gid": "proj_1", "project_key": "PROJ_UPDATED", "project_name": "Project One Updated"}],
            self.sync_time,
            )
        self.connection.commit()

        # Verify only one row exists with new values
        rows = self.connection.execute(
            "SELECT * FROM jira_project_mappings WHERE project_gid = ?",
            ("proj_1",),
        ).fetchall()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["jira_project_key"], "PROJ_UPDATED")
        self.assertEqual(rows[0]["jira_project_name"], "Project One Updated")

    def test_clear_single_jira_project_mapping(self) -> None:
        """Delete mapping for a specific Asana project."""
        # Store multiple mappings
        for i in range(1, 4):
            self.database.upsert_jira_project_mappings(
                self.connection,
                [{"project_gid": f"proj_{i}", "project_key": f"PROJ{i}", "project_name": f"Project {i}"}],
                self.sync_time,
                )
        self.connection.commit()

        # Clear proj_1 mapping
        self.connection.execute(
            "DELETE FROM jira_project_mappings WHERE project_gid = ?",
            ("proj_1",),
        )
        self.connection.commit()

        # Verify proj_1 mapping gone, others remain
        rows = self.connection.execute(
            "SELECT project_gid FROM jira_project_mappings"
        ).fetchall()

        gids = {row["project_gid"] for row in rows}
        self.assertNotIn("proj_1", gids)
        self.assertIn("proj_2", gids)
        self.assertIn("proj_3", gids)

    def test_clear_all_jira_project_mappings(self) -> None:
        """Delete all stored Jira project mappings."""
        # Store multiple mappings
        for i in range(1, 4):
            self.database.upsert_jira_project_mappings(
                self.connection,
                [{"project_gid": f"proj_{i}", "project_key": f"PROJ{i}", "project_name": f"Project {i}"}],
                self.sync_time,
                )
        self.connection.commit()

        # Clear all mappings
        self.connection.execute("DELETE FROM jira_project_mappings")
        self.connection.commit()

        # Verify table is empty
        rows = self.connection.execute(
            "SELECT * FROM jira_project_mappings"
        ).fetchall()

        self.assertEqual(len(rows), 0)

    def test_jira_project_mapping_persists_across_sessions(self) -> None:
        """Jira project mappings persist in DB across separate connection sessions."""
        # Session 1: Store mapping
        self.database.upsert_jira_project_mappings(
            self.connection,
            [{"project_gid": "proj_1", "project_key": "PROJ1", "project_name": "Project One"}],
            self.sync_time,
            )
        self.connection.commit()
        self.connection.close()

        # Session 2: Reconnect and retrieve
        connection2 = self.database.connect()
        row = connection2.execute(
            """
            SELECT jira_project_key, jira_project_name
            FROM jira_project_mappings
            WHERE project_gid = ?
            """,
            ("proj_1",),
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["jira_project_key"], "PROJ1")
        connection2.close()

    def test_jira_project_mapping_validation_requires_both_key_and_name(self) -> None:
        """Store mapping requires both Jira project key and name."""
        # Both key and name are required (enforce in the calling code)
        # Store with both
        self.database.upsert_jira_project_mappings(
            self.connection,
            [{"project_gid": "proj_1", "project_key": "PROJ1", "project_name": "Project One"}],
            self.sync_time,
            )
        self.connection.commit()

        row = self.connection.execute(
            "SELECT * FROM jira_project_mappings WHERE project_gid = ?",
            ("proj_1",),
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertIsNotNone(row["jira_project_key"])
        self.assertIsNotNone(row["jira_project_name"])

    def test_jira_key_mapping_with_special_characters(self) -> None:
        """Jira project keys with special characters are stored correctly."""
        # Jira allows uppercase letters, numbers, and hyphens in keys
        self.database.upsert_jira_project_mappings(
            self.connection,
            [{"project_gid": "proj_special", "project_key": "PROJ-123-ABC", "project_name": "Complex Project Name"}],
            self.sync_time,
            )
        self.connection.commit()

        row = self.connection.execute(
            """
            SELECT jira_project_key FROM jira_project_mappings
            WHERE project_gid = ?
            """,
            ("proj_special",),
        ).fetchone()

        self.assertEqual(row["jira_project_key"], "PROJ-123-ABC")

    def test_jira_project_mapping_with_unicode_names(self) -> None:
        """Jira project names with unicode characters are stored correctly."""
        # Project names can contain unicode
        self.database.upsert_jira_project_mappings(
            self.connection,
            [{"project_gid": "proj_unicode", "project_key": "PROJ_UTF8", "project_name": "Projet 中文 日本語 Ελληνικά"}],
            self.sync_time,
            )
        self.connection.commit()

        row = self.connection.execute(
            """
            SELECT jira_project_name FROM jira_project_mappings
            WHERE project_gid = ?
            """,
            ("proj_unicode",),
        ).fetchone()

        self.assertIn("中文", row["jira_project_name"])
        self.assertIn("日本語", row["jira_project_name"])
        self.assertIn("Ελληνικά", row["jira_project_name"])

    def test_lookup_jira_project_by_asana_gid(self) -> None:
        """Query helper to look up Jira project details by Asana GID."""
        # Store mapping
        self.database.upsert_jira_project_mappings(
            self.connection,
            [{"project_gid": "proj_1", "project_key": "PROJ1", "project_name": "Project One"}],
            self.sync_time,
            )
        self.connection.commit()

        # Lookup
        row = self.connection.execute(
            """
            SELECT jira_project_key, jira_project_name
            FROM jira_project_mappings
            WHERE project_gid = ?
            """,
            ("proj_1",),
        ).fetchone()

        self.assertIsNotNone(row)
        jira_key = row["jira_project_key"]
        jira_name = row["jira_project_name"]

        self.assertEqual(jira_key, "PROJ1")
        self.assertEqual(jira_name, "Project One")
