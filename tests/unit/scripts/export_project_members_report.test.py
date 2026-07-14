from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.export_project_members_report import (
    build_member_sheets,
    export_project_members_report,
)


SYNC_TIME = datetime(2025, 1, 1, tzinfo=timezone.utc)


class ExportProjectMembersReportTest(TestCase):
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

    def _user(self, gid: str, name: str, email: str = "") -> None:
        self.database.upsert_user(
            self.connection,
            {"gid": gid, "name": name, "email": email},
            SYNC_TIME,
        )

    def _project(self, gid: str, name: str, team_gid: str, members=None) -> None:
        project = {
            "gid": gid,
            "name": name,
            "team": {"gid": team_gid, "name": team_gid},
            "members": [{"gid": mg, "name": mn} for mg, mn in (members or [])],
        }
        self.database.upsert_project(self.connection, project, SYNC_TIME)

    # ---- sheet access helpers --------------------------------------------

    def _sheets(self, team_gid=None):
        return {
            title: rows
            for title, rows in build_member_sheets(
                self.connection, workspace_gid="ws_1", team_gid=team_gid
            )
        }

    @staticmethod
    def _as_dicts(rows):
        header = rows[0]
        return [dict(zip(header, record)) for record in rows[1:]]

    # ---- tests -----------------------------------------------------------

    def test_project_members_one_row_per_membership(self) -> None:
        self._team("team_1", "Team One")
        self._user("u1", "Ada Lovelace", "ada@example.com")
        self._user("u2", "Alan Turing", "alan@example.com")
        self._project("proj_1", "Project One", "team_1", members=[("u1", "Ada L."), ("u2", "Alan T.")])
        self.connection.commit()

        members = self._as_dicts(self._sheets()["Project Members"])
        self.assertEqual(len(members), 2)
        by_user = {row["user_gid"]: row for row in members}
        # Canonical user name/email win over the membership-embedded name.
        self.assertEqual(by_user["u1"]["member_name"], "Ada Lovelace")
        self.assertEqual(by_user["u1"]["member_email"], "ada@example.com")
        self.assertEqual(by_user["u1"]["team_name"], "Team One")
        self.assertEqual(by_user["u1"]["project_name"], "Project One")
        self.assertEqual(by_user["u1"]["project_url"], "https://app.asana.com/1/ws_1/project/proj_1")

    def test_member_falls_back_to_embedded_name_when_no_user_row(self) -> None:
        self._team("team_1", "Team One")
        self._project("proj_1", "Project One", "team_1", members=[("u_ghost", "Ghost Member")])
        self.connection.commit()

        members = self._as_dicts(self._sheets()["Project Members"])
        self.assertEqual(members[0]["member_name"], "Ghost Member")
        self.assertEqual(members[0]["member_email"], "")

    def test_member_summary_counts_and_project_list(self) -> None:
        self._team("team_1", "Team One")
        self._user("u1", "Ada Lovelace", "ada@example.com")
        self._user("u2", "Alan Turing", "alan@example.com")
        self._project("proj_1", "Alpha", "team_1", members=[("u1", "Ada"), ("u2", "Alan")])
        self._project("proj_2", "Bravo", "team_1", members=[("u1", "Ada")])
        self.connection.commit()

        summary = {row["user_gid"]: row for row in self._as_dicts(self._sheets()["Member Summary"])}
        self.assertEqual(summary["u1"]["project_count"], 2)
        self.assertEqual(summary["u1"]["project_names"], "Alpha; Bravo")
        self.assertEqual(summary["u2"]["project_count"], 1)
        self.assertEqual(summary["u2"]["project_names"], "Alpha")
        # Most-projects member sorts first.
        first_row = self._as_dicts(self._sheets()["Member Summary"])[0]
        self.assertEqual(first_row["user_gid"], "u1")

    def test_team_scope_filters_projects(self) -> None:
        self._team("team_1", "Team One")
        self._team("team_2", "Team Two")
        self._user("u1", "Ada Lovelace")
        self._user("u2", "Alan Turing")
        self._project("proj_1", "Project One", "team_1", members=[("u1", "Ada")])
        self._project("proj_2", "Project Two", "team_2", members=[("u2", "Alan")])
        self.connection.commit()

        scoped = self._as_dicts(self._sheets(team_gid="team_1")["Project Members"])
        self.assertEqual([row["project_gid"] for row in scoped], ["proj_1"])
        summary = self._as_dicts(self._sheets(team_gid="team_1")["Member Summary"])
        self.assertEqual([row["user_gid"] for row in summary], ["u1"])

    def test_empty_database_yields_header_only_sheets(self) -> None:
        sheets = self._sheets()
        self.assertEqual(set(sheets), {"Project Members", "Member Summary"})
        for title, rows in sheets.items():
            self.assertEqual(len(rows), 1, f"{title} should have a header row only")

    def test_export_writes_workbook_with_overview(self) -> None:
        self._team("team_1", "Team One")
        self._user("u1", "Ada Lovelace", "ada@example.com")
        self._project("proj_1", "Project One", "team_1", members=[("u1", "Ada")])
        self.connection.commit()

        output_path = export_project_members_report(
            str(self.db_path),
            self.workspace / "exports",
            filename="members.xlsx",
            workspace_gid="ws_1",
        )
        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.name, "members.xlsx")
        self.assertGreater(output_path.stat().st_size, 0)
