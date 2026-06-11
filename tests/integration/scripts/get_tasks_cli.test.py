import sqlite3
import tempfile
from os import environ
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import scripts.get_tasks as get_tasks_cli


class GetTasksCliIntegrationTest(TestCase):
    def test_main_persists_tasks_with_streaming(self) -> None:
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

            fake_tasks = [
                {
                    "gid": f"task_{idx}",
                    "name": f"Task {idx}",
                    "resource_type": "task",
                    "resource_subtype": "default_task",
                    "completed": False,
                    "custom_fields": [],
                    "memberships": [],
                    "workspace": {"gid": "workspace", "name": "Workspace"},
                }
                for idx in range(10)
            ]

            def fake_task_detail(client, task_gid, opt_fields=None):  # noqa: D401
                return {
                    "gid": task_gid,
                    "name": f"Detail {task_gid}",
                    "resource_type": "task",
                    "resource_subtype": "default_task",
                    "completed": False,
                    "custom_fields": [],
                    "memberships": [],
                    "workspace": {"gid": "workspace", "name": "Workspace"},
                }

            with (
                patch.object(get_tasks_cli, "DEFAULT_CONFIG_ROOT", str(tmp_path)),
                patch.object(get_tasks_cli, "DEFAULT_DB_PATH", str(database_path)),
                patch.object(get_tasks_cli, "AsanaHttpClient", MagicMock(return_value=MagicMock())),
                patch.object(
                    get_tasks_cli,
                    "determine_projects",
                    MagicMock(return_value=[
                        {
                            "project_gid": "proj_1",
                            "project_name": "Project",
                            "team_name": "Team",
                        }
                    ]),
                ),
                patch.object(get_tasks_cli, "fetch_project_tasks", MagicMock(return_value=fake_tasks)),
                patch.object(get_tasks_cli, "fetch_task_detail", side_effect=fake_task_detail),
                patch.object(get_tasks_cli, "fetch_subtasks", MagicMock(return_value=[])),
                patch("src.lib.user_backfill.UserBackfill.ensure_users", autospec=True, return_value=None),
                patch("src.lib.user_backfill.UserBackfill.prime", autospec=True, return_value=None),
                patch.dict(environ, {"ASANA_JIRA_ENV_FILE": str(env_path)}, clear=False),
            ):
                get_tasks_cli.main(["--project-gid", "proj_1"])

            connection = sqlite3.connect(database_path)
            try:
                rows = list(
                    connection.execute(
                        "SELECT gid, name, hierarchy_depth FROM tasks ORDER BY gid"
                    )
                )
            finally:
                connection.close()

            self.assertEqual(len(rows), 10)
            self.assertTrue(all(row[0].startswith("task_") for row in rows))
            self.assertTrue(all(row[2] == 0 for row in rows))
