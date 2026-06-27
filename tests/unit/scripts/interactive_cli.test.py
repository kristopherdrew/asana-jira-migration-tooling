from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from scripts import interactive_cli
from src.lib.config import Credentials


class IncrementalArgsBuilderTest(TestCase):
    def test_initial_team_args_include_memberships(self) -> None:
        self.assertEqual(
            interactive_cli.args_initial_teams("INFO"),
            ["--log-level", "INFO", "--include-members"],
        )

    def test_initial_project_args_prompt_for_archived_scope(self) -> None:
        with patch("builtins.input", side_effect=["all"]):
            self.assertEqual(
                interactive_cli.args_initial_projects("INFO"),
                ["--log-level", "INFO", "--archived-scope", "all"],
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
        with patch("scripts.interactive_cli._open_sqlite", side_effect=SystemExit), patch(
            "builtins.input",
            side_effect=["n", "2", "n", "1", "proj123"],
        ):
            args = interactive_cli.args_incremental_updates("INFO")
        self.assertEqual(
            args,
            ["--log-level", "INFO", "--days-back", "2", "--project-gid", "proj123"],
        )

    def test_team_scope_defaults_to_picker(self) -> None:
        with patch("scripts.interactive_cli._open_sqlite", side_effect=SystemExit), patch(
            "builtins.input",
            side_effect=["", "", "n", "", ""],
        ):
            args = interactive_cli.args_incremental_updates("WARNING")
        self.assertEqual(
            args,
            ["--log-level", "WARNING", "--days-back", "3", "--team", "--project-breakdown", "no"],
        )

    def test_create_team_project_jira_issues_include_empty_with_team(self) -> None:
        # Prompt order: project key, prod? (sandbox=n), [team via mock], include-empty?, dry-run?
        with patch(
            "scripts.interactive_cli._prompt_team_gid_from_lookup", return_value="team_a"
        ), patch(
            "builtins.input",
            side_effect=["MIG", "n", "y", "n"],
        ):
            args = interactive_cli.args_create_team_project_jira_issues("INFO")
        self.assertEqual(
            args,
            [
                "--log-level",
                "INFO",
                "--jira-project-key",
                "MIG",
                "--team-gid",
                "team_a",
                "--include-empty-projects",
            ],
        )

    def test_create_team_project_jira_issues_prod_flag_and_dry_run(self) -> None:
        # Choosing production appends --prod (before the team scope); blank team, dry-run on.
        with patch(
            "scripts.interactive_cli._prompt_team_gid_from_lookup", return_value=""
        ), patch(
            "builtins.input",
            side_effect=["MIG", "y", "n", "y"],
        ):
            args = interactive_cli.args_create_team_project_jira_issues("INFO")
        self.assertEqual(
            args,
            [
                "--log-level",
                "INFO",
                "--jira-project-key",
                "MIG",
                "--prod",
                "--dry-run",
            ],
        )

    def test_team_gids_scope_selected(self) -> None:
        with patch("scripts.interactive_cli._open_sqlite", side_effect=SystemExit), patch(
            "builtins.input",
            side_effect=["n", "7", "n", "3", "team1, team2", "n"],
        ):
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

    def test_personal_tasks_assignee_search_picks_user(self) -> None:
        with patch("scripts.interactive_cli._open_sqlite") as mock_open_sqlite, patch(
            "scripts.interactive_cli._fuzzy_users",
            return_value=[{"gid": "user_1", "name": "Ada", "email": "ada@example.com"}],
        ), patch("builtins.input", side_effect=["ada", "1", "n"]):
            mock_open_sqlite.return_value = MagicMock()
            args = interactive_cli.args_personal_tasks("INFO")

        self.assertEqual(args, ["--log-level", "INFO", "--assignee", "user_1"])

    def test_personal_tasks_assignee_falls_back_to_manual_gid(self) -> None:
        with patch("scripts.interactive_cli._open_sqlite", side_effect=SystemExit), patch(
            "builtins.input",
            side_effect=["user_2", "n"],
        ):
            args = interactive_cli.args_personal_tasks("INFO")

        self.assertEqual(args, ["--log-level", "INFO", "--assignee", "user_2"])

    @patch("scripts.interactive_cli.bootstrap_credentials", return_value=Path("/tmp/creds.env"))
    @patch("scripts.interactive_cli.load_credentials", side_effect=FileNotFoundError)
    def test_ensure_interactive_credentials_bootstraps_missing_reference(
        self,
        mock_load_credentials,
        mock_bootstrap_credentials,
    ) -> None:
        interactive_cli.ensure_interactive_credentials(Path("/tmp/project"))

        mock_load_credentials.assert_called_once_with(Path("/tmp/project"))
        mock_bootstrap_credentials.assert_called_once_with(Path("/tmp/project"))

    @patch("scripts.interactive_cli.write_project_env_pointer")
    @patch("scripts.interactive_cli.write_credentials_file")
    @patch(
        "scripts.interactive_cli.prompt_credentials_values",
        return_value=Credentials(
            token="asana-token",
            workspace_gid="workspace-id",
            skip_ssl_verification=False,
            atlassian_username="jira-user@example.com",
            atlassian_token="jira-token",
        ),
    )
    @patch("scripts.interactive_cli.prompt_credentials_env_path", return_value=Path("/tmp/client.env"))
    def test_bootstrap_credentials_writes_file_and_pointer(
        self,
        mock_prompt_env_path,
        mock_prompt_values,
        mock_write_credentials_file,
        mock_write_project_env_pointer,
    ) -> None:
        result = interactive_cli.bootstrap_credentials(Path("/tmp/project"))

        self.assertEqual(result, Path("/tmp/client.env"))
        mock_prompt_env_path.assert_called_once()
        mock_prompt_values.assert_called_once()
        mock_write_credentials_file.assert_called_once()
        mock_write_project_env_pointer.assert_called_once_with(Path("/tmp/project"), Path("/tmp/client.env"))

    @patch("scripts.interactive_cli.write_project_env_pointer")
    @patch("scripts.interactive_cli.write_credentials_file")
    @patch("scripts.interactive_cli.prompt_bool", return_value=True)
    @patch(
        "scripts.interactive_cli.prompt_credentials_values",
        return_value=Credentials(
            token="asana-token",
            workspace_gid="workspace-id",
            skip_ssl_verification=True,
            atlassian_username=None,
            atlassian_token=None,
        ),
    )
    @patch("scripts.interactive_cli.prompt_credentials_env_path", return_value=Path("/tmp/new-client.env"))
    def test_change_credentials_file_reference_creates_missing_file(
        self,
        mock_prompt_env_path,
        mock_prompt_values,
        mock_prompt_bool,
        mock_write_credentials_file,
        mock_write_project_env_pointer,
    ) -> None:
        with patch("scripts.interactive_cli.resolve_credentials_path", return_value=Path("/tmp/current.env")):
            result = interactive_cli.change_credentials_file_reference(Path("/tmp/project"))

        self.assertEqual(result, Path("/tmp/new-client.env"))
        mock_prompt_bool.assert_called_once()
        mock_prompt_values.assert_called_once()
        mock_write_credentials_file.assert_called_once()
        mock_write_project_env_pointer.assert_called_once_with(Path("/tmp/project"), Path("/tmp/new-client.env"))

    @patch("scripts.interactive_cli.log_run")
    @patch("scripts.interactive_cli.run_script")
    def test_initial_metadata_uses_team_membership_sync(self, mock_run_script, mock_log_run) -> None:
        with patch("scripts.interactive_cli.prompt", side_effect=["active"]):
            interactive_cli.run_initial_metadata("WARNING")

        self.assertEqual(mock_run_script.call_count, 5)
        first_call = mock_run_script.call_args_list[0]
        self.assertIs(first_call.args[0], interactive_cli.get_teams.main)
        self.assertEqual(first_call.args[1], ["--log-level", "WARNING", "--include-members"])
        self.assertIs(mock_run_script.call_args_list[1].args[0], interactive_cli.get_users.main)
        self.assertEqual(mock_run_script.call_args_list[1].args[1], ["--log-level", "WARNING"])
        self.assertIs(mock_run_script.call_args_list[2].args[0], interactive_cli.add_service_user_to_teams.main)
        self.assertEqual(mock_run_script.call_args_list[2].args[1], ["--log-level", "WARNING"])
        self.assertIs(mock_run_script.call_args_list[3].args[0], interactive_cli.get_projects.main)
        self.assertEqual(mock_run_script.call_args_list[3].args[1], ["--log-level", "WARNING", "--archived-scope", "active"])
        self.assertIs(mock_run_script.call_args_list[4].args[0], interactive_cli.get_custom_fields.main)
        self.assertEqual(mock_run_script.call_args_list[4].args[1], ["--log-level", "WARNING"])

    @patch("scripts.interactive_cli.confirm", return_value=False)
    @patch("scripts.interactive_cli.run_script")
    @patch("scripts.interactive_cli._fuzzy_teams", return_value=[{"gid": "team_1", "name": "Team One"}])
    @patch("scripts.interactive_cli._open_sqlite")
    @patch("scripts.interactive_cli.prompt", side_effect=["team", "1", "team", "1", "team", "1", "team", "1"])
    def test_reports_menu_dispatches_exports(
        self,
        mock_prompt,
        mock_open_sqlite,
        mock_fuzzy_teams,
        mock_run_script,
        mock_confirm,
    ) -> None:
        mock_open_sqlite.return_value = MagicMock()
        menu = interactive_cli.build_reports_menu("DEBUG")

        self.assertEqual(
            [
                menu["1"].label,
                menu["2"].label,
                menu["3"].label,
                menu["4"].label,
                menu["5"].label,
                menu["6"].label,
            ],
            [
                "Export project details report",
                "Export users report",
                "Export team memberships report",
                "Export team report",
                "Export custom field usage report",
                "Export team/project Jira CSV",
            ],
        )

        menu["1"].handler()
        menu["2"].handler()
        menu["3"].handler()
        menu["4"].handler()
        menu["5"].handler()
        menu["6"].handler()

        self.assertEqual(mock_run_script.call_count, 6)
        self.assertIs(mock_run_script.call_args_list[0].args[0], interactive_cli.export_project_details_report.main)
        self.assertIs(mock_run_script.call_args_list[1].args[0], interactive_cli.export_users.main)
        self.assertIs(mock_run_script.call_args_list[2].args[0], interactive_cli.export_team_memberships.main)
        self.assertIs(mock_run_script.call_args_list[3].args[0], interactive_cli.export_team_report.main)
        self.assertIs(mock_run_script.call_args_list[4].args[0], interactive_cli.export_custom_field_usage_report.main)
        self.assertIs(mock_run_script.call_args_list[5].args[0], interactive_cli.export_team_project_jira_csv.main)
        self.assertEqual(mock_run_script.call_args_list[0].args[1], ["--log-level", "DEBUG", "--team-gid", "team_1"])
        self.assertEqual(mock_run_script.call_args_list[1].args[1], ["--log-level", "DEBUG"])
        self.assertEqual(mock_run_script.call_args_list[2].args[1], ["--log-level", "DEBUG", "--team-gid", "team_1"])
        self.assertEqual(mock_run_script.call_args_list[3].args[1], ["--log-level", "DEBUG", "--team-gid", "team_1"])
        self.assertEqual(mock_run_script.call_args_list[4].args[1], ["--log-level", "DEBUG", "--team-gid", "team_1"])
        self.assertEqual(mock_run_script.call_args_list[5].args[1], ["--log-level", "DEBUG"])

    @patch("scripts.interactive_cli.run_script")
    @patch("scripts.interactive_cli._open_sqlite")
    @patch("scripts.interactive_cli.prompt", side_effect=["", "team_1"])
    def test_reports_menu_project_details_falls_back_to_manual_gid(
        self,
        mock_prompt,
        mock_open_sqlite,
        mock_run_script,
    ) -> None:
        mock_open_sqlite.return_value = MagicMock()
        menu = interactive_cli.build_reports_menu("DEBUG")

        menu["1"].handler()

        self.assertEqual(mock_run_script.call_count, 1)
        self.assertIs(mock_run_script.call_args_list[0].args[0], interactive_cli.export_project_details_report.main)
        self.assertEqual(mock_run_script.call_args_list[0].args[1], ["--log-level", "DEBUG", "--team-gid", "team_1"])

    @patch("scripts.interactive_cli.run_script")
    @patch("scripts.interactive_cli._open_sqlite")
    @patch("scripts.interactive_cli.prompt", side_effect=["", ""])
    def test_reports_menu_team_memberships_all_teams_when_blank(
        self,
        mock_prompt,
        mock_open_sqlite,
        mock_run_script,
    ) -> None:
        mock_open_sqlite.return_value = MagicMock()
        menu = interactive_cli.build_reports_menu("DEBUG")

        menu["3"].handler()

        self.assertEqual(mock_run_script.call_count, 1)
        self.assertIs(mock_run_script.call_args_list[0].args[0], interactive_cli.export_team_memberships.main)
        self.assertEqual(mock_run_script.call_args_list[0].args[1], ["--log-level", "DEBUG"])

    @patch("scripts.interactive_cli.run_script")
    @patch("scripts.interactive_cli._open_sqlite")
    @patch("scripts.interactive_cli.prompt", side_effect=["", ""])
    def test_reports_menu_team_report_all_teams_when_blank(
        self,
        mock_prompt,
        mock_open_sqlite,
        mock_run_script,
    ) -> None:
        mock_open_sqlite.return_value = MagicMock()
        menu = interactive_cli.build_reports_menu("DEBUG")

        menu["4"].handler()

        self.assertEqual(mock_run_script.call_count, 1)
        self.assertIs(mock_run_script.call_args_list[0].args[0], interactive_cli.export_team_report.main)
        self.assertEqual(mock_run_script.call_args_list[0].args[1], ["--log-level", "DEBUG"])

    @patch("scripts.interactive_cli.prompt_required", return_value="service_user_1")
    @patch("scripts.interactive_cli.confirm", side_effect=[True, True])
    @patch("scripts.interactive_cli.run_script")
    @patch("scripts.interactive_cli.args_get_tasks", return_value=[
        "--log-level",
        "INFO",
        "--all-teams",
        "--resume-all-teams",
        "--team-gids",
        "team_1,team_2",
        "--page-size",
        "200",
        "--workers",
        "7",
    ])
    def test_task_sync_flow_prompts_for_membership_and_retry(
        self,
        mock_args_get_tasks,
        mock_run_script,
        mock_confirm,
        mock_prompt_required,
    ) -> None:
        result = SimpleNamespace(
            missing_membership_candidates=[
                SimpleNamespace(project_gid="proj_1"),
                SimpleNamespace(project_gid="proj_2"),
            ],
            missing_membership_state_path=None,
        )
        mock_run_script.side_effect = [result, None, None]

        interactive_cli.run_task_sync_flow("INFO")

        self.assertEqual(mock_run_script.call_count, 3)
        self.assertIs(mock_run_script.call_args_list[0].args[0], interactive_cli.get_tasks.main)
        self.assertEqual(
            mock_run_script.call_args_list[0].args[1],
            [
                "--log-level",
                "INFO",
                "--all-teams",
                "--resume-all-teams",
                "--team-gids",
                "team_1,team_2",
                "--page-size",
                "200",
                "--workers",
                "7",
            ],
        )
        self.assertIs(mock_run_script.call_args_list[1].args[0], interactive_cli.add_service_user_to_projects.main)
        self.assertEqual(
            mock_run_script.call_args_list[1].args[1],
            [
                "--log-level",
                "INFO",
                "--user-gid",
                "service_user_1",
                "--project-gids",
                "proj_1,proj_2",
            ],
        )
        self.assertIs(mock_run_script.call_args_list[2].args[0], interactive_cli.get_tasks.main)
        self.assertEqual(
            mock_run_script.call_args_list[2].args[1],
            [
                "--log-level",
                "INFO",
                "--page-size",
                "200",
                "--workers",
                "7",
                "--project-gids",
                "proj_1,proj_2",
            ],
        )
        mock_args_get_tasks.assert_called_once_with("INFO")
        self.assertEqual(mock_confirm.call_count, 2)
        mock_prompt_required.assert_called_once_with("Service user GID")
