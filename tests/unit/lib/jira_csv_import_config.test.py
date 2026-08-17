from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from src.lib.jira_csv_import_config import (
    ArchiveImportSettings,
    JiraFieldChoice,
    build_archive_import_config,
    copy_active_profile_import_config,
    load_archive_import_settings,
    normalize_fields,
    prompt_field_choice,
    profile_import_config_path,
    save_archive_import_settings,
)


class JiraCsvImportConfigTest(TestCase):
    def test_active_profile_config_is_copied_into_export_bundle(self) -> None:
        with TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            env_path = root / "acme.env"
            source = profile_import_config_path(env_path, date(2026, 8, 16))
            source.write_text('{"config.version": "2.0"}\n', encoding="utf-8")
            output_dir = root / "export"
            with patch(
                "src.lib.jira_csv_import_config.resolve_credentials_path",
                return_value=env_path,
            ):
                copied = copy_active_profile_import_config(output_dir)
            self.assertEqual(
                copied,
                output_dir / "acme_2026-08-16_jira_config.txt",
            )
            self.assertEqual(
                copied.read_text(encoding="utf-8"),
                source.read_text(encoding="utf-8"),
            )

    def test_field_picker_prefers_exact_name_before_fuzzy_matches(self) -> None:
        fields = [
            JiraFieldChoice("customfield_10001", "Asana Project Link", "URL", "string"),
            JiraFieldChoice("customfield_10002", "Legacy Asana Project Link", "URL", "string"),
        ]
        output = []
        selected, persist = prompt_field_choice(
            "Asana Project Link",
            fields,
            None,
            input_fn=lambda _prompt: "1",
            output_fn=output.append,
        )
        self.assertEqual(selected.field_id, "customfield_10001")
        self.assertTrue(persist)
        self.assertNotIn("Legacy Asana Project Link", "\n".join(output))
        self.assertIn("[2] Search custom fields by name", "\n".join(output))

    def test_field_picker_can_search_instead_of_using_exact_match(self) -> None:
        fields = [
            JiraFieldChoice("customfield_10001", "Asana Section", "Text", "string"),
            JiraFieldChoice("customfield_10002", "Imported Section", "Text", "string"),
        ]
        answers = iter(["2", "imported", "1"])
        selected, persist = prompt_field_choice(
            "Asana Section",
            fields,
            None,
            input_fn=lambda _prompt: next(answers),
            output_fn=lambda _message: None,
        )
        self.assertEqual(selected.field_id, "customfield_10002")
        self.assertTrue(persist)

    def test_field_picker_can_search_custom_fields_by_name(self) -> None:
        fields = [
            JiraFieldChoice("summary", "Summary", "Text", "string"),
            JiraFieldChoice("customfield_10001", "Asana Section", "Text", "string"),
            JiraFieldChoice("customfield_10002", "Legacy Section", "Text", "string"),
            JiraFieldChoice("customfield_10003", "Asana Start Date", "Date", "date"),
        ]
        answers = iter(["1", "asana", "2"])
        output = []
        selected, persist = prompt_field_choice(
            "Section",
            fields,
            None,
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
        )
        self.assertEqual(selected.field_id, "customfield_10003")
        self.assertTrue(persist)
        rendered = "\n".join(output)
        self.assertIn("Asana Section", rendered)
        self.assertIn("Asana Start Date", rendered)
        self.assertNotIn("Summary —", rendered)

    def test_field_search_excludes_weak_fuzzy_results_when_substrings_match(self) -> None:
        fields = [
            JiraFieldChoice("customfield_10001", "Team (Asana)", "URL", "string"),
            JiraFieldChoice("customfield_10002", "Asana Migration Decision", "Select", "option"),
            JiraFieldChoice("customfield_10003", "Last name", "Text", "string"),
            JiraFieldChoice("customfield_10004", "Reason", "Text", "string"),
        ]
        answers = iter(["1", "Asana", "1"])
        output = []
        selected, persist = prompt_field_choice(
            "Asana Section",
            fields,
            None,
            input_fn=lambda _prompt: next(answers),
            output_fn=output.append,
        )
        self.assertEqual(selected.field_id, "customfield_10001")
        self.assertTrue(persist)
        rendered = "\n".join(output)
        self.assertIn("Team (Asana)", rendered)
        self.assertIn("Asana Migration Decision", rendered)
        self.assertNotIn("Last name", rendered)
        self.assertNotIn("Reason", rendered)

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
                project_link=JiraFieldChoice(
                    "customfield_16194", "Asana Project Link", "URL", "string"
                ),
                task_link=JiraFieldChoice(
                    "customfield_16195", "Asana Task Link", "URL", "string"
                ),
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
                "task_name,modified_section,Section,Asana Project Link,Asana Task Link,"
                "External issue ID,start_on,completed,Tags\n"
                "Task,Open,Inbox,https://asana/project,https://asana/task,1,"
                "01/Jan/25 1:00 PM,Yes,Migration\n",
                encoding="utf-8",
            )
            settings = ArchiveImportSettings(
                external_id=JiraFieldChoice(
                    "customfield_10699", "External issue ID", "Text", "string"
                ),
            )
            config = build_archive_import_config(csv_path, settings)
            mappings = config["config.field.mappings"]
            self.assertNotIn("Section", mappings)
            self.assertNotIn("start_on", mappings)
            self.assertEqual(
                mappings["External issue ID"]["existing.custom.field"], "10699"
            )
            self.assertEqual(mappings["modified_section"]["jira.field"], "status")
            self.assertEqual(mappings["Tags"]["jira.field"], "labels")
            self.assertEqual(mappings["completed"]["jira.field"], "resolution")
            self.assertTrue(mappings["completed"]["manualMapping"])
            self.assertNotIn("modified_section", config["config.value.mappings"])
            self.assertNotIn("completed", config["config.value.mappings"])
            json.dumps(config)
