from __future__ import annotations

import csv
import tempfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.export_recurring_tasks_report import (
    ERROR_FIELDNAMES,
    INSTANCE_FIELDNAMES,
    SERIES_FIELDNAMES,
    export_recurring_tasks_report,
    main,
    parse_args,
    write_errors_csv,
    write_instances_csv,
    write_series_csv,
)
from src.lib.database import Database
from src.tasks.recurring_tasks import (
    RecurringTaskInstance,
    RecurringTaskLookupError,
    RecurringTaskSeries,
)


class ParseArgsTest(TestCase):
    def test_defaults(self) -> None:
        args = parse_args(["--project-gids", "proj_1"])
        self.assertEqual(args.project_gids, ["proj_1"])
        self.assertIsNone(args.project_gids_file)
        self.assertEqual(args.output_dir, "exports")
        self.assertIsNone(args.filename)
        self.assertEqual(args.page_size, 100)
        self.assertEqual(args.log_level, "INFO")

    def test_project_gids_can_repeat(self) -> None:
        args = parse_args(["--project-gids", "proj_1", "--project-gids", "proj_2"])
        self.assertEqual(args.project_gids, ["proj_1", "proj_2"])

    def test_database_flag_overrides_default(self) -> None:
        args = parse_args(["--project-gids", "proj_1", "--database", "custom.sqlite"])
        self.assertEqual(args.database, "custom.sqlite")


class MainScopeValidationTest(TestCase):
    def test_raises_system_exit_when_no_scope_provided(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            main([])
        self.assertIn("No project scope specified", str(ctx.exception))


class WriteCsvTest(TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.workspace = Path(self.tmp_dir.name)

    def test_write_instances_csv_shape(self) -> None:
        path = self.workspace / "instances.csv"
        instance = RecurringTaskInstance(
            project_gid="proj_1",
            project_name="Proj",
            task_gid="task_1",
            task_name="Weekly sync",
            task_type="task",
            parent_gid="",
            parent_name="",
            recurrence_type="weekly",
            recurrence_frequency="1",
            due_on="2026-01-01",
            completed=True,
            permalink_url="https://app.asana.com/0/1/1",
        )
        write_instances_csv(path, [instance])

        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, INSTANCE_FIELDNAMES)
            rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_gid"], "task_1")
        self.assertEqual(rows[0]["completed"], "True")

    def test_write_instances_csv_empty_list_writes_header_only(self) -> None:
        path = self.workspace / "empty.csv"
        write_instances_csv(path, [])
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, INSTANCE_FIELDNAMES)
            self.assertEqual(list(reader), [])

    def test_write_series_csv_shape(self) -> None:
        path = self.workspace / "series.csv"
        series = RecurringTaskSeries(
            project_gid="proj_1",
            project_name="Proj",
            task_name="Weekly sync",
            recurrence_type="weekly",
            recurrence_frequency="1",
            occurrence_count=3,
            first_due_on="2026-01-01",
            last_due_on="2026-01-15",
        )
        write_series_csv(path, [series])

        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, SERIES_FIELDNAMES)
            rows = list(reader)
        self.assertEqual(rows[0]["occurrence_count"], "3")
        self.assertEqual(rows[0]["first_due_on"], "2026-01-01")

    def test_write_errors_csv_shape(self) -> None:
        path = self.workspace / "errors.csv"
        error = RecurringTaskLookupError(
            project_gid="proj_1",
            project_name="Proj",
            task_gid="task_1",
            task_name="Weekly sync",
            status="403",
            message="Forbidden",
        )
        write_errors_csv(path, [error])

        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, ERROR_FIELDNAMES)
            rows = list(reader)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_gid"], "task_1")
        self.assertEqual(rows[0]["status"], "403")
        self.assertEqual(rows[0]["message"], "Forbidden")

    def test_write_errors_csv_empty_list_writes_header_only(self) -> None:
        path = self.workspace / "errors_empty.csv"
        write_errors_csv(path, [])
        with path.open("r", newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, ERROR_FIELDNAMES)
            self.assertEqual(list(reader), [])


