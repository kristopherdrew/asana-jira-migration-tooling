from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

from scripts import interactive_cli


class IncrementalArgsBuilderTest(TestCase):
    def test_initial_team_args_include_memberships(self) -> None:
        self.assertEqual(
            interactive_cli.args_initial_teams("INFO"),
            ["--log-level", "INFO", "--include-members"],
        )

    def test_all_teams_with_optional_exclude(self) -> None:
        with patch("builtins.input", side_effect=["n", "5", "y", "team_a,team_b", "n"]):
            args = interactive_cli.args_incremental_updates("DEBUG")
        self.assertEqual(
            args,
            [
                "--log-level",
                "DEBUG",
                "--days-back",
                "5",
                "--all-teams",
                "--exclude-team-gids",
                "team_a,team_b",
                "--project-breakdown",
                "no",
            ],
        )

    def test_project_scope_selected(self) -> None:
        with patch("builtins.input", side_effect=["n", "2", "n", "1", "proj123"]):
            args = interactive_cli.args_incremental_updates("INFO")
        self.assertEqual(
            args,
            ["--log-level", "INFO", "--days-back", "2", "--project-gid", "proj123"],
        )

    def test_team_scope_defaults_to_picker(self) -> None:
        with patch("builtins.input", side_effect=["", "", "n", "", ""]):
            args = interactive_cli.args_incremental_updates("WARNING")
        self.assertEqual(
            args,
            ["--log-level", "WARNING", "--days-back", "3", "--team", "--project-breakdown", "no"],
        )

    def test_team_gids_scope_selected(self) -> None:
        with patch("builtins.input", side_effect=["n", "7", "n", "3", "team1, team2", "n"]):
            args = interactive_cli.args_incremental_updates("ERROR")
        self.assertEqual(
            args,
            [
                "--log-level",
                "ERROR",
                "--days-back",
                "7",
                "--team-gids",
                "team1, team2",
                "--project-breakdown",
                "no",
            ],
        )

    @patch("scripts.interactive_cli.log_run")
    @patch("scripts.interactive_cli.run_script")
    def test_initial_metadata_uses_team_membership_sync(self, mock_run_script, mock_log_run) -> None:
        interactive_cli.run_initial_metadata("WARNING")

        self.assertEqual(mock_run_script.call_count, 4)
        first_call = mock_run_script.call_args_list[0]
        self.assertIs(first_call.args[0], interactive_cli.get_teams.main)
        self.assertEqual(first_call.args[1], ["--log-level", "WARNING", "--include-members"])

        for call in mock_run_script.call_args_list[1:]:
            self.assertEqual(call.args[1], ["--log-level", "WARNING"])
