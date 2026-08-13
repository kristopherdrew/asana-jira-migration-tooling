from __future__ import annotations

import io
import tempfile
from contextlib import redirect_stdout
from os import environ
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from scripts import interactive_cli
from src.lib.config import Credentials, JiraSite, load_credentials, write_credentials_file


class IncrementalArgsBuilderTest(TestCase):
    def setUp(self) -> None:
        # Force the env-file backend so credential tests stay hermetic (no real Keychain).
        patcher = patch("src.lib.keychain.is_available", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Baseline session (sandbox) so the args builders and DB helpers have state.
        self._orig_session = interactive_cli._session
        interactive_cli._session = interactive_cli.CliSession(
            reference_path=Path("/tmp/project"),
            env_path=Path("/tmp/.asana_jira_env"),
            use_prod=False,
        )
        self.addCleanup(lambda: setattr(interactive_cli, "_session", self._orig_session))

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
        with patch(
            "builtins.input",
            side_effect=["n", "2", "n", "2", "proj123", "n"],
        ):
            args = interactive_cli.args_incremental_updates("INFO")
        self.assertEqual(
            args,
            [
                "--log-level",
                "INFO",
                "--days-back",
                "2",
                "--project-gids",
                "proj123",
                "--project-breakdown",
                "no",
            ],
        )

    def test_incremental_scope_defaults_to_fuzzy_team_lookup(self) -> None:
        with patch(
            "scripts.interactive_cli._prompt_team_gid_from_lookup", return_value="team_a"
        ), patch("builtins.input", side_effect=["", "", "n", "", "n"]):
            args = interactive_cli.args_incremental_updates("WARNING")
        self.assertEqual(
            args,
            [
                "--log-level",
                "WARNING",
                "--days-back",
                "3",
                "--team-gids",
                "team_a",
                "--project-breakdown",
                "no",
            ],
        )

    def test_create_team_project_jira_issues_include_empty_with_team(self) -> None:
        # Sandbox session (no per-command prompt). Prompt order: project key, [team via mock],
        # include-empty?, dry-run?
        interactive_cli._session.use_prod = False
        with patch(
            "scripts.interactive_cli._prompt_team_gid_from_lookup", return_value="team_a"
        ), patch(
            "builtins.input",
            side_effect=["MIG", "y", "n"],
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
        # A PRODUCTION session appends --prod (before the team scope); blank team, dry-run on.
        interactive_cli._session.use_prod = True
        with patch(
            "scripts.interactive_cli._prompt_team_gid_from_lookup", return_value=""
        ), patch(
            "builtins.input",
            side_effect=["MIG", "n", "y"],
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

    def test_incremental_project_gids_scope_selected(self) -> None:
        with patch(
            "builtins.input",
            side_effect=["n", "7", "n", "2", "proj1, proj2", "n"],
        ):
            args = interactive_cli.args_incremental_updates("ERROR")
        self.assertEqual(
            args,
            [
                "--log-level",
                "ERROR",
                "--days-back",
                "7",
                "--project-gids",
                "proj1, proj2",
                "--project-breakdown",
                "no",
            ],
        )

    def test_task_sync_uses_fuzzy_team_lookup_without_legacy_picker(self) -> None:
        with patch(
            "scripts.interactive_cli._prompt_team_gid_from_lookup", return_value="team_42"
        ), patch("builtins.input", side_effect=["2", "8"]):
            args = interactive_cli.args_get_tasks("INFO")
        self.assertEqual(args, ["--log-level", "INFO", "--team-gids", "team_42", "--workers", "8"])

    def test_task_extras_accepts_comma_separated_project_gids(self) -> None:
        with patch("builtins.input", side_effect=["2", "proj_a, proj_b"]):
            args = interactive_cli.args_get_task_extras("INFO")
        self.assertEqual(args, ["--log-level", "INFO", "--project-gids", "proj_a, proj_b"])

    def test_task_sync_can_select_project_gids_file_from_inputs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            inputs_dir = Path(tmpdir) / "inputs"
            inputs_dir.mkdir()
            gids_path = inputs_dir / "migration_project_gids.txt"
            gids_path.write_text("proj_a\nproj_b\n", encoding="utf-8")
            with patch("scripts.interactive_cli.PROJECT_ROOT", Path(tmpdir)), patch(
                "builtins.input", side_effect=["4", "1", "5"]
            ):
                args = interactive_cli.args_get_tasks("INFO")
        self.assertEqual(
            args,
            ["--log-level", "INFO", "--project-gids-file", str(gids_path.resolve()), "--workers", "5"],
        )

    def test_csv_file_picker_searches_inputs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            inputs_dir = root / "inputs"
            inputs_dir.mkdir()
            csv_path = inputs_dir / "jira-keys.csv"
            csv_path.write_text("Issue key,External issue ID\nABC-1,1\n", encoding="utf-8")
            (inputs_dir / "ignore.txt").write_text("not a csv", encoding="utf-8")
            with patch("scripts.interactive_cli.PROJECT_ROOT", root), patch(
                "builtins.input", side_effect=["1", "", ""]
            ):
                args = interactive_cli.args_import_jira_keys("INFO")
        self.assertEqual(
            args,
            [
                "--log-level",
                "INFO",
                "--csv",
                str(csv_path.resolve()),
                "--task-column",
                "External issue ID",
                "--jira-column",
                "Issue key",
            ],
        )

    def test_search_jira_external_ids_builds_sandbox_dry_run_args(self) -> None:
        with patch(
            "builtins.input",
            side_effect=[
                'project = ENG AND "External issue ID" is not EMPTY',
                "customfield_10001",
                "250",
                "y",
            ],
        ):
            args = interactive_cli.args_search_jira_external_ids("DEBUG")

        self.assertEqual(
            args,
            [
                "--log-level",
                "DEBUG",
                "--jql",
                'project = ENG AND "External issue ID" is not EMPTY',
                "--external-id-field",
                "customfield_10001",
                "--max-results",
                "250",
                "--dry-run",
            ],
        )

    def test_search_jira_external_ids_uses_production_session(self) -> None:
        interactive_cli._session.use_prod = True
        with patch(
            "builtins.input",
            side_effect=["project = ENG", "customfield_10001", "500", "n"],
        ):
            args = interactive_cli.args_search_jira_external_ids("INFO")

        self.assertIn("--prod", args)
        self.assertNotIn("--dry-run", args)

    def test_user_and_project_map_imports_use_inputs_csv_picker(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            inputs_dir = root / "inputs"
            inputs_dir.mkdir()
            csv_path = inputs_dir / "mapping.csv"
            csv_path.write_text("gid,value\n1,x\n", encoding="utf-8")
            with patch("scripts.interactive_cli.PROJECT_ROOT", root), patch(
                "builtins.input", side_effect=["1", "1"]
            ):
                user_args = interactive_cli.args_update_user_map("INFO")
                project_args = interactive_cli.args_import_jira_project_map("INFO")
        expected = ["--log-level", "INFO", "--csv", str(csv_path.resolve())]
        self.assertEqual(user_args, expected)
        self.assertEqual(project_args, expected)

    def test_attachment_sync_accepts_project_gids_file(self) -> None:
        with patch(
            "scripts.interactive_cli._prompt_project_gids_file",
            return_value="/repo/projects.csv",
        ), patch("builtins.input", side_effect=["2", "y"]):
            args = interactive_cli.args_sync_attachments("INFO")
        self.assertEqual(
            args,
            ["--log-level", "INFO", "--project-gids-file", "/repo/projects.csv", "--dry-run"],
        )

    def test_attachment_archive_download_and_upload_args(self) -> None:
        with patch("builtins.input", side_effect=["1", "/secure/archives", "wave-01"]):
            download_args = interactive_cli.args_download_attachment_archive("INFO")
        self.assertEqual(
            download_args,
            [
                "--log-level", "INFO", "--all", "--archive-root",
                "/secure/archives", "--archive-id", "wave-01",
            ],
        )
        with patch("builtins.input", side_effect=["/secure/archives/wave-01/manifest.json", "y"]):
            upload_args = interactive_cli.args_upload_attachment_archive("INFO")
        self.assertEqual(
            upload_args,
            [
                "--log-level", "INFO", "--manifest",
                "/secure/archives/wave-01/manifest.json", "--dry-run",
            ],
        )

    def test_regular_and_archival_exports_use_project_gid_file_picker(self) -> None:
        with patch("scripts.interactive_cli._open_sqlite", side_effect=SystemExit), patch(
            "scripts.interactive_cli._prompt_project_gids_file",
            return_value="/repo/projects.csv",
        ), patch(
            "builtins.input",
            side_effect=["2", "y", "", "n", "n", "", "", "2", "y", "", "", ""],
        ):
            regular_args, allow_merge = interactive_cli.args_export_project_to_jira("INFO")
            archival_args = interactive_cli.args_export_archival_project("INFO")
        self.assertTrue(allow_merge)
        self.assertIn("--project-gids-file", regular_args)
        self.assertIn("/repo/projects.csv", regular_args)
        self.assertIn("--project-gids-file", archival_args)
        self.assertIn("/repo/projects.csv", archival_args)

    def test_archival_export_accepts_inline_task_gids(self) -> None:
        with patch(
            "builtins.input",
            side_effect=["4", "n", "task_1,task_2", "", "", ""],
        ):
            args = interactive_cli.args_export_archival_project("INFO")
        self.assertIn("--task-gids", args)
        self.assertIn("task_1,task_2", args)

    def test_regular_export_accepts_inline_task_gids(self) -> None:
        with patch(
            "scripts.interactive_cli._open_sqlite",
            side_effect=SystemExit,
        ), patch(
            "builtins.input",
            side_effect=["4", "n", "task_1,task_2", "", "n", "n", "", ""],
        ):
            args, allow_merge = interactive_cli.args_export_project_to_jira("INFO")
        self.assertFalse(allow_merge)
        self.assertIn("--task-gids", args)
        self.assertIn("task_1,task_2", args)

    def test_regular_and_archival_exports_use_task_gid_file_picker(self) -> None:
        with patch(
            "scripts.interactive_cli._open_sqlite",
            side_effect=SystemExit,
        ), patch(
            "scripts.interactive_cli._prompt_task_gids_file",
            return_value="/repo/missing-task-gids.txt",
        ), patch(
            "builtins.input",
            side_effect=["4", "y", "", "n", "n", "", "", "4", "y", "", "", ""],
        ):
            regular_args, allow_merge = interactive_cli.args_export_project_to_jira("INFO")
            archival_args = interactive_cli.args_export_archival_project("INFO")
        self.assertFalse(allow_merge)
        self.assertIn("--task-gids-file", regular_args)
        self.assertIn("/repo/missing-task-gids.txt", regular_args)
        self.assertIn("--task-gids-file", archival_args)
        self.assertIn("/repo/missing-task-gids.txt", archival_args)

    def test_status_updates_uses_fuzzy_team_lookup(self) -> None:
        with patch(
            "scripts.interactive_cli._prompt_team_gid_from_lookup", return_value="team_99"
        ), patch("builtins.input", side_effect=["1", "6"]):
            args = interactive_cli.args_get_project_status_updates("DEBUG")
        self.assertEqual(args, ["--log-level", "DEBUG", "--team-gids", "team_99", "--workers", "6"])

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
        with redirect_stdout(io.StringIO()):
            interactive_cli.ensure_interactive_credentials(Path("/tmp/project"))

        mock_load_credentials.assert_called_once_with(Path("/tmp/project"))
        mock_bootstrap_credentials.assert_called_once_with(Path("/tmp/project"))

    @patch("scripts.interactive_cli.offer_legacy_db_adoption")
    @patch("scripts.interactive_cli._set_session")
    @patch("scripts.interactive_cli.report_credential_checks", return_value=True)
    @patch("scripts.interactive_cli.write_active_slug")
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
    @patch("scripts.interactive_cli.project_profile_path", return_value=Path("/tmp/project/profiles/acme_corp.env"))
    @patch("scripts.interactive_cli.prompt_required", return_value="Acme Corp")
    def test_bootstrap_credentials_writes_project_profile_and_marker(
        self,
        mock_prompt_name,
        mock_project_path,
        mock_prompt_values,
        mock_write_credentials_file,
        mock_write_active_slug,
        mock_report_checks,
        mock_set_session,
        mock_offer_adopt,
    ) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            result = interactive_cli.bootstrap_credentials(Path("/tmp/project"))

        self.assertEqual(result, Path("/tmp/project/profiles/acme_corp.env"))
        mock_prompt_name.assert_called_once()
        # The slug ("acme_corp") drives both the profile path and the active marker.
        mock_project_path.assert_called_once_with("acme_corp", Path("/tmp/project"))
        mock_prompt_values.assert_called_once()
        mock_report_checks.assert_called_once()
        # The free-text client name is stamped onto the saved credentials.
        saved_creds = mock_write_credentials_file.call_args.args[1]
        self.assertEqual(saved_creds.client_name, "Acme Corp")
        mock_write_active_slug.assert_called_once_with("acme_corp", Path("/tmp/project"))
        mock_set_session.assert_called_once()

    @patch("scripts.interactive_cli.bootstrap_credentials", return_value=Path("/tmp/new.env"))
    def test_manage_credentials_walks_through_when_missing(self, mock_bootstrap) -> None:
        with patch(
            "scripts.interactive_cli.resolve_credentials_path", return_value=Path("/tmp/does-not-exist.env")
        ):
            result = interactive_cli.manage_credentials(Path("/tmp/project"))
        self.assertEqual(result, Path("/tmp/new.env"))
        mock_bootstrap.assert_called_once_with(Path("/tmp/project"))

    def test_manage_credentials_edits_existing_merges_and_masks_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / "client.env"
            write_credentials_file(
                env_path,
                Credentials(
                    token="asana-token-SECRET",
                    workspace_gid="ws-1",
                    skip_ssl_verification=False,
                    atlassian_username="u@example.com",
                    atlassian_token="jira-token-SECRET",
                    jira_sites=(JiraSite("sandbox_jira", "Sandbox Jira", "https://old-sb.example"),),
                    jira_default_site="sandbox_jira",
                ),
            )
            (project_root / ".asana_jira_env_path").write_text(f"{env_path}\n", encoding="utf-8")

            buffer = io.StringIO()
            # Pick [4] ATLASSIAN_USERNAME, enter a new value, then save.
            # Token validation is exercised separately; stub it to pass here.
            with patch.dict(environ, {}, clear=True), patch(
                "scripts.interactive_cli.report_credential_checks", return_value=True
            ), patch(
                "builtins.input", side_effect=["4", "new@example.com", "s"]
            ), redirect_stdout(buffer):
                interactive_cli.manage_credentials(project_root)

            output = buffer.getvalue()
            # Tokens are masked, never printed in the clear (backend annotated).
            self.assertIn("******** (set, env file)", output)
            self.assertNotIn("asana-token-SECRET", output)
            self.assertNotIn("jira-token-SECRET", output)

            # The shared edit is merged and named Jira sites are preserved.
            with patch.dict(environ, {}, clear=True):
                updated = load_credentials(project_root)
            self.assertEqual(updated.jira_sites[0].base_url, "https://old-sb.example")
            self.assertEqual(updated.token, "asana-token-SECRET")
            self.assertEqual(updated.atlassian_token, "jira-token-SECRET")
            self.assertEqual(updated.atlassian_username, "new@example.com")

    def test_manage_credentials_cancel_discards_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / "client.env"
            write_credentials_file(
                env_path,
                Credentials(
                    token="t",
                    workspace_gid="ws-1",
                    skip_ssl_verification=False,
                    atlassian_username="old@example.com",
                ),
            )
            (project_root / ".asana_jira_env_path").write_text(f"{env_path}\n", encoding="utf-8")

            # Edit the shared username but cancel without saving.
            with patch.dict(environ, {}, clear=True), patch(
                "builtins.input", side_effect=["4", "discarded@example.com", "c"]
            ), redirect_stdout(io.StringIO()):
                interactive_cli.manage_credentials(project_root)

            with patch.dict(environ, {}, clear=True):
                unchanged = load_credentials(project_root)
            self.assertEqual(unchanged.atlassian_username, "old@example.com")

    def test_manage_credentials_does_not_save_when_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / "client.env"
            write_credentials_file(
                env_path,
                Credentials(
                    token="t",
                    workspace_gid="ws-1",
                    skip_ssl_verification=False,
                    atlassian_username="old@example.com",
                ),
            )
            (project_root / ".asana_jira_env_path").write_text(f"{env_path}\n", encoding="utf-8")

            # Edit + save, but validation fails -> 'c' to leave.
            with patch.dict(environ, {}, clear=True), patch(
                "scripts.interactive_cli.report_credential_checks", return_value=False
            ), patch(
                "builtins.input", side_effect=["4", "new@example.com", "s", "c"]
            ), redirect_stdout(io.StringIO()):
                interactive_cli.manage_credentials(project_root)

            with patch.dict(environ, {}, clear=True):
                unchanged = load_credentials(project_root)
            self.assertEqual(unchanged.atlassian_username, "old@example.com")

    @patch("scripts.interactive_cli.check_credentials")
    def test_report_credential_checks_passes_without_prompting(self, mock_check) -> None:
        from src.lib.credential_check import CheckResult

        mock_check.return_value = [CheckResult("Asana", True, "authenticated as Jane")]
        with patch("scripts.interactive_cli.prompt_bool") as mock_prompt_bool, redirect_stdout(io.StringIO()):
            creds = Credentials(token="t", workspace_gid="w", skip_ssl_verification=False)
            self.assertTrue(interactive_cli.report_credential_checks(creds))
        mock_prompt_bool.assert_not_called()

    @patch("scripts.interactive_cli.check_credentials")
    def test_report_credential_checks_prompts_save_anyway_on_failure(self, mock_check) -> None:
        from src.lib.credential_check import CheckResult

        mock_check.return_value = [CheckResult("Asana", False, "Asana token rejected (HTTP 401).")]
        with patch("scripts.interactive_cli.prompt_bool", return_value=False) as mock_prompt_bool, redirect_stdout(
            io.StringIO()
        ):
            creds = Credentials(token="bad", workspace_gid="w", skip_ssl_verification=False)
            self.assertFalse(interactive_cli.report_credential_checks(creds))
        mock_prompt_bool.assert_called_once()

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

    @patch("builtins.input", return_value="1")
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
        mock_input,
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
                "Export initial metadata report (.xlsx)",
                "Export field & section migration review (.xlsx)",
                "Export project details report",
                "Export users report",
                "Export team memberships report",
                "Export team report",
            ],
        )

        menu["1"].handler()
        menu["2"].handler()
        menu["3"].handler()
        menu["4"].handler()
        menu["5"].handler()
        menu["6"].handler()

        self.assertEqual(mock_run_script.call_count, 6)
        self.assertIs(mock_run_script.call_args_list[0].args[0], interactive_cli.export_all_reports_xlsx.main)
        self.assertIs(mock_run_script.call_args_list[1].args[0], interactive_cli.export_field_section_review_report.main)
        self.assertIs(mock_run_script.call_args_list[2].args[0], interactive_cli.export_project_details_report.main)
        self.assertIs(mock_run_script.call_args_list[3].args[0], interactive_cli.export_users.main)
        self.assertIs(mock_run_script.call_args_list[4].args[0], interactive_cli.export_team_memberships.main)
        self.assertIs(mock_run_script.call_args_list[5].args[0], interactive_cli.export_team_report.main)
        self.assertEqual(mock_run_script.call_args_list[0].args[1], ["--log-level", "DEBUG"])
        self.assertEqual(mock_run_script.call_args_list[1].args[1], ["--log-level", "DEBUG", "--team-gid", "team_1"])
        self.assertEqual(mock_run_script.call_args_list[2].args[1], ["--log-level", "DEBUG", "--team-gid", "team_1"])
        self.assertEqual(mock_run_script.call_args_list[3].args[1], ["--log-level", "DEBUG"])
        self.assertEqual(mock_run_script.call_args_list[4].args[1], ["--log-level", "DEBUG", "--team-gid", "team_1"])
        self.assertEqual(mock_run_script.call_args_list[5].args[1], ["--log-level", "DEBUG", "--team-gid", "team_1"])

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

        menu["3"].handler()

        self.assertEqual(mock_run_script.call_count, 1)
        self.assertIs(mock_run_script.call_args_list[0].args[0], interactive_cli.export_project_details_report.main)
        self.assertEqual(mock_run_script.call_args_list[0].args[1], ["--log-level", "DEBUG", "--team-gid", "team_1"])

    @patch("scripts.interactive_cli.run_script")
    @patch(
        "scripts.interactive_cli._prompt_jira_keys_file",
        return_value="/tmp/jira_keys.txt",
    )
    def test_field_section_review_accepts_jira_keys_file(
        self,
        mock_prompt_file,
        mock_run_script,
    ) -> None:
        with patch("builtins.input", side_effect=["4", "2"]):
            interactive_cli.build_reports_menu("INFO")["2"].handler()

        mock_prompt_file.assert_called_once_with("field & section review")
        mock_run_script.assert_called_once_with(
            interactive_cli.export_field_section_review_report.main,
            ["--log-level", "INFO", "--jira-keys-file", "/tmp/jira_keys.txt"],
        )

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

        menu["5"].handler()

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

        menu["6"].handler()

        self.assertEqual(mock_run_script.call_count, 1)
        self.assertIs(mock_run_script.call_args_list[0].args[0], interactive_cli.export_team_report.main)
        self.assertEqual(mock_run_script.call_args_list[0].args[1], ["--log-level", "DEBUG"])

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


class TokenMigrationTest(TestCase):
    """offer_token_migration / run_token_migration with a faked available Keychain."""

    def setUp(self) -> None:
        from src.lib import keychain

        self.store: dict[str, str] = {}
        patchers = [
            patch.object(keychain, "is_available", return_value=True),
            patch.object(keychain, "get_secret", side_effect=self.store.get),
            patch.object(keychain, "set_secret", side_effect=self.store.__setitem__),
            patch.object(keychain, "delete_secret", side_effect=lambda a: bool(self.store.pop(a, None))),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _write_env(self, tmpdir, with_tokens=True) -> Path:
        env_path = Path(tmpdir) / "client.env"
        lines = ["export ASANA_WORKSPACE_GID=ws-1"]
        if with_tokens:
            lines += ["export ASANA_TOKEN=asana-secret", "export ATLASSIAN_TOKEN=jira-secret"]
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return env_path

    def test_offer_token_migration_moves_tokens_when_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = self._write_env(tmpdir, with_tokens=True)
            with patch("scripts.interactive_cli.prompt_bool", return_value=True), redirect_stdout(io.StringIO()):
                migrated = interactive_cli.offer_token_migration(env_path)
            self.assertTrue(migrated)
            # Accounts are namespaced by profile slug (client.env -> "client").
            self.assertEqual(self.store["client:ASANA_TOKEN"], "asana-secret")
            self.assertEqual(self.store["client:ATLASSIAN_TOKEN"], "jira-secret")
            contents = env_path.read_text(encoding="utf-8")
            self.assertNotIn("ASANA_TOKEN", contents)
            self.assertIn("ASANA_WORKSPACE_GID=ws-1", contents)

    def test_offer_token_migration_declined_keeps_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = self._write_env(tmpdir, with_tokens=True)
            with patch("scripts.interactive_cli.prompt_bool", return_value=False), redirect_stdout(io.StringIO()):
                migrated = interactive_cli.offer_token_migration(env_path)
            self.assertFalse(migrated)
            self.assertIn("ASANA_TOKEN", env_path.read_text(encoding="utf-8"))
            self.assertEqual(self.store, {})

    def test_run_token_migration_reports_when_nothing_to_migrate(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = self._write_env(tmpdir, with_tokens=False)
            (Path(tmpdir) / ".asana_jira_env_path").write_text(f"{env_path}\n", encoding="utf-8")
            buffer = io.StringIO()
            with patch.dict(environ, {}, clear=True), redirect_stdout(buffer):
                interactive_cli.run_token_migration(Path(tmpdir))
            self.assertIn("No plaintext tokens", buffer.getvalue())


class ProfileSessionTest(TestCase):
    """The session-level profile + Sandbox/Production state and switching."""

    def setUp(self) -> None:
        self._orig_session = interactive_cli._session
        self.addCleanup(lambda: setattr(interactive_cli, "_session", self._orig_session))
        keychain_patch = patch("src.lib.keychain.is_available", return_value=False)
        keychain_patch.start()
        self.addCleanup(keychain_patch.stop)
        # These tests legitimately set the per-profile DB env var; don't leak it.
        self._orig_db_env = environ.get(interactive_cli.ASANA_JIRA_DB_PATH_ENV)

        def _restore_db_env() -> None:
            if self._orig_db_env is None:
                environ.pop(interactive_cli.ASANA_JIRA_DB_PATH_ENV, None)
            else:
                environ[interactive_cli.ASANA_JIRA_DB_PATH_ENV] = self._orig_db_env

        self.addCleanup(_restore_db_env)

    def _set_session(self, env_path: Path, use_prod: bool = False) -> None:
        interactive_cli._session = interactive_cli.CliSession(
            reference_path=Path("/tmp/project"), env_path=env_path, use_prod=use_prod
        )

    def test_append_jira_env_flag_follows_session_toggle(self) -> None:
        self._set_session(Path("/tmp/.asana_jira_env_acme"), use_prod=False)
        args: list = []
        interactive_cli.append_jira_env_flag(args)
        self.assertEqual(args, [])

        interactive_cli._session.use_prod = True
        interactive_cli.append_jira_env_flag(args)
        self.assertEqual(args, ["--prod"])

    def test_db_path_for_env_is_per_profile(self) -> None:
        # Legacy home names and current project names map to the same DB.
        default_db = interactive_cli.db_path_for_env(Path.home() / ".asana_jira_env")
        named_db = interactive_cli.db_path_for_env(Path.home() / ".asana_jira_env_acme")
        self.assertEqual(default_db.name, "asana.sqlite")
        self.assertEqual(named_db.name, "asana_acme.sqlite")
        self.assertEqual(
            interactive_cli.db_path_for_env(Path("/x/profiles/default.env")).name, "asana.sqlite"
        )
        self.assertEqual(
            interactive_cli.db_path_for_env(Path("/x/profiles/acme.env")).name, "asana_acme.sqlite"
        )

    def test_print_session_header_shows_profile_and_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".asana_jira_env_acme"
            write_credentials_file(
                env_path,
                Credentials(
                    token="t",
                    workspace_gid="w",
                    skip_ssl_verification=False,
                    client_name="Acme Corp",
                    jira_base_url="https://prod.example.net",
                    jira_sandbox_base_url="https://sandbox.example.net",
                ),
            )
            interactive_cli._set_session(Path(tmpdir), env_path, use_prod=False)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                interactive_cli.print_session_header()
            output = buffer.getvalue()
            self.assertIn("Profile: Acme Corp", output)
            # Sandbox session shows the sandbox URL next to the environment.
            self.assertIn("Jira site: Sandbox Jira (https://sandbox.example.net)", output)
            self.assertIn("Database: asana_acme.sqlite", output)
            # A bordered box wraps the block.
            self.assertIn("┌", output)
            self.assertIn("│ Profile: Acme Corp", output)
            self.assertIn("└", output)

    def test_print_session_header_uses_production_url_when_prod(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".asana_jira_env_acme"
            write_credentials_file(
                env_path,
                Credentials(
                    token="t",
                    workspace_gid="w",
                    skip_ssl_verification=False,
                    client_name="Acme Corp",
                    jira_base_url="https://prod.example.net",
                    jira_sandbox_base_url="https://sandbox.example.net",
                ),
            )
            interactive_cli._set_session(Path(tmpdir), env_path, use_prod=True)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                interactive_cli.print_session_header()
            self.assertIn("Jira site: Production Jira (https://prod.example.net)", buffer.getvalue())

    def test_offer_legacy_db_adoption_renames_when_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            legacy = root / "data" / "asana.sqlite"
            legacy.write_bytes(b"x" * 4096)
            env_path = root / ".asana_jira_env_acme"
            with patch("scripts.interactive_cli.PROJECT_ROOT", root):
                self._set_session(env_path, use_prod=False)
                target = interactive_cli.db_path_for_env(env_path)
                self.assertFalse(target.exists())
                with patch("scripts.interactive_cli.prompt_bool", return_value=True), redirect_stdout(io.StringIO()):
                    interactive_cli.offer_legacy_db_adoption()
            self.assertFalse(legacy.exists())
            self.assertTrue(target.exists())
            self.assertEqual(target.name, "asana_acme.sqlite")

    def test_offer_legacy_db_adoption_declined_keeps_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            legacy = root / "data" / "asana.sqlite"
            legacy.write_bytes(b"x" * 4096)
            env_path = root / ".asana_jira_env_acme"
            with patch("scripts.interactive_cli.PROJECT_ROOT", root):
                self._set_session(env_path, use_prod=False)
                with patch("scripts.interactive_cli.prompt_bool", return_value=False) as mock_bool, redirect_stdout(io.StringIO()):
                    interactive_cli.offer_legacy_db_adoption()
                    mock_bool.assert_called_once()
            self.assertTrue(legacy.exists())

    def test_offer_legacy_db_adoption_skips_default_profile_and_missing_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "data").mkdir()
            legacy = root / "data" / "asana.sqlite"
            legacy.write_bytes(b"x" * 4096)
            with patch("scripts.interactive_cli.PROJECT_ROOT", root):
                # Default profile: target IS the legacy file -> never prompts.
                self._set_session(root / ".asana_jira_env", use_prod=False)
                with patch("scripts.interactive_cli.prompt_bool") as mock_bool:
                    interactive_cli.offer_legacy_db_adoption()
                    mock_bool.assert_not_called()
                # Named profile but no legacy file -> never prompts.
                legacy.unlink()
                self._set_session(root / ".asana_jira_env_acme", use_prod=False)
                with patch("scripts.interactive_cli.prompt_bool") as mock_bool:
                    interactive_cli.offer_legacy_db_adoption()
                    mock_bool.assert_not_called()

    def test_select_jira_site_can_persist_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "client.env"
            env_path.write_text(
                "ASANA_WORKSPACE_GID=w\nJIRA_SITES=production_jira,sandbox_jira\n"
                "JIRA_DEFAULT_SITE=sandbox_jira\n"
                "JIRA_SITE_PRODUCTION_JIRA_NAME='Production Jira'\n"
                "JIRA_SITE_PRODUCTION_JIRA_URL=https://prod.example.net\n"
                "JIRA_SITE_SANDBOX_JIRA_NAME='Sandbox Jira'\n"
                "JIRA_SITE_SANDBOX_JIRA_URL=https://sandbox.example.net\n",
                encoding="utf-8",
            )
            interactive_cli._set_session(Path(tmpdir), env_path)
            with patch("scripts.interactive_cli.prompt", return_value="1"), redirect_stdout(io.StringIO()):
                interactive_cli.select_jira_site(make_default=True)
            self.assertEqual(interactive_cli._session.jira_site_key, "production_jira")
            self.assertIn("JIRA_DEFAULT_SITE=production_jira", env_path.read_text(encoding="utf-8"))

    def test_switch_profile_writes_marker_and_sets_db_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            env_path = project_root / "profiles" / "globex.env"
            env_path.parent.mkdir(parents=True, exist_ok=True)
            write_credentials_file(
                env_path,
                Credentials(token="t", workspace_gid="w", skip_ssl_verification=False, client_name="Globex"),
            )
            self._set_session(project_root / "profiles" / "default.env", use_prod=True)
            buffer = io.StringIO()
            with patch("scripts.interactive_cli.list_project_profiles", return_value=[(env_path, "Globex")]), \
                patch("scripts.interactive_cli.offer_legacy_db_adoption"), \
                patch("builtins.input", side_effect=["1"]), \
                redirect_stdout(buffer):
                interactive_cli.switch_profile(project_root)

                # The active-profile marker now names the chosen profile's slug.
                marker = (project_root / "profiles" / ".asana_jira_active").read_text(encoding="utf-8").strip()
                self.assertEqual(marker, "globex")
                # Session + DB env follow the switch; the toggle is preserved.
                self.assertEqual(interactive_cli._session.client_name, "Globex")
                self.assertTrue(interactive_cli._session.use_prod)
                self.assertEqual(
                    environ[interactive_cli.ASANA_JIRA_DB_PATH_ENV],
                    str(interactive_cli.db_path_for_env(env_path)),
                )

    def test_run_startup_migration_adopts_single_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_root = Path(tmpdir)
            acme = project_root / "profiles" / "acme.env"
            with patch(
                "scripts.interactive_cli.migrate_home_profiles_into_project",
                return_value=[("acme", Path("/home/x/.asana_jira_env_acme"), acme)],
            ) as mock_migrate, patch(
                "scripts.interactive_cli.list_project_profiles", return_value=[(acme, "Acme")]
            ), redirect_stdout(io.StringIO()):
                interactive_cli.run_startup_migration(project_root)

            mock_migrate.assert_called_once_with(project_root)
            # With a single migrated profile and no prior marker, it is adopted.
            self.assertEqual(
                (project_root / "profiles" / ".asana_jira_active").read_text(encoding="utf-8").strip(), "acme"
            )


class MenuSortingTest(TestCase):
    def test_q_exits_from_any_prompt(self) -> None:
        with patch("builtins.input", return_value=" Q ") as mock_input:
            with self.assertRaises(interactive_cli.CliExit):
                interactive_cli.prompt("Any value")
        self.assertIn("q to quit", mock_input.call_args.args[0])

    def test_terminal_interrupts_exit_from_any_prompt(self) -> None:
        for interruption in (KeyboardInterrupt(), EOFError()):
            with self.subTest(interruption=type(interruption).__name__), patch(
                "builtins.input", side_effect=interruption
            ):
                with self.assertRaises(interactive_cli.CliExit):
                    interactive_cli.prompt("Any value")

    def test_main_reports_a_clean_exit(self) -> None:
        output = io.StringIO()
        with patch("scripts.interactive_cli._run_cli", side_effect=interactive_cli.CliExit), redirect_stdout(output):
            interactive_cli.main([])
        self.assertIn("Exiting Asana -> Jira Migration Toolkit.", output.getvalue())

    def test_q_exits_from_an_invoked_script_prompt(self) -> None:
        def prompting_script(_argv) -> None:
            input("Nested script prompt: ")

        with patch("builtins.input", return_value="q"):
            with self.assertRaises(interactive_cli.CliExit):
                interactive_cli.run_script(prompting_script, [])

    def test_q_exits_new_profile_wizard_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir, patch(
            "builtins.input", return_value="q"
        ) as mock_input:
            with self.assertRaises(interactive_cli.CliExit):
                interactive_cli.bootstrap_credentials(Path(tmpdir))
        self.assertIn("Client / profile name", mock_input.call_args.args[0])
        self.assertIn("q to quit", mock_input.call_args.args[0])

    def test_q_exits_add_jira_site_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "client.env"
            original_session = interactive_cli._session
            interactive_cli._session = interactive_cli.CliSession(
                reference_path=Path(tmpdir), env_path=env_path
            )
            try:
                with patch("builtins.input", return_value="q") as mock_input:
                    with self.assertRaises(interactive_cli.CliExit):
                        interactive_cli.add_jira_site()
                self.assertFalse(env_path.exists())
                self.assertIn("Jira site label", mock_input.call_args.args[0])
                self.assertIn("q to quit", mock_input.call_args.args[0])
            finally:
                interactive_cli._session = original_session

    def test_menu_sort_key_orders_digits_letters_back_quit(self) -> None:
        keys = ["b", "9", "a", "1", "q", "2"]
        self.assertEqual(
            sorted(keys, key=interactive_cli._menu_sort_key),
            ["1", "2", "9", "a", "b", "q"],
        )

    def test_print_menu_renders_mixed_digit_and_letter_menu(self) -> None:
        menu = {
            "1": interactive_cli.MenuItem("1", "One", lambda: None),
            "9": interactive_cli.MenuItem("9", "Nine", lambda: None),
            "a": interactive_cli.MenuItem("a", "Letter A", lambda: None),
            "b": interactive_cli.MenuItem("b", "Back", lambda: None),
            "q": interactive_cli.MenuItem("q", "Quit", lambda: None),
        }
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            interactive_cli.print_menu("Reports", menu)
        lines = [line for line in buffer.getvalue().splitlines() if line.startswith("[")]
        self.assertEqual(
            lines,
            ["[1] One", "[9] Nine", "[a] Letter A", "[b] Back", "[q] Quit"],
        )


class LifecycleMenuTest(TestCase):
    def test_top_menu_follows_migration_lifecycle(self) -> None:
        menu = interactive_cli.build_top_menu("INFO")
        self.assertEqual(
            [menu[str(index)].label for index in range(1, 8)],
            [
                "Client & Environment",
                "Discover & Refresh Asana",
                "Assess & Plan Migration",
                "Prepare Jira",
                "Migrate Projects",
                "Maintain & Validate",
                "Advanced & Recovery",
            ],
        )
        self.assertEqual(menu["q"].label, "Quit")

    def test_lifecycle_submenus_have_matching_display_and_selection_keys(self) -> None:
        menus = [
            interactive_cli.build_client_environment_menu(),
            interactive_cli.build_discover_refresh_menu("INFO"),
            interactive_cli.build_assess_plan_menu("INFO"),
            interactive_cli.build_prepare_jira_menu("INFO"),
            interactive_cli.build_migrate_projects_menu("INFO"),
            interactive_cli.build_maintain_validate_menu("INFO"),
            interactive_cli.build_advanced_recovery_menu("INFO"),
        ]
        for menu in menus:
            self.assertEqual(list(menu), [item.key for item in menu.values()])

    def test_token_migration_is_only_in_advanced_recovery(self) -> None:
        client_menu = interactive_cli.build_client_environment_menu()
        advanced_menu = interactive_cli.build_advanced_recovery_menu("INFO")
        token_label = "Migrate tokens from env file to macOS Keychain"

        self.assertNotIn(token_label, [item.label for item in client_menu.values()])
        self.assertEqual(advanced_menu["5"].label, token_label)

        with patch("scripts.interactive_cli.run_token_migration") as mock_migrate:
            advanced_menu["5"].handler()
        mock_migrate.assert_called_once_with(interactive_cli.PROJECT_ROOT)

    @patch("scripts.interactive_cli.args_get_project_status_updates", return_value=["status"])
    @patch("scripts.interactive_cli.args_get_task_extras", return_value=["extras"])
    @patch("scripts.interactive_cli.run_script")
    @patch("scripts.interactive_cli.run_task_sync_flow")
    @patch("scripts.interactive_cli.run_initial_metadata")
    @patch("scripts.interactive_cli.confirm", side_effect=[True, True, False, False])
    def test_guided_refresh_preserves_dependency_order(
        self,
        mock_confirm,
        mock_metadata,
        mock_tasks,
        mock_run_script,
        mock_extras_args,
        mock_status_args,
    ) -> None:
        interactive_cli.run_guided_asana_refresh("INFO")

        mock_metadata.assert_called_once_with("INFO")
        mock_tasks.assert_called_once_with("INFO")
        self.assertEqual(
            [call.args for call in mock_run_script.call_args_list],
            [
                (interactive_cli.get_task_extras.main, ["extras"]),
                (interactive_cli.get_project_status_updates.main, ["status"]),
            ],
        )

    def test_readiness_handles_missing_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing.sqlite"
            output = io.StringIO()
            with patch("scripts.interactive_cli.active_db_path", return_value=missing), redirect_stdout(output):
                interactive_cli.show_migration_readiness()
        self.assertIn("Database has not been created", output.getvalue())

    def test_readiness_reports_present_and_missing_layers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "readiness.sqlite"
            connection = interactive_cli.sqlite3.connect(str(db_path))
            connection.executescript(
                """
                CREATE TABLE projects (gid TEXT, synced_at TEXT);
                INSERT INTO projects VALUES ('p1', '2026-08-12T12:00:00Z');
                CREATE TABLE tasks (gid TEXT, synced_at TEXT);
                CREATE TABLE user_atlassian_accounts (user_gid TEXT, synced_at TEXT);
                INSERT INTO user_atlassian_accounts VALUES ('u1', '2026-08-12T13:00:00Z');
                """
            )
            connection.commit()
            connection.close()
            output = io.StringIO()
            with patch("scripts.interactive_cli.active_db_path", return_value=db_path), redirect_stdout(output):
                interactive_cli.show_migration_readiness()
        rendered = output.getvalue()
        self.assertIn("[x] Asana projects: 1", rendered)
        self.assertIn("[ ] Asana tasks: 0", rendered)
        self.assertIn("[x] Atlassian user mappings: 1", rendered)
        self.assertIn("[ ] Task Jira mappings: 0", rendered)

    @patch("scripts.interactive_cli.args_clear_jira_keys")
    @patch("scripts.interactive_cli.run_script")
    @patch("scripts.interactive_cli.confirm", return_value=False)
    def test_recovery_action_requires_confirmation_before_prompting_for_scope(
        self,
        mock_confirm,
        mock_run_script,
        mock_args,
    ) -> None:
        interactive_cli.build_advanced_recovery_menu("INFO")["1"].handler()
        mock_confirm.assert_called_once()
        mock_args.assert_not_called()
        mock_run_script.assert_not_called()
