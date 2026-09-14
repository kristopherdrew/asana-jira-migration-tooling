from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from unittest import TestCase
from unittest.mock import MagicMock, patch

from scripts import inspect_task_apps
from src.lib.config import Credentials


class InspectTaskAppsScriptTest(TestCase):
    def test_main_prints_probe_json(self) -> None:
        probe = {
            "task_gid": "task_1",
            "summary": {"total_attachments": 0},
            "attachments": [],
            "attachment_details": [],
        }
        credentials = Credentials(
            token="asana-token",
            workspace_gid="workspace_1",
            skip_ssl_verification=True,
        )
        client = MagicMock()

        with patch.object(inspect_task_apps, "load_credentials", return_value=credentials), patch.object(
            inspect_task_apps, "AsanaHttpClient", return_value=client
        ) as client_cls, patch.object(
            inspect_task_apps, "fetch_task_app_attachment_probe", return_value=probe
        ) as fetch_probe:
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                inspect_task_apps.main(["--task-gid", "task_1", "--no-details", "--log-level", "ERROR"])

        client_cls.assert_called_once_with(token="asana-token", skip_ssl_verification=True)
        fetch_probe.assert_called_once_with(client, "task_1", include_details=False, page_size=100)
        self.assertEqual(json.loads(stdout.getvalue())["task_gid"], "task_1")
