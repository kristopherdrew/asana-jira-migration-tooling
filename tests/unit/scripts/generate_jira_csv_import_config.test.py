from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import MagicMock, patch

from src.lib.config import Credentials, JiraSite
from src.lib.jira_csv_import_config import (
    ArchiveImportSettings,
    JiraFieldChoice,
)
from scripts.atlassian.generate_jira_csv_import_config import main


class GenerateJiraCsvImportConfigTest(TestCase):
    def test_generates_reusable_config_without_an_existing_csv(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_path = root / "client.env"
            output_path = root / f"example_sandbox_jira_{date.today().isoformat()}_jira_config.txt"
            env_path.write_text("export CLIENT_NAME=Example\n", encoding="utf-8")
            credentials = Credentials(
                token="asana",
                workspace_gid="workspace",
                skip_ssl_verification=True,
                atlassian_username="user@example.com",
                atlassian_token="token",
                jira_sites=(
                    JiraSite(
                        "sandbox_jira",
                        "Sandbox Jira",
                        "https://jira.example.atlassian.net",
                    ),
                ),
                jira_default_site="sandbox_jira",
            )
            settings = ArchiveImportSettings(
                external_id=JiraFieldChoice(
                    "customfield_10699",
                    "External issue ID",
                    "Text",
                    "string",
                ),
                start_date=JiraFieldChoice(
                    "customfield_10700",
                    "Start Date",
                    "Date",
                    "date",
                ),
                project_link=JiraFieldChoice(
                    "customfield_10701",
                    "Asana Project Link",
                    "URL",
                    "string",
                ),
                task_link=JiraFieldChoice(
                    "customfield_10702",
                    "Asana Task Link",
                    "URL",
                    "string",
                ),
            )
            client = MagicMock()
            client.get_custom_fields.return_value = []
            with patch(
                "scripts.atlassian.generate_jira_csv_import_config.load_credentials",
                return_value=credentials,
            ), patch(
                "scripts.atlassian.generate_jira_csv_import_config.resolve_credentials_path",
                return_value=env_path,
            ), patch(
                "scripts.atlassian.generate_jira_csv_import_config.JiraClient",
                return_value=client,
            ) as client_class, patch(
                "scripts.atlassian.generate_jira_csv_import_config.review_archive_import_settings",
                return_value=(settings, settings),
            ), patch(
                "builtins.input",
                return_value="",
            ):
                main([
                    "--csv-name",
                    "future_export.csv",
                    "--jira-site",
                    "sandbox_jira",
                ])

            self.assertTrue(client_class.call_args.kwargs["skip_ssl_verification"])
            config = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(config["config.file.name"], "future_export.csv")
            self.assertEqual(
                config["config.field.mappings"]["start_on"]["existing.custom.field"],
                "10700",
            )
            self.assertEqual(config["config.field.mappings"]["Tags"]["jira.field"], "labels")
            self.assertNotIn("completed", config["config.value.mappings"])
            self.assertIn("task_name", config["config.field.mappings"])
            client.get_resolutions.assert_not_called()

    def test_requires_atlassian_credentials(self) -> None:
        credentials = Credentials(
            token="asana",
            workspace_gid="workspace",
            skip_ssl_verification=False,
            atlassian_username=None,
            atlassian_token=None,
        )
        with patch(
            "scripts.atlassian.generate_jira_csv_import_config.load_credentials",
            return_value=credentials,
        ):
            with self.assertRaisesRegex(SystemExit, "ATLASSIAN_USERNAME"):
                main([])
