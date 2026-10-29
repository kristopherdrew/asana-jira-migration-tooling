from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.atlassian import get_jira_migration_decisions as decisions_mod
from src.lib.database import Database
from src.lib.migration_defaults import DEFAULT_SETTINGS, MigrationSettings, save_migration_settings


class FakeClient:
    def __init__(self, responses, fields=None):
        self.responses = responses
        self.fields = fields or []
        self.calls = []

    def list_fields(self):
        return self.fields

    def search(self, jql, fields, max_results, next_page_token=None):
        self.calls.append(
            {"jql": jql, "fields": tuple(fields), "max_results": max_results, "next_page_token": next_page_token}
        )
        return self.responses.pop(0) if self.responses else {"issues": []}


class ResolveFieldIdTests(unittest.TestCase):
    def test_resolves_by_name_case_insensitively(self) -> None:
        client = FakeClient(
            [],
            fields=[
                {"id": "customfield_10010", "name": "Asana Migration Decision"},
                {"id": "summary", "name": "Summary"},
            ],
        )
        self.assertEqual(
            decisions_mod.resolve_field_id(client, "asana migration decision"),
            "customfield_10010",
        )

    def test_missing_field_exits(self) -> None:
        client = FakeClient([], fields=[{"id": "summary", "name": "Summary"}])
        with self.assertRaises(SystemExit):
            decisions_mod.resolve_field_id(client, "Asana Migration Decision")

    def test_ambiguous_field_exits(self) -> None:
        client = FakeClient(
            [],
            fields=[
                {"id": "customfield_1", "name": "Decision"},
                {"id": "customfield_2", "name": "Decision"},
            ],
        )
        with self.assertRaises(SystemExit):
            decisions_mod.resolve_field_id(client, "Decision")


class StringifyDecisionTests(unittest.TestCase):
    def test_handles_value_types(self) -> None:
        self.assertEqual(decisions_mod._stringify_decision("Migrate"), "Migrate")
        self.assertIsNone(decisions_mod._stringify_decision("  "))
        self.assertIsNone(decisions_mod._stringify_decision(None))
        self.assertEqual(decisions_mod._stringify_decision({"value": "Archive"}), "Archive")
        self.assertEqual(decisions_mod._stringify_decision({"name": "Hold"}), "Hold")
        self.assertEqual(
            decisions_mod._stringify_decision([{"value": "A"}, {"value": "B"}]),
            "A, B",
        )
        self.assertEqual(decisions_mod._stringify_decision(7), "7")


class CollectDecisionsTests(unittest.TestCase):
    def test_pages_and_extracts_select_option(self) -> None:
        responses = [
            {
                "issues": [
                    {
                        "key": "MIG-1",
                        "fields": {
                            "created": "2024-01-01T00:00:00.000+0000",
                            "customfield_10010": {"value": "Migrate"},
                        },
                    }
                ],
                "nextPageToken": None,
            },
            {
                "issues": [
                    {
                        "key": "MIG-2",
                        "fields": {
                            "created": "2024-01-02T00:00:00.000+0000",
                            "customfield_10010": None,
                        },
                    }
                ],
                "nextPageToken": None,
            },
        ]
        client = FakeClient(responses)
        issues = decisions_mod.collect_decisions(client, "project = MIG", "customfield_10010", max_results=1)
        self.assertEqual([(i.key, i.external_id) for i in issues], [("MIG-1", "Migrate"), ("MIG-2", None)])
        # second batch's first call must carry the created cursor exclusion
        self.assertIn("issuekey NOT IN", client.calls[1]["jql"])


class DefaultJqlTests(unittest.TestCase):
    def test_derive_project_keys_distinct_and_sorted(self) -> None:
        keys = decisions_mod.derive_project_keys(["MIG-12", "MIG-3", "ABC2-9", "broken", "ABC2-1"])
        self.assertEqual(keys, ["ABC2", "MIG"])

    def test_build_default_jql_scopes_to_projects_and_tasks(self) -> None:
        jql = decisions_mod.build_default_jql(["MIG", "ABC"], "Asana Migration Decision")
        self.assertEqual(
            jql,
            'project IN ("MIG", "ABC") AND issuetype = "Task" AND "Asana Migration Decision" is not EMPTY',
        )

    def test_build_default_jql_without_keys_omits_project_clause(self) -> None:
        jql = decisions_mod.build_default_jql([], "Decision")
        self.assertEqual(jql, 'issuetype = "Task" AND "Decision" is not EMPTY')

    def test_default_jql_from_database_uses_project_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/asana.sqlite"
            database = Database(db_path=db_path)
            with database.connect() as conn:
                database.initialize(conn)
                database.upsert_team_project_jira_mappings(
                    conn,
                    [
                        {"kind": "project", "asana_gid": "proj_1", "jira_key": "MIG-1"},
                        {"kind": "project", "asana_gid": "proj_2", "jira_key": "MIG-7"},
                        {"kind": "team", "asana_gid": "team_1", "jira_key": "OPS-9"},
                    ],
                    datetime.now(timezone.utc),
                )
            jql = decisions_mod.default_jql_from_database(db_path, "Asana Migration Decision")
            # Only the project-level key prefix (MIG) is used; the team mapping is ignored.
            self.assertEqual(
                jql,
                'project IN ("MIG") AND issuetype = "Task" AND "Asana Migration Decision" is not EMPTY',
            )

    def test_default_jql_from_database_without_project_mappings_exits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/asana.sqlite"
            database = Database(db_path=db_path)
            with database.connect() as conn:
                database.initialize(conn)
            with self.assertRaises(SystemExit):
                decisions_mod.default_jql_from_database(db_path, "Asana Migration Decision")


