import sqlite3
import tempfile
from os import environ
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import scripts.get_projects as get_projects_cli


class GetProjectsCliIntegrationTest(TestCase):
    def test_main_writes_projects_to_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            env_path = tmp_path / ".asana_jira_env"
            env_path.write_text(
                "\n".join(
                    [
                        "ASANA_TOKEN=token",
                        "ASANA_WORKSPACE_GID=workspace",
                        "SKIP_SSL_VERIFICATION=false",
                    ]
                ),
                encoding="utf-8",
            )
            database_path = tmp_path / "asana.sqlite"

            fake_projects = [
                {
                    "gid": "proj_1",
                    "name": "Integration Project",
                    "resource_subtype": "default",
                    "archived": False,
                    "permalink_url": "https://app.asana.com/0/123/456",
                    "created_at": "2025-01-01T00:00:00.000Z",
                    "modified_at": "2025-01-02T00:00:00.000Z",
                    "start_on": None,
                    "due_on": None,
                    "completed": False,
                    "completed_at": None,
                    "completed_by": None,
                    "owner": {"gid": "user_1", "name": "Owner"},
                    "team": {"gid": "team_1", "name": "Team"},
                    "privacy_setting": None,
                    "notes": "",
                    "html_notes": "",
                    "custom_fields": [],
                    "custom_field_settings": [],
                }
            ]

            fetch_mock = MagicMock(return_value=fake_projects)
            with (
                patch.object(get_projects_cli, "DEFAULT_CONFIG_ROOT", str(tmp_path)),
                patch.object(get_projects_cli, "DEFAULT_DB_PATH", str(database_path)),
                patch.object(get_projects_cli, "AsanaHttpClient", MagicMock(return_value=MagicMock())),
                patch.object(get_projects_cli, "fetch_workspace_projects", fetch_mock),
                patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=False),
            ):
                get_projects_cli.main([])
            fetch_mock.assert_called_once()
            _, kwargs = fetch_mock.call_args
            self.assertFalse(kwargs.get("archived"))

            connection = sqlite3.connect(database_path)
            try:
                row = connection.execute("SELECT gid, name FROM projects").fetchone()
            finally:
                connection.close()

            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row[0], "proj_1")
            self.assertEqual(row[1], "Integration Project")

    def test_main_includes_archived_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            env_path = tmp_path / ".asana_jira_env"
            env_path.write_text(
                "\n".join(
                    [
                        "ASANA_TOKEN=token",
                        "ASANA_WORKSPACE_GID=workspace",
                        "SKIP_SSL_VERIFICATION=false",
                    ]
                ),
                encoding="utf-8",
            )
            database_path = tmp_path / "asana.sqlite"

            active_projects = [{"gid": "proj_active", "name": "Active", "custom_fields": [], "custom_field_settings": []}]
            archived_projects = [{"gid": "proj_archived", "name": "Archived", "custom_fields": [], "custom_field_settings": []}]

            fetch_mock = MagicMock(side_effect=[active_projects, archived_projects])
            with (
                patch.object(get_projects_cli, "DEFAULT_CONFIG_ROOT", str(tmp_path)),
                patch.object(get_projects_cli, "DEFAULT_DB_PATH", str(database_path)),
                patch.object(get_projects_cli, "AsanaHttpClient", MagicMock(return_value=MagicMock())),
                patch.object(get_projects_cli, "fetch_workspace_projects", fetch_mock),
                patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=False),
            ):
                get_projects_cli.main(["--archived-scope", "all"])

            self.assertEqual(fetch_mock.call_count, 2)
            archived_values = [call.kwargs.get("archived") for call in fetch_mock.call_args_list]
            self.assertEqual(archived_values, [False, True])
