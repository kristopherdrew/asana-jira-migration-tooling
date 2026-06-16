from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.export_team_project_jira_csv import export_team_project_jira_csv


def _description_to_map(description: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not description:
        return result
    for line in description.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
            value = value.lstrip()
        else:
            continue
        result[key] = value
    return result


class ExportTeamProjectJiraCsvTest(TestCase):
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

    def test_exports_single_csv_with_epics_first_and_project_parent_links(self) -> None:
        sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        self.database.upsert_team(self.connection, {"gid": "team_a", "name": "Alpha Team"}, sync_time)
        self.database.upsert_team(self.connection, {"gid": "team_b", "name": "Beta Team"}, sync_time)

        self.database.upsert_user(
            self.connection,
            {
                "gid": "admin_a",
                "name": "Alice Admin",
                "resource_type": "user",
                "email": "alice@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "admin_b",
                "name": "Bob Admin",
                "resource_type": "user",
                "email": "bob@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "owner_alpha",
                "name": "Alpha Owner",
                "resource_type": "user",
                "email": "alpha@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "owner_zeta",
                "name": "Zeta Owner",
                "resource_type": "user",
                "email": "zeta@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "owner_display",
                "name": "Display Owner",
                "resource_type": "user",
                "email": "",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )

        self.database.upsert_team_members(
            self.connection,
            "team_a",
            [
                {
                    "user": {"gid": "admin_a", "name": "Alice Admin", "email": "alice@example.com", "is_active": True},
                    "role": "member",
                    "is_admin": True,
                },
                {
                    "user": {"gid": "admin_b", "name": "Bob Admin", "email": "bob@example.com", "is_active": True},
                    "role": "member",
                    "is_admin": True,
                },
            ],
            sync_time,
        )

        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_a1",
                "name": "Alpha Project",
                "archived": False,
                "owner": {"gid": "owner_alpha", "name": "Alpha Owner"},
                "team": {"gid": "team_a", "name": "Alpha Team"},
                "privacy_setting": "private",
                "created_at": "2025-01-14T09:00:00Z",
                "completed": False,
                "completed_at": None,
                "is_personal_project": False,
                "num_tasks": 10,
                "num_incomplete_tasks": 10,
                "num_completed_tasks": 0,
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_a2",
                "name": "Beta Project",
                "archived": True,
                "owner": {"gid": "owner_zeta", "name": "Zeta Owner"},
                "team": {"gid": "team_a", "name": "Alpha Team"},
                "privacy_setting": "private",
                "created_at": "2024-12-01T09:00:00Z",
                "completed": True,
                "completed_at": "2024-12-31T09:00:00Z",
                "is_personal_project": False,
                "num_tasks": 4,
                "num_incomplete_tasks": 0,
                "num_completed_tasks": 4,
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_a3",
                "name": "Unexported Project",
                "archived": False,
                "owner": {"gid": "owner_alpha", "name": "Alpha Owner"},
                "team": {"gid": "team_a", "name": "Alpha Team"},
                "privacy_setting": "private",
                "created_at": "2024-11-01T09:00:00Z",
                "completed": False,
                "is_personal_project": False,
                "num_tasks": 1,
                "num_incomplete_tasks": 1,
                "num_completed_tasks": 0,
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_b1",
                "name": "Gamma Project",
                "archived": False,
                "owner": {"gid": "owner_alpha", "name": "Alpha Owner"},
                "team": {"gid": "team_b", "name": "Beta Team"},
                "privacy_setting": "private",
                "created_at": "2023-01-10T09:00:00Z",
                "completed": False,
                "is_personal_project": False,
                "num_tasks": 3,
                "num_incomplete_tasks": 3,
                "num_completed_tasks": 0,
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_b2",
                "name": "Omega Project",
                "archived": False,
                "owner": {"gid": "owner_zeta", "name": "Zeta Owner"},
                "team": {"gid": "team_b", "name": "Beta Team"},
                "privacy_setting": "public",
                "created_at": "2023-02-10T09:00:00Z",
                "completed": False,
                "is_personal_project": False,
                "num_tasks": 2,
                "num_incomplete_tasks": 2,
                "num_completed_tasks": 0,
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_nt",
                "name": "Loose Project",
                "archived": False,
                "owner": {"gid": "owner_display", "name": "Display Owner"},
                "privacy_setting": "private",
                "created_at": "2022-05-10T09:00:00Z",
                "completed": False,
                "is_personal_project": False,
                "num_tasks": 1,
                "num_incomplete_tasks": 1,
                "num_completed_tasks": 0,
            },
            sync_time,
        )

        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_a1",
                "name": "Task A1",
                "created_at": "2025-02-01T09:00:00Z",
                "modified_at": "2025-03-05T09:00:00Z",
                "completed_at": None,
                "memberships": [{"project": {"gid": "proj_a1", "name": "Alpha Project"}}],
                "workspace": {"gid": "ws_1", "name": "Workspace"},
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_a2",
                "name": "Task A2",
                "created_at": "2025-02-10T09:00:00Z",
                "modified_at": "2025-03-06T09:00:00Z",
                "completed_at": None,
                "memberships": [{"project": {"gid": "proj_a1", "name": "Alpha Project"}}],
                "workspace": {"gid": "ws_1", "name": "Workspace"},
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_a3",
                "name": "Task A3",
                "created_at": "2024-12-15T09:00:00Z",
                "modified_at": "2024-12-16T09:00:00Z",
                "completed_at": "2024-12-17T09:00:00Z",
                "memberships": [{"project": {"gid": "proj_a2", "name": "Beta Project"}}],
                "workspace": {"gid": "ws_1", "name": "Workspace"},
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_b1",
                "name": "Task B1",
                "created_at": "2023-01-11T09:00:00Z",
                "modified_at": "2023-01-12T09:00:00Z",
                "completed_at": None,
                "memberships": [{"project": {"gid": "proj_b1", "name": "Gamma Project"}}],
                "workspace": {"gid": "ws_1", "name": "Workspace"},
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_b2",
                "name": "Task B2",
                "created_at": "2023-02-11T09:00:00Z",
                "modified_at": "2023-02-12T09:00:00Z",
                "completed_at": None,
                "memberships": [{"project": {"gid": "proj_b2", "name": "Omega Project"}}],
                "workspace": {"gid": "ws_1", "name": "Workspace"},
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_nt",
                "name": "Task NT",
                "created_at": "2022-05-11T09:00:00Z",
                "modified_at": "2022-05-12T09:00:00Z",
                "completed_at": None,
                "memberships": [{"project": {"gid": "proj_nt", "name": "Loose Project"}}],
                "workspace": {"gid": "ws_1", "name": "Workspace"},
            },
            sync_time,
        )
        self.connection.commit()

        output_path = export_team_project_jira_csv(str(self.db_path), self.workspace / "exports", filename="report.csv")

        with output_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))

        self.assertEqual([row["Issue Type"] for row in rows], ["Epic", "Epic", "Epic", "Task", "Task", "Task", "Task", "Task"])
        self.assertEqual([row["Task ID"] for row in rows], ["1", "2", "3", "4", "5", "6", "7", "8"])
        self.assertEqual([row["Summary"] for row in rows], [
            "Alpha Team",
            "Beta Team",
            "No Team",
            "Alpha Project",
            "Beta Project",
            "Gamma Project",
            "Omega Project",
            "Loose Project",
        ])

        alpha_epic = rows[0]
        self.assertEqual(alpha_epic["Owner"], "alice@example.com")
        self.assertEqual(alpha_epic["Parent Id"], "")
        self.assertEqual(alpha_epic["Project Status"], "")
        self.assertEqual(alpha_epic["Labels"], "")
        alpha_description = _description_to_map(alpha_epic["Description"])
        self.assertEqual(alpha_description["team_url"], "https://app.asana.com/0/team_a/")
        self.assertEqual(alpha_description["project_count_total"], "3")
        self.assertEqual(alpha_description["project_count_active"], "2")
        self.assertEqual(alpha_description["project_count_archived"], "1")
        self.assertEqual(alpha_description["project_task_count_total"], "15")
        self.assertEqual(alpha_description["actual_task_count_total"], "3")
        self.assertEqual(alpha_description["first_admin_email"], "alice@example.com")
        self.assertEqual(alpha_description["additional_admin_emails"], "bob@example.com")

        beta_epic = rows[1]
        self.assertEqual(beta_epic["Owner"], "alpha@example.com")
        beta_description = _description_to_map(beta_epic["Description"])
        self.assertEqual(beta_description["first_admin_email"], "alpha@example.com")
        self.assertEqual(beta_description["additional_admin_emails"], "zeta@example.com")

        no_team_epic = rows[2]
        self.assertEqual(no_team_epic["Summary"], "No Team")
        self.assertEqual(no_team_epic["Owner"], "")
        self.assertEqual(no_team_epic["Description"], "")

        alpha_task = rows[3]
        self.assertEqual(alpha_task["Parent Id"], "1")
        self.assertEqual(alpha_task["Project Status"], "Active")
        self.assertEqual(alpha_task["Labels"], "last_active_2025")
        alpha_task_description = _description_to_map(alpha_task["Description"])
        self.assertEqual(alpha_task_description["project_url"], "https://app.asana.com/1/ws_1/project/proj_a1")
        self.assertEqual(alpha_task_description["team_gid"], "team_a")
        self.assertEqual(alpha_task_description["team_name"], "Alpha Team")
        self.assertEqual(alpha_task_description["privacy_setting"], "private")
        self.assertEqual(alpha_task_description["created_at"], "2025-01-14")
        self.assertEqual(alpha_task_description["completed"], "false")
        self.assertEqual(alpha_task_description["num_tasks"], "10")
        self.assertEqual(alpha_task_description["actual_task_count"], "2")

        beta_task = rows[4]
        self.assertEqual(beta_task["Parent Id"], "1")
        self.assertEqual(beta_task["Project Status"], "Archived")
        self.assertEqual(beta_task["Labels"], "last_active_2024")

        gamma_task = rows[5]
        self.assertEqual(gamma_task["Parent Id"], "2")

        omega_task = rows[6]
        self.assertEqual(omega_task["Parent Id"], "2")

        loose_task = rows[7]
        self.assertEqual(loose_task["Parent Id"], "3")
        self.assertEqual(loose_task["Owner"], "Display Owner")
        self.assertEqual(loose_task["Labels"], "last_active_2022")
        loose_description = _description_to_map(loose_task["Description"])
        self.assertEqual(loose_description["team_gid"], "")
        self.assertEqual(loose_description["team_name"], "")
        self.assertEqual(loose_description["team_url"], "")
        self.assertEqual(loose_description["project_url"], "https://app.asana.com/1/ws_1/project/proj_nt")

        self.assertNotIn("Unexported Project", [row["Summary"] for row in rows])
