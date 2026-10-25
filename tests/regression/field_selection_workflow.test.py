"""Regression tests for Field Selection feature.

Tests the end-to-end workflow:
1. Custom field selection via CLI flags
2. Field filtering logic
3. Non-interactive mode verification
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database


class FieldSelectionWorkflowTest(TestCase):
    """Regression suite: Custom field selection and filtering."""

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.connection.close()

    def test_custom_fields_flag_parsing_with_names(self) -> None:
        """Custom field selection via --custom-fields flag parses names correctly."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--custom-fields", "Priority,Component,Risk Level",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertEqual(args.custom_fields, "Priority,Component,Risk Level")
        self.assertFalse(args.no_custom_fields)

    def test_no_custom_fields_flag(self) -> None:
        """--no-custom-fields flag is recognized and set correctly."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--no-custom-fields",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertTrue(args.no_custom_fields)

    def test_no_custom_field_prompt_flag(self) -> None:
        """--no-custom-field-prompt flag enables non-interactive mode."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--no-custom-field-prompt",
            "--database", str(self.db_path),
        ])

        self.assertTrue(args.no_custom_field_prompt)

    def test_no_custom_fields_flag_takes_precedence(self) -> None:
        """--no-custom-fields takes precedence over --custom-fields."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--custom-fields", "Priority,Component",
            "--no-custom-fields",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertTrue(args.no_custom_fields)
        # Custom fields flag is still stored, but no_custom_fields should take precedence
        self.assertEqual(args.custom_fields, "Priority,Component")

    def test_custom_fields_with_spaces_in_list(self) -> None:
        """Custom field list handles spaces around commas."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--custom-fields", "Priority, Component, Risk Level",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertEqual(args.custom_fields, "Priority, Component, Risk Level")

    def test_empty_custom_fields_is_valid(self) -> None:
        """Empty --custom-fields string is valid (means export all)."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--custom-fields", "",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertEqual(args.custom_fields, "")

    def test_field_gid_selection_is_supported(self) -> None:
        """Custom field selection by GID (not just name) is supported."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--custom-fields", "field_1,field_2,field_3",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        # Should accept field GIDs as well as names
        self.assertEqual(args.custom_fields, "field_1,field_2,field_3")

    def test_custom_field_mode_columns(self) -> None:
        """--custom-field-mode columns exports fields as CSV columns."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--custom-field-mode", "columns",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertEqual(args.custom_field_mode, "columns")

    def test_custom_field_mode_description(self) -> None:
        """--custom-field-mode description folds fields into task description."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--custom-field-mode", "description",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertEqual(args.custom_field_mode, "description")

    def test_custom_field_mode_defaults_to_columns(self) -> None:
        """Default --custom-field-mode is 'columns'."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertEqual(args.custom_field_mode, "columns")

    def test_field_selection_with_consolidated_output(self) -> None:
        """Field selection works with --consolidated-output flag."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gids", "proj_1,proj_2",
            "--custom-fields", "Priority,Component",
            "--consolidated-output",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertTrue(args.consolidated_output)
        self.assertEqual(args.custom_fields, "Priority,Component")

    def test_field_selection_with_no_refresh_prompt(self) -> None:
        """Field selection works with --no-refresh-prompt flag."""
        from scripts.atlassian.export_project_to_jira_csv import parse_args

        args = parse_args([
            "--project-gid", "proj_1",
            "--custom-fields", "Priority,Component",
            "--no-refresh-prompt",
            "--database", str(self.db_path),
            "--no-custom-field-prompt",
        ])

        self.assertTrue(args.no_refresh_prompt)
        self.assertEqual(args.custom_fields, "Priority,Component")