class DummyClient:
    """Fake Asana client returning canned per-project task/subtask pages, keyed by endpoint."""

    def __init__(self, project_names, paginated_payloads, error_endpoints=None):
        self.project_names = project_names
        self.paginated_payloads = paginated_payloads
        self.error_endpoints = error_endpoints or set()

    def get(self, endpoint, params=None):
        if endpoint in self.error_endpoints:
            from src.lib.http_client import AsanaHttpError

            raise AsanaHttpError(403, "Forbidden")
        gid = endpoint.rsplit("/", 1)[-1]
        return {"data": {"name": self.project_names.get(gid, "")}}

    def get_paginated(self, endpoint, params=None):
        if endpoint in self.error_endpoints:
            from src.lib.http_client import AsanaHttpError

            raise AsanaHttpError(403, "Forbidden")
        for item in self.paginated_payloads.get(endpoint, []):
            yield item


class ExportRecurringTasksReportTest(TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.workspace = Path(self.tmp_dir.name)

        self.db_tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.db_tmpdir.cleanup)
        db_path = Path(self.db_tmpdir.name) / "asana.sqlite"
        self.database = Database(str(db_path))

    def test_end_to_end_aggregates_multiple_projects(self) -> None:
        client = DummyClient(
            project_names={"proj_1": "Alpha", "proj_2": "Beta"},
            paginated_payloads={
                "/projects/proj_1/tasks": [
                    {"gid": "task_1", "name": "Weekly sync", "recurrence": {"type": "weekly", "data": {"frequency": 1}}, "due_on": "2026-01-01"},
                ],
                "/tasks/task_1/subtasks": [],
                "/projects/proj_2/tasks": [
                    {"gid": "task_2", "name": "Daily standup", "recurrence": {"type": "daily", "data": {"frequency": 1}}, "due_on": "2026-02-01"},
                    {"gid": "task_3", "name": "One-off task", "recurrence": None},
                ],
                "/tasks/task_2/subtasks": [],
                "/tasks/task_3/subtasks": [],
            },
        )
        output_dir = self.workspace / "exports"

        instances_path, series_path, errors_path = export_recurring_tasks_report(
            client=client,
            database=self.database,
            project_gids=["proj_1", "proj_2"],
            output_dir=output_dir,
            filename="recurring.csv",
        )

        self.assertEqual(instances_path, output_dir.resolve() / "recurring.csv")
        self.assertEqual(series_path, output_dir.resolve() / "recurring_series_summary.csv")
        self.assertEqual(errors_path, output_dir.resolve() / "recurring_errors.csv")
        self.assertTrue(instances_path.exists())
        self.assertTrue(series_path.exists())
        self.assertTrue(errors_path.exists())
        with errors_path.open("r", newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])

        with instances_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 2)
        gids = {row["task_gid"] for row in rows}
        self.assertEqual(gids, {"task_1", "task_2"})
        alpha_row = next(row for row in rows if row["task_gid"] == "task_1")
        self.assertEqual(alpha_row["project_name"], "Alpha")

        with series_path.open("r", newline="", encoding="utf-8") as handle:
            series_rows = list(csv.DictReader(handle))
        self.assertEqual(len(series_rows), 2)

        # Every task seen (recurring or not) was written back to the DB, so a re-run
        # against the same projects would need no further live API calls.
        connection = self.database.connect()
        try:
            checked = {
                row["gid"]: row["recurrence_checked_at"]
                for row in connection.execute("SELECT gid, recurrence_checked_at FROM tasks")
            }
        finally:
            connection.close()
        self.assertEqual(set(checked), {"task_1", "task_2", "task_3"})
        self.assertTrue(all(value is not None for value in checked.values()))

    def test_default_filename_derives_series_summary_name(self) -> None:
        client = DummyClient(project_names={"proj_1": "Alpha"}, paginated_payloads={
            "/projects/proj_1/tasks": [],
        })
        output_dir = self.workspace / "exports"

        instances_path, series_path, errors_path = export_recurring_tasks_report(
            client=client,
            database=self.database,
            project_gids=["proj_1"],
            output_dir=output_dir,
        )

        self.assertTrue(instances_path.name.startswith("recurring_tasks_instances_"))
        self.assertTrue(instances_path.name.endswith(".csv"))
        expected_series_name = f"{instances_path.stem}_series_summary.csv"
        self.assertEqual(series_path.name, expected_series_name)

    def test_no_recurring_tasks_writes_empty_csvs(self) -> None:
        client = DummyClient(project_names={"proj_1": "Alpha"}, paginated_payloads={
            "/projects/proj_1/tasks": [{"gid": "task_1"}],
            "/tasks/task_1/subtasks": [],
        })
        output_dir = self.workspace / "exports"

        instances_path, series_path, errors_path = export_recurring_tasks_report(
            client=client,
            database=self.database,
            project_gids=["proj_1"],
            output_dir=output_dir,
            filename="none.csv",
        )

        with instances_path.open("r", newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])
        with series_path.open("r", newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])
        with errors_path.open("r", newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])

    def test_per_task_lookup_failure_writes_error_row(self) -> None:
        # A task synced before `recurrence` existed (unchecked) whose live detail lookup
        # fails must appear in the errors CSV, without aborting the rest of the project.
        connection = self.database.connect()
        try:
            self.database.initialize(connection)
            connection.execute(
                "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
                ("proj_1", "Alpha", "2025-10-31T00:00:00+00:00"),
            )
            connection.execute(
                "INSERT INTO tasks (gid, name, synced_at) VALUES (?, ?, ?)",
                ("task_forbidden", "Forbidden task", "2025-10-31T00:00:00+00:00"),
            )
            connection.execute(
                "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
                ("task_forbidden", "proj_1", "2025-10-31T00:00:00+00:00"),
            )
            connection.commit()
        finally:
            connection.close()

        client = DummyClient(
            project_names={},
            paginated_payloads={},
            error_endpoints={"/tasks/task_forbidden"},
        )
        output_dir = self.workspace / "exports"

        instances_path, series_path, errors_path = export_recurring_tasks_report(
            client=client,
            database=self.database,
            project_gids=["proj_1"],
            output_dir=output_dir,
            filename="per_task_error.csv",
        )

        with errors_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project_gid"], "proj_1")
        self.assertEqual(rows[0]["project_name"], "Alpha")
        self.assertEqual(rows[0]["task_gid"], "task_forbidden")
        self.assertEqual(rows[0]["task_name"], "Forbidden task")
        self.assertEqual(rows[0]["status"], "403")
        with instances_path.open("r", newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])

    def test_whole_project_failure_writes_project_level_error_row(self) -> None:
        # A never-synced project whose live name/task lookup itself fails (e.g. no access)
        # must produce a project-level error row with blank task_gid/task_name.
        client = DummyClient(
            project_names={},
            paginated_payloads={},
            error_endpoints={"/projects/proj_bad"},
        )
        output_dir = self.workspace / "exports"

        instances_path, series_path, errors_path = export_recurring_tasks_report(
            client=client,
            database=self.database,
            project_gids=["proj_bad"],
            output_dir=output_dir,
            filename="project_error.csv",
        )

        with errors_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["project_gid"], "proj_bad")
        self.assertEqual(rows[0]["project_name"], "")
        self.assertEqual(rows[0]["task_gid"], "")
        self.assertEqual(rows[0]["task_name"], "")
        self.assertEqual(rows[0]["status"], "403")
        with instances_path.open("r", newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])

    def test_synced_project_uses_db_and_avoids_task_page_calls(self) -> None:
        """A project already synced via the normal task sync (task_projects populated)
        should read recurrence from SQLite instead of walking Asana live."""
        connection = self.database.connect()
        try:
            self.database.initialize(connection)
            connection.execute(
                "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
                ("proj_1", "Alpha", "2025-10-31T00:00:00+00:00"),
            )
            connection.execute(
                """
                INSERT INTO tasks (gid, name, due_on, recurrence, recurrence_checked_at, synced_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "task_1",
                    "Weekly sync",
                    "2026-01-01",
                    '{"type": "weekly", "data": {"frequency": 1}}',
                    "2025-10-31T00:00:00+00:00",
                    "2025-10-31T00:00:00+00:00",
                ),
            )
            connection.execute(
                "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
                ("task_1", "proj_1", "2025-10-31T00:00:00+00:00"),
            )
            connection.commit()
        finally:
            connection.close()

        # No project-name or pagination payloads configured — a live walk would find nothing.
        client = DummyClient(project_names={}, paginated_payloads={})
        output_dir = self.workspace / "exports"

        instances_path, _, _ = export_recurring_tasks_report(
            client=client,
            database=self.database,
            project_gids=["proj_1"],
            output_dir=output_dir,
            filename="synced.csv",
        )

        with instances_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["task_gid"], "task_1")
        self.assertEqual(rows[0]["project_name"], "Alpha")
