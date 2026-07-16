from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.export_field_section_review_report import (
    build_review_sheets,
    export_field_section_review_report,
)


SYNC_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


class ExportFieldSectionReviewReportTest(TestCase):
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

    # ---- seeding helpers -------------------------------------------------

    def _team(self, gid: str, name: str) -> None:
        self.database.upsert_team(self.connection, {"gid": gid, "name": name}, SYNC_TIME)

    def _project(self, gid: str, name: str, team_gid: str, owner_gid: str = None) -> None:
        project = {"gid": gid, "name": name, "team": {"gid": team_gid, "name": team_gid}}
        if owner_gid:
            project["owner"] = {"gid": owner_gid, "name": f"Owner {owner_gid}"}
        self.database.upsert_project(self.connection, project, SYNC_TIME)

    def _enum_field(self, gid: str, name: str, subtype: str, options) -> None:
        self.database.upsert_custom_field(
            self.connection,
            {
                "gid": gid,
                "name": name,
                "resource_subtype": subtype,
                "is_global_to_workspace": True,
                "enum_options": [{"gid": og, "name": on} for og, on in options],
            },
            SYNC_TIME,
        )

    def _text_field(self, gid: str, name: str) -> None:
        self.database.upsert_custom_field(
            self.connection,
            {"gid": gid, "name": name, "resource_subtype": "text", "is_global_to_workspace": False},
            SYNC_TIME,
        )

    def _task(self, gid: str, project_gid, section=None, completed=False, custom_fields=None) -> None:
        membership = {"project": {"gid": project_gid, "name": project_gid}}
        if section:
            membership["section"] = {"gid": section[0], "name": section[1]}
        self.database.upsert_task(
            self.connection,
            {
                "gid": gid,
                "name": gid,
                "completed": completed,
                "memberships": [membership],
                "custom_fields": custom_fields or [],
            },
            SYNC_TIME,
        )

    # ---- sheet access helpers --------------------------------------------

    def _sheets(self, team_gid=None, owner_gid=None):
        return {
            title: rows
            for title, rows in build_review_sheets(
                self.connection, workspace_gid="ws_1", team_gid=team_gid, owner_gid=owner_gid
            )
        }

    @staticmethod
    def _as_dicts(rows):
        header = rows[0]
        return [dict(zip(header, record)) for record in rows[1:]]

    # ---- tests -----------------------------------------------------------

    def test_sections_rollup_counts(self) -> None:
        self._team("team_1", "Team One")
        self._project("proj_1", "Project One", "team_1", owner_gid="u1")
        self._task("task_1", "proj_1", section=("s1", "Doing"), completed=False)
        self._task("task_2", "proj_1", section=("s1", "Doing"), completed=True)
        self._task("task_3", "proj_1", section=("s2", "Done"), completed=True)
        self._task("task_4", "proj_1", section=None)  # no section -> excluded from Sections tab
        self.connection.commit()

        sheets = self._sheets()
        sections = self._as_dicts(sheets["Sections"])
        by_name = {row["section_name"]: row for row in sections}
        self.assertEqual(by_name["Doing"]["task_count"], 2)
        self.assertEqual(by_name["Doing"]["completed_task_count"], 1)
        self.assertEqual(by_name["Done"]["task_count"], 1)
        self.assertEqual(by_name["Done"]["completed_task_count"], 1)
        self.assertEqual(len(sections), 2)

        projects = self._as_dicts(sheets["Projects"])
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["section_count"], 2)
        self.assertEqual(projects[0]["task_count"], 4)
        self.assertEqual(projects[0]["project_url"], "https://app.asana.com/1/ws_1/project/proj_1")

    def test_projects_tab_includes_jira_migration_decision(self) -> None:
        self._team("team_1", "Team One")
        self._project("proj_1", "Project One", "team_1", owner_gid="u1")
        self._project("proj_2", "Project Two", "team_1", owner_gid="u1")
        self._task("task_1", "proj_1", section=("s1", "Doing"))
        self.database.upsert_project_jira_decisions(
            self.connection,
            [{"project_gid": "proj_1", "jira_key": "MIG-1", "decision": "Migrate"}],
            SYNC_TIME,
        )
        self.connection.commit()

        projects = {row["project_gid"]: row for row in self._as_dicts(self._sheets()["Projects"])}
        self.assertEqual(projects["proj_1"]["jira_migration_decision"], "Migrate")
        self.assertEqual(projects["proj_2"]["jira_migration_decision"], "")  # no decision recorded

    def test_custom_field_usage_and_value_breakdown(self) -> None:
        self._team("team_1", "Team One")
        self._project("proj_1", "Project One", "team_1", owner_gid="u1")
        self._enum_field(
            "cf_stage", "Stage", "enum",
            [("opt_todo", "To Do"), ("opt_prog", "In Progress"), ("opt_done", "Done")],
        )
        self._text_field("cf_note", "Note")

        def enum_val(option):
            return {"gid": "cf_stage", "type": "enum", "enum_value": {"gid": option}, "is_value_set": True}

        def note_val(text):
            return {"gid": "cf_note", "type": "text", "text_value": text, "display_value": text, "is_value_set": True}

        self._task("task_1", "proj_1", custom_fields=[enum_val("opt_todo"), note_val("alpha")])
        self._task("task_2", "proj_1", custom_fields=[enum_val("opt_todo"), note_val("beta")])
        self._task("task_3", "proj_1", custom_fields=[enum_val("opt_done")])
        self._task("task_4", "proj_1")  # no values
        self.connection.commit()

        sheets = self._sheets()
        fields = {row["custom_field_name"]: row for row in self._as_dicts(sheets["Custom Fields"])}
        self.assertEqual(fields["Stage"]["tasks_with_values"], 3)
        self.assertEqual(fields["Stage"]["distinct_values"], 2)
        self.assertEqual(fields["Stage"]["project_task_count"], 4)
        self.assertEqual(fields["Stage"]["usage_pct"], 75.0)
        self.assertEqual(fields["Note"]["tasks_with_values"], 2)
        self.assertEqual(fields["Note"]["distinct_values"], 2)

        values = self._as_dicts(sheets["Custom Field Values"])
        stage_values = {row["option_or_value"]: row for row in values if row["custom_field_name"] == "Stage"}
        self.assertEqual(stage_values["To Do"]["task_count"], 2)
        self.assertEqual(stage_values["Done"]["task_count"], 1)
        self.assertNotIn("In Progress", stage_values)  # unused option not listed

        note_rows = [row for row in values if row["custom_field_name"] == "Note"]
        self.assertEqual(len(note_rows), 1)  # non-enum collapses to one summary row
        self.assertEqual(note_rows[0]["option_or_value"], "(2 distinct values)")
        self.assertEqual(note_rows[0]["task_count"], 2)
        self.assertIn("alpha", note_rows[0]["sample_values"])

    def test_multi_enum_value_breakdown(self) -> None:
        self._team("team_1", "Team One")
        self._project("proj_1", "Project One", "team_1")
        self._enum_field("cf_labels", "Labels", "multi_enum", [("l_a", "Backend"), ("l_b", "Frontend")])
        self._task(
            "task_1", "proj_1",
            custom_fields=[{"gid": "cf_labels", "type": "multi_enum", "multi_enum_values": [{"gid": "l_a"}, {"gid": "l_b"}]}],
        )
        self._task(
            "task_2", "proj_1",
            custom_fields=[{"gid": "cf_labels", "type": "multi_enum", "multi_enum_values": [{"gid": "l_a"}]}],
        )
        self.connection.commit()

        values = self._as_dicts(self._sheets()["Custom Field Values"])
        labels = {row["option_or_value"]: row for row in values if row["custom_field_name"] == "Labels"}
        self.assertEqual(labels["Backend"]["task_count"], 2)
        self.assertEqual(labels["Frontend"]["task_count"], 1)

    def test_field_catalog_counts_projects_using_a_field(self) -> None:
        self._team("team_1", "Team One")
        self._project("proj_1", "Project One", "team_1")
        self._project("proj_2", "Project Two", "team_1")
        self._enum_field("cf_stage", "Stage", "enum", [("opt_a", "A")])
        self._task("task_1", "proj_1", custom_fields=[{"gid": "cf_stage", "type": "enum", "enum_value": {"gid": "opt_a"}}])
        self._task("task_2", "proj_2", custom_fields=[{"gid": "cf_stage", "type": "enum", "enum_value": {"gid": "opt_a"}}])
        self.connection.commit()

        catalog = self._as_dicts(self._sheets()["Field Catalog"])
        self.assertEqual(len(catalog), 1)
        entry = catalog[0]
        self.assertEqual(entry["custom_field_name"], "Stage")
        self.assertEqual(entry["project_count"], 2)
        self.assertEqual(entry["distinct_options"], 1)
        self.assertEqual(entry["option_set"], "A")
        self.assertEqual(entry["project_names"], "Project One; Project Two")

    def test_section_catalog_groups_names_case_insensitively(self) -> None:
        self._team("team_1", "Team One")
        self._project("proj_1", "Project One", "team_1")
        self._project("proj_2", "Project Two", "team_1")
        self._task("task_1", "proj_1", section=("s1", "Done"))
        self._task("task_2", "proj_2", section=("s2", "done"))
        self.connection.commit()

        catalog = self._as_dicts(self._sheets()["Section Catalog"])
        done = [row for row in catalog if row["section_name"].casefold() == "done"]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["project_count"], 2)
        self.assertEqual(done[0]["task_count"], 2)
        self.assertEqual(done[0]["project_names"], "Project One; Project Two")

    def test_team_and_owner_scope_filters(self) -> None:
        self._team("team_1", "Team One")
        self._team("team_2", "Team Two")
        self._project("proj_1", "Project One", "team_1", owner_gid="u1")
        self._project("proj_2", "Project Two", "team_2", owner_gid="u2")
        self._task("task_1", "proj_1", section=("s1", "Doing"))
        self._task("task_2", "proj_2", section=("s2", "Doing"))
        self.connection.commit()

        team_scoped = self._as_dicts(self._sheets(team_gid="team_1")["Projects"])
        self.assertEqual([row["project_gid"] for row in team_scoped], ["proj_1"])

        owner_scoped = self._as_dicts(self._sheets(owner_gid="u2")["Projects"])
        self.assertEqual([row["project_gid"] for row in owner_scoped], ["proj_2"])

    def test_empty_database_yields_header_only_sheets(self) -> None:
        sheets = self._sheets()
        expected = {
            "Projects",
            "Project Members",
            "Member Summary",
            "Sections",
            "Custom Fields",
            "Custom Field Values",
            "Field Catalog",
            "Section Catalog",
        }
        self.assertEqual(set(sheets), expected)
        for title, rows in sheets.items():
            self.assertEqual(len(rows), 1, f"{title} should have a header row only")

    def test_membership_tabs_populated_and_scoped(self) -> None:
        self._team("team_1", "Team One")
        self._team("team_2", "Team Two")
        self.database.upsert_user(self.connection, {"gid": "u1", "name": "Ada", "email": "ada@x.com"}, SYNC_TIME)
        self.database.upsert_user(self.connection, {"gid": "u2", "name": "Alan", "email": "alan@x.com"}, SYNC_TIME)
        self.database.upsert_project(
            self.connection,
            {"gid": "proj_1", "name": "Alpha", "team": {"gid": "team_1"}, "members": [{"gid": "u1", "name": "Ada"}]},
            SYNC_TIME,
        )
        self.database.upsert_project(
            self.connection,
            {"gid": "proj_2", "name": "Bravo", "team": {"gid": "team_2"}, "members": [{"gid": "u2", "name": "Alan"}]},
            SYNC_TIME,
        )
        self.connection.commit()

        scoped = self._sheets(team_gid="team_1")
        members = self._as_dicts(scoped["Project Members"])
        self.assertEqual([row["user_gid"] for row in members], ["u1"])
        summary = self._as_dicts(scoped["Member Summary"])
        self.assertEqual([row["user_gid"] for row in summary], ["u1"])
        self.assertEqual(summary[0]["project_names"], "Alpha")

    def test_workbook_named_after_team(self) -> None:
        self._team("team_1", "Payments / Billing")
        self._project("proj_1", "Project One", "team_1", owner_gid="u1")
        self.connection.commit()

        output_path = export_field_section_review_report(
            str(self.db_path),
            self.workspace / "exports",
            team_gid="team_1",
            workspace_gid="ws_1",
        )
        # Team name drives the filename; unsafe chars are sanitized.
        self.assertEqual(output_path.name, "Payments Billing Migration Review.xlsx")
        self.assertTrue(output_path.exists())

    def test_workbook_named_workspace_when_unscoped(self) -> None:
        self._team("team_1", "Team One")
        self._project("proj_1", "Project One", "team_1")
        self.connection.commit()

        output_path = export_field_section_review_report(
            str(self.db_path),
            self.workspace / "exports",
            workspace_gid="ws_1",
        )
        self.assertEqual(output_path.name, "Workspace Migration Review.xlsx")

    def test_export_writes_workbook_with_overview(self) -> None:
        self._team("team_1", "Team One")
        self._project("proj_1", "Project One", "team_1", owner_gid="u1")
        self._task("task_1", "proj_1", section=("s1", "Doing"))
        self.connection.commit()

        output_path = export_field_section_review_report(
            str(self.db_path),
            self.workspace / "exports",
            filename="review.xlsx",
            workspace_gid="ws_1",
        )
        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.name, "review.xlsx")
        self.assertGreater(output_path.stat().st_size, 0)
