from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.export_project_details_report import export_project_details


class ExportProjectDetailsReportTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def test_exports_snapshot_and_actual_task_counts(self) -> None:
        sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.database.upsert_team(self.connection, {"gid": "team_1", "name": "Team One"}, sync_time)
        self.database.upsert_user(
            self.connection,
            {
                "gid": "owner_1",
                "name": "Olivia Owner",
                "resource_type": "user",
                "email": "olivia@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "creator_a",
                "name": "Adam Active",
                "resource_type": "user",
                "email": "adam@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.replace_user_atlassian_accounts(
            self.connection,
            [
                {
                    "gid": "owner_1",
                    "atlassianAccountId": "atl-owner-1",
                    "atlassianEmail": "olivia.atl@example.com",
                    "atlassianStatus": "active",
                },
                {
                    "gid": "creator_a",
                    "atlassianAccountId": "atl-creator-a",
                    "atlassianEmail": "adam.atl@example.com",
                    "atlassianStatus": "active",
                },
            ],
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_1",
                "name": "Project One",
                "team": {"gid": "team_1", "name": "Team One"},
                "owner": {"gid": "owner_1", "name": "Olivia Owner"},
                "num_tasks": 12,
                "num_incomplete_tasks": 5,
                "num_completed_tasks": 7,
                "task_counts_captured_at": "2025-01-01T12:00:00Z",
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_2",
                "name": "Project Two",
                "team": {"gid": "team_1", "name": "Team One"},
                "num_tasks": 0,
                "num_incomplete_tasks": 0,
                "num_completed_tasks": 0,
                "task_counts_captured_at": "2025-01-01T13:00:00Z",
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_1",
                "name": "Task One",
                "created_at": "2025-01-01T09:00:00Z",
                "modified_at": "2025-01-01T10:00:00Z",
                "completed_at": "2025-01-01T11:00:00Z",
                "created_by": {"gid": "creator_a"},
                "memberships": [{"project": {"gid": "proj_1", "name": "Project One"}}],
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_2",
                "name": "Task Two",
                "created_at": "2025-01-02T09:30:00Z",
                "modified_at": "2025-01-02T10:30:00Z",
                "completed_at": None,
                "created_by": {"gid": "creator_a"},
                "memberships": [{"project": {"gid": "proj_1", "name": "Project One"}}],
            },
            sync_time,
        )
        self.connection.commit()

        output_path = export_project_details(
            str(self.db_path),
            self.workspace / "exports",
            filename="projects.csv",
            workspace_gid="ws_1",
        )

        self.assertTrue(output_path.exists())
        with output_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 2)
        project_one = next(row for row in rows if row["gid"] == "proj_1")
        self.assertEqual(project_one["project_url"], "https://app.asana.com/1/ws_1/project/proj_1")
        self.assertEqual(project_one["team_name"], "Team One")
        self.assertEqual(project_one["team_url"], "https://app.asana.com/0/team_1/")
        self.assertEqual(project_one["num_tasks"], "12")
        self.assertEqual(project_one["num_incomplete_tasks"], "5")
        self.assertEqual(project_one["num_completed_tasks"], "7")
        self.assertEqual(project_one["task_counts_captured_at"], "2025-01-01")
        self.assertEqual(project_one["last_task_created_at"], "2025-01-02")
        self.assertEqual(project_one["last_task_resolved_at"], "2025-01-01")
        self.assertEqual(project_one["last_task_updated_at"], "2025-01-02")
        self.assertEqual(project_one["actual_task_count"], "2")
        self.assertEqual(project_one["actual_task_data_present"], "true")
        self.assertEqual(project_one["owner_name"], "Olivia Owner")
        self.assertEqual(project_one["owner_email"], "olivia@example.com")
        self.assertEqual(project_one["owner_atlassian_status"], "active")
        # Active project owner becomes the effective owner.
        self.assertEqual(project_one["effective_owner_source"], "project_owner")
        self.assertEqual(project_one["effective_owner_name"], "Olivia Owner")
        self.assertEqual(project_one["effective_owner_email"], "olivia@example.com")
        self.assertEqual(project_one["effective_owner_atlassian_status"], "active")
        self.assertEqual(project_one["top_task_creator_name"], "Adam Active")
        self.assertEqual(project_one["top_task_creator_email"], "adam@example.com")
        self.assertEqual(project_one["top_task_creator_atlassian_status"], "active")
        self.assertEqual(project_one["top_task_creator_task_count"], "2")
        # Dropped columns and final-column ordering.
        self.assertNotIn("resource_subtype", project_one)
        self.assertNotIn("owner_gid", project_one)
        self.assertEqual(list(project_one)[-1], "task_counts_captured_at")

        project_two = next(row for row in rows if row["gid"] == "proj_2")
        self.assertEqual(project_two["last_task_created_at"], "")
        self.assertEqual(project_two["last_task_resolved_at"], "")
        self.assertEqual(project_two["last_task_updated_at"], "")
        self.assertEqual(project_two["actual_task_count"], "")
        self.assertEqual(project_two["actual_task_data_present"], "false")
        self.assertEqual(project_two["top_task_creator_name"], "")
        self.assertEqual(project_two["top_task_creator_task_count"], "")

    def test_top_task_creator_picks_active_atlassian_user(self) -> None:
        sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.database.upsert_team(self.connection, {"gid": "team_1", "name": "Team One"}, sync_time)
        self.database.upsert_user(
            self.connection,
            {
                "gid": "active_low",
                "name": "Active Low",
                "resource_type": "user",
                "email": "active.low@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "inactive_high",
                "name": "Inactive High",
                "resource_type": "user",
                "email": "inactive.high@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.replace_user_atlassian_accounts(
            self.connection,
            [
                {
                    "gid": "active_low",
                    "atlassianAccountId": "atl-active-low",
                    "atlassianEmail": "active.low.atl@example.com",
                    "atlassianStatus": "active",
                },
                {
                    "gid": "inactive_high",
                    "atlassianAccountId": "atl-inactive-high",
                    "atlassianEmail": "inactive.high.atl@example.com",
                    "atlassianStatus": "inactive",
                },
            ],
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_1",
                "name": "Project One",
                "team": {"gid": "team_1", "name": "Team One"},
                "num_tasks": 3,
                "task_counts_captured_at": "2025-01-01T12:00:00Z",
            },
            sync_time,
        )
        # Inactive user created more tasks but must be excluded from the pick.
        for gid, creator in (("t_inactive_1", "inactive_high"), ("t_inactive_2", "inactive_high"), ("t_active_1", "active_low")):
            self.database.upsert_task(
                self.connection,
                {
                    "gid": gid,
                    "name": gid,
                    "created_by": {"gid": creator},
                    "memberships": [{"project": {"gid": "proj_1", "name": "Project One"}}],
                },
                sync_time,
            )
        self.connection.commit()

        output_path = export_project_details(
            str(self.db_path),
            self.workspace / "exports",
            filename="projects.csv",
            workspace_gid="ws_1",
        )

        with output_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        project_one = next(row for row in rows if row["gid"] == "proj_1")
        self.assertEqual(project_one["top_task_creator_name"], "Active Low")
        self.assertEqual(project_one["top_task_creator_email"], "active.low@example.com")
        self.assertEqual(project_one["top_task_creator_atlassian_status"], "active")
        self.assertEqual(project_one["top_task_creator_task_count"], "1")
        # No project owner, so the effective owner falls back to the top active creator.
        self.assertEqual(project_one["effective_owner_source"], "task_creator")
        self.assertEqual(project_one["effective_owner_name"], "Active Low")
        self.assertEqual(project_one["effective_owner_email"], "active.low@example.com")
        self.assertEqual(project_one["effective_owner_atlassian_status"], "active")

    def test_exports_only_selected_team(self) -> None:
        sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self.database.upsert_team(self.connection, {"gid": "team_1", "name": "Team One"}, sync_time)
        self.database.upsert_team(self.connection, {"gid": "team_2", "name": "Team Two"}, sync_time)
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_1",
                "name": "Project One",
                "team": {"gid": "team_1", "name": "Team One"},
                "num_tasks": 1,
                "task_counts_captured_at": "2025-01-01T12:00:00Z",
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_2",
                "name": "Project Two",
                "team": {"gid": "team_2", "name": "Team Two"},
                "num_tasks": 2,
                "task_counts_captured_at": "2025-01-01T12:00:00Z",
            },
            sync_time,
        )
        self.connection.commit()

        output_path = export_project_details(
            str(self.db_path),
            self.workspace / "exports",
            filename="projects_team_1.csv",
            team_gid="team_1",
            workspace_gid="ws_1",
        )

        with output_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual([row["gid"] for row in rows], ["proj_1"])
