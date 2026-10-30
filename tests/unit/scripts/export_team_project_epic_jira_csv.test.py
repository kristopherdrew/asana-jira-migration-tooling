from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.export_team_project_epic_jira_csv import export_team_project_epic_jira_csv
from src.lib.database import Database

SYNCED_AT = datetime(2026, 10, 1, tzinfo=timezone.utc)
PROJECT_GID = "1200000000000001"
OTHER_PROJECT_GID = "1200000000000002"
ALICE_GID = "1300000000000001"
BOB_GID = "1300000000000002"
ALICE_ATLASSIAN_ID = "712020:alice"
BOB_ATLASSIAN_ID = "712020:bob"


def _people_field(gid: str, name: str, people: list[dict]) -> dict:
    return {"gid": gid, "name": name, "type": "people", "people_value": people, "enabled": True}


def _multi_enum_field(gid: str, name: str, options: list[dict]) -> dict:
    return {"gid": gid, "name": name, "type": "multi_enum", "multi_enum_values": options, "enabled": True}


def _project(gid: str, name: str, custom_fields: list[dict]) -> dict:
    return {"gid": gid, "name": name, "notes": "", "archived": False, "custom_fields": custom_fields}


class ExportTeamProjectEpicJiraCsvTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        with self.database.connect() as connection:
            self.database.initialize(connection)
            self.database.upsert_user(connection, {"gid": ALICE_GID, "name": "Alice Agent", "email": "alice@example.com"}, SYNCED_AT)
            self.database.upsert_user(connection, {"gid": BOB_GID, "name": "Bob Agent", "email": "bob@example.com"}, SYNCED_AT)
            connection.execute(
                "INSERT INTO user_atlassian_accounts (user_gid, atlassian_account_id, atlassian_email, atlassian_status, synced_at) VALUES (?, ?, ?, ?, ?)",
                (ALICE_GID, ALICE_ATLASSIAN_ID, "alice@example.com", "active", SYNCED_AT.isoformat()),
            )
            connection.execute(
                "INSERT INTO user_atlassian_accounts (user_gid, atlassian_account_id, atlassian_email, atlassian_status, synced_at) VALUES (?, ?, ?, ?, ?)",
                (BOB_GID, BOB_ATLASSIAN_ID, "bob@example.com", "active", SYNCED_AT.isoformat()),
            )
            connection.commit()

    def _upsert_projects(self, projects: list[dict]) -> None:
        with self.database.connect() as connection:
            for project in projects:
                self.database.upsert_project(connection, project, SYNCED_AT)
            connection.commit()

    def _export(self, project_gids: list[str]) -> tuple[list[str], list[list[str]]]:
        output_dir = self.workspace / "exports"
        output_path, _, _ = export_team_project_epic_jira_csv(
            str(self.db_path),
            output_dir,
            path="simple",
            filename="phase1.csv",
            project_gids=project_gids,
        )
        with output_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        return rows[0], rows[1:]

    def test_people_fields_export_name_email_and_atlassian_id_per_person(self) -> None:
        self._upsert_projects(
            [
                _project(
                    PROJECT_GID,
                    "Travel Tracker",
                    [
                        _people_field("2000", "Lead Agent", [{"gid": ALICE_GID, "name": "Alice Agent"}]),
                        _people_field(
                            "2001",
                            "Agents",
                            [{"gid": ALICE_GID, "name": "Alice Agent"}, {"gid": BOB_GID, "name": "Bob Agent"}],
                        ),
                    ],
                )
            ]
        )

        header, rows = self._export([PROJECT_GID])

        self.assertEqual(
            header[header.index("Lead Agent"): header.index("Lead Agent") + 3],
            ["Lead Agent", "Lead Agent_email", "Lead Agent_atlassian_id"],
            "Single people field should export one name/email/Atlassian ID triple",
        )
        agent_columns = [i for i, name in enumerate(header) if name == "Agents"]
        self.assertEqual(len(agent_columns), 2, "Two agents should produce two repeated Agents columns")
        self.assertEqual(header[agent_columns[0] + 1], "Agents_email")
        self.assertEqual(header[agent_columns[0] + 2], "Agents_atlassian_id")

        row = rows[0]
        lead_index = header.index("Lead Agent")
        self.assertEqual(row[lead_index], "Alice Agent")
        self.assertEqual(row[lead_index + 1], "alice@example.com")
        self.assertEqual(row[lead_index + 2], ALICE_ATLASSIAN_ID)
        self.assertEqual(row[agent_columns[1]], "Bob Agent")
        self.assertEqual(row[agent_columns[1] + 1], "bob@example.com")
        self.assertEqual(row[agent_columns[1] + 2], BOB_ATLASSIAN_ID)

    def test_people_field_without_atlassian_account_leaves_id_blank(self) -> None:
        self._upsert_projects(
            [
                _project(
                    PROJECT_GID,
                    "Travel Tracker",
                    [_people_field("2000", "Lead Agent", [{"gid": ALICE_GID, "name": "Alice Agent"}])],
                )
            ]
        )
        with self.database.connect() as connection:
            connection.execute("DELETE FROM user_atlassian_accounts WHERE user_gid = ?", (ALICE_GID,))
            connection.commit()

        header, rows = self._export([PROJECT_GID])

        self.assertEqual(rows[0][header.index("Lead Agent_atlassian_id")], "")
        self.assertEqual(rows[0][header.index("Lead Agent_email")], "alice@example.com")

    def test_multi_enum_field_exports_every_option_as_repeated_column(self) -> None:
        self._upsert_projects(
            [
                _project(
                    PROJECT_GID,
                    "Travel Tracker",
                    [
                        _multi_enum_field(
                            "3000",
                            "OKR",
                            [{"gid": "a", "name": "Q1"}, {"gid": "b", "name": "Q2"}, {"gid": "c", "name": "Q3"}],
                        )
                    ],
                )
            ]
        )

        header, rows = self._export([PROJECT_GID])

        okr_columns = [i for i, name in enumerate(header) if name == "OKR"]
        self.assertEqual([rows[0][i] for i in okr_columns], ["Q1", "Q2", "Q3"])

    def test_people_values_are_scoped_to_each_project(self) -> None:
        self._upsert_projects(
            [
                _project(
                    PROJECT_GID,
                    "Travel Tracker",
                    [_people_field("2001", "Agents", [{"gid": ALICE_GID, "name": "Alice Agent"}])],
                ),
                _project(
                    OTHER_PROJECT_GID,
                    "Other",
                    [_people_field("2001", "Agents", [{"gid": BOB_GID, "name": "Bob Agent"}])],
                ),
            ]
        )

        header, rows = self._export([PROJECT_GID])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][header.index("Agents")], "Alice Agent")
        self.assertNotIn("Bob Agent", rows[0])

    def test_resync_replaces_stored_people_values(self) -> None:
        self._upsert_projects(
            [
                _project(
                    PROJECT_GID,
                    "Travel Tracker",
                    [_people_field("2001", "Agents", [{"gid": ALICE_GID, "name": "Alice Agent"}, {"gid": BOB_GID, "name": "Bob Agent"}])],
                )
            ]
        )
        self._upsert_projects(
            [_project(PROJECT_GID, "Travel Tracker", [_people_field("2001", "Agents", [{"gid": BOB_GID, "name": "Bob Agent"}])])]
        )

        with self.database.connect() as connection:
            stored = connection.execute(
                "SELECT value_gid FROM project_custom_field_values WHERE project_gid = ? ORDER BY position",
                (PROJECT_GID,),
            ).fetchall()

        self.assertEqual([row["value_gid"] for row in stored], [BOB_GID])
