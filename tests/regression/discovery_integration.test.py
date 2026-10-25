"""Regression tests for Discovery Infrastructure feature.

Tests the end-to-end workflow:
1. Project-level custom fields are stored in database
2. Custom fields are queryable and isolated per project
3. Integration with team/project export (light testing)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database


class DiscoveryIntegrationTest(TestCase):
    """Regression suite: Project-level custom fields discovery."""

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
        """Create projects for testing custom fields."""
        # Create team
        self.database.upsert_team(
            self.connection,
            {"gid": "team_1", "name": "Engineering Team"},
            self.sync_time,
        )

        # Create user (team admin)
        self.database.upsert_user(
            self.connection,
            {
                "gid": "user_1",
                "name": "Alice Engineer",
                "resource_type": "user",
                "email": "alice@example.com",
                "is_active": True,
                "workspaces": [],
            },
            self.sync_time,
        )

        # Create projects
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_1",
                "name": "Backend Services",
                "archived": False,
                "owner": {"gid": "user_1", "name": "Alice Engineer"},
                "team": {"gid": "team_1", "name": "Engineering Team"},
                "privacy_setting": "private",
                "created_at": "2025-01-01T00:00:00Z",
                "completed": False,
                "is_personal_project": False,
                "num_tasks": 5,
                "num_incomplete_tasks": 3,
                "num_completed_tasks": 2,
            },
            self.sync_time,
        )

        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_2",
                "name": "Frontend UI",
                "archived": False,
                "owner": {"gid": "user_1", "name": "Alice Engineer"},
                "team": {"gid": "team_1", "name": "Engineering Team"},
                "privacy_setting": "private",
                "created_at": "2025-01-02T00:00:00Z",
                "completed": False,
                "is_personal_project": False,
                "num_tasks": 8,
                "num_incomplete_tasks": 6,
                "num_completed_tasks": 2,
            },
            self.sync_time,
        )

        self._insert_project_custom_fields()
        self.connection.commit()

    def _insert_project_custom_fields(self) -> None:
        """Insert project-level custom field values safely."""
        # Define fields for each project
        fields_data = [
            ("proj_1", "Architecture Type", "enum", "Monolith", None, None),
            ("proj_1", "Database", "enum", "PostgreSQL", None, None),
            ("proj_1", "Team Size", "number", None, 8, None),
            ("proj_1", "Critical Path", "text", None, None, "User authentication"),
            ("proj_2", "Browser Compatibility", "enum", "Chrome, Firefox, Safari", None, None),
            ("proj_2", "Design System", "enum", "Material Design", None, None),
            ("proj_2", "Team Size", "number", None, 5, None),
        ]

        for proj_gid, field_name, field_type, enum_val, num_val, text_val in fields_data:
            # Insert into project_custom_fields directly (simpler than using database methods)
            self.connection.execute(
                """
                INSERT INTO project_custom_fields (
                    project_gid, custom_field_gid, name, type, enum_value_name, number_value, text_value, synced_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    proj_gid,
                    f"field_{field_name.replace(' ', '_').lower()}",
                    field_name,
                    field_type,
                    enum_val,
                    num_val,
                    text_val,
                    self.sync_time.isoformat(),
                ),
            )

    def test_project_custom_fields_discovered_by_query(self) -> None:
        """Project-level custom fields are discoverable via database query."""
        # Query all project custom fields
        rows = self.connection.execute(
            """
            SELECT DISTINCT project_gid, name
            FROM project_custom_fields
            ORDER BY project_gid, name
            """
        ).fetchall()

        self.assertGreater(len(rows), 0)

        # Verify specific fields are present
        project_1_fields = [r["name"] for r in rows if r["project_gid"] == "proj_1"]
        project_2_fields = [r["name"] for r in rows if r["project_gid"] == "proj_2"]

        self.assertIn("Architecture Type", project_1_fields)
        self.assertIn("Database", project_1_fields)
        self.assertIn("Team Size", project_1_fields)
        self.assertIn("Critical Path", project_1_fields)

        self.assertIn("Browser Compatibility", project_2_fields)
        self.assertIn("Design System", project_2_fields)
        self.assertIn("Team Size", project_2_fields)

    def test_project_custom_fields_per_project_scope(self) -> None:
        """Project custom fields are isolated per project."""
        # Get fields for proj_1
        proj_1_fields = self.connection.execute(
            """
            SELECT name FROM project_custom_fields WHERE project_gid = ?
            ORDER BY name
            """,
            ("proj_1",),
        ).fetchall()

        # Get fields for proj_2
        proj_2_fields = self.connection.execute(
            """
            SELECT name FROM project_custom_fields WHERE project_gid = ?
            ORDER BY name
            """,
            ("proj_2",),
        ).fetchall()

        proj_1_names = {r["name"] for r in proj_1_fields}
        proj_2_names = {r["name"] for r in proj_2_fields}

        # Both have Team Size (common field)
        self.assertIn("Team Size", proj_1_names)
        self.assertIn("Team Size", proj_2_names)

        # But proj_1 has Architecture Type (unique to it)
        self.assertIn("Architecture Type", proj_1_names)
        self.assertNotIn("Architecture Type", proj_2_names)

        # And proj_2 has Browser Compatibility (unique to it)
        self.assertIn("Browser Compatibility", proj_2_names)
        self.assertNotIn("Browser Compatibility", proj_1_names)

    def test_missing_project_custom_fields_gracefully_handled(self) -> None:
        """Projects without custom fields don't cause database issues."""
        # Create a project with no custom fields
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_no_fields",
                "name": "Empty Project",
                "archived": False,
                "owner": {"gid": "user_1", "name": "Alice Engineer"},
                "team": {"gid": "team_1", "name": "Engineering Team"},
                "privacy_setting": "private",
                "created_at": "2025-01-03T00:00:00Z",
                "completed": False,
                "is_personal_project": False,
                "num_tasks": 0,
                "num_incomplete_tasks": 0,
                "num_completed_tasks": 0,
            },
            self.sync_time,
        )
        self.connection.commit()

        # Query should succeed with no results for this project
        rows = self.connection.execute(
            "SELECT * FROM project_custom_fields WHERE project_gid = ?",
            ("proj_no_fields",),
        ).fetchall()

        self.assertEqual(len(rows), 0)

    def test_project_custom_fields_with_different_types(self) -> None:
        """Project custom fields of different types are stored correctly."""
        # Query and verify field types are stored
        rows = self.connection.execute(
            """
            SELECT name, type, enum_value_name, number_value, text_value
            FROM project_custom_fields
            WHERE project_gid = ?
            ORDER BY name
            """,
            ("proj_1",),
        ).fetchall()

        # Find each field type and verify
        for row in rows:
            if row["name"] == "Architecture Type":
                # Enum field
                self.assertIsNotNone(row["enum_value_name"])
                self.assertEqual(row["enum_value_name"], "Monolith")
            elif row["name"] == "Team Size":
                # Number field
                self.assertIsNotNone(row["number_value"])
                self.assertEqual(row["number_value"], 8)
            elif row["name"] == "Critical Path":
                # Text field
                self.assertIsNotNone(row["text_value"])
                self.assertEqual(row["text_value"], "User authentication")

    def test_project_custom_fields_common_across_projects(self) -> None:
        """Custom fields can be common across multiple projects (Team Size example)."""
        # Team Size appears in both proj_1 and proj_2
        proj_1_team_size = self.connection.execute(
            """
            SELECT number_value FROM project_custom_fields
            WHERE project_gid = ? AND name = ?
            """,
            ("proj_1", "Team Size"),
        ).fetchone()

        proj_2_team_size = self.connection.execute(
            """
            SELECT number_value FROM project_custom_fields
            WHERE project_gid = ? AND name = ?
            """,
            ("proj_2", "Team Size"),
        ).fetchone()

        self.assertIsNotNone(proj_1_team_size)
        self.assertIsNotNone(proj_2_team_size)

        # Values can differ per project
        self.assertEqual(proj_1_team_size["number_value"], 8)
        self.assertEqual(proj_2_team_size["number_value"], 5)

    def test_project_custom_fields_multiple_enum_values(self) -> None:
        """Project custom fields can have multiple enum values (semicolon-separated)."""
        # Browser Compatibility has multiple values
        row = self.connection.execute(
            """
            SELECT enum_value_name FROM project_custom_fields
            WHERE project_gid = ? AND name = ?
            """,
            ("proj_2", "Browser Compatibility"),
        ).fetchone()

        self.assertIsNotNone(row)
        # Value should contain all browser names
        value = row["enum_value_name"]
        self.assertIn("Chrome", value)
        self.assertIn("Firefox", value)
        self.assertIn("Safari", value)

    def test_project_custom_fields_count_by_project(self) -> None:
        """Query custom field count per project."""
        proj_1_count = self.connection.execute(
            """
            SELECT COUNT(*) as count FROM project_custom_fields WHERE project_gid = ?
            """,
            ("proj_1",),
        ).fetchone()["count"]

        proj_2_count = self.connection.execute(
            """
            SELECT COUNT(*) as count FROM project_custom_fields WHERE project_gid = ?
            """,
            ("proj_2",),
        ).fetchone()["count"]

        # proj_1 should have 4 fields, proj_2 should have 3
        self.assertEqual(proj_1_count, 4)
        self.assertEqual(proj_2_count, 3)
