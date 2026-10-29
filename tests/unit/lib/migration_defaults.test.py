from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from unittest import TestCase

from src.lib.migration_defaults import (
    DEFAULT_SETTINGS,
    MigrationSettings,
    is_migration_settings_confirmed,
    load_migration_settings,
    save_migration_settings,
)

CUSTOM_SETTINGS = MigrationSettings(
    epic_deadline="July 8",
    atlassian_team_name="Acme Atlassian",
    archive_read_only_label="Archive-Read Only (Acme)",
    archive_preservation_label="Archive-Preservation Only (Acme)",
    last_working_day_field_id="customfield_99999",
    decision_field_name="Acme Migration Decision",
    jira_project_key="ACME",
)


class MigrationDefaultsTest(TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.env_path = Path(self._tmp.name) / "profile.env"
        self.env_path.write_text("export CLIENT_NAME=Acme\n", encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_profile_file_yields_neutral_defaults(self) -> None:
        missing = Path(self._tmp.name) / "absent.env"

        settings = load_migration_settings(missing)

        self.assertEqual(settings, DEFAULT_SETTINGS, "A missing profile must fall back to neutral defaults")
        self.assertIsNone(settings.epic_deadline, "No deadline should be rendered unless configured")
        self.assertFalse(is_migration_settings_confirmed(missing), "A missing profile is unconfirmed")

    def test_save_then_load_round_trips_custom_values(self) -> None:
        save_migration_settings(self.env_path, CUSTOM_SETTINGS)

        self.assertEqual(load_migration_settings(self.env_path), CUSTOM_SETTINGS, "Saved values must reload unchanged")
        self.assertTrue(is_migration_settings_confirmed(self.env_path), "Saving marks the settings confirmed")
        self.assertIn("export CLIENT_NAME=Acme", self.env_path.read_text(encoding="utf-8"), "Unrelated profile lines must be preserved")

    def test_clearing_optional_values_removes_them_from_profile(self) -> None:
        save_migration_settings(self.env_path, CUSTOM_SETTINGS)

        save_migration_settings(self.env_path, replace(CUSTOM_SETTINGS, epic_deadline=None, jira_project_key=None))

        reloaded = load_migration_settings(self.env_path)
        self.assertIsNone(reloaded.epic_deadline, "A cleared deadline must not resurface from the previous save")
        self.assertIsNone(reloaded.jira_project_key, "A cleared project key must not resurface from the previous save")

    def test_blank_profile_value_falls_back_to_default(self) -> None:
        self.env_path.write_text("export MIGRATION_ATLASSIAN_TEAM_NAME=''\n", encoding="utf-8")

        settings = load_migration_settings(self.env_path)

        self.assertEqual(
            settings.atlassian_team_name,
            DEFAULT_SETTINGS.atlassian_team_name,
            "An empty override must not blank out the wording",
        )
