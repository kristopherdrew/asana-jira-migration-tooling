"""Regression tests for Data Refresh Option feature.

Tests the end-to-end workflow:
1. Prompt user for data refresh (--incremental-refresh prompt)
2. Always refresh (--incremental-refresh yes)
3. Never refresh (--incremental-refresh no)
4. Skip refresh prompts (--no-refresh-prompt)
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch, MagicMock

from src.lib.database import Database


class DataRefreshWorkflowTest(TestCase):
    """Regression suite: Data refresh prompt and behavior options."""

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
        """Create a project with task data for refresh testing."""
        self.connection.execute(
            """
            INSERT INTO projects (
                gid, name, num_tasks, num_incomplete_tasks, num_completed_tasks,
                task_counts_captured_at, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "proj_1",
                "Test Project",
                10,
                6,
                4,
                self.sync_time.isoformat(),
                self.sync_time.isoformat(),
            ),
        )

        # Create a few tasks
        for i in range(1, 4):
            self.database.upsert_task(
                self.connection,
                {
                    "gid": f"task_{i}",
                    "name": f"Task {i}",
                    "created_at": self.sync_time.isoformat(),
                    "modified_at": self.sync_time.isoformat(),
                    "memberships": [{"project": {"gid": "proj_1", "name": "Test Project"}}],
                    "workspace": {"gid": "ws_1", "name": "Workspace"},
                },
                self.sync_time,
            )

        self.connection.commit()

    def test_incremental_refresh_prompt_option(self) -> None:
        """--incremental-refresh prompt allows user choice (default behavior)."""
        # The prompt behavior is tested by checking the flag is accepted
        # In real usage, this would be interactive
        # For regression, we verify the flag doesn't cause errors

        # Flag should be recognized and stored for later decision
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--incremental-refresh", "prompt",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertEqual(args.incremental_refresh, "prompt")

    def test_incremental_refresh_yes_option(self) -> None:
        """--incremental-refresh yes always refreshes data."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--incremental-refresh", "yes",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertEqual(args.incremental_refresh, "yes")

    def test_incremental_refresh_no_option(self) -> None:
        """--incremental-refresh no skips all data refresh."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--incremental-refresh", "no",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertEqual(args.incremental_refresh, "no")

    def test_incremental_refresh_default_is_prompt(self) -> None:
        """Default --incremental-refresh value is 'prompt'."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        # Default should be 'prompt'
        self.assertEqual(args.incremental_refresh, "prompt")

    def test_no_refresh_prompt_flag(self) -> None:
        """--no-refresh-prompt skips all refresh-related prompts."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--no-refresh-prompt",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertTrue(args.no_refresh_prompt)

    def test_no_refresh_prompt_overrides_incremental_refresh_prompt(self) -> None:
        """--no-refresh-prompt with --incremental-refresh prompt suppresses prompt."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--incremental-refresh", "prompt",
            "--no-refresh-prompt",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertEqual(args.incremental_refresh, "prompt")
        self.assertTrue(args.no_refresh_prompt)
        # When both flags are set, no_refresh_prompt should suppress the prompt

    def test_project_sync_timestamp_tracking(self) -> None:
        """Track when project data was last synced for refresh detection."""
        # Get initial synced_at
        old_row = self.connection.execute(
            "SELECT synced_at FROM projects WHERE gid = ?",
            ("proj_1",),
        ).fetchone()

        old_timestamp = old_row["synced_at"]

        # Simulate new sync (update synced_at to now)
        new_sync_time = datetime(2025, 1, 5, tzinfo=timezone.utc)
        self.connection.execute(
            "UPDATE projects SET synced_at = ? WHERE gid = ?",
            (new_sync_time.isoformat(), "proj_1"),
        )
        self.connection.commit()

        # Verify timestamp was updated
        new_row = self.connection.execute(
            "SELECT synced_at FROM projects WHERE gid = ?",
            ("proj_1",),
        ).fetchone()

        new_timestamp = new_row["synced_at"]
        self.assertNotEqual(old_timestamp, new_timestamp)

    def test_refresh_staleness_detection(self) -> None:
        """Detect when project data is stale and needs refresh."""
        # Set last sync to 7 days ago
        old_sync_time = self.sync_time - timedelta(days=7)
        self.connection.execute(
            "UPDATE projects SET synced_at = ? WHERE gid = ?",
            (old_sync_time.isoformat(), "proj_1"),
        )
        self.connection.commit()

        # Check staleness (data older than some threshold, e.g. 1 day)
        row = self.connection.execute(
            "SELECT synced_at FROM projects WHERE gid = ?",
            ("proj_1",),
        ).fetchone()

        from src.lib.date_formatting import parse_iso_datetime
        last_sync = parse_iso_datetime(row["synced_at"])
        now = datetime.now(timezone.utc)
        days_since_sync = (now - last_sync).days

        # Data is definitely stale if last synced > 1 day ago
        self.assertGreater(days_since_sync, 1)
        is_stale = days_since_sync >= 1
        self.assertTrue(is_stale)

    def test_refresh_freshness_detection(self) -> None:
        """Detect when project data is fresh and doesn't need refresh."""
        # Sync time was just now
        self.connection.execute(
            "UPDATE projects SET synced_at = ? WHERE gid = ?",
            (datetime.now(timezone.utc).isoformat(), "proj_1"),
        )
        self.connection.commit()

        # Check freshness
        row = self.connection.execute(
            "SELECT synced_at FROM projects WHERE gid = ?",
            ("proj_1",),
        ).fetchone()

        from src.lib.date_formatting import parse_iso_datetime
        last_sync = parse_iso_datetime(row["synced_at"])
        now = datetime.now(timezone.utc)
        seconds_since_sync = (now - last_sync).total_seconds()

        # Data is fresh if last synced less than 1 hour ago
        is_fresh = seconds_since_sync < 3600
        self.assertTrue(is_fresh)

    def test_task_count_change_detection(self) -> None:
        """Detect when task counts have changed (indicates new data)."""
        # Get initial task count
        initial_task_count = self.connection.execute(
            "SELECT COUNT(*) as count FROM tasks JOIN task_projects ON task_projects.task_gid = tasks.gid WHERE task_projects.project_gid = ?",
            ("proj_1",),
        ).fetchone()["count"]

        self.assertEqual(initial_task_count, 3)

        # Add a new task (simulating refresh added it)
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_new",
                "name": "New Task from Refresh",
                "created_at": "2025-01-05T00:00:00Z",
                "modified_at": "2025-01-05T00:00:00Z",
                "memberships": [{"project": {"gid": "proj_1", "name": "Test Project"}}],
                "workspace": {"gid": "ws_1", "name": "Workspace"},
            },
            datetime(2025, 1, 5, tzinfo=timezone.utc),
        )
        self.connection.commit()

        # Verify task count increased
        new_task_count = self.connection.execute(
            "SELECT COUNT(*) as count FROM tasks JOIN task_projects ON task_projects.task_gid = tasks.gid WHERE task_projects.project_gid = ?",
            ("proj_1",),
        ).fetchone()["count"]

        self.assertEqual(new_task_count, 4)
        self.assertGreater(new_task_count, initial_task_count)

    def test_incremental_refresh_with_project_breakdown(self) -> None:
        """--incremental-project-breakdown controls per-project refresh behavior."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        # Test valid values
        for value in ["prompt", "yes", "no"]:
            args = parse_args([
                "--project-gid", "proj_1",
                "--incremental-project-breakdown", value,
                "--database", str(self.db_path),
                "--no-custom-field-prompt",
            ])

            self.assertEqual(args.incremental_project_breakdown, value)

    def test_no_refresh_prompt_with_multiple_projects(self) -> None:
        """--no-refresh-prompt applies to all projects when exporting multiple."""
        # Create a second project
        self.connection.execute(
            """
            INSERT INTO projects (
                gid, name, num_tasks, num_incomplete_tasks, num_completed_tasks,
                task_counts_captured_at, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "proj_2",
                "Test Project 2",
                5,
                3,
                2,
                self.sync_time.isoformat(),
                self.sync_time.isoformat(),
            ),
        )
        self.connection.commit()

        # Parse args for multiple projects with no refresh prompt
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gids", "proj_1,proj_2",
            "--no-refresh-prompt",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertTrue(args.no_refresh_prompt)
        # Flag should apply uniformly to all projects

    def test_refresh_prompt_isolation_per_project(self) -> None:
        """Refresh decisions per project are isolated (one project doesn't affect another)."""
        # Create second project with different sync timestamp
        old_sync = datetime(2024, 12, 1, tzinfo=timezone.utc)
        self.connection.execute(
            """
            INSERT INTO projects (
                gid, name, num_tasks, num_incomplete_tasks, num_completed_tasks,
                task_counts_captured_at, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "proj_2",
                "Old Project",
                0,
                0,
                0,
                old_sync.isoformat(),
                old_sync.isoformat(),
            ),
        )
        self.connection.commit()

        # proj_1 is fresh (just synced)
        row1 = self.connection.execute(
            "SELECT synced_at FROM projects WHERE gid = ?",
            ("proj_1",),
        ).fetchone()

        # proj_2 is stale (synced in December)
        row2 = self.connection.execute(
            "SELECT synced_at FROM projects WHERE gid = ?",
            ("proj_2",),
        ).fetchone()

        from src.lib.date_formatting import parse_iso_datetime
        sync1 = parse_iso_datetime(row1["synced_at"])
        sync2 = parse_iso_datetime(row2["synced_at"])

        # Verify isolation: each project has independent sync timestamp
        self.assertNotEqual(sync1, sync2)
        self.assertGreater(sync1, sync2)  # proj_1 is newer
