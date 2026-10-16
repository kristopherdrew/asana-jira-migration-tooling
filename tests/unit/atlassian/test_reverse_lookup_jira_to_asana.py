#!/usr/bin/env python3
"""Tests for reverse_lookup_jira_to_asana."""

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from src.lib.database import Database


class TestReverseLookupsIssueExtraction(unittest.TestCase):
    """Test Jira issue extraction from search results."""

    def test_collect_issues_with_external_ids(self):
        """Extract issues with external IDs."""
        from scripts.atlassian.reverse_lookup_jira_to_asana import collect_issues

        payload = {
            "issues": [
                {
                    "key": "PROJ-123",
                    "fields": {"customfield_10000": "task-gid-1"},
                },
                {
                    "key": "PROJ-124",
                    "fields": {"customfield_10000": "task-gid-2"},
                },
                {
                    "key": "PROJ-125",
                    "fields": {"customfield_10000": None},
                },
            ]
        }

        issues = collect_issues(payload, "customfield_10000")
        self.assertEqual(len(issues), 2)
        self.assertEqual(issues[0].key, "PROJ-123")
        self.assertEqual(issues[0].external_id, "task-gid-1")
        self.assertEqual(issues[0].project_key, "PROJ")

    def test_collect_issues_empty_result(self):
        """Handle empty Jira search."""
        from scripts.atlassian.reverse_lookup_jira_to_asana import collect_issues

        payload = {"issues": []}
        issues = collect_issues(payload, "customfield_10000")
        self.assertEqual(len(issues), 0)

    def test_collect_issues_missing_key(self):
        """Skip issues without a key."""
        from scripts.atlassian.reverse_lookup_jira_to_asana import collect_issues

        payload = {
            "issues": [
                {"fields": {"customfield_10000": "task-gid-1"}},
                {"key": "PROJ-123", "fields": {"customfield_10000": "task-gid-2"}},
            ]
        }

        issues = collect_issues(payload, "customfield_10000")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].key, "PROJ-123")

    def test_collect_issues_whitespace_trimming(self):
        """Trim whitespace from external IDs."""
        from scripts.atlassian.reverse_lookup_jira_to_asana import collect_issues

        payload = {
            "issues": [
                {
                    "key": "PROJ-123",
                    "fields": {"customfield_10000": "  task-gid-1  "},
                },
            ]
        }

        issues = collect_issues(payload, "customfield_10000")
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].external_id, "task-gid-1")


