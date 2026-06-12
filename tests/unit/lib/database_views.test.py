import tempfile
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database, synced_at_timestamp


class DatabaseViewsTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(db_path=str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tmpdir.cleanup()

    def test_project_custom_field_configurations_view_returns_expected_row(self) -> None:
        sync_time = synced_at_timestamp()
        team = {"gid": "team_1", "name": "Analytics Team"}
        self.database.upsert_team(self.connection, team, sync_time)

        project_payload = {
            "gid": "project_1",
            "name": "Roadmap",
            "team": {"gid": "team_1", "name": "Analytics Team"},
            "custom_field_settings": [
                {
                    "custom_field": {
                        "gid": "cf_priority",
                        "name": "Priority",
                        "type": "enum",
                        "is_global_to_workspace": False,
                    },
                    "is_important": True,
                }
            ],
        }
        self.database.upsert_project(self.connection, project_payload, sync_time)

        custom_field_definition = {
            "gid": "cf_priority",
            "name": "Priority",
            "resource_type": "custom_field",
            "resource_subtype": "enum",
            "workspace": {"gid": "ws_1", "name": "Workspace"},
        }
        self.database.upsert_custom_field(self.connection, custom_field_definition, sync_time)

        memberships = [{"project": {"gid": "project_1", "name": "Roadmap"}}]
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_1",
                "name": "Investigate issue",
                "memberships": memberships,
                "custom_fields": [
                    {
                        "gid": "cf_priority",
                        "type": "enum",
                        "enum_value": {"gid": "high", "name": "High"},
                        "is_value_set": True,
                    }
                ],
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_2",
                "name": "Write brief",
                "memberships": memberships,
                "custom_fields": [
                    {
                        "gid": "cf_priority",
                        "type": "enum",
                        "enum_value": {"gid": "medium", "name": "Medium"},
                        "is_value_set": True,
                    }
                ],
            },
            sync_time,
        )
        self.connection.commit()

        row = self.connection.execute(
            """
            SELECT
                team_name,
                teamId,
                project_name,
                project_gid,
                custom_field_gid,
                custom_field_name,
                custom_field_type,
                is_global_to_workspace,
                is_important,
                value_count
            FROM view_project_custom_field_configurations
            """
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["team_name"], "Analytics Team")
        self.assertEqual(row["teamId"], "team_1")
        self.assertEqual(row["project_name"], "Roadmap")
        self.assertEqual(row["project_gid"], "project_1")
        self.assertEqual(row["custom_field_gid"], "cf_priority")
        self.assertEqual(row["custom_field_name"], "Priority")
        self.assertEqual(row["custom_field_type"], "enum")
        self.assertEqual(row["is_global_to_workspace"], 0)
        self.assertEqual(row["is_important"], 1)
        self.assertEqual(row["value_count"], 2)

    def test_project_task_hierarchy_counts_view_returns_depth_aggregations(self) -> None:
        sync_time = synced_at_timestamp()
        team = {"gid": "team_1", "name": "Analytics Team"}
        self.database.upsert_team(self.connection, team, sync_time)

        project_payload = {
            "gid": "project_1",
            "name": "Roadmap",
            "team": {"gid": "team_1", "name": "Analytics Team"},
        }
        self.database.upsert_project(self.connection, project_payload, sync_time)

        memberships = [{"project": {"gid": "project_1", "name": "Roadmap"}}]
        parent_task = {
            "gid": "task_parent",
            "name": "Parent Task",
            "created_at": "2024-01-01T09:00:00Z",
            "modified_at": "2024-01-03T14:00:00Z",
            "memberships": memberships,
        }
        first_child = {
            "gid": "task_child_1",
            "name": "Child Task 1",
            "parent": {"gid": "task_parent"},
            "created_at": "2024-01-05T10:00:00Z",
            "modified_at": "2024-01-08T11:00:00Z",
            "memberships": memberships,
        }
        second_child = {
            "gid": "task_child_2",
            "name": "Child Task 2",
            "parent": {"gid": "task_parent"},
            "created_at": "2024-01-07T09:30:00Z",
            "modified_at": "2024-01-12T09:30:00Z",
            "memberships": memberships,
        }

        self.database.upsert_task(self.connection, parent_task, sync_time)
        self.database.upsert_task(self.connection, first_child, sync_time)
        self.database.upsert_task(self.connection, second_child, sync_time)
        self.connection.commit()

        row = self.connection.execute(
            """
            SELECT
                team_name,
                project_name,
                project_gid,
                level_0_tasks,
                level_1_tasks,
                level_2_tasks,
                level_3_tasks,
                level_4_tasks,
                total_task_count,
                oldest_created_issue,
                last_updated_issue
            FROM view_project_task_hierarchy_counts
            """
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["team_name"], "Analytics Team")
        self.assertEqual(row["project_name"], "Roadmap")
        self.assertEqual(row["project_gid"], "project_1")
        self.assertEqual(row["level_0_tasks"], 1)
        self.assertEqual(row["level_1_tasks"], 2)
        self.assertEqual(row["level_2_tasks"], 0)
        self.assertEqual(row["level_3_tasks"], 0)
        self.assertEqual(row["level_4_tasks"], 0)
        self.assertEqual(row["total_task_count"], 3)
        self.assertEqual(row["oldest_created_issue"], "2024-01-01T09:00:00Z")
        self.assertEqual(row["last_updated_issue"], "2024-01-12T09:30:00Z")

    def test_team_memberships_view_returns_joined_rows(self) -> None:
        sync_time = synced_at_timestamp()
        self.database.upsert_team(
            self.connection,
            {
                "gid": "team_1",
                "name": "Analytics Team",
                "is_public": True,
            },
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "user_1",
                "name": "Ada Lovelace",
                "resource_type": "user",
                "email": "ada@example.com",
                "is_active": True,
            },
            sync_time,
        )
        self.database.upsert_team_members(
            self.connection,
            "team_1",
            [
                {
                    "user": {"gid": "user_1", "is_active": True},
                    "role": "member",
                }
            ],
            sync_time,
        )
        self.connection.commit()

        row = self.connection.execute(
            """
            SELECT
                team_gid,
                team_name,
                team_is_public,
                user_gid,
                user_name,
                user_email,
                resource_type,
                user_is_active,
                role,
                membership_is_active,
                membership_synced_at,
                user_synced_at,
                team_synced_at
            FROM view_team_memberships
            """
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["team_gid"], "team_1")
        self.assertEqual(row["team_name"], "Analytics Team")
        self.assertEqual(row["team_is_public"], 1)
        self.assertEqual(row["user_gid"], "user_1")
        self.assertEqual(row["user_name"], "Ada Lovelace")
        self.assertEqual(row["user_email"], "ada@example.com")
        self.assertEqual(row["resource_type"], "user")
        self.assertEqual(row["user_is_active"], 1)
        self.assertEqual(row["role"], "member")
        self.assertEqual(row["membership_is_active"], 1)
        self.assertEqual(row["membership_synced_at"], sync_time.isoformat())
        self.assertEqual(row["user_synced_at"], sync_time.isoformat())
        self.assertEqual(row["team_synced_at"], sync_time.isoformat())