class UpsertDecisionsTests(unittest.TestCase):
    def _seed_db(self, db_path):
        database = Database(db_path=db_path)
        with database.connect() as conn:
            database.initialize(conn)
            database.upsert_team_project_jira_mappings(
                conn,
                [
                    {"kind": "project", "asana_gid": "proj_1", "jira_key": "MIG-1"},
                    {"kind": "project", "asana_gid": "proj_2", "jira_key": "MIG-2"},
                    {"kind": "team", "asana_gid": "team_1", "jira_key": "MIG-9"},
                ],
                datetime.now(timezone.utc),
            )

    def test_maps_keys_skips_unmapped_and_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/asana.sqlite"
            self._seed_db(db_path)
            issues = [
                decisions_mod.JiraIssue(key="MIG-1", created="c", external_id="Migrate"),
                decisions_mod.JiraIssue(key="MIG-2", created="c", external_id=None),  # empty -> skipped
                decisions_mod.JiraIssue(key="MIG-9", created="c", external_id="X"),  # team -> unmapped
                decisions_mod.JiraIssue(key="MIG-404", created="c", external_id="Y"),  # unknown -> unmapped
            ]
            summary = decisions_mod.upsert_decisions(db_path, issues)
            self.assertEqual(summary["written"], 1)
            self.assertEqual(summary["unmapped"], 2)
            self.assertEqual(summary["empty_skipped"], 1)

            database = Database(db_path=db_path)
            with database.connect() as conn:
                rows = conn.execute(
                    "SELECT project_gid, decision FROM project_jira_decisions"
                ).fetchall()
            self.assertEqual([(r["project_gid"], r["decision"]) for r in rows], [("proj_1", "Migrate")])

    def test_include_empty_stores_blank_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/asana.sqlite"
            self._seed_db(db_path)
            issues = [
                decisions_mod.JiraIssue(key="MIG-1", created="c", external_id="Migrate"),
                decisions_mod.JiraIssue(key="MIG-2", created="c", external_id=None),
            ]
            summary = decisions_mod.upsert_decisions(db_path, issues, include_empty=True)
            self.assertEqual(summary["written"], 2)
            self.assertEqual(summary["empty_skipped"], 0)


class DecisionFieldNameSettingsTests(unittest.TestCase):
    PROFILE_FIELD_NAME = "Client Decision Field"
    EXPLICIT_FIELD_NAME = "Other Decision Field"

    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.profile_path = Path(temp_dir.name) / "profile.env"
        self.profile_path.write_text("", encoding="utf-8")
        self.resolve_field_id = MagicMock(return_value="customfield_1")
        patchers = [
            patch.object(decisions_mod, "resolve_credentials_path", return_value=self.profile_path),
            patch.object(decisions_mod, "load_credentials", return_value=MagicMock()),
            patch.object(decisions_mod, "ensure_atlassian_credentials", return_value=("user", "token")),
            patch.object(decisions_mod, "resolve_jira_base_url", return_value="https://example.atlassian.net"),
            patch.object(decisions_mod, "JiraSearchClient"),
            patch.object(decisions_mod, "resolve_field_id", self.resolve_field_id),
            patch.object(decisions_mod, "collect_decisions", return_value=[]),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _resolved_field_name(self, argv: list[str]) -> str:
        decisions_mod.main([*argv, "--jql", "project = MIG"])
        self.resolve_field_id.assert_called_once()
        return self.resolve_field_id.call_args.args[1]

    def test_profile_field_name_used_when_flag_omitted(self) -> None:
        save_migration_settings(self.profile_path, MigrationSettings(decision_field_name=self.PROFILE_FIELD_NAME))
        self.assertEqual(self._resolved_field_name([]), self.PROFILE_FIELD_NAME)

    def test_explicit_field_name_overrides_profile(self) -> None:
        save_migration_settings(self.profile_path, MigrationSettings(decision_field_name=self.PROFILE_FIELD_NAME))
        self.assertEqual(
            self._resolved_field_name(["--field-name", self.EXPLICIT_FIELD_NAME]), self.EXPLICIT_FIELD_NAME
        )

    def test_unset_profile_falls_back_to_default_field_name(self) -> None:
        save_migration_settings(self.profile_path, DEFAULT_SETTINGS)
        self.assertEqual(self._resolved_field_name([]), DEFAULT_SETTINGS.decision_field_name)


if __name__ == "__main__":
    unittest.main()