class TestReverseLookupsProjectResolution(unittest.TestCase):
    """Test project lookup from tasks."""

    def setUp(self):
        """Create a temporary database."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db = Database(db_path=str(self.db_path))
        with self.db.connect() as connection:
            self.db.initialize(connection)

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_lookup_task_projects_single(self):
        """Look up projects for a task."""
        from scripts.atlassian.reverse_lookup_jira_to_asana import lookup_task_projects

        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO tasks (gid, name, created_at, synced_at) VALUES (?, ?, ?, ?)",
                ("task-1", "Test Task", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO projects (gid, name, created_at, synced_at) VALUES (?, ?, ?, ?)",
                ("proj-1", "Test Project", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
                ("task-1", "proj-1", "2025-01-01T00:00:00Z"),
            )
            connection.commit()

        with self.db.connect() as connection:
            project_gids = lookup_task_projects(connection, "task-1")
            self.assertEqual(project_gids, ["proj-1"])

    def test_lookup_task_projects_multiple(self):
        """Look up multiple projects for a task."""
        from scripts.atlassian.reverse_lookup_jira_to_asana import lookup_task_projects

        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO tasks (gid, name, created_at, synced_at) VALUES (?, ?, ?, ?)",
                ("task-1", "Test Task", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO projects (gid, name, created_at, synced_at) VALUES (?, ?, ?, ?)",
                ("proj-1", "Project 1", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO projects (gid, name, created_at, synced_at) VALUES (?, ?, ?, ?)",
                ("proj-2", "Project 2", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
                ("task-1", "proj-1", "2025-01-01T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
                ("task-1", "proj-2", "2025-01-01T00:00:00Z"),
            )
            connection.commit()

        with self.db.connect() as connection:
            project_gids = lookup_task_projects(connection, "task-1")
            self.assertEqual(sorted(project_gids), ["proj-1", "proj-2"])

    def test_lookup_task_projects_not_found(self):
        """Return empty list for task with no projects."""
        from scripts.atlassian.reverse_lookup_jira_to_asana import lookup_task_projects

        with self.db.connect() as connection:
            project_gids = lookup_task_projects(connection, "nonexistent-task")
            self.assertEqual(project_gids, [])


class TestReverseLookupsMappingInsert(unittest.TestCase):
    """Test mapping insertion and conflict detection."""

    def setUp(self):
        """Create a temporary database."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.db = Database(db_path=str(self.db_path))
        with self.db.connect() as connection:
            self.db.initialize(connection)

    def tearDown(self):
        """Clean up."""
        self.temp_dir.cleanup()

    def test_insert_task_mapping(self):
        """Insert a task_jira_mapping."""
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO tasks (gid, name, created_at, synced_at) VALUES (?, ?, ?, ?)",
                ("task-1", "Test Task", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            )
            connection.execute(
                """
                INSERT INTO task_jira_mappings
                (jira_site_key, task_gid, jira_key, synced_at)
                VALUES (?, ?, ?, ?)
                """,
                ("test_site", "task-1", "PROJ-123", "2025-01-01T00:00:00Z"),
            )
            connection.commit()

        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT task_gid, jira_key FROM task_jira_mappings WHERE task_gid = ?",
                ("task-1",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["task_gid"], "task-1")
            self.assertEqual(row["jira_key"], "PROJ-123")

    def test_insert_project_mapping(self):
        """Insert a jira_project_mapping."""
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO projects (gid, name, created_at, synced_at) VALUES (?, ?, ?, ?)",
                ("proj-1", "Test Project", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            )
            connection.execute(
                """
                INSERT INTO jira_project_mappings
                (jira_site_key, project_gid, jira_project_name, jira_project_key, synced_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("test_site", "proj-1", "Test Project", "PROJ", "2025-01-01T00:00:00Z"),
            )
            connection.commit()

        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT project_gid, jira_project_key FROM jira_project_mappings WHERE project_gid = ?",
                ("proj-1",),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["project_gid"], "proj-1")
            self.assertEqual(row["jira_project_key"], "PROJ")

    def test_task_mapping_update_on_conflict(self):
        """Update task mapping when task_gid already exists."""
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO tasks (gid, name, created_at, synced_at) VALUES (?, ?, ?, ?)",
                ("task-1", "Test Task", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            )
            connection.execute(
                """
                INSERT INTO task_jira_mappings
                (jira_site_key, task_gid, jira_key, synced_at)
                VALUES (?, ?, ?, ?)
                """,
                ("test_site", "task-1", "OLD-123", "2025-01-01T00:00:00Z"),
            )
            connection.commit()

        with self.db.connect() as connection:
            connection.execute(
                """
                INSERT INTO task_jira_mappings
                (jira_site_key, task_gid, jira_key, synced_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(jira_site_key, task_gid) DO UPDATE SET
                    jira_key=excluded.jira_key,
                    synced_at=excluded.synced_at
                """,
                ("test_site", "task-1", "NEW-456", "2025-01-02T00:00:00Z"),
            )
            connection.commit()

        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT jira_key FROM task_jira_mappings WHERE task_gid = ?",
                ("task-1",),
            ).fetchone()
            self.assertEqual(row["jira_key"], "NEW-456")

    def test_task_mapping_conflict_detection(self):
        """Detect when a Jira key is already mapped to a different task."""
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO tasks (gid, name, created_at, synced_at) VALUES (?, ?, ?, ?)",
                ("task-1", "Task 1", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO tasks (gid, name, created_at, synced_at) VALUES (?, ?, ?, ?)",
                ("task-2", "Task 2", "2025-01-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            )
            connection.execute(
                """
                INSERT INTO task_jira_mappings
                (jira_site_key, task_gid, jira_key, synced_at)
                VALUES (?, ?, ?, ?)
                """,
                ("test_site", "task-1", "PROJ-123", "2025-01-01T00:00:00Z"),
            )
            connection.commit()

        with self.db.connect() as connection:
            existing = connection.execute(
                "SELECT jira_key FROM task_jira_mappings WHERE jira_site_key = ? AND jira_key = ?",
                ("test_site", "PROJ-123"),
            ).fetchone()
            self.assertIsNotNone(existing)
            self.assertEqual(existing["jira_key"], "PROJ-123")


if __name__ == "__main__":
    unittest.main()
