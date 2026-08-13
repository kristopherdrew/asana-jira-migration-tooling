from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.jira_csv_import_config import (
    ArchiveImportSettings,
    JiraFieldChoice,
    JiraResolutionChoice,
    build_archive_import_config,
    load_archive_import_settings,
    normalize_fields,
    save_archive_import_settings,
)


class JiraCsvImportConfigTest(TestCase):
    def test_normalize_fields_reports_name_id_and_type(self) -> None:
        fields = normalize_fields(
            [
                {
                    "id": "customfield_10015",
                    "name": "Start date",
                    "schema": {
                        "type": "date",
                        "custom": "com.atlassian.jira.plugin.system.customfieldtypes:datepicker",
                    },
                }
            ]
        )
        self.assertEqual(fields[0].field_id, "customfield_10015")
        self.assertEqual(fields[0].name, "Start date")
        self.assertEqual(fields[0].schema_type, "date")
        self.assertIn("Date", fields[0].field_type)

    def test_settings_round_trip_per_site(self) -> None:
        with TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "client.env"
            env_path.write_text("export CLIENT_NAME=Example\n", encoding="utf-8")
            settings = ArchiveImportSettings(
                section=JiraFieldChoice("customfield_16193", "Section", "Text", "string"),
                completed_resolution=JiraResolutionChoice("10000", "Done"),
            )
            save_archive_import_settings(env_path, "sandbox_jira", settings)
            loaded = load_archive_import_settings(env_path, "sandbox_jira")
            self.assertEqual(loaded, settings)
            self.assertEqual(
                load_archive_import_settings(env_path, "production_jira"),
                ArchiveImportSettings(),
            )
            self.assertIn("CLIENT_NAME=Example", env_path.read_text(encoding="utf-8"))

    def test_config_omits_skipped_fields_and_status_value_mapping(self) -> None:
        with TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "archive.csv"
            csv_path.write_text(
                "task_name,modified_section,Section,External issue ID,start_at,completed\n"
                "Task,Open,Inbox,1,01/Jan/25 1:00 PM,Yes\n",
                encoding="utf-8",
            )
            settings = ArchiveImportSettings(
                external_id=JiraFieldChoice(
                    "customfield_10699", "External issue ID", "Text", "string"
                ),
                completed_resolution=JiraResolutionChoice("10000", "Done"),
            )
            config = build_archive_import_config(csv_path, settings)
            mappings = config["config.field.mappings"]
            self.assertNotIn("Section", mappings)
            self.assertNotIn("start_at", mappings)
            self.assertEqual(
                mappings["External issue ID"]["existing.custom.field"], "10699"
            )
            self.assertEqual(mappings["modified_section"]["jira.field"], "status")
            self.assertNotIn("modified_section", config["config.value.mappings"])
            self.assertEqual(
                config["config.value.mappings"]["completed"],
                {"No": "<<blank>>", "Yes": "10000"},
            )
            json.dumps(config)

