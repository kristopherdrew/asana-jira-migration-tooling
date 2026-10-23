"""Tests for the project data inspection script."""

import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase

from scripts.inspect_project_data import (
    build_project_core_fields_sheet,
    build_custom_fields_sheet,
    build_status_updates_sheet,
    build_members_sheet,
    build_field_usage_summary_sheet,
    _resolve_project_scope,
)
from src.lib.database import Database


class TestInspectProjectData(TestCase):
    """Test the project data inspection functionality."""

    def setUp(self) -> None:
        """Create a test database with sample data."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

        self.db = Database(self.db_path)
        with self.db.connect() as connection:
            self.db.initialize(connection)

            # Create test data
            connection.execute(
                """
                INSERT INTO teams (gid, name, synced_at) VALUES
                ('team_1', 'Design Team', '2025-01-01T00:00:00Z'),
                ('team_2', 'Platform Team', '2025-01-01T00:00:00Z')
                """
            )

            connection.execute(
                """
                INSERT INTO users (gid, name, email, synced_at) VALUES
                ('user_1', 'Alice', 'alice@example.com', '2025-01-01T00:00:00Z'),
                ('user_2', 'Bob', 'bob@example.com', '2025-01-01T00:00:00Z')
                """
            )

            # Projects with different field values
            connection.execute(
                """
                INSERT INTO projects
                (gid, name, team_gid, team_name, owner_gid, owner_name,
                 created_at, modified_at, start_on, due_on, archived, completed,
                 notes, html_notes, num_tasks, is_personal_project, synced_at)
                VALUES
                ('proj_1', 'Website Redesign', 'team_1', 'Design Team', 'user_1', 'Alice',
                 '2025-01-01T00:00:00Z', '2025-01-15T00:00:00Z',
                 '2025-01-01', '2025-03-01', 0, 0,
                 'Redesign the company website', '<p>Redesign the company website</p>',
                 10, 0, '2025-01-15T00:00:00Z'),
                ('proj_2', 'API Development', 'team_2', 'Platform Team', 'user_2', 'Bob',
                 '2025-01-05T00:00:00Z', '2025-01-20T00:00:00Z',
                 NULL, '2025-04-01', 0, 0,
                 NULL, NULL,
                 20, 0, '2025-01-20T00:00:00Z'),
                ('proj_3', 'Archived Project', 'team_1', 'Design Team', 'user_1', 'Alice',
                 '2024-01-01T00:00:00Z', '2024-12-31T00:00:00Z',
                 NULL, NULL, 1, 0,
                 NULL, NULL,
                 0, 0, '2024-12-31T00:00:00Z')
                """
            )

            # Custom fields
            connection.execute(
                """
                INSERT INTO custom_fields
                (gid, name, resource_type, resource_subtype, synced_at)
                VALUES
                ('field_1', 'Priority', 'custom_field', 'enum', '2025-01-01T00:00:00Z'),
                ('field_2', 'Budget', 'custom_field', 'number', '2025-01-01T00:00:00Z')
                """
            )

            # Project custom field values
            connection.execute(
                """
                INSERT INTO project_custom_fields
                (project_gid, custom_field_gid, name, type, enum_value_name, number_value, synced_at)
                VALUES
                ('proj_1', 'field_1', 'Priority', 'enum', 'High', NULL, '2025-01-01T00:00:00Z'),
                ('proj_1', 'field_2', 'Budget', 'number', NULL, 50000, '2025-01-01T00:00:00Z'),
                ('proj_2', 'field_1', 'Priority', 'enum', 'Medium', NULL, '2025-01-01T00:00:00Z')
                """
            )

            # Project updates
            connection.execute(
                """
                INSERT INTO project_updates
                (gid, project_gid, title, created_at, created_by_gid,
                 text, kind, is_deleted, synced_at)
                VALUES
                ('update_1', 'proj_1', 'Design kickoff complete', '2025-01-10T00:00:00Z',
                 'user_1', 'We have completed the initial design kickoff meeting',
                 'status_update', 0, '2025-01-15T00:00:00Z'),
                ('update_2', 'proj_2', 'API specification ready', '2025-01-12T00:00:00Z',
                 'user_2', 'The API specification has been finalized and approved',
                 'status_update', 0, '2025-01-20T00:00:00Z')
                """
            )

            # Project members
            connection.execute(
                """
                INSERT INTO project_members (project_gid, user_gid, synced_at)
                VALUES
                ('proj_1', 'user_1', '2025-01-15T00:00:00Z'),
                ('proj_1', 'user_2', '2025-01-15T00:00:00Z'),
                ('proj_2', 'user_2', '2025-01-20T00:00:00Z')
                """
            )

            connection.commit()

    def tearDown(self) -> None:
        """Clean up the test database."""
        Path(self.db_path).unlink()

    def test_resolve_project_scope_all(self) -> None:
        """Test resolving all projects when no scope is specified."""
        with self.db.connect() as connection:
            gids = _resolve_project_scope(connection, None, None, None)
            self.assertEqual(len(gids), 3)
            self.assertIn("proj_1", gids)
            self.assertIn("proj_2", gids)
            self.assertIn("proj_3", gids)

    def test_resolve_project_scope_by_team(self) -> None:
        """Test resolving projects by team."""
        with self.db.connect() as connection:
            gids = _resolve_project_scope(connection, "team_1", None, None)
            self.assertEqual(len(gids), 2)
            self.assertIn("proj_1", gids)
            self.assertIn("proj_3", gids)
            self.assertNotIn("proj_2", gids)

    def test_project_core_fields_sheet(self) -> None:
        """Test building the project core fields sheet."""
        with self.db.connect() as connection:
            title, rows = build_project_core_fields_sheet(connection, ["proj_1", "proj_2"])

        self.assertEqual(title, "Project Core Fields")
        # Header + 2 data rows
        self.assertEqual(len(rows), 3)
        # Check header
        self.assertEqual(rows[0][0], "Project GID")
        self.assertEqual(rows[0][1], "Project Name")
        # Check data
        self.assertEqual(rows[1][0], "proj_1")
        self.assertEqual(rows[1][1], "Website Redesign")
        self.assertEqual(rows[2][0], "proj_2")
        self.assertEqual(rows[2][1], "API Development")

    def test_custom_fields_sheet(self) -> None:
        """Test building the custom fields sheet."""
        with self.db.connect() as connection:
            title, rows = build_custom_fields_sheet(connection, ["proj_1", "proj_2"])

        self.assertEqual(title, "Custom Fields")
        # Header + 3 data rows (proj_1 has 2 fields, proj_2 has 1)
        self.assertEqual(len(rows), 4)
        # Check header
        self.assertEqual(rows[0][0], "Project GID")
        self.assertEqual(rows[0][4], "Field Name")
        # Check data - Budget row for proj_1
        budget_rows = [r for r in rows[1:] if r[4] == "Budget" and r[0] == "proj_1"]
        self.assertEqual(len(budget_rows), 1)
        # Value might be stored as float, so check it's close to 50000
        self.assertIn("50000", budget_rows[0][6])

    def test_status_updates_sheet(self) -> None:
        """Test building the status updates sheet."""
        with self.db.connect() as connection:
            title, rows = build_status_updates_sheet(connection, ["proj_1", "proj_2"])

        self.assertEqual(title, "Status Updates")
        # Header + 2 data rows
        self.assertEqual(len(rows), 3)
        # Check header
        self.assertEqual(rows[0][0], "Project GID")
        self.assertEqual(rows[0][4], "Title")
        # Check that we have updates (might be sorted differently based on query ordering)
        gids = [row[0] for row in rows[1:]]
        self.assertIn("proj_1", gids)
        self.assertIn("proj_2", gids)

    def test_members_sheet(self) -> None:
        """Test building the members sheet."""
        with self.db.connect() as connection:
            title, rows = build_members_sheet(connection, ["proj_1", "proj_2", "proj_3"])

        self.assertEqual(title, "Members")
        # Header + 3 data rows
        self.assertEqual(len(rows), 4)
        # Check header
        self.assertEqual(rows[0][0], "Project GID")
        self.assertEqual(rows[0][3], "Member Count")
        # Check member counts (build dict for easier lookup regardless of order)
        member_counts = {row[0]: row[3] for row in rows[1:]}
        self.assertEqual(member_counts.get("proj_1"), 2)  # user_1, user_2
        self.assertEqual(member_counts.get("proj_2"), 1)  # user_2
        self.assertEqual(member_counts.get("proj_3"), 0)  # no members

    def test_field_usage_summary_sheet(self) -> None:
        """Test building the field usage summary sheet."""
        with self.db.connect() as connection:
            title, rows = build_field_usage_summary_sheet(connection, ["proj_1", "proj_2", "proj_3"])

        self.assertEqual(title, "Field Usage Summary")
        # Header + 8 field usage rows
        self.assertEqual(len(rows), 9)
        # Check header
        self.assertEqual(rows[0][0], "Field/Feature")
        self.assertEqual(rows[0][1], "Projects with Data")
        # Check some field usage data
        # start_on: proj_1 has it (1/3)
        self.assertEqual(rows[1][0], "start_on")
        self.assertEqual(rows[1][1], 1)
        # custom_fields: proj_1 and proj_2 have it (2/3)
        custom_fields_row = [r for r in rows if r[0] == "custom_fields"][0]
        self.assertEqual(custom_fields_row[1], 2)
        # status_updates: proj_1 and proj_2 have it (2/3)
        status_updates_row = [r for r in rows if r[0] == "status_updates"][0]
        self.assertEqual(status_updates_row[1], 2)

    def test_empty_project_scope(self) -> None:
        """Test sheets with empty project scope."""
        with self.db.connect() as connection:
            title, rows = build_project_core_fields_sheet(connection, [])

        self.assertEqual(title, "Project Core Fields")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "No projects found")
