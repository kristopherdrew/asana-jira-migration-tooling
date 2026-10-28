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

    def test_recent_task_changes_args_accept_project_file_and_days_back(self) -> None:
        with patch(
            "scripts.interactive_cli._prompt_project_gids_file", return_value="inputs/projects.txt"
        ), patch("scripts.interactive_cli.prompt", side_effect=["2", "21"]):
            args = interactive_cli.args_recent_task_changes_api("DEBUG")
        self.assertEqual(
            args,
            [
                "--log-level", "DEBUG",
                "--project-gids-file", "inputs/projects.txt",
                "--days-back", "21",
            ],
        )

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

    def test_search_jira_external_ids_builds_sandbox_dry_run_args(self) -> None:
        with patch("builtins.input", side_effect=["1", "y", "n"]):
            args = interactive_cli.args_search_jira_external_ids("DEBUG")

        self.assertEqual(
            args,
            [
                "--log-level",
                "DEBUG",
                "--dry-run",
            ],
        )

    def test_search_jira_external_ids_uses_production_session(self) -> None:
        interactive_cli._session.use_prod = True
        with patch("builtins.input", side_effect=["1", "n", "n"]):
            args = interactive_cli.args_search_jira_external_ids("INFO")

        self.assertIn("--prod", args)
        self.assertNotIn("--dry-run", args)

    def test_search_jira_external_ids_uses_saved_or_discovered_field_by_default(self) -> None:
        with patch("builtins.input", side_effect=["1", "y", "n"]):
            args = interactive_cli.args_search_jira_external_ids("INFO")
        self.assertNotIn("--external-id-field", args)
        self.assertNotIn("--jql", args)

    def test_search_jira_external_ids_accepts_project_gid_scope(self) -> None:
        with patch("builtins.input", side_effect=["2", "proj_1,proj_2", "n", "n"]):
            args = interactive_cli.args_search_jira_external_ids("INFO")

        self.assertEqual(
            args,
            ["--log-level", "INFO", "--project-gids", "proj_1,proj_2"],
        )

    def test_search_jira_external_ids_accepts_project_gid_file_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            inputs_dir = Path(tmpdir) / "inputs"
            inputs_dir.mkdir()
            gids_path = inputs_dir / "migration_project_gids.txt"
            gids_path.write_text("proj_a\nproj_b\n", encoding="utf-8")
            with patch("scripts.interactive_cli.PROJECT_ROOT", Path(tmpdir)), patch(
                "builtins.input", side_effect=["3", "1", "y", "n"]
            ):
                args = interactive_cli.args_search_jira_external_ids("INFO")

        self.assertEqual(
            args,
            [
                "--log-level",
                "INFO",
                "--project-gids-file",
                str(gids_path.resolve()),
                "--dry-run",
            ],
        )

    def test_search_jira_external_ids_accepts_overwrite_flag(self) -> None:
        with patch("builtins.input", side_effect=["1", "n", "y"]):
            args = interactive_cli.args_search_jira_external_ids("INFO")

        self.assertIn("--overwrite", args)

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
        self.assertEqual(
            user_args,
            [
                "--log-level",
                "INFO",
                "--atlassian-export",
                str(csv_path.resolve()),
            ],
        )
        self.assertEqual(
            project_args,
            ["--log-level", "INFO", "--csv", str(csv_path.resolve())],
        )

    def test_project_map_overwrite_reviews_and_updates_csv_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            inputs_dir = root / "inputs"
            inputs_dir.mkdir()
            csv_path = inputs_dir / "mapping.csv"
            csv_path.write_text(
                "project_gid,project_name,project_key\n"
                "proj_1,First,OLD\n"
                "proj_2,Second,OLD\n",
                encoding="utf-8",
            )
            db_path = root / "asana.sqlite"
            database = interactive_cli.Database(db_path=str(db_path))
            with database.connect() as connection:
                database.initialize(connection)
                connection.execute(
                    "INSERT INTO teams (gid, name, synced_at) "
                    "VALUES ('team_1', 'Team One', 'now')"
                )
                connection.execute(
                    "INSERT INTO projects (gid, name, team_gid, synced_at) VALUES "
                    "('proj_1', 'First Project', 'team_1', 'now'), "
                    "('proj_2', 'Second Project', 'team_1', 'now')"
                )
                database.upsert_jira_project_mappings(
                    connection,
                    [
                        {
                            "project_gid": "proj_1",
                            "project_name": "Old Project",
                            "project_key": "OLD",
                        },
                        {
                            "project_gid": "proj_2",
                            "project_name": "Old Project",
                            "project_key": "OLD",
                        },
                    ],
                    interactive_cli.datetime.now(interactive_cli.timezone.utc),
                    jira_site_key="sandbox",
                )
            interactive_cli._session.jira_sites = (
                JiraSite("sandbox", "Sandbox Jira", "https://example.atlassian.net"),
            )
            interactive_cli._session.jira_site_key = "sandbox"
            output = io.StringIO()
            with patch("scripts.interactive_cli.PROJECT_ROOT", root), \
                    patch("scripts.interactive_cli.active_db_path", return_value=db_path), \
                    patch(
                        "scripts.interactive_cli.load_credentials",
                        return_value=Credentials(
                            token="",
                            workspace_gid="",
                            skip_ssl_verification=False,
                            atlassian_username="u",
                            atlassian_token="t",
                        ),
                    ), \
                    patch(
                        "scripts.interactive_cli.export_project_to_jira_csv.lookup_jira_project_name",
                        return_value="New Project",
                    ), \
                    patch("builtins.input", side_effect=["2", "1", "new", "y"]), \
                    redirect_stdout(output):
                interactive_cli.run_import_jira_project_map("INFO")

            rendered = output.getvalue()
            self.assertIn("First Project", rendered)
            self.assertIn("Current Jira project: OLD (Old Project)", rendered)
            self.assertIn("New Jira project: NEW (New Project)", rendered)
            with database.connect() as connection:
                rows = connection.execute(
                    "SELECT project_gid, jira_project_key, jira_project_name "
                    "FROM jira_project_mappings ORDER BY project_gid"
                ).fetchall()
            self.assertEqual(
                [
                    (
                        row["project_gid"],
                        row["jira_project_key"],
                        row["jira_project_name"],
                    )
                    for row in rows
                ],
                [("proj_1", "NEW", "New Project"), ("proj_2", "NEW", "New Project")],
            )

    def test_project_map_overwrite_accepts_pasted_project_gids(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "asana.sqlite"
            database = interactive_cli.Database(db_path=str(db_path))
            with database.connect() as connection:
                database.initialize(connection)
                connection.execute(
                    "INSERT INTO teams (gid, name, synced_at) "
                    "VALUES ('team_1', 'Team One', 'now')"
                )
                connection.execute(
                    "INSERT INTO projects (gid, name, team_gid, synced_at) VALUES "
                    "('proj_1', 'First Project', 'team_1', 'now'), "
                    "('proj_2', 'Second Project', 'team_1', 'now')"
                )
                database.upsert_jira_project_mappings(
                    connection,
                    [
                        {
                            "project_gid": "proj_1",
                            "project_name": "Old Project",
                            "project_key": "OLD",
                        },
                        {
                            "project_gid": "proj_2",
                            "project_name": "Old Project",
                            "project_key": "OLD",
                        },
                    ],
                    interactive_cli.datetime.now(interactive_cli.timezone.utc),
                    jira_site_key="sandbox",
                )
            interactive_cli._session.jira_sites = (
                JiraSite("sandbox", "Sandbox Jira", "https://example.atlassian.net"),
            )
            interactive_cli._session.jira_site_key = "sandbox"
            output = io.StringIO()
            with patch("scripts.interactive_cli.active_db_path", return_value=db_path), \
                    patch(
                        "scripts.interactive_cli.load_credentials",
                        return_value=Credentials(
                            token="",
                            workspace_gid="",
                            skip_ssl_verification=False,
                            atlassian_username="u",
                            atlassian_token="t",
                        ),
                    ), \
                    patch(
                        "scripts.interactive_cli.export_project_to_jira_csv.lookup_jira_project_name",
                        return_value="New Project",
                    ), \
                    patch("builtins.input", side_effect=["3", "1", "proj_1,proj_2,proj_1", "new", "y"]), \
                    redirect_stdout(output):
                interactive_cli.run_import_jira_project_map("INFO")

            rendered = output.getvalue()
            self.assertIn("Project GID scope for association overwrite", rendered)
            self.assertIn("First Project", rendered)
            self.assertIn("Second Project", rendered)
            with database.connect() as connection:
                rows = connection.execute(
                    "SELECT project_gid, jira_project_key, jira_project_name "
                    "FROM jira_project_mappings ORDER BY project_gid"
                ).fetchall()
            self.assertEqual(
                [
                    (
                        row["project_gid"],
                        row["jira_project_key"],
                        row["jira_project_name"],
                    )
                    for row in rows
                ],
                [("proj_1", "NEW", "New Project"), ("proj_2", "NEW", "New Project")],
            )

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
        with patch(
            "scripts.interactive_cli._prompt_attachment_archive_manifest",
            return_value="/secure/archives/wave-01/manifest.json",
        ), patch("builtins.input", side_effect=["1", "y"]):
            upload_args = interactive_cli.args_upload_attachment_archive("INFO")
        self.assertEqual(
            upload_args,
            [
                "--log-level", "INFO", "--manifest",
                "/secure/archives/wave-01/manifest.json", "--dry-run",
            ],
        )

    def test_attachment_archive_upload_accepts_project_gids(self) -> None:
        with patch(
            "scripts.interactive_cli._prompt_attachment_archive_manifest",
            return_value="/secure/archives/wave-01/manifest.json",
        ), patch("builtins.input", side_effect=["2", "project_1,project_2", "y"]):
            args = interactive_cli.args_upload_attachment_archive("INFO")

        self.assertEqual(
            args,
            [
                "--log-level", "INFO", "--manifest", "/secure/archives/wave-01/manifest.json",
                "--project-gids", "project_1,project_2", "--dry-run",
            ],
        )

    def test_attachment_archive_upload_lists_and_selects_existing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            archive_root = project_root / "attachment_archives"
            manifest = archive_root / "wave-01" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}", encoding="utf-8")

            with patch.object(interactive_cli, "PROJECT_ROOT", project_root), patch(
                "builtins.input", side_effect=["1", "1", "y"]
            ):
                args = interactive_cli.args_upload_attachment_archive("INFO")

            self.assertEqual(
                args,
                ["--log-level", "INFO", "--manifest", str(manifest), "--dry-run"],
            )

    def test_regular_and_archival_exports_use_project_gid_file_picker(self) -> None:
        with patch("scripts.interactive_cli._open_sqlite", side_effect=SystemExit), patch(
            "scripts.interactive_cli._prompt_project_gids_file",
            return_value="/repo/projects.csv",
        ), patch(
            "builtins.input",
            side_effect=[
                "2", "y", "", "n", "n", "", "", "", "", "", "", "n",
                "2", "y", "", "", "", "", "",
            ],
        ):
            regular_args, allow_merge = interactive_cli.args_export_project_to_jira("INFO")
            archival_args = interactive_cli.args_export_archival_project("INFO")
        self.assertTrue(allow_merge)
        self.assertIn("--project-gids-file", regular_args)
        self.assertIn("/repo/projects.csv", regular_args)
        self.assertIn("--project-gids-file", archival_args)
        self.assertIn("/repo/projects.csv", archival_args)
        self.assertNotIn("--workspace-gid", archival_args)

    @patch("scripts.interactive_cli.run_export_project_to_jira")
    def test_unified_task_export_uses_column_mode_by_default(self, mock_regular) -> None:
        interactive_cli.run_export_tasks_to_jira("INFO")
        mock_regular.assert_called_once_with("INFO")

    @patch("scripts.interactive_cli.run_export_project_to_jira")
    def test_unified_task_export_delegates_to_project_exporter(self, mock_regular) -> None:
        interactive_cli.run_export_tasks_to_jira("INFO")
        mock_regular.assert_called_once_with("INFO")

    def test_archival_export_accepts_inline_task_gids(self) -> None:
        with patch(
            "builtins.input",
            side_effect=["4", "n", "task_1,task_2", "", "", "", "", ""],
        ):
            args = interactive_cli.args_export_archival_project("INFO")
        self.assertIn("--task-gids", args)
        self.assertIn("task_1,task_2", args)

    def test_jira_csv_importer_configuration_is_standalone(self) -> None:
        with patch(
            "builtins.input",
            side_effect=["y"],
        ):
            args = interactive_cli.args_generate_jira_csv_import_config("INFO")
        self.assertIn("--configure", args)

    def test_regular_export_accepts_inline_task_gids(self) -> None:
        with patch(
            "scripts.interactive_cli._open_sqlite",
            side_effect=SystemExit,
        ), patch(
            "builtins.input",
            side_effect=["4", "n", "task_1,task_2", "", "n", "n", "", "", "", "", "", "", "n"],
        ):
            args, allow_merge = interactive_cli.args_export_project_to_jira("INFO")
        self.assertFalse(allow_merge)
        self.assertIn("--task-gids", args)
        self.assertIn("task_1,task_2", args)

    def test_regular_export_accepts_custom_synthetic_epic_limit(self) -> None:
        with patch(
            "scripts.interactive_cli._open_sqlite",
            side_effect=SystemExit,
        ), patch(
            "builtins.input",
            side_effect=["4", "n", "task_1,task_2", "", "n", "n", "", "", "", "", "", "250", "n"],
        ):
            args, _allow_merge = interactive_cli.args_export_project_to_jira("INFO")
        self.assertIn("--synthetic-epic-limit", args)
        self.assertIn("250", args)

    def test_archival_export_accepts_custom_synthetic_epic_limit(self) -> None:
        with patch(
            "builtins.input",
            side_effect=["4", "n", "task_1,task_2", "", "", "", "", "250"],
        ):
            args = interactive_cli.args_export_archival_project("INFO")
        self.assertIn("--synthetic-epic-limit", args)
        self.assertIn("250", args)

    def test_regular_and_archival_exports_use_task_gid_file_picker(self) -> None:
        with patch(
            "scripts.interactive_cli._open_sqlite",
            side_effect=SystemExit,
        ), patch(
            "scripts.interactive_cli._prompt_task_gids_file",
            return_value="/repo/missing-task-gids.txt",
        ), patch(
            "builtins.input",
            side_effect=[
                "4", "y", "", "n", "n", "n", "", "", "", "", "", "n",
                "4", "y", "", "", "", "", "",
            ],
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
        with patch("builtins.input", return_value="1"):
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

        with patch("builtins.input", return_value="1"):
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

    def test_remove_active_default_jira_site_selects_remaining_site(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "client.env"
            env_path.write_text(
                "ASANA_WORKSPACE_GID=w\nJIRA_SITES=production_jira,sandbox_jira\n"
                "JIRA_DEFAULT_SITE=production_jira\n"
                "JIRA_SITE_PRODUCTION_JIRA_NAME='Production Jira'\n"
                "JIRA_SITE_PRODUCTION_JIRA_URL=https://prod.example.net\n"
                "JIRA_SITE_SANDBOX_JIRA_NAME='Sandbox Jira'\n"
                "JIRA_SITE_SANDBOX_JIRA_URL=https://sandbox.example.net\n",
                encoding="utf-8",
            )
            interactive_cli._set_session(Path(tmpdir), env_path)
            with patch("scripts.interactive_cli.prompt", return_value="1"), \
                patch("scripts.interactive_cli.confirm", return_value=True), \
                redirect_stdout(io.StringIO()):
                interactive_cli.remove_jira_site()

            self.assertEqual(interactive_cli._session.jira_site_key, "sandbox_jira")
            self.assertEqual(interactive_cli._session.default_jira_site_key, "sandbox_jira")
            saved = env_path.read_text(encoding="utf-8")
            self.assertIn("JIRA_SITES=sandbox_jira", saved)
            self.assertIn("JIRA_DEFAULT_SITE=sandbox_jira", saved)
            self.assertNotIn("JIRA_SITE_PRODUCTION_JIRA_URL", saved)

    def test_edit_jira_site_preserves_key_and_updates_saved_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / "client.env"
            env_path.write_text(
                "ASANA_WORKSPACE_GID=w\nJIRA_SITES=sandbox_jira\n"
                "JIRA_DEFAULT_SITE=sandbox_jira\n"
                "JIRA_SITE_SANDBOX_JIRA_NAME='Sandbox Jira'\n"
                "JIRA_SITE_SANDBOX_JIRA_URL=https://sandbox.example.net\n",
                encoding="utf-8",
            )
            interactive_cli._set_session(Path(tmpdir), env_path)
            with patch(
                "scripts.interactive_cli.prompt",
                side_effect=["1", "Updated Sandbox", "https://updated.example.net/"],
            ), redirect_stdout(io.StringIO()):
                interactive_cli.edit_jira_site()

            site = interactive_cli._session.jira_sites[0]
            self.assertEqual(site.key, "sandbox_jira")
            self.assertEqual(site.name, "Updated Sandbox")
            self.assertEqual(site.base_url, "https://updated.example.net")
            saved = env_path.read_text(encoding="utf-8")
            self.assertIn("JIRA_SITE_SANDBOX_JIRA_NAME='Updated Sandbox'", saved)
            self.assertIn("JIRA_SITE_SANDBOX_JIRA_URL=https://updated.example.net", saved)

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

    def test_b_goes_back_from_any_prompt_inside_a_command_group(self) -> None:
        original_depth = interactive_cli._menu_depth
        interactive_cli._menu_depth = 1
        try:
            with patch("builtins.input", return_value=" B ") as mock_input:
                with self.assertRaises(interactive_cli.CliBack):
                    interactive_cli.prompt("Any value")
            rendered_prompt = mock_input.call_args.args[0]
            self.assertIn("b to go back", rendered_prompt)
            self.assertIn("q to quit", rendered_prompt)
        finally:
            interactive_cli._menu_depth = original_depth

    def test_b_from_nested_script_returns_to_command_group(self) -> None:
        def prompting_script(_argv) -> None:
            input("Nested script prompt: ")

        menu = {
            "1": interactive_cli.MenuItem(
                "1", "Prompting command", lambda: interactive_cli.run_script(prompting_script, [])
            ),
            "b": interactive_cli.MenuItem("b", "Back", lambda: None),
        }
        with patch("builtins.input", side_effect=["1", "b"]), redirect_stdout(io.StringIO()):
            interactive_cli.run_group("Test", menu)
        self.assertEqual(interactive_cli._menu_depth, 0)

    def test_invoked_script_failure_returns_to_command_group(self) -> None:
        def failing_script(_argv) -> None:
            raise SystemExit("No Jira project/space keys are stored for the selected Jira site.")

        menu = {
            "1": interactive_cli.MenuItem(
                "1", "Failing command", lambda: interactive_cli.run_script(failing_script, [])
            ),
            "b": interactive_cli.MenuItem("b", "Back", lambda: None),
        }
        output = io.StringIO()
        with patch("builtins.input", side_effect=["1", "b"]), redirect_stdout(output):
            interactive_cli.run_group("Test", menu)
        self.assertIn("Command could not complete", output.getvalue())
        self.assertIn("No Jira project/space keys", output.getvalue())

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

    def test_print_menu_adds_quit_to_submenu(self) -> None:
        menu = {
            "1": interactive_cli.MenuItem("1", "One", lambda: None),
            "b": interactive_cli.MenuItem("b", "Back", lambda: None),
        }
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            interactive_cli.print_menu("Submenu", menu)
        self.assertIn("[b] Back", buffer.getvalue())
        self.assertIn("[q] Quit", buffer.getvalue())


class LifecycleMenuTest(TestCase):
    def test_top_menu_follows_migration_lifecycle(self) -> None:
        menu = interactive_cli.build_top_menu("INFO")
        self.assertEqual(
            [menu[str(index)].label for index in range(1, 8)],
            [
                "Client & Environment",
                "Discover & Refresh Asana",
                "Prepare Jira",
                "Assess & Plan Migration",
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

    def test_migrate_projects_menu_uses_the_durable_attachment_workflow(self) -> None:
        menu = interactive_cli.build_migrate_projects_menu("INFO")

        self.assertEqual(list(menu), ["1", "2", "3", "4", "5", "6", "7", "8", "b"])
        self.assertEqual(
            menu["3"].label,
            "Associate Jira Task Keys with Asana Task Keys",
        )
        self.assertNotIn("Sync Jira attachments immediately", [item.label for item in menu.values()])
        self.assertEqual(menu["4"].label, "Import/update Jira project associations")
        self.assertEqual(menu["5"].label, "Validate Jira task migration coverage")
        self.assertEqual(menu["6"].label, "Manage export IDs")

    @patch("scripts.interactive_cli.run_script")
    @patch("scripts.interactive_cli.confirm", return_value=True)
    @patch(
        "scripts.interactive_cli.list_export_ids",
        return_value=[
            interactive_cli.ExportIdSummary(
                export_id="wave_one",
                task_count=2,
                project_count=1,
                first_used="2025-01-01T00:00:00+00:00",
                last_used="2025-01-02T00:00:00+00:00",
            )
        ],
    )
    @patch("scripts.interactive_cli.prompt", side_effect=["1", "wave_one"])
    def test_manage_export_ids_confirms_before_clearing_one_id(
        self,
        mock_prompt,
        mock_list_export_ids,
        mock_confirm,
        mock_run_script,
    ) -> None:
        interactive_cli.run_clear_one_export_id("INFO")

        mock_confirm.assert_called_once()
        mock_run_script.assert_called_once()
        args = mock_run_script.call_args.args[1]
        self.assertIn("--clear-export-id", args)
        self.assertIn("wave_one", args)

    def test_discover_menu_exposes_new_project_workflow(self) -> None:
        menu = interactive_cli.build_discover_refresh_menu("INFO")
        self.assertEqual(menu["9"].label, "Discover and sync newly found projects")

    @patch("scripts.interactive_cli.run_task_sync_for_args")
    @patch("scripts.interactive_cli.run_script")
    @patch("scripts.interactive_cli._select_discovered_project_gids", return_value=["proj_1", "proj_2"])
    @patch("scripts.interactive_cli._new_projects_after_refresh", return_value=[object()])
    @patch("scripts.interactive_cli._known_project_gids", return_value={"existing"})
    @patch("scripts.interactive_cli.confirm", side_effect=[True, True, True, False])
    @patch("scripts.interactive_cli.prompt", return_value="6")
    def test_new_project_discovery_syncs_selected_projects(
        self,
        mock_prompt,
        mock_confirm,
        mock_known,
        mock_new,
        mock_select,
        mock_run_script,
        mock_task_sync,
    ) -> None:
        interactive_cli.run_new_project_discovery_flow("INFO")

        mock_new.assert_called_once_with({"existing"})
        mock_select.assert_called_once()
        self.assertEqual(
            [call.args for call in mock_run_script.call_args_list],
            [
                (interactive_cli.get_projects.main, ["--log-level", "INFO", "--archived-scope", "active"]),
                (interactive_cli.add_service_user_to_projects.main, ["--log-level", "INFO", "--project-gids", "proj_1,proj_2"]),
                (interactive_cli.get_task_extras.main, ["--log-level", "INFO", "--project-gids", "proj_1,proj_2"]),
            ],
        )
        mock_task_sync.assert_called_once_with(
            ["--log-level", "INFO", "--project-gids", "proj_1,proj_2", "--workers", "6"],
            "INFO",
        )

    def test_token_migration_is_only_in_advanced_recovery(self) -> None:
        client_menu = interactive_cli.build_client_environment_menu()
        advanced_menu = interactive_cli.build_advanced_recovery_menu("INFO")
        token_label = "Migrate tokens from env file to macOS Keychain"

        self.assertNotIn(token_label, [item.label for item in client_menu.values()])
        self.assertEqual(advanced_menu["5"].label, token_label)

        with patch("scripts.interactive_cli.run_token_migration") as mock_migrate:
            advanced_menu["5"].handler()
        mock_migrate.assert_called_once_with(interactive_cli.PROJECT_ROOT)

    def test_advanced_recovery_exposes_database_anonymizer(self) -> None:
        menu = interactive_cli.build_advanced_recovery_menu("INFO")

        self.assertEqual(menu["6"].label, "Create anonymized troubleshooting database")

    def test_prepare_jira_starts_with_standard_import_field_setup(self) -> None:
        menu = interactive_cli.build_prepare_jira_menu("INFO")
        self.assertEqual(menu["1"].label, "Create Jira fields for task-import mappings")
        self.assertEqual(menu["2"].label, "Create Jira fields for Team/Project tracking")
        self.assertEqual(menu["5"].label, "Preview or create Team/Project Jira hierarchy (API)")
        self.assertNotIn(
            "Export Team -> Epic / Project -> Task CSV",
            [item.label for item in menu.values()],
        )

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

    def test_readiness_reports_current_service_account_team_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "readiness.sqlite"
            connection = interactive_cli.sqlite3.connect(str(db_path))
            connection.executescript(
                """
                CREATE TABLE projects (gid TEXT, synced_at TEXT);
                CREATE TABLE tasks (gid TEXT, synced_at TEXT);
                CREATE TABLE teams (gid TEXT);
                INSERT INTO teams VALUES ('team-1'), ('team-2');
                CREATE TABLE team_members (
                    team_gid TEXT, user_gid TEXT, is_active INTEGER, synced_at TEXT
                );
                INSERT INTO team_members VALUES
                    ('team-1', 'service-user', 1, '2026-08-17T00:00:00Z');
                """
            )
            connection.commit()
            connection.close()
            credentials = interactive_cli.Credentials(
                token="token", workspace_gid="workspace", skip_ssl_verification=False
            )
            output = io.StringIO()
            with patch("scripts.interactive_cli.active_db_path", return_value=db_path), \
                    patch("scripts.interactive_cli.load_credentials", return_value=credentials), \
                    patch("scripts.interactive_cli.AsanaHttpClient") as mock_client, \
                    redirect_stdout(output):
                mock_client.return_value.get.return_value = {
                    "data": {"gid": "service-user", "name": "Migration Service"}
                }
                interactive_cli.show_migration_readiness()
        rendered = output.getvalue()
        self.assertIn(
            "[!] Service-account team access (Migration Service): 1 of 2 teams (all teams)",
            rendered,
        )
        self.assertIn("Active service account is absent from 1 team(s)", rendered)

    def test_scoped_export_readiness_is_advisory_when_csv_config_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "readiness.sqlite"
            connection = interactive_cli.sqlite3.connect(str(db_path))
            connection.executescript(
                """
                CREATE TABLE projects (gid TEXT, team_gid TEXT, synced_at TEXT);
                INSERT INTO projects VALUES ('p1', 'team-1', '2026-08-17T00:00:00Z');
                CREATE TABLE tasks (gid TEXT, synced_at TEXT, assignee_gid TEXT, created_by_gid TEXT);
                INSERT INTO tasks VALUES ('t1', '2026-08-17T00:00:00Z', '', '');
                CREATE TABLE task_projects (task_gid TEXT, project_gid TEXT);
                INSERT INTO task_projects VALUES ('t1', 'p1');
                CREATE TABLE task_comments (task_gid TEXT, created_by_gid TEXT, synced_at TEXT);
                CREATE TABLE task_attachments (task_gid TEXT, synced_at TEXT);
                CREATE TABLE user_atlassian_accounts (user_gid TEXT, atlassian_account_id TEXT);
                CREATE TABLE jira_project_mappings (
                    project_gid TEXT, jira_site_key TEXT, jira_project_name TEXT,
                    jira_project_key TEXT
                );
                INSERT INTO jira_project_mappings VALUES
                    ('p1', 'sandbox', 'Migration', 'MIG');
                CREATE TABLE teams (gid TEXT);
                INSERT INTO teams VALUES ('team-1');
                CREATE TABLE team_members (
                    team_gid TEXT, user_gid TEXT, is_active INTEGER, synced_at TEXT
                );
                INSERT INTO team_members VALUES
                    ('team-1', 'service-user', 1, '2026-08-17T00:00:00Z');
                """
            )
            connection.commit()
            connection.close()
            output = io.StringIO()
            with patch("scripts.interactive_cli.active_db_path", return_value=db_path), \
                    patch("scripts.interactive_cli.latest_profile_import_config_path", return_value=None), \
                    patch("scripts.interactive_cli.load_credentials", return_value=interactive_cli.Credentials(
                        token="token", workspace_gid="workspace", skip_ssl_verification=False
                    )), \
                    patch("scripts.interactive_cli.AsanaHttpClient") as mock_client, \
                    redirect_stdout(output):
                mock_client.return_value.get.return_value = {
                    "data": {"gid": "service-user", "name": "Migration Service"}
                }
                interactive_cli.show_export_readiness(
                    ["--team-gid", "team-1", "--jira-site", "sandbox"],
                    "Jira task CSV",
                    jira_csv=True,
                )
        rendered = output.getvalue()
        self.assertIn("[x] Selected data: 1 projects / 1 tasks", rendered)
        self.assertIn("projects 2026-08-17 / tasks 2026-08-17", rendered)
        self.assertNotIn("2026-08-17T00:00:00Z", rendered)
        self.assertIn("[!] Jira CSV configuration: no saved profile", rendered)
        self.assertIn(
            "[x] Jira project associations (sandbox): 1 of 1 selected projects mapped",
            rendered,
        )
        self.assertIn(
            "[x] Service-account team access (Migration Service): 1 of 1 selected teams",
            rendered,
        )
        self.assertIn("warnings do not prevent export", rendered)

    def test_scoped_export_readiness_uses_project_gid_column_and_reports_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "readiness.sqlite"
            scope_file = root / "project-map.csv"
            scope_file.write_text(
                "project_gid,project_key,project_name\np1,FAC-1,Found\np2,FAC-2,Missing\n"
            )
            connection = interactive_cli.sqlite3.connect(str(db_path))
            connection.executescript(
                """
                CREATE TABLE projects (gid TEXT, team_gid TEXT, synced_at TEXT);
                INSERT INTO projects VALUES ('p1', 'team-1', '2026-08-17T00:00:00Z');
                CREATE TABLE tasks (gid TEXT, synced_at TEXT, assignee_gid TEXT, created_by_gid TEXT);
                INSERT INTO tasks VALUES ('t1', '2026-08-17T00:00:00Z', '', '');
                CREATE TABLE task_projects (task_gid TEXT, project_gid TEXT);
                INSERT INTO task_projects VALUES ('t1', 'p1');
                CREATE TABLE task_comments (task_gid TEXT, created_by_gid TEXT, synced_at TEXT);
                CREATE TABLE task_attachments (task_gid TEXT, synced_at TEXT);
                CREATE TABLE user_atlassian_accounts (user_gid TEXT, atlassian_account_id TEXT);
                """
            )
            connection.commit()
            connection.close()
            output = io.StringIO()
            with patch("scripts.interactive_cli.active_db_path", return_value=db_path), redirect_stdout(output):
                interactive_cli.show_export_readiness(
                    ["--project-gids-file", str(scope_file)], "Jira task CSV", jira_csv=True
                )
        rendered = output.getvalue()
        self.assertIn("1 of 2 requested projects / 1 tasks", rendered)
        self.assertIn("1 requested project GID(s) were not found", rendered)

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
