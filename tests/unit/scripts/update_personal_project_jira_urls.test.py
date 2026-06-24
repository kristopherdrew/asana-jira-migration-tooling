from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.atlassian.update_personal_project_jira_urls import (
    update_personal_project_jira_urls,
)


TASK_FIELDS = {
    "Summary": {"fieldId": "summary", "schema": {"type": "string"}},
    "Project (Asana)": {"fieldId": "customfield_10002", "schema": {"type": "string"}},
}
ISSUE_TYPES = [{"id": "10001", "name": "Task"}]


class FakeJiraClient:
    def __init__(self):
        self.updates = []  # (jira_key, payload)

    def get_createmeta_issue_types(self, project_key):
        return ISSUE_TYPES

    def get_createmeta_fields(self, project_key, issue_type_id):
        return TASK_FIELDS

    def update_issue(self, jira_key, payload):
        self.updates.append((jira_key, payload))


def _read_report(report_path):
    with open(report_path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class UpdatePersonalProjectJiraUrlsTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.addCleanup(self.connection.close)

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
        self.addCleanup(
            lambda k=key, p=previous: os.environ.__setitem__(k, p)
            if p is not None
            else os.environ.pop(k, None)
        )

    def _seed_personal_project(self, *, user_gid, utl_gid, mapped_key):
        sync_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.database.upsert_user(
            self.connection,
            {"gid": user_gid, "name": user_gid, "resource_type": "user", "email": f"{user_gid}@x.com", "is_active": True, "workspaces": []},
            sync_time,
        )
        if utl_gid:
            self.database.update_user_task_list_gid(self.connection, user_gid, utl_gid, sync_time)
        project_gid = f"personal_{user_gid}"
        self.database.upsert_project(
            self.connection,
            {
                "gid": project_gid,
                "name": user_gid,
                "resource_subtype": "personal_project_placeholder",
                "archived": False,
                "owner": {"gid": user_gid, "name": user_gid},
                "team": {"gid": None, "name": user_gid},
                "privacy_setting": "private",
                "created_at": "2025-01-14T09:00:00Z",
                "completed": False,
                "is_personal_project": True,
                "num_tasks": 1,
                "num_incomplete_tasks": 1,
                "num_completed_tasks": 0,
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": f"task_{user_gid}",
                "name": f"Task {user_gid}",
                "created_at": "2025-02-01T09:00:00Z",
                "modified_at": "2025-03-05T09:00:00Z",
                "completed_at": None,
                "memberships": [{"project": {"gid": project_gid, "name": user_gid}}],
                "workspace": {"gid": "ws_1", "name": "Workspace"},
            },
            sync_time,
        )
        if mapped_key:
            self.database.upsert_team_project_jira_mappings(
                self.connection,
                [{"kind": "project", "asana_gid": project_gid, "jira_key": mapped_key}],
                sync_time,
            )
        self.connection.commit()
        return project_gid

    def _run(self, client, **kwargs):
        return update_personal_project_jira_urls(
            database_path=str(self.db_path),
            jira_base_url="https://example.atlassian.net",
            jira_project_key="MIG",
            output_dir=self.workspace / "exports",
            assume_yes=True,
            client=client,
            **kwargs,
        )

    def test_updates_my_tasks_url_in_place(self) -> None:
        self._seed_personal_project(user_gid="u1", utl_gid="utl_1", mapped_key="MIG-50")
        client = FakeJiraClient()

        report = _read_report(self._run(client))

        self.assertEqual(len(client.updates), 1)
        jira_key, payload = client.updates[0]
        self.assertEqual(jira_key, "MIG-50")
        self.assertEqual(
            payload, {"fields": {"customfield_10002": "https://app.asana.com/0/utl_1/list"}}
        )
        updated = [r for r in report if r["status"] == "updated"]
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["jira_key"], "MIG-50")
        self.assertEqual(updated[0]["project_url"], "https://app.asana.com/0/utl_1/list")

    def test_skips_fallback_rows_without_my_tasks_gid(self) -> None:
        # No UTL gid -> export emits the profile-URL fallback, which we must not write.
        self._seed_personal_project(user_gid="u2", utl_gid=None, mapped_key="MIG-51")
        client = FakeJiraClient()

        report = _read_report(self._run(client))

        self.assertEqual(client.updates, [])
        skipped = [r for r in report if r["status"] == "skipped-fallback"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["jira_key"], "MIG-51")

    def test_skips_unmapped_personal_projects(self) -> None:
        self._seed_personal_project(user_gid="u3", utl_gid="utl_3", mapped_key=None)
        client = FakeJiraClient()

        report = _read_report(self._run(client))

        self.assertEqual(client.updates, [])
        skipped = [r for r in report if r["status"] == "skipped-unmapped"]
        self.assertEqual(len(skipped), 1)

    def test_dry_run_makes_no_updates(self) -> None:
        self._seed_personal_project(user_gid="u4", utl_gid="utl_4", mapped_key="MIG-52")
        client = FakeJiraClient()

        report = _read_report(self._run(client, dry_run=True))

        self.assertEqual(client.updates, [])
        self.assertEqual([r["status"] for r in report], ["dry-run"])

    def test_limit_caps_updates(self) -> None:
        self._seed_personal_project(user_gid="u5", utl_gid="utl_5", mapped_key="MIG-53")
        self._seed_personal_project(user_gid="u6", utl_gid="utl_6", mapped_key="MIG-54")
        client = FakeJiraClient()

        report = _read_report(self._run(client, limit=1))

        self.assertEqual(len(client.updates), 1)
        self.assertEqual(len([r for r in report if r["status"] == "updated"]), 1)
        self.assertEqual(len([r for r in report if r["status"] == "skipped-limit"]), 1)
