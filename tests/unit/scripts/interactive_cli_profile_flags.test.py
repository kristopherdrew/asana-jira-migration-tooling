from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from scripts import get_projects, inspect_task_apps, interactive_cli
from src.lib.config import JiraSite

PROD_KEY = "production_jira"
ARCHIVE_KEY = "archive_jira"
ACTIVE_DB = Path("/tmp/active_profile.sqlite")
SITE_FLAG = "--jira-site"
DATABASE_FLAG = "--database"
IMPORT_STATUS_MENU_KEY = "j"
ATJM_COVERAGE_MENU_KEY = "k"
ALL_SITES_CHOICE = "all"
PROD_SITE_NUMBER = "1"


class ReportSiteSelectionTest(TestCase):
    """The report handlers must pass exactly the sites the operator picked, never the session site."""

    def setUp(self) -> None:
        self._orig_session = interactive_cli._session
        interactive_cli._session = interactive_cli.CliSession(
            reference_path=Path("/tmp/project"),
            env_path=Path("/tmp/.asana_jira_env"),
            jira_sites=(
                JiraSite(PROD_KEY, "Production Jira", "https://prod.example.atlassian.net"),
                JiraSite(ARCHIVE_KEY, "Archive Jira", "https://archive.example.atlassian.net"),
            ),
            jira_site_key=ARCHIVE_KEY,
        )
        self.addCleanup(lambda: setattr(interactive_cli, "_session", self._orig_session))

    def _run_report(self, menu_key: str, answers: list[str]) -> list[str]:
        captured: dict[str, list[str]] = {}

        def fake_run_script(main_func, argv):
            captured["argv"] = list(argv)

        menu = interactive_cli.build_reports_menu("INFO")
        with (
            patch.object(interactive_cli, "run_script", side_effect=fake_run_script),
            patch("builtins.input", side_effect=answers),
            redirect_stdout(io.StringIO()),
        ):
            menu[menu_key].handler()
        return captured["argv"]

    def _site_values(self, argv: list[str]) -> list[str]:
        return [argv[i + 1] for i, arg in enumerate(argv) if arg == SITE_FLAG]

    def test_import_status_picking_production_ignores_active_archive_site(self) -> None:
        argv = self._run_report(IMPORT_STATUS_MENU_KEY, [PROD_SITE_NUMBER, "1"])
        self.assertEqual(self._site_values(argv), [PROD_KEY], "only the picked site may be passed")

    def test_import_status_all_sites_passes_no_site_filter(self) -> None:
        argv = self._run_report(IMPORT_STATUS_MENU_KEY, [ALL_SITES_CHOICE, "1"])
        self.assertEqual(self._site_values(argv), [], "all must not collapse to the active session site")

    def test_atjm_coverage_picking_production_ignores_active_archive_site(self) -> None:
        argv = self._run_report(ATJM_COVERAGE_MENU_KEY, [PROD_SITE_NUMBER, "1", "1", "1"])
        self.assertEqual(self._site_values(argv), [PROD_KEY])

    def test_atjm_coverage_all_sites_passes_no_site_filter(self) -> None:
        argv = self._run_report(ATJM_COVERAGE_MENU_KEY, [ALL_SITES_CHOICE, "1", "1", "1"])
        self.assertEqual(self._site_values(argv), [])


class ActiveDatabaseInjectionTest(TestCase):
    def setUp(self) -> None:
        patcher = patch.object(interactive_cli, "active_db_path", return_value=ACTIVE_DB)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_script_with_database_flag_receives_active_profile_db(self) -> None:
        argv = interactive_cli.with_active_database(get_projects.main, ["--log-level", "INFO"])
        self.assertEqual(argv[-2:], [DATABASE_FLAG, str(ACTIVE_DB)])

    def test_script_without_database_flag_is_left_untouched(self) -> None:
        original = ["--log-level", "INFO"]
        self.assertEqual(interactive_cli.with_active_database(inspect_task_apps.main, original), original)

    def test_explicit_database_flag_is_not_duplicated(self) -> None:
        explicit = ["--database", "/tmp/other.sqlite"]
        self.assertEqual(interactive_cli.with_active_database(get_projects.main, explicit), explicit)
        equals_form = ["--database=/tmp/other.sqlite"]
        self.assertEqual(interactive_cli.with_active_database(get_projects.main, equals_form), equals_form)

    def test_run_script_passes_injected_database_to_main(self) -> None:
        received: list[str] = []

        def fake_main(argv):
            received.extend(argv)

        fake_main.__module__ = get_projects.main.__module__
        interactive_cli.run_script(fake_main, ["--log-level", "INFO"])
        self.assertIn(str(ACTIVE_DB), received)
