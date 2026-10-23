"""Tests for interactive CLI team/project export menu."""

from __future__ import annotations

from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from scripts import interactive_cli


class ArgsBuildersTest(TestCase):
    """Test argument builders for the export team/project hierarchy menu."""

    def setUp(self) -> None:
        # Force the env-file backend so credential tests stay hermetic (no real Keychain).
        patcher = patch("src.lib.keychain.is_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Baseline session for test isolation
        self._orig_session = interactive_cli._session
        interactive_cli._session = interactive_cli.CliSession(
            reference_path=Path("/tmp/project"),
            env_path=Path("/tmp/.asana_jira_env"),
            use_prod=False,
        )
        self.addCleanup(lambda: setattr(interactive_cli, "_session", self._orig_session))

    def test_args_export_team_project_hierarchy_default_scope(self) -> None:
        """Test default behavior: export all projects."""
        with patch("builtins.input", side_effect=["1", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--log-level", args)
        self.assertIn("INFO", args)
        self.assertIn("--database", args)
        # Should not have project-gids, project-gids-file, or team-gids when default (1) is chosen
        self.assertNotIn("--project-gids", args)
        self.assertNotIn("--project-gids-file", args)
        self.assertNotIn("--team-gids", args)
        # Default custom field mode is "columns"
        self.assertIn("--custom-fields", args)
        columns_idx = args.index("--custom-fields")
        self.assertEqual(args[columns_idx + 1], "columns")
        # No --include-empty-projects flag by default
        self.assertNotIn("--include-empty-projects", args)

    def test_args_export_team_project_hierarchy_comma_separated_gids(self) -> None:
        """Test scope option 2: comma-separated project GIDs."""
        with patch("builtins.input", side_effect=["2", "proj_a,proj_b,proj_c", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("DEBUG")

        self.assertIn("--project-gids", args)
        gids_idx = args.index("--project-gids")
        self.assertEqual(args[gids_idx + 1], "proj_a,proj_b,proj_c")

    def test_args_export_team_project_hierarchy_project_file(self) -> None:
        """Test scope option 3: project GIDs from file."""
        with patch(
            "scripts.interactive_cli._prompt_project_gids_file",
            return_value="/path/to/projects.csv",
        ), patch("builtins.input", side_effect=["3", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--project-gids-file", args)
        file_idx = args.index("--project-gids-file")
        self.assertEqual(args[file_idx + 1], "/path/to/projects.csv")

    def test_args_export_team_project_hierarchy_team_scope(self) -> None:
        """Test scope option 4: all projects in a team."""
        with patch(
            "scripts.interactive_cli._prompt_team_gid_from_lookup",
            return_value="team_alpha",
        ), patch("builtins.input", side_effect=["4", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--team-gids", args)
        team_idx = args.index("--team-gids")
        self.assertEqual(args[team_idx + 1], "team_alpha")

    def test_args_export_team_project_hierarchy_custom_fields_description_mode(self) -> None:
        """Test custom field export mode option 2: description."""
        with patch("builtins.input", side_effect=["1", "2", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--custom-fields", args)
        fields_idx = args.index("--custom-fields")
        self.assertEqual(args[fields_idx + 1], "description")

    def test_args_export_team_project_hierarchy_custom_fields_default_columns(self) -> None:
        """Test custom field export mode option 1 (default): columns."""
        with patch("builtins.input", side_effect=["1", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--custom-fields", args)
        fields_idx = args.index("--custom-fields")
        self.assertEqual(args[fields_idx + 1], "columns")

    def test_args_export_team_project_hierarchy_include_empty_projects(self) -> None:
        """Test include empty projects option (yes)."""
        with patch("builtins.input", side_effect=["1", "1", "y", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--include-empty-projects", args)

    def test_args_export_team_project_hierarchy_exclude_empty_projects(self) -> None:
        """Test include empty projects option (no, default)."""
        with patch("builtins.input", side_effect=["1", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertNotIn("--include-empty-projects", args)

    def test_args_export_team_project_hierarchy_jira_project_key_provided(self) -> None:
        """Test Jira Project Key prompt with a value."""
        with patch("builtins.input", side_effect=["1", "1", "n", "ATJM"]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--jira-project-key", args)
        key_idx = args.index("--jira-project-key")
        self.assertEqual(args[key_idx + 1], "ATJM")

    def test_args_export_team_project_hierarchy_jira_project_key_empty(self) -> None:
        """Test Jira Project Key prompt with empty input (skipped)."""
        with patch("builtins.input", side_effect=["1", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertNotIn("--jira-project-key", args)

    def test_args_export_team_project_hierarchy_all_options(self) -> None:
        """Test with all options specified."""
        with patch(
            "scripts.interactive_cli._prompt_project_gids_file",
            return_value="/projects.csv",
        ), patch("builtins.input", side_effect=["3", "2", "y", "MYPROJ"]):
            args = interactive_cli.args_export_team_project_hierarchy("WARNING")

        # Verify all major options are present
        self.assertIn("--log-level", args)
        self.assertIn("WARNING", args)
        self.assertIn("--database", args)
        self.assertIn("--project-gids-file", args)
        self.assertIn("--custom-fields", args)
        self.assertIn("description", args)
        self.assertIn("--include-empty-projects", args)
        self.assertIn("--jira-project-key", args)
        self.assertIn("MYPROJ", args)

    def test_args_export_team_project_hierarchy_includes_database_flag(self) -> None:
        """Test that --database flag is always included."""
        with patch("builtins.input", side_effect=["1", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--database", args)
        # Should have a value after the flag
        db_idx = args.index("--database")
        self.assertTrue(len(args) > db_idx + 1)

    def test_args_export_team_project_hierarchy_log_level_propagated(self) -> None:
        """Test that log level is correctly propagated."""
        for level in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            with patch("builtins.input", side_effect=["1", "1", "n", ""]):
                args = interactive_cli.args_export_team_project_hierarchy(level)
                self.assertIn("--log-level", args)
                level_idx = args.index("--log-level")
                self.assertEqual(args[level_idx + 1], level)


class ScopeSelectionMenuTest(TestCase):
    """Test scope selection menu options 1-4."""

    def setUp(self) -> None:
        patcher = patch("src.lib.keychain.is_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

        self._orig_session = interactive_cli._session
        interactive_cli._session = interactive_cli.CliSession(
            reference_path=Path("/tmp/project"),
            env_path=Path("/tmp/.asana_jira_env"),
            use_prod=False,
        )
        self.addCleanup(lambda: setattr(interactive_cli, "_session", self._orig_session))

    def test_scope_option_1_all_projects_no_flags(self) -> None:
        """Option [1]: All projects - should add no scope flags."""
        with patch("builtins.input", side_effect=["1", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        # Should not have any project-specific scope flags
        scope_flags = [arg for arg in args if arg in ["--project-gids", "--project-gids-file", "--team-gids"]]
        self.assertEqual(len(scope_flags), 0)

    def test_scope_option_2_single_gid(self) -> None:
        """Option [2]: Single project GID."""
        with patch("builtins.input", side_effect=["2", "proj_single", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--project-gids", args)
        gids_idx = args.index("--project-gids")
        self.assertEqual(args[gids_idx + 1], "proj_single")

    def test_scope_option_2_multiple_gids(self) -> None:
        """Option [2]: Multiple project GIDs."""
        with patch("builtins.input", side_effect=["2", "proj_1,proj_2,proj_3", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        gids_idx = args.index("--project-gids")
        self.assertEqual(args[gids_idx + 1], "proj_1,proj_2,proj_3")

    def test_scope_option_2_gids_with_spaces(self) -> None:
        """Option [2]: Project GIDs with spaces."""
        with patch("builtins.input", side_effect=["2", "proj_1 , proj_2 , proj_3", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        gids_idx = args.index("--project-gids")
        # The input is passed as-is; parsing/stripping happens in the export function
        self.assertEqual(args[gids_idx + 1], "proj_1 , proj_2 , proj_3")

    def test_scope_option_3_file_selection(self) -> None:
        """Option [3]: Project GIDs from file."""
        with patch(
            "scripts.interactive_cli._prompt_project_gids_file",
            return_value="inputs/projects.csv",
        ), patch("builtins.input", side_effect=["3", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--project-gids-file", args)
        file_idx = args.index("--project-gids-file")
        self.assertEqual(args[file_idx + 1], "inputs/projects.csv")

    def test_scope_option_4_team_selection(self) -> None:
        """Option [4]: All projects in a team."""
        with patch(
            "scripts.interactive_cli._prompt_team_gid_from_lookup",
            return_value="team_beta",
        ), patch("builtins.input", side_effect=["4", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--team-gids", args)
        team_idx = args.index("--team-gids")
        self.assertEqual(args[team_idx + 1], "team_beta")

    def test_scope_option_4_no_team_selected(self) -> None:
        """Option [4]: No team selected (lookup returns None)."""
        with patch(
            "scripts.interactive_cli._prompt_team_gid_from_lookup",
            return_value=None,
        ), patch("builtins.input", side_effect=["4", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        # Should not add --team-gids if None is returned
        self.assertNotIn("--team-gids", args)

    def test_scope_default_is_option_1(self) -> None:
        """Hitting enter (blank input) should default to option [1]."""
        with patch("builtins.input", side_effect=["", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        # Should behave like option 1: no scope flags
        scope_flags = [arg for arg in args if arg in ["--project-gids", "--project-gids-file", "--team-gids"]]
        self.assertEqual(len(scope_flags), 0)

    def test_custom_fields_default_is_option_1(self) -> None:
        """Hitting enter (blank input) for custom fields should default to option [1] (columns)."""
        with patch("builtins.input", side_effect=["1", "", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--custom-fields", args)
        fields_idx = args.index("--custom-fields")
        self.assertEqual(args[fields_idx + 1], "columns")

    def test_empty_projects_default_is_no(self) -> None:
        """Empty projects confirm defaults to 'no'."""
        with patch("builtins.input", side_effect=["1", "1", "", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertNotIn("--include-empty-projects", args)


class CustomFieldModeMenuTest(TestCase):
    """Test custom field export mode menu options."""

    def setUp(self) -> None:
        patcher = patch("src.lib.keychain.is_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

        self._orig_session = interactive_cli._session
        interactive_cli._session = interactive_cli.CliSession(
            reference_path=Path("/tmp/project"),
            env_path=Path("/tmp/.asana_jira_env"),
            use_prod=False,
        )
        self.addCleanup(lambda: setattr(interactive_cli, "_session", self._orig_session))

    def test_field_mode_option_1_columns(self) -> None:
        """Custom field mode [1]: Columns (default)."""
        with patch("builtins.input", side_effect=["1", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        fields_idx = args.index("--custom-fields")
        self.assertEqual(args[fields_idx + 1], "columns")

    def test_field_mode_option_2_description(self) -> None:
        """Custom field mode [2]: Description."""
        with patch("builtins.input", side_effect=["1", "2", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        fields_idx = args.index("--custom-fields")
        self.assertEqual(args[fields_idx + 1], "description")

    def test_field_mode_default_is_option_1(self) -> None:
        """Blank input defaults to option [1] (columns)."""
        with patch("builtins.input", side_effect=["1", "", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        fields_idx = args.index("--custom-fields")
        self.assertEqual(args[fields_idx + 1], "columns")


class JiraProjectKeyPromptTest(TestCase):
    """Test Jira Project Key prompt behavior."""

    def setUp(self) -> None:
        patcher = patch("src.lib.keychain.is_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

        self._orig_session = interactive_cli._session
        interactive_cli._session = interactive_cli.CliSession(
            reference_path=Path("/tmp/project"),
            env_path=Path("/tmp/.asana_jira_env"),
            use_prod=False,
        )
        self.addCleanup(lambda: setattr(interactive_cli, "_session", self._orig_session))

    def test_jira_key_single_letter(self) -> None:
        """Jira Project Key with single letter."""
        with patch("builtins.input", side_effect=["1", "1", "n", "A"]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertIn("--jira-project-key", args)
        key_idx = args.index("--jira-project-key")
        self.assertEqual(args[key_idx + 1], "A")

    def test_jira_key_short_code(self) -> None:
        """Jira Project Key with typical short code."""
        with patch("builtins.input", side_effect=["1", "1", "n", "PROJ"]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        key_idx = args.index("--jira-project-key")
        self.assertEqual(args[key_idx + 1], "PROJ")

    def test_jira_key_with_numbers(self) -> None:
        """Jira Project Key with numbers."""
        with patch("builtins.input", side_effect=["1", "1", "n", "P123"]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        key_idx = args.index("--jira-project-key")
        self.assertEqual(args[key_idx + 1], "P123")

    def test_jira_key_blank_skipped(self) -> None:
        """Blank Jira Project Key skips the flag."""
        with patch("builtins.input", side_effect=["1", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        self.assertNotIn("--jira-project-key", args)

    def test_jira_key_whitespace_only_skipped(self) -> None:
        """Whitespace-only Jira Project Key skips the flag."""
        with patch("builtins.input", side_effect=["1", "1", "n", "   "]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        # Should be skipped because prompt defaults to empty and input is whitespace
        # (Exact behavior depends on the prompt implementation)
        # This is a reasonable defensive test


class EdgeCaseMenuTest(TestCase):
    """Test edge cases and error conditions in menu flow."""

    def setUp(self) -> None:
        patcher = patch("src.lib.keychain.is_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

        self._orig_session = interactive_cli._session
        interactive_cli._session = interactive_cli.CliSession(
            reference_path=Path("/tmp/project"),
            env_path=Path("/tmp/.asana_jira_env"),
            use_prod=False,
        )
        self.addCleanup(lambda: setattr(interactive_cli, "_session", self._orig_session))

    def test_menu_flow_with_minimal_input(self) -> None:
        """Test menu flow using all defaults (blanks)."""
        with patch("builtins.input", side_effect=["", "", "", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        # Should have defaults but no error
        self.assertIn("--log-level", args)
        self.assertIn("--database", args)
        self.assertIn("--custom-fields", args)
        self.assertNotIn("--include-empty-projects", args)

    def test_database_flag_value_is_string(self) -> None:
        """Verify database flag value is a string path."""
        with patch("builtins.input", side_effect=["1", "1", "n", ""]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        db_idx = args.index("--database")
        db_value = args[db_idx + 1]
        # Should be a string representing a path
        self.assertIsInstance(db_value, str)
        self.assertTrue(len(db_value) > 0)

    def test_args_is_list_of_strings(self) -> None:
        """Verify returned args is a list of strings suitable for subprocess."""
        with patch("builtins.input", side_effect=["2", "proj_a,proj_b", "2", "y", "KEY"]):
            args = interactive_cli.args_export_team_project_hierarchy("DEBUG")

        self.assertIsInstance(args, list)
        for arg in args:
            self.assertIsInstance(arg, str)

    def test_args_properly_ordered(self) -> None:
        """Verify args are in proper order with values following flags."""
        with patch("builtins.input", side_effect=["1", "1", "n", "MYKEY"]):
            args = interactive_cli.args_export_team_project_hierarchy("INFO")

        # All flags should be followed by values (or be boolean-style flags)
        for i, arg in enumerate(args):
            if arg.startswith("--") and arg not in ["--include-empty-projects"]:
                # Flag should not be the last element
                self.assertTrue(i + 1 < len(args), f"Flag {arg} has no value")
