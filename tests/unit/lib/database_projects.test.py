import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database, synced_at_timestamp


class DatabaseProjectsTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(db_path=str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tmpdir.cleanup()

    def test_upsert_project_persists_core_fields_and_custom_data(self) -> None:
        project = {
            "gid": "proj_1",
            "name": "Project One",
            "resource_subtype": "default_project",
            "archived": False,
            "permalink_url": "https://app.asana.com/0/123/456",
            "created_at": "2023-02-01T00:00:00.000Z",
            "modified_at": "2023-02-02T00:00:00.000Z",
            "start_on": "2023-02-03",
            "due_on": "2023-02-10",
            "completed": True,
            "completed_at": "2023-02-11T00:00:00.000Z",
            "completed_by": {"gid": "user_2"},
            "owner": {"gid": "user_1", "name": "Owner Name"},
            "team": {"gid": "team_1", "name": "Team Name"},
            "privacy_setting": "favor_members",
            "notes": "Plain description",
            "html_notes": "<body>Plain description</body>",
            "num_tasks": 12,
            "num_incomplete_tasks": 3,
            "num_completed_tasks": 9,
            "task_counts_captured_at": "2023-02-01T12:34:56Z",
            "custom_fields": [
                {
                    "gid": "cf_1",
                    "name": "Priority",
                    "type": "enum",
                    "enum_value": {"gid": "enum_high", "name": "High"},
                    "enabled": True,
                },
                {
                    "gid": "cf_2",
                    "name": "Budget",
                    "type": "number",
                    "number_value": 1000.5,
                    "text_value": None,
                    "enabled": False,
                },
            ],
            "custom_field_settings": [
                {
                    "custom_field": {
                        "gid": "cf_1",
                        "name": "Priority",
                        "type": "enum",
                        "is_global_to_workspace": True,
                    },
                    "is_important": True,
                },
                {
                    "custom_field": {
                        "gid": "cf_2",
                        "name": "Budget",
                        "type": "number",
                        "is_global_to_workspace": False,
                    },
                    "is_important": False,
                },
            ],
        }

        sync_time = synced_at_timestamp()
        self.database.upsert_project(self.connection, project, sync_time)
        self.connection.commit()

        row = self.connection.execute("SELECT * FROM projects WHERE gid = ?", ("proj_1",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Project One")
        self.assertEqual(row["resource_subtype"], "default_project")
        self.assertEqual(row["archived"], 0)
        self.assertEqual(row["completed"], 1)
        self.assertEqual(row["completed_by_gid"], "user_2")
        self.assertEqual(row["owner_gid"], "user_1")
        self.assertEqual(row["owner_name"], "Owner Name")
        self.assertEqual(row["team_gid"], "team_1")
        self.assertEqual(row["team_name"], "Team Name")
        self.assertEqual(row["privacy_setting"], "favor_members")
        self.assertEqual(row["notes"], "Plain description")
        self.assertEqual(row["html_notes"], "<body>Plain description</body>")
        self.assertEqual(row["num_tasks"], 12)
        self.assertEqual(row["num_incomplete_tasks"], 3)
        self.assertEqual(row["num_completed_tasks"], 9)
        self.assertEqual(row["task_counts_captured_at"], "2023-02-01T12:34:56Z")

        custom_fields = list(
            self.connection.execute(
                """
                SELECT custom_field_gid, name, type, enum_value_gid, enabled
                FROM project_custom_fields
                WHERE project_gid = ?
                ORDER BY custom_field_gid
                """,
                ("proj_1",),
            )
        )
        self.assertEqual(len(custom_fields), 2)
        self.assertEqual(custom_fields[0]["custom_field_gid"], "cf_1")
        self.assertEqual(custom_fields[0]["enum_value_gid"], "enum_high")
        self.assertEqual(custom_fields[0]["enabled"], 1)
        self.assertEqual(custom_fields[1]["custom_field_gid"], "cf_2")
        self.assertEqual(custom_fields[1]["type"], "number")

        settings = list(
            self.connection.execute(
                """
                SELECT custom_field_gid, custom_field_name, is_important, is_global_to_workspace
                FROM project_custom_field_settings
                WHERE project_gid = ?
                ORDER BY custom_field_gid
                """,
                ("proj_1",),
            )
        )
        self.assertEqual(len(settings), 2)
        self.assertEqual(settings[0]["custom_field_gid"], "cf_1")
        self.assertEqual(settings[0]["is_important"], 1)
        self.assertEqual(settings[0]["is_global_to_workspace"], 1)
        self.assertEqual(settings[1]["is_important"], 0)
        self.assertEqual(settings[1]["is_global_to_workspace"], 0)

    def test_upsert_project_replaces_custom_field_rows(self) -> None:
        project = {
            "gid": "proj_replace",
            "name": "Replace CF",
            "custom_fields": [{"gid": "cf_initial", "name": "Initial", "type": "text"}],
            "custom_field_settings": [
                {
                    "custom_field": {
                        "gid": "cf_initial",
                        "name": "Initial",
                        "type": "text",
                        "is_global_to_workspace": False,
                    },
                    "is_important": True,
                }
            ],
        }

        sync_time = synced_at_timestamp()
        self.database.upsert_project(self.connection, project, sync_time)
        self.connection.commit()

        updated_project = {
            "gid": "proj_replace",
            "name": "Replace CF",
            "custom_fields": [{"gid": "cf_updated", "name": "Updated", "type": "number"}],
            "custom_field_settings": [
                {
                    "custom_field": {
                        "gid": "cf_updated",
                        "name": "Updated",
                        "type": "number",
                        "is_global_to_workspace": True,
                    },
                    "is_important": False,
                }
            ],
        }
        later_sync = synced_at_timestamp()
        self.database.upsert_project(self.connection, updated_project, later_sync)
        self.connection.commit()

        custom_fields = list(
            self.connection.execute(
                "SELECT custom_field_gid FROM project_custom_fields WHERE project_gid = ?", ("proj_replace",)
            )
        )
        self.assertEqual(custom_fields[0]["custom_field_gid"], "cf_updated")
        self.assertEqual(len(custom_fields), 1)

        settings = list(
            self.connection.execute(
                "SELECT custom_field_gid FROM project_custom_field_settings WHERE project_gid = ?", ("proj_replace",)
            )
        )
        self.assertEqual(settings[0]["custom_field_gid"], "cf_updated")
        self.assertEqual(len(settings), 1)

    def test_upsert_project_persists_members(self) -> None:
        project = {
            "gid": "proj_members",
            "name": "Members Project",
            "members": [
                {"gid": "user_1", "name": "Ada Lovelace"},
                {"gid": "user_2", "name": "Alan Turing"},
                {"gid": "user_2", "name": "Alan Turing"},  # duplicate ignored
                {"name": "No GID"},  # skipped (missing gid)
                "not-a-dict",  # skipped (not a dict)
            ],
        }

        sync_time = synced_at_timestamp()
        self.database.upsert_project(self.connection, project, sync_time)
        self.connection.commit()

        members = list(
            self.connection.execute(
                "SELECT user_gid, user_name FROM project_members WHERE project_gid = ? ORDER BY user_gid",
                ("proj_members",),
            )
        )
        self.assertEqual(len(members), 2)
        self.assertEqual(members[0]["user_gid"], "user_1")
        self.assertEqual(members[0]["user_name"], "Ada Lovelace")
        self.assertEqual(members[1]["user_gid"], "user_2")
        self.assertEqual(members[1]["user_name"], "Alan Turing")

    def test_upsert_project_replaces_member_rows(self) -> None:
        project = {
            "gid": "proj_member_replace",
            "name": "Member Replace",
            "members": [
                {"gid": "user_1", "name": "Stays Then Goes"},
                {"gid": "user_2", "name": "Also Goes"},
            ],
        }
        self.database.upsert_project(self.connection, project, synced_at_timestamp())
        self.connection.commit()

        updated_project = {
            "gid": "proj_member_replace",
            "name": "Member Replace",
            "members": [
                {"gid": "user_1", "name": "Stays"},
                {"gid": "user_3", "name": "New Member"},
            ],
        }
        self.database.upsert_project(self.connection, updated_project, synced_at_timestamp())
        self.connection.commit()

        members = list(
            self.connection.execute(
                "SELECT user_gid FROM project_members WHERE project_gid = ? ORDER BY user_gid",
                ("proj_member_replace",),
            )
        )
        self.assertEqual([m["user_gid"] for m in members], ["user_1", "user_3"])

    def test_upsert_project_with_no_members_writes_no_rows(self) -> None:
        project = {"gid": "proj_no_members", "name": "No Members"}

        self.database.upsert_project(self.connection, project, synced_at_timestamp())
        self.connection.commit()

        count = self.connection.execute(
            "SELECT COUNT(*) AS c FROM project_members WHERE project_gid = ?",
            ("proj_no_members",),
        ).fetchone()["c"]
        self.assertEqual(count, 0)

    def test_upsert_project_retains_null_task_counts_when_missing(self) -> None:
        project = {
            "gid": "proj_counts_missing",
            "name": "Counts Missing",
        }

        sync_time = synced_at_timestamp()
        self.database.upsert_project(self.connection, project, sync_time)
        self.connection.commit()

        row = self.connection.execute(
            "SELECT num_tasks, num_incomplete_tasks, num_completed_tasks, task_counts_captured_at FROM projects WHERE gid = ?",
            ("proj_counts_missing",),
        ).fetchone()

        self.assertIsNotNone(row)
        self.assertIsNone(row["num_tasks"])
        self.assertIsNone(row["num_incomplete_tasks"])
        self.assertIsNone(row["num_completed_tasks"])
        self.assertIsNone(row["task_counts_captured_at"])
