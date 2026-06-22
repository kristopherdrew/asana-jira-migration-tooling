from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.export_team_project_jira_csv import (
    EPIC_INSTRUCTIONS,
    TASK_INSTRUCTIONS,
    export_team_project_jira_csv,
)


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
        self.database.replace_user_atlassian_accounts(
            self.connection,
            [
                {
                    "gid": "admin_a",
                    "atlassianAccountId": "atl-admin-a",
                    "atlassianEmail": "alice.atl@example.com",
                    "atlassianStatus": "active",
                },
                {
                    "gid": "admin_b",
                    "atlassianAccountId": "atl-admin-b",
                    "atlassianEmail": "bob.atl@example.com",
                    "atlassianStatus": "deactivated",
                },
                {
                    "gid": "owner_alpha",
                    "atlassianAccountId": "atl-owner-alpha",
                    "atlassianEmail": "alpha.atl@example.com",
                    "atlassianStatus": "active",
                },
                {
                    "gid": "owner_zeta",
                    "atlassianAccountId": "atl-owner-zeta",
                    "atlassianEmail": "zeta.atl@example.com",
                    "atlassianStatus": "active",
                },
            ],
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
            raw_rows = list(csv.reader(handle))

        header = raw_rows[0]
        # Atlassian receives the owner source and the last-active label as two
        # separate columns that both share the "Labels" header.
        self.assertEqual(header.count("Labels"), 2)
        # Instructions sits immediately before Description.
        self.assertEqual(header.index("Instructions"), header.index("Description") - 1)
        owner_source_idx, last_active_idx = [i for i, name in enumerate(header) if name == "Labels"]
        # dict(zip(...)) collapses the duplicate "Labels" header to the last
        # (last-active) column, matching the dedicated index lookups below.
        rows = [dict(zip(header, values)) for values in raw_rows[1:]]
        owner_source_labels = [values[owner_source_idx] for values in raw_rows[1:]]

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
        self.assertEqual(alpha_epic["Owner"], "Alice Admin")
        self.assertEqual(alpha_epic["Owner Email"], "alice@example.com")
        self.assertEqual(alpha_epic["Owner Atlassian ID"], "atl-admin-a")
        self.assertEqual(alpha_epic["Owner Atlassian Status"], "active")
        self.assertEqual(owner_source_labels[0], "team_admin")
        self.assertEqual(alpha_epic["is_personal_project"], "")
        self.assertEqual(alpha_epic["Parent Id"], "")
        self.assertEqual(alpha_epic["Project Status"], "")
        self.assertEqual(alpha_epic["Labels"], "")
        self.assertEqual(alpha_epic["Instructions"], EPIC_INSTRUCTIONS)
        alpha_description = _description_to_map(alpha_epic["Description"])
        self.assertNotIn("team_url", alpha_description)
        self.assertEqual(alpha_description["project_count_total"], "3")
        self.assertEqual(alpha_description["project_count_active"], "2")
        self.assertEqual(alpha_description["project_count_archived"], "1")
        self.assertNotIn("project_task_count_total", alpha_description)
        self.assertEqual(alpha_description["actual_task_count_total"], "3")
        self.assertNotIn("first_admin_gid", alpha_description)
        self.assertEqual(alpha_description["first_admin_email"], "alice@example.com")
        self.assertEqual(alpha_description["additional_admin_emails"], "bob.atl@example.com")
        self.assertEqual(alpha_description["effective_admin"], "Alice Admin")
        self.assertNotIn("effective_admin_atlassian_status", alpha_description)
        self.assertNotIn("effective_admin_atlassian_id", alpha_description)
        self.assertNotIn("effective_admin_atlassian_email", alpha_description)
        self.assertEqual(alpha_description["effective_admin_source"], "team_admin")
        self.assertEqual(alpha_description["project_owner"], "Alpha Owner")
        self.assertNotIn("project_owner_atlassian_id", alpha_description)
        self.assertNotIn("project_owner_atlassian_email", alpha_description)
        self.assertEqual(alpha_description["project_owner_project_count"], "2")
        self.assertNotIn("is_personal_project", alpha_description)

        beta_epic = rows[1]
        self.assertEqual(beta_epic["Owner"], "Alpha Owner")
        self.assertEqual(beta_epic["Owner Email"], "alpha@example.com")
        self.assertEqual(beta_epic["Owner Atlassian ID"], "atl-owner-alpha")
        self.assertEqual(owner_source_labels[1], "project_owner")
        self.assertEqual(beta_epic["is_personal_project"], "")
        beta_description = _description_to_map(beta_epic["Description"])
        self.assertEqual(beta_description["effective_admin"], "Alpha Owner")
        self.assertEqual(beta_description["effective_admin_source"], "project_owner")
        self.assertEqual(beta_description["project_owner"], "Alpha Owner")
        self.assertEqual(beta_description["project_owner_project_count"], "1")
        self.assertEqual(beta_description["task_creator"], "")

        no_team_epic = rows[2]
        self.assertEqual(no_team_epic["Summary"], "No Team")
        self.assertEqual(no_team_epic["Owner"], "")
        self.assertEqual(no_team_epic["Instructions"], EPIC_INSTRUCTIONS)
        self.assertEqual(no_team_epic["Description"], "")

        alpha_task = rows[3]
        self.assertEqual(alpha_task["Parent Id"], "1")
        self.assertEqual(alpha_task["Owner"], "Alpha Owner")
        self.assertEqual(alpha_task["Owner Email"], "alpha.atl@example.com")
        self.assertEqual(alpha_task["Owner Atlassian ID"], "atl-owner-alpha")
        self.assertEqual(alpha_task["Owner Atlassian Status"], "active")
        self.assertEqual(owner_source_labels[3], "project_owner")
        self.assertEqual(alpha_task["is_personal_project"], "false")
        self.assertEqual(alpha_task["Project Status"], "Active")
        self.assertEqual(alpha_task["Labels"], "last_active_2025")
        self.assertEqual(alpha_task["Instructions"], TASK_INSTRUCTIONS)
        alpha_task_description = _description_to_map(alpha_task["Description"])
        self.assertEqual(alpha_task_description["project_url"], "https://app.asana.com/1/ws_1/project/proj_a1")
        self.assertNotIn("team_gid", alpha_task_description)
        self.assertNotIn("team_name", alpha_task_description)
        self.assertNotIn("team_url", alpha_task_description)
        self.assertNotIn("privacy_setting", alpha_task_description)
        self.assertEqual(alpha_task_description["created_at"], "2025-01-14")
        self.assertEqual(alpha_task_description["completed"], "false")
        self.assertNotIn("is_personal_project", alpha_task_description)
        self.assertNotIn("num_tasks", alpha_task_description)
        self.assertEqual(alpha_task_description["actual_task_count"], "2")
        self.assertEqual(alpha_task_description["owner_name"], "Alpha Owner")
        self.assertEqual(alpha_task_description["owner_email"], "alpha@example.com")
        self.assertEqual(alpha_task_description["owner_atlassian_status"], "active")
        self.assertEqual(alpha_task_description["effective_owner_source"], "project_owner")
        self.assertEqual(alpha_task_description["effective_owner_name"], "Alpha Owner")
        self.assertEqual(alpha_task_description["effective_owner_email"], "alpha@example.com")
        self.assertEqual(alpha_task_description["effective_owner_atlassian_status"], "active")
        self.assertEqual(alpha_task_description["top_task_creator_name"], "")
        self.assertEqual(alpha_task_description["top_task_creator_task_count"], "")

        beta_task = rows[4]
        self.assertEqual(beta_task["Parent Id"], "1")
        self.assertEqual(beta_task["Owner"], "Zeta Owner")
        self.assertEqual(beta_task["Owner Email"], "zeta.atl@example.com")
        self.assertEqual(beta_task["Owner Atlassian ID"], "atl-owner-zeta")
        self.assertEqual(beta_task["is_personal_project"], "false")
        self.assertEqual(beta_task["Project Status"], "Archived")
        self.assertEqual(beta_task["Labels"], "last_active_2024")

        gamma_task = rows[5]
        self.assertEqual(gamma_task["Parent Id"], "2")

        omega_task = rows[6]
        self.assertEqual(omega_task["Parent Id"], "2")

        loose_task = rows[7]
        self.assertEqual(loose_task["Parent Id"], "3")
        self.assertEqual(loose_task["Owner"], "Display Owner")
        self.assertEqual(loose_task["Owner Email"], "")
        self.assertEqual(loose_task["Owner Atlassian ID"], "")
        self.assertEqual(loose_task["Owner Atlassian Status"], "")
        self.assertEqual(owner_source_labels[7], "project_owner")
        self.assertEqual(loose_task["is_personal_project"], "false")
        self.assertEqual(loose_task["Labels"], "last_active_2022")
        loose_description = _description_to_map(loose_task["Description"])
        self.assertNotIn("team_gid", loose_description)
        self.assertNotIn("team_name", loose_description)
        self.assertNotIn("team_url", loose_description)
        self.assertEqual(loose_description["project_url"], "https://app.asana.com/1/ws_1/project/proj_nt")
        self.assertEqual(loose_description["owner_name"], "Display Owner")
        self.assertEqual(loose_description["owner_atlassian_status"], "")
        self.assertEqual(loose_description["effective_owner_source"], "")
        self.assertEqual(loose_description["effective_owner_name"], "")
        self.assertEqual(loose_description["top_task_creator_name"], "")

        self.assertNotIn("Unexported Project", [row["Summary"] for row in rows])

    def test_prefers_active_admin_then_task_creator_when_top_owner_is_inactive(self) -> None:
        sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        self.database.upsert_team(self.connection, {"gid": "team_c", "name": "Gamma Team"}, sync_time)
        self.database.upsert_team(self.connection, {"gid": "team_d", "name": "Delta Team"}, sync_time)

        self.database.upsert_user(
            self.connection,
            {
                "gid": "admin_inactive",
                "name": "Inactive Admin",
                "resource_type": "user",
                "email": "inactive.admin@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "admin_active",
                "name": "Active Admin",
                "resource_type": "user",
                "email": "active.admin@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "owner_inactive",
                "name": "Inactive Owner",
                "resource_type": "user",
                "email": "inactive.owner@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "owner_active",
                "name": "Active Owner",
                "resource_type": "user",
                "email": "active.owner@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "creator_active",
                "name": "Active Creator",
                "resource_type": "user",
                "email": "creator@example.com",
                "is_active": True,
                "workspaces": [],
            },
            sync_time,
        )
        self.database.replace_user_atlassian_accounts(
            self.connection,
            [
                {
                    "gid": "admin_inactive",
                    "atlassianAccountId": "atl-admin-inactive",
                    "atlassianEmail": "inactive.admin.atl@example.com",
                    "atlassianStatus": "deactivated",
                },
                {
                    "gid": "admin_active",
                    "atlassianAccountId": "atl-admin-active",
                    "atlassianEmail": "active.admin.atl@example.com",
                    "atlassianStatus": "active",
                },
                {
                    "gid": "owner_inactive",
                    "atlassianAccountId": "atl-owner-inactive",
                    "atlassianEmail": "inactive.owner.atl@example.com",
                    "atlassianStatus": "deactivated",
                },
                {
                    "gid": "owner_active",
                    "atlassianAccountId": "atl-owner-active",
                    "atlassianEmail": "active.owner.atl@example.com",
                    "atlassianStatus": "active",
                },
                {
                    "gid": "creator_active",
                    "atlassianAccountId": "atl-creator-active",
                    "atlassianEmail": "creator.atl@example.com",
                    "atlassianStatus": "active",
                },
            ],
            sync_time,
        )

        self.database.upsert_team_members(
            self.connection,
            "team_c",
            [
                {
                    "user": {"gid": "admin_inactive", "name": "Inactive Admin", "email": "inactive.admin@example.com", "is_active": True},
                    "role": "member",
                    "is_admin": True,
                },
                {
                    "user": {"gid": "admin_active", "name": "Active Admin", "email": "active.admin@example.com", "is_active": True},
                    "role": "member",
                    "is_admin": True,
                },
            ],
            sync_time,
        )

        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_c1",
                "name": "Gamma Project",
                "team": {"gid": "team_c", "name": "Gamma Team"},
                "owner": {"gid": "owner_inactive", "name": "Inactive Owner"},
                "archived": False,
                "num_tasks": 1,
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_d1",
                "name": "Delta One",
                "team": {"gid": "team_d", "name": "Delta Team"},
                "owner": {"gid": "owner_inactive", "name": "Inactive Owner"},
                "archived": False,
                "num_tasks": 1,
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_d2",
                "name": "Delta Two",
                "team": {"gid": "team_d", "name": "Delta Team"},
                "owner": {"gid": "owner_inactive", "name": "Inactive Owner"},
                "archived": False,
                "num_tasks": 1,
            },
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_d3",
                "name": "Delta Three",
                "team": {"gid": "team_d", "name": "Delta Team"},
                "owner": {"gid": "owner_active", "name": "Active Owner"},
                "archived": False,
                "num_tasks": 1,
            },
            sync_time,
        )

        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_c1",
                "name": "Task C1",
                "created_by": {"gid": "creator_active", "name": "Active Creator"},
                "memberships": [{"project": {"gid": "proj_c1", "name": "Gamma Project"}}],
                "workspace": {"gid": "ws_1", "name": "Workspace"},
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_d1",
                "name": "Task D1",
                "created_by": {"gid": "creator_active", "name": "Active Creator"},
                "memberships": [{"project": {"gid": "proj_d1", "name": "Delta One"}}],
                "workspace": {"gid": "ws_1", "name": "Workspace"},
            },
            sync_time,
        )
        self.connection.commit()

        output_path = export_team_project_jira_csv(str(self.db_path), self.workspace / "exports", filename="fallback.csv")

        with output_path.open("r", newline="", encoding="utf-8") as handle:
            raw_rows = list(csv.reader(handle))

        header = raw_rows[0]
        self.assertEqual(header.count("Labels"), 2)
        owner_source_idx = [i for i, name in enumerate(header) if name == "Labels"][0]
        rows = [dict(zip(header, values)) for values in raw_rows[1:]]
        owner_source_by_summary = {
            values[header.index("Summary")]: values[owner_source_idx] for values in raw_rows[1:]
        }

        gamma_epic = next(row for row in rows if row["Summary"] == "Gamma Team")
        self.assertEqual(gamma_epic["Owner"], "Active Admin")
        self.assertEqual(gamma_epic["Owner Email"], "active.admin@example.com")
        self.assertEqual(gamma_epic["Owner Atlassian Status"], "active")
        self.assertEqual(owner_source_by_summary["Gamma Team"], "team_admin")
        gamma_description = _description_to_map(gamma_epic["Description"])
        self.assertEqual(gamma_description["effective_admin_source"], "team_admin")

        delta_epic = next(row for row in rows if row["Summary"] == "Delta Team")
        self.assertEqual(delta_epic["Owner"], "Active Creator")
        self.assertEqual(delta_epic["Owner Email"], "creator@example.com")
        self.assertEqual(owner_source_by_summary["Delta Team"], "task_creator")
        delta_description = _description_to_map(delta_epic["Description"])
        self.assertEqual(delta_description["project_owner"], "Inactive Owner")
        self.assertEqual(delta_description["project_owner_atlassian_status"], "deactivated")
        self.assertEqual(delta_description["task_creator"], "Active Creator")
        self.assertEqual(delta_description["effective_admin_source"], "task_creator")

        # proj_c1 owner is deactivated but has an active top task creator, so the
        # project-level effective owner falls back to that creator.
        gamma_task = next(row for row in rows if row["Summary"] == "Gamma Project")
        gamma_task_description = _description_to_map(gamma_task["Description"])
        self.assertEqual(gamma_task_description["owner_name"], "Inactive Owner")
        self.assertEqual(gamma_task_description["owner_atlassian_status"], "deactivated")
        self.assertEqual(gamma_task_description["effective_owner_source"], "task_creator")
        self.assertEqual(gamma_task_description["effective_owner_name"], "Active Creator")
        self.assertEqual(gamma_task_description["effective_owner_email"], "creator@example.com")
        self.assertEqual(gamma_task_description["effective_owner_atlassian_status"], "active")
        self.assertEqual(gamma_task_description["top_task_creator_name"], "Active Creator")
        self.assertEqual(gamma_task_description["top_task_creator_task_count"], "1")
