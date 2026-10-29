from __future__ import annotations

from unittest import TestCase

from scripts.atlassian.export_team_project_jira_csv import (
    EPIC_INSTRUCTIONS,
    TASK_INSTRUCTIONS,
    build_instruction_texts,
)
from src.lib.migration_defaults import DEFAULT_SETTINGS, MigrationSettings

DEADLINE_SENTENCE = "Ensure a migration decision has been recorded for every project by"
CUSTOM_TEAM_NAME = "Acme Atlassian"
CUSTOM_READ_ONLY_LABEL = "Acme Read-Only"
CUSTOM_PRESERVATION_LABEL = "Acme Preservation-Only"


class ExportInstructionSettingsTest(TestCase):
    def test_default_settings_render_the_module_level_constants(self) -> None:
        texts = build_instruction_texts(DEFAULT_SETTINGS)

        self.assertEqual(texts.epic, EPIC_INSTRUCTIONS, "Default settings must match the public constant")
        self.assertEqual(texts.task, TASK_INSTRUCTIONS, "Default settings must match the public constant")

    def test_epic_deadline_sentence_is_omitted_when_unset(self) -> None:
        texts = build_instruction_texts(DEFAULT_SETTINGS)

        self.assertNotIn(DEADLINE_SENTENCE, texts.epic, "An unset deadline must not render a stale date")
        self.assertIn(
            "If someone else should be representing this Asana team",
            texts.epic,
            "Other Epic bullets must remain when the deadline is omitted",
        )

    def test_epic_deadline_sentence_renders_configured_deadline(self) -> None:
        texts = build_instruction_texts(MigrationSettings(epic_deadline="March 3"))

        self.assertIn(f"{DEADLINE_SENTENCE} March 3.", texts.epic, "A configured deadline must appear verbatim")
        self.assertIn(f"{DEADLINE_SENTENCE} March 3.", str(texts.epic_adf), "The ADF path must carry the same deadline")

    def test_custom_archive_labels_and_team_name_appear_in_task_instructions(self) -> None:
        texts = build_instruction_texts(
            MigrationSettings(
                atlassian_team_name=CUSTOM_TEAM_NAME,
                archive_read_only_label=CUSTOM_READ_ONLY_LABEL,
                archive_preservation_label=CUSTOM_PRESERVATION_LABEL,
            )
        )

        self.assertIn(CUSTOM_READ_ONLY_LABEL, texts.task, "The read-only archive heading must use the setting")
        self.assertIn(CUSTOM_PRESERVATION_LABEL, texts.task, "The preservation archive heading must use the setting")
        self.assertIn(f"needed by the {CUSTOM_TEAM_NAME} team.", texts.task, "Team wording must use the setting")
        self.assertIn(
            f"{CUSTOM_READ_ONLY_LABEL}, {CUSTOM_PRESERVATION_LABEL} or Abandon.",
            texts.task,
            "The Required Action decision list must use the archive labels",
        )

    def test_custom_settings_do_not_leak_into_default_rendering(self) -> None:
        build_instruction_texts(MigrationSettings(atlassian_team_name=CUSTOM_TEAM_NAME, epic_deadline="July 8"))

        self.assertEqual(
            build_instruction_texts(DEFAULT_SETTINGS).task,
            TASK_INSTRUCTIONS,
            "Rendering with custom settings must not mutate the default constants",
        )
