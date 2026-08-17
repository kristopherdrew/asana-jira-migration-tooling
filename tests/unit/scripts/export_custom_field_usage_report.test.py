from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.export_custom_field_usage_report import export_custom_field_usage


SYNC_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


class ExportCustomFieldUsageReportTest(TestCase):
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

    def _custom_field(self, gid: str, name: str, subtype: str, *, is_global: bool = True) -> None:
        self.database.upsert_custom_field(
            self.connection,
            {
                "gid": gid,
                "name": name,
                "resource_subtype": subtype,
                "is_global_to_workspace": is_global,
            },
            SYNC_TIME,
        )

    def _run(self, filename: str, team_gid=None):
        output_path = export_custom_field_usage(
            str(self.db_path),
            self.workspace / "exports",
            filename=filename,
            team_gid=team_gid,
            workspace_gid="ws_1",
        )
        with output_path.open("r", newline="", encoding="utf-8") as handle:
            return output_path, list(csv.DictReader(handle))

    def test_counts_distinct_tasks_with_values(self) -> None:
        self.database.upsert_team(self.connection, {"gid": "team_1", "name": "Team One"}, SYNC_TIME)
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_1",
                "name": "Project One",
                "team": {"gid": "team_1", "name": "Team One"},
            },
            SYNC_TIME,
        )
        self._custom_field("cf_text", "Priority Note", "text")
        # Two of three tasks carry a value for the field.
        for gid, text in (("task_1", "high"), ("task_2", "low"), ("task_3", None)):
            self.database.upsert_task(
                self.connection,
                {
                    "gid": gid,
                    "name": gid,
                    "memberships": [{"project": {"gid": "proj_1", "name": "Project One"}}],
                    "custom_fields": [
                        {"gid": "cf_text", "type": "text", "text_value": text, "is_value_set": bool(text)}
                    ],
                },
                SYNC_TIME,
            )
        self.connection.commit()

        _, rows = self._run("usage.csv")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["project_gid"], "proj_1")
        self.assertEqual(row["project_url"], "https://app.asana.com/1/ws_1/project/proj_1")
        self.assertEqual(row["team_name"], "Team One")
        self.assertEqual(row["custom_field_name"], "Priority Note")
        self.assertEqual(row["field_type"], "text")
        self.assertEqual(row["is_global_to_workspace"], "true")
        self.assertEqual(row["tasks_with_values"], "2")
        self.assertEqual(row["project_task_count"], "3")
        self.assertEqual(row["usage_pct"], "66.7")

    def test_enabled_but_empty_field_excluded(self) -> None:
        self.database.upsert_team(self.connection, {"gid": "team_1", "name": "Team One"}, SYNC_TIME)
        self._custom_field("cf_used", "Used Field", "text")
        self._custom_field("cf_unused", "Unused Field", "number")
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_1",
                "name": "Project One",
                "team": {"gid": "team_1", "name": "Team One"},
                # Both fields are enabled on the project...
                "custom_field_settings": [
                    {"custom_field": {"gid": "cf_used", "name": "Used Field"}, "is_important": True},
                    {"custom_field": {"gid": "cf_unused", "name": "Unused Field"}, "is_important": False},
                ],
            },
            SYNC_TIME,
        )
        # ...but only cf_used has a value on a task.
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_1",
                "name": "Task One",
                "memberships": [{"project": {"gid": "proj_1", "name": "Project One"}}],
                "custom_fields": [{"gid": "cf_used", "type": "text", "text_value": "x", "is_value_set": True}],
            },
            SYNC_TIME,
        )
        self.connection.commit()

        _, rows = self._run("usage.csv")

        self.assertEqual([row["custom_field_gid"] for row in rows], ["cf_used"])
        self.assertEqual(rows[0]["is_important"], "true")

    def test_multi_enum_field_included(self) -> None:
        self.database.upsert_project(
            self.connection,
            {"gid": "proj_1", "name": "Project One"},
            SYNC_TIME,
        )
        self._custom_field("cf_multi", "Labels", "multi_enum")
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_1",
                "name": "Task One",
                "memberships": [{"project": {"gid": "proj_1", "name": "Project One"}}],
                "custom_fields": [
                    {
                        "gid": "cf_multi",
                        "type": "multi_enum",
                        "multi_enum_values": [{"gid": "opt_a"}, {"gid": "opt_b"}],
                    }
                ],
            },
            SYNC_TIME,
        )
        self.connection.commit()

        _, rows = self._run("usage.csv")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["field_type"], "multi_enum")
        self.assertEqual(rows[0]["tasks_with_values"], "1")

    def test_team_filter_restricts_projects(self) -> None:
        self.database.upsert_team(self.connection, {"gid": "team_1", "name": "Team One"}, SYNC_TIME)
        self.database.upsert_team(self.connection, {"gid": "team_2", "name": "Team Two"}, SYNC_TIME)
        self._custom_field("cf_text", "Note", "text")
        for proj, team in (("proj_1", "team_1"), ("proj_2", "team_2")):
            self.database.upsert_project(
                self.connection,
                {"gid": proj, "name": proj, "team": {"gid": team, "name": team}},
                SYNC_TIME,
            )
            self.database.upsert_task(
                self.connection,
                {
                    "gid": f"task_{proj}",
                    "name": f"task_{proj}",
                    "memberships": [{"project": {"gid": proj, "name": proj}}],
                    "custom_fields": [{"gid": "cf_text", "type": "text", "text_value": "v"}],
                },
                SYNC_TIME,
            )
        self.connection.commit()

        _, rows = self._run("usage_team_1.csv", team_gid="team_1")

        self.assertEqual([row["project_gid"] for row in rows], ["proj_1"])

    def test_project_gid_filter_restricts_projects(self) -> None:
        self.database.upsert_project(self.connection, {"gid": "proj_1", "name": "One"}, SYNC_TIME)
        self.database.upsert_project(self.connection, {"gid": "proj_2", "name": "Two"}, SYNC_TIME)
        self._custom_field("cf_text", "Note", "text")
        for gid in ("proj_1", "proj_2"):
            self.database.upsert_task(self.connection, {"gid": f"task_{gid}", "name": gid,
                "memberships": [{"project": {"gid": gid}}],
                "custom_fields": [{"gid": "cf_text", "type": "text", "text_value": "v"}]}, SYNC_TIME)
        self.connection.commit()
        output_path = export_custom_field_usage(str(self.db_path), self.workspace / "exports",
            filename="selected.csv", project_gids=["proj_2"], workspace_gid="ws_1")
        with output_path.open("r", newline="", encoding="utf-8") as handle:
            self.assertEqual([row["project_gid"] for row in csv.DictReader(handle)], ["proj_2"])

    def test_empty_database_writes_header_only(self) -> None:
        output_path, rows = self._run("usage.csv")
        self.assertTrue(output_path.exists())
        self.assertEqual(rows, [])
        with output_path.open("r", newline="", encoding="utf-8") as handle:
            header = handle.readline().strip()
        self.assertEqual(header.split(",")[0], "project_gid")
        self.assertIn("usage_pct", header)
