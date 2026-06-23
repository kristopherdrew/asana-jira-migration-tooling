from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.atlassian.create_team_project_jira_issues import (
    build_issue_payload,
    create_team_project_jira_issues,
    format_custom_value,
    load_field_map,
    resolve_issue_type_id,
)


EPIC_FIELDS = {
    "Summary": {"fieldId": "summary", "schema": {"type": "string"}},
    "Description": {"fieldId": "description", "schema": {"type": "doc"}},
    "Assignee": {"fieldId": "assignee", "schema": {"type": "user"}},
    "Labels": {"fieldId": "labels", "schema": {"type": "array", "items": "string"}},
    "Team URL": {"fieldId": "customfield_10001", "schema": {"type": "string"}},
    "Project URL": {"fieldId": "customfield_10002", "schema": {"type": "string"}},
    "Instructions": {"fieldId": "customfield_10003", "schema": {"type": "doc"}},
    "is_personal_project": {"fieldId": "customfield_10004", "schema": {"type": "string"}},
    "Project Status": {"fieldId": "customfield_10005", "schema": {"type": "option"}},
}
TASK_FIELDS = dict(EPIC_FIELDS, Parent={"fieldId": "parent", "schema": {"type": "issuelink"}})

ISSUE_TYPES = [{"id": "10000", "name": "Epic"}, {"id": "10001", "name": "Task"}]


class FakeJiraClient:
    def __init__(self):
        self.created = []

    def get_createmeta_issue_types(self, project_key):
        return ISSUE_TYPES

    def get_createmeta_fields(self, project_key, issue_type_id):
        return EPIC_FIELDS if issue_type_id == "10000" else TASK_FIELDS

    def create_issue(self, payload):
        key = f"MIG-{len(self.created) + 1}"
        self.created.append(payload)
        return {"key": key, "id": str(len(self.created))}


class FormatCustomValueTest(TestCase):
    def test_types(self) -> None:
        self.assertEqual(format_custom_value({"type": "string"}, "x"), "x")
        self.assertEqual(format_custom_value({"type": "option"}, "Active"), {"value": "Active"})
        self.assertEqual(format_custom_value({"type": "array", "items": "option"}, "a"), [{"value": "a"}])
        self.assertEqual(format_custom_value({"type": "array", "items": "string"}, "a"), ["a"])
        self.assertEqual(format_custom_value({"type": "number"}, "10"), 10)
        self.assertEqual(format_custom_value({"type": "doc"}, "hi")["type"], "doc")


class ResolveIssueTypeTest(TestCase):
    def test_case_insensitive(self) -> None:
        self.assertEqual(resolve_issue_type_id(ISSUE_TYPES, "epic"), "10000")

    def test_missing_raises(self) -> None:
        with self.assertRaises(ValueError):
            resolve_issue_type_id(ISSUE_TYPES, "Bug")


