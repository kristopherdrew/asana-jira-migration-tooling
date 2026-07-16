from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from src.lib.team_analysis import build_team_analysis


class BuildTeamAnalysisTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.connection.close()

    def test_effective_admin_falls_back_to_active_project_member(self) -> None:
        self.database.upsert_team(self.connection, {"gid": "team_a", "name": "Alpha"}, self.sync_time)
        self.database.upsert_user(
            self.connection,
            {"gid": "owner_inactive", "name": "Inactive Owner", "resource_type": "user", "email": "o@example.com", "is_active": True, "workspaces": []},
            self.sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {"gid": "member_active", "name": "Active Member", "resource_type": "user", "email": "m@example.com", "is_active": True, "workspaces": []},
            self.sync_time,
        )
        self.database.replace_user_atlassian_accounts(
            self.connection,
            [
                {"gid": "owner_inactive", "atlassianAccountId": "atl-owner", "atlassianEmail": "o.atl@example.com", "atlassianStatus": "deactivated"},
                {"gid": "member_active", "atlassianAccountId": "atl-member", "atlassianEmail": "m.atl@example.com", "atlassianStatus": "active"},
            ],
            self.sync_time,
        )
        # No admins, inactive owner, and the only task creator is also inactive -> the
        # sole active-mapped candidate is a project member.
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_a1",
                "name": "Alpha Project",
                "team": {"gid": "team_a", "name": "Alpha"},
                "owner": {"gid": "owner_inactive", "name": "Inactive Owner"},
                "archived": False,
                "num_tasks": 1,
                "members": [{"gid": "member_active", "name": "Active Member"}],
            },
            self.sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_a1",
                "name": "Task A1",
                "created_by": {"gid": "owner_inactive", "name": "Inactive Owner"},
                "memberships": [{"project": {"gid": "proj_a1", "name": "Alpha Project"}}],
                "workspace": {"gid": "ws_1", "name": "Workspace"},
            },
            self.sync_time,
        )
        self.connection.commit()

        analyses = build_team_analysis(self.connection)
        analysis = analyses["team_a"]
        self.assertEqual(analysis.effective_admin_source, "project_member")
        self.assertEqual(analysis.effective_admin_name, "Active Member")
        self.assertEqual(analysis.effective_admin_atlassian_id, "atl-member")
        self.assertEqual(analysis.effective_admin_atlassian_status, "active")

    def test_effective_admin_empty_when_no_active_candidate(self) -> None:
        self.database.upsert_team(self.connection, {"gid": "team_b", "name": "Beta"}, self.sync_time)
        self.database.upsert_user(
            self.connection,
            {"gid": "member_inactive", "name": "Inactive Member", "resource_type": "user", "email": "mi@example.com", "is_active": True, "workspaces": []},
            self.sync_time,
        )
        self.database.replace_user_atlassian_accounts(
            self.connection,
            [
                {"gid": "member_inactive", "atlassianAccountId": "atl-mi", "atlassianEmail": "mi.atl@example.com", "atlassianStatus": "deactivated"},
            ],
            self.sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_b1",
                "name": "Beta Project",
                "team": {"gid": "team_b", "name": "Beta"},
                "owner": {"gid": "member_inactive", "name": "Inactive Member"},
                "archived": False,
                "num_tasks": 0,
                "members": [{"gid": "member_inactive", "name": "Inactive Member"}],
            },
            self.sync_time,
        )
        self.connection.commit()

        analysis = build_team_analysis(self.connection)["team_b"]
        self.assertEqual(analysis.effective_admin_source, "")
        self.assertEqual(analysis.effective_admin_name, "")
