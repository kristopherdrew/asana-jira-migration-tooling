from __future__ import annotations

import csv
import os
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from src.lib.database import Database
from src.lib.migration_defaults import DEFAULT_SETTINGS, MigrationSettings, save_migration_settings
from scripts.atlassian import set_project_last_updated_jira_field as set_project_module
from scripts.atlassian.set_project_last_updated_jira_field import (
    set_project_last_updated_jira_field,
)


class FakeJiraClient:
    def __init__(self):
        self.updates = []  # (jira_key, payload)

    def update_issue(self, jira_key, payload):
        self.updates.append((jira_key, payload))


def _read_report(report_path):
    with open(report_path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class SetProjectLastUpdatedJiraFieldTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.addCleanup(self.connection.close)
        self.sync_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

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

    def _project(self, gid: str, name: str, team_gid: str = "team_a") -> None:
        self.database.upsert_project(
            self.connection,
            {"gid": gid, "name": name, "team": {"gid": team_gid, "name": team_gid}},
            self.sync_time,
        )

    def _task(self, gid: str, project_gid: str, modified_at: str) -> None:
        self.database.upsert_task(
            self.connection,
            {
                "gid": gid,
                "name": f"Task {gid}",
                "modified_at": modified_at,
                "memberships": [{"project": {"gid": project_gid}}],
            },
            self.sync_time,
        )

    def _map(self, project_gid: str, jira_key: str) -> None:
        self.database.upsert_team_project_jira_mappings(
            self.connection,
            [{"kind": "project", "asana_gid": project_gid, "jira_key": jira_key}],
            self.sync_time,
        )

    def _run(self, client, **kwargs):
        return set_project_last_updated_jira_field(
            database_path=str(self.db_path),
            jira_base_url="https://example.atlassian.net",
            output_dir=self.workspace / "exports",
            assume_yes=True,
            client=client,
            **kwargs,
        )

    def test_sets_last_updated_date_on_mapped_project(self) -> None:
        self._project("proj_1", "Website Redesign")
        self._task("task_1", "proj_1", "2026-01-02T09:00:00.000Z")
        self._task("task_2", "proj_1", "2026-07-10T15:30:00.000Z")
        self._map("proj_1", "ATJM-42")
        self.connection.commit()
        client = FakeJiraClient()

        report = _read_report(self._run(client))

        self.assertEqual(len(client.updates), 1)
        jira_key, payload = client.updates[0]
        self.assertEqual(jira_key, "ATJM-42")
        self.assertEqual(payload, {"fields": {"customfield_11475": "2026-07-10"}})
        updated = [r for r in report if r["status"] == "updated"]
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0]["jira_key"], "ATJM-42")
        self.assertEqual(updated[0]["last_updated"], "2026-07-10")

    def test_skips_project_without_tasks(self) -> None:
        self._project("proj_empty", "Archived Ideas")
        self._map("proj_empty", "ATJM-99")
        self.connection.commit()
        client = FakeJiraClient()

        report = _read_report(self._run(client))

        self.assertEqual(client.updates, [])
        skipped = [r for r in report if r["status"] == "skipped-no-date"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["jira_key"], "ATJM-99")

    def test_skips_unmapped_project(self) -> None:
        self._project("proj_2", "Mobile App")
        self._task("task_3", "proj_2", "2026-05-02T12:00:00.000Z")
        self.connection.commit()
        client = FakeJiraClient()

        report = _read_report(self._run(client))

        self.assertEqual(client.updates, [])
        skipped = [r for r in report if r["status"] == "skipped-unmapped"]
        self.assertEqual(len(skipped), 1)

    def test_dry_run_makes_no_updates(self) -> None:
        self._project("proj_1", "Website Redesign")
        self._task("task_1", "proj_1", "2026-07-10T15:30:00.000Z")
        self._map("proj_1", "ATJM-42")
        self.connection.commit()
        client = FakeJiraClient()

        report = _read_report(self._run(client, dry_run=True))

        self.assertEqual(client.updates, [])
        self.assertEqual([r["status"] for r in report], ["dry-run-updated"])

    def test_limit_caps_updates(self) -> None:
        self._project("proj_1", "Alpha")
        self._task("task_1", "proj_1", "2026-07-10T00:00:00.000Z")
        self._map("proj_1", "ATJM-1")
        self._project("proj_2", "Beta")
        self._task("task_2", "proj_2", "2026-06-01T00:00:00.000Z")
        self._map("proj_2", "ATJM-2")
        self.connection.commit()
        client = FakeJiraClient()

        report = _read_report(self._run(client, limit=1))

        self.assertEqual(len(client.updates), 1)
        self.assertEqual(len([r for r in report if r["status"] == "updated"]), 1)
        self.assertEqual(len([r for r in report if r["status"] == "skipped-limit"]), 1)

    def test_team_gid_scopes_updates(self) -> None:
        self._project("proj_a", "Alpha", team_gid="team_a")
        self._task("task_a", "proj_a", "2026-07-10T00:00:00.000Z")
        self._map("proj_a", "ATJM-1")
        self._project("proj_b", "Beta", team_gid="team_b")
        self._task("task_b", "proj_b", "2026-06-01T00:00:00.000Z")
        self._map("proj_b", "ATJM-2")
        self.connection.commit()
        client = FakeJiraClient()

        self._run(client, team_gid="team_a")

        self.assertEqual(len(client.updates), 1)
        self.assertEqual(client.updates[0][0], "ATJM-1")


class SetProjectFieldSettingsTest(TestCase):
    PROFILE_FIELD_ID = "customfield_99999"
    EXPLICIT_FIELD_ID = "customfield_555"

    def setUp(self) -> None:
        temp_dir = TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.profile_path = Path(temp_dir.name) / "profile.env"
        self.profile_path.write_text("", encoding="utf-8")
        self.core = MagicMock(return_value=Path("report.csv"))
        patchers = [
            patch.object(set_project_module, "resolve_credentials_path", return_value=self.profile_path),
            patch.object(set_project_module, "load_credentials", return_value=MagicMock()),
            patch.object(set_project_module, "resolve_jira_base_url", return_value="https://example.atlassian.net"),
            patch.object(set_project_module, "set_project_last_updated_jira_field", self.core),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _field_id_passed(self, argv: list[str]) -> str:
        set_project_module.main(argv)
        self.core.assert_called_once()
        return self.core.call_args.kwargs["field_id"]

    def test_profile_field_id_used_when_flag_omitted(self) -> None:
        save_migration_settings(self.profile_path, MigrationSettings(last_working_day_field_id=self.PROFILE_FIELD_ID))
        self.assertEqual(self._field_id_passed([]), self.PROFILE_FIELD_ID)

    def test_explicit_field_id_overrides_profile(self) -> None:
        save_migration_settings(self.profile_path, MigrationSettings(last_working_day_field_id=self.PROFILE_FIELD_ID))
        self.assertEqual(self._field_id_passed(["--field-id", self.EXPLICIT_FIELD_ID]), self.EXPLICIT_FIELD_ID)

    def test_unset_profile_falls_back_to_default_field_id(self) -> None:
        save_migration_settings(self.profile_path, DEFAULT_SETTINGS)
        self.assertEqual(self._field_id_passed([]), DEFAULT_SETTINGS.last_working_day_field_id)