class LoadFieldMapTest(TestCase):
    def test_overrides_merge(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "map.json"
            path.write_text('{"Team URL": "Asana Team URL"}', encoding="utf-8")
            field_map = load_field_map(str(path))
        self.assertEqual(field_map["Team URL"], "Asana Team URL")
        self.assertEqual(field_map["Project URL"], "Project URL")  # default preserved


class BuildIssuePayloadTest(TestCase):
    def _epic_row(self, **overrides):
        row = {
            "Summary": "Alpha Team",
            "Description": "project_count_total: 2",
            "Owner Atlassian ID": "atl-1",
            "Owner Atlassian Status": "active",
            "Owner Source": "team_admin",
            "Labels": "",
            "Team URL": "https://app.asana.com/0/team_a/",
            "Project Status": "Active",
        }
        row.update(overrides)
        return row

    def test_epic_standard_and_custom_fields(self) -> None:
        payload = build_issue_payload(
            self._epic_row(),
            project_key="MIG",
            issue_type_id="10000",
            field_defs=EPIC_FIELDS,
            custom_field_map=load_field_map(None),
        )
        fields = payload["fields"]
        self.assertEqual(fields["project"], {"key": "MIG"})
        self.assertEqual(fields["issuetype"], {"id": "10000"})
        self.assertEqual(fields["summary"], "Alpha Team")
        self.assertEqual(fields["description"]["type"], "doc")
        self.assertEqual(fields["assignee"], {"accountId": "atl-1"})
        self.assertEqual(fields["labels"], ["team_admin"])
        self.assertEqual(fields["customfield_10001"], "https://app.asana.com/0/team_a/")
        self.assertEqual(fields["customfield_10005"], {"value": "Active"})
        self.assertNotIn("parent", fields)

    def test_assignee_skipped_when_inactive(self) -> None:
        payload = build_issue_payload(
            self._epic_row(**{"Owner Atlassian Status": "deactivated"}),
            project_key="MIG",
            issue_type_id="10000",
            field_defs=EPIC_FIELDS,
            custom_field_map=load_field_map(None),
        )
        self.assertNotIn("assignee", payload["fields"])

    def test_labels_combine_and_strip_spaces(self) -> None:
        payload = build_issue_payload(
            self._epic_row(**{"Owner Source": "team admin", "Labels": "last_active_2025"}),
            project_key="MIG",
            issue_type_id="10000",
            field_defs=EPIC_FIELDS,
            custom_field_map=load_field_map(None),
        )
        self.assertEqual(payload["fields"]["labels"], ["team_admin", "last_active_2025"])

    def test_task_parent_link(self) -> None:
        payload = build_issue_payload(
            {"Summary": "Proj", "Owner Source": "project_owner"},
            project_key="MIG",
            issue_type_id="10001",
            field_defs=TASK_FIELDS,
            custom_field_map=load_field_map(None),
            parent_key="MIG-1",
        )
        self.assertEqual(payload["fields"]["parent"], {"key": "MIG-1"})

    def test_instructions_uses_adf_override_for_doc_field(self) -> None:
        override = {"type": "doc", "version": 1, "content": [{"type": "heading", "attrs": {"level": 2}, "content": []}]}
        row = self._epic_row(**{"Instructions": "plain text fallback", "_adf_overrides": {"Instructions": override}})
        payload = build_issue_payload(
            row,
            project_key="MIG",
            issue_type_id="10000",
            field_defs=EPIC_FIELDS,  # Instructions -> customfield_10003, schema doc
            custom_field_map=load_field_map(None),
        )
        self.assertEqual(payload["fields"]["customfield_10003"], override)

    def test_instructions_plain_text_when_field_not_doc(self) -> None:
        string_fields = dict(EPIC_FIELDS)
        string_fields["Instructions"] = {"fieldId": "customfield_10003", "schema": {"type": "string"}}
        override = {"type": "doc", "version": 1, "content": []}
        row = self._epic_row(**{"Instructions": "plain text", "_adf_overrides": {"Instructions": override}})
        payload = build_issue_payload(
            row,
            project_key="MIG",
            issue_type_id="10000",
            field_defs=string_fields,
            custom_field_map=load_field_map(None),
        )
        self.assertEqual(payload["fields"]["customfield_10003"], "plain text")

    def test_missing_custom_field_is_skipped(self) -> None:
        fields_without_team_url = {k: v for k, v in EPIC_FIELDS.items() if k != "Team URL"}
        payload = build_issue_payload(
            self._epic_row(),
            project_key="MIG",
            issue_type_id="10000",
            field_defs=fields_without_team_url,
            custom_field_map=load_field_map(None),
        )
        self.assertNotIn("customfield_10001", payload["fields"])


class CreateTeamProjectJiraIssuesTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.addCleanup(self.connection.close)
        self._seed()

        for key, value in {
            "ASANA_TOKEN": "token",
            "ASANA_WORKSPACE_GID": "ws_1",
            "ATLASSIAN_USERNAME": "user@example.com",
            "ATLASSIAN_TOKEN": "atl-token",
            "SKIP_SSL_VERIFICATION": "false",
        }.items():
            self._set_env(key, value)

    def _set_env(self, key: str, value: str) -> None:
        previous = os.environ.get(key)
        os.environ[key] = value
        self.addCleanup(lambda k=key, p=previous: os.environ.__setitem__(k, p) if p is not None else os.environ.pop(k, None))

    def _seed(self) -> None:
        sync_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.database.upsert_team(self.connection, {"gid": "team_a", "name": "Alpha Team"}, sync_time)
        self.database.upsert_user(
            self.connection,
            {"gid": "admin_a", "name": "Alice", "resource_type": "user", "email": "a@x.com", "is_active": True, "workspaces": []},
            sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {"gid": "owner_a", "name": "Olly", "resource_type": "user", "email": "o@x.com", "is_active": True, "workspaces": []},
            sync_time,
        )
        self.database.replace_user_atlassian_accounts(
            self.connection,
            [
                {"gid": "admin_a", "atlassianAccountId": "atl-admin-a", "atlassianEmail": "a.atl@x.com", "atlassianStatus": "active"},
                {"gid": "owner_a", "atlassianAccountId": "atl-owner-a", "atlassianEmail": "o.atl@x.com", "atlassianStatus": "active"},
            ],
            sync_time,
        )
        self.database.upsert_team_members(
            self.connection,
            "team_a",
            [{"user": {"gid": "admin_a", "name": "Alice", "email": "a@x.com", "is_active": True}, "role": "member", "is_admin": True}],
            sync_time,
        )
        self.database.upsert_project(
            self.connection,
            {
                "gid": "proj_1",
                "name": "Alpha Project",
                "archived": False,
                "owner": {"gid": "owner_a", "name": "Olly"},
                "team": {"gid": "team_a", "name": "Alpha Team"},
                "privacy_setting": "private",
                "created_at": "2025-01-14T09:00:00Z",
                "completed": False,
                "completed_at": None,
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
                "gid": "task_1",
                "name": "Task 1",
                "created_at": "2025-02-01T09:00:00Z",
                "modified_at": "2025-03-05T09:00:00Z",
                "completed_at": None,
                "created_by": {"gid": "admin_a", "name": "Alice"},
                "memberships": [{"project": {"gid": "proj_1", "name": "Alpha Project"}}],
                "workspace": {"gid": "ws_1", "name": "Workspace"},
            },
            sync_time,
        )

    def _run(self, client, **kwargs):
        # Release the write lock so the script's own connection can run DDL.
        self.connection.commit()
        return create_team_project_jira_issues(
            database_path=str(self.db_path),
            jira_base_url="https://example.atlassian.net",
            jira_project_key="MIG",
            output_dir=self.workspace,
            assume_yes=True,
            client=client,
            **kwargs,
        )

    def test_apply_creates_epic_then_task_with_parent_link(self) -> None:
        client = FakeJiraClient()
        self._run(client)
        self.assertEqual(len(client.created), 2)
        epic_payload, task_payload = client.created
        self.assertEqual(epic_payload["fields"]["issuetype"], {"id": "10000"})
        self.assertEqual(task_payload["fields"]["issuetype"], {"id": "10001"})
        self.assertEqual(task_payload["fields"]["parent"], {"key": "MIG-1"})
        self.assertEqual(epic_payload["fields"]["assignee"], {"accountId": "atl-admin-a"})

        # Instructions is a doc field here, so the rich ADF (heading + bullet list)
        # built in build_team_project_rows should flow through to the payload.
        instructions = task_payload["fields"]["customfield_10003"]
        self.assertEqual(instructions["type"], "doc")
        node_types = [node["type"] for node in instructions["content"]]
        self.assertIn("heading", node_types)
        self.assertIn("bulletList", node_types)

        mappings = self.database.load_team_project_jira_mappings(self.connection)
        self.assertEqual(mappings[("team", "team_a")], "MIG-1")
        self.assertEqual(mappings[("project", "proj_1")], "MIG-2")

    def test_rerun_skips_existing(self) -> None:
        self._run(FakeJiraClient())
        second = FakeJiraClient()
        self._run(second)
        self.assertEqual(second.created, [])

    def test_dry_run_does_not_create(self) -> None:
        client = FakeJiraClient()
        self._run(client, dry_run=True)
        self.assertEqual(client.created, [])
        self.assertEqual(self.database.load_team_project_jira_mappings(self.connection), {})

    def test_limit_caps_creations(self) -> None:
        client = FakeJiraClient()
        self._run(client, limit=1)
        self.assertEqual(len(client.created), 1)  # only the epic
        mappings = self.database.load_team_project_jira_mappings(self.connection)
        self.assertIn(("team", "team_a"), mappings)
        self.assertNotIn(("project", "proj_1"), mappings)
