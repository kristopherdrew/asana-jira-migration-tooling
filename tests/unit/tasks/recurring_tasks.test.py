from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database
from src.lib.http_client import AsanaHttpError
from src.tasks.recurring_tasks import (
    RecurringTaskInstance,
    collect_recurring_task_instances,
    collect_recurring_task_instances_hybrid,
    extract_recurrence_type_and_frequency,
    fetch_project_name,
    fetch_project_name_from_db,
    fetch_project_task_rows,
    group_into_series,
    is_recurring,
    walk_project_tasks,
)


class DummyClient:
    """Fake Asana client keyed by endpoint, mirroring the pattern used for tasks.py."""

    def __init__(self, get_payloads=None, paginated_payloads=None, error_endpoints=None):
        self.get_payloads = get_payloads or {}
        self.paginated_payloads = paginated_payloads or {}
        self.error_endpoints = error_endpoints or set()
        self.calls = []

    def get(self, endpoint, params=None):
        self.calls.append(("get", endpoint, params))
        if endpoint in self.error_endpoints:
            raise AsanaHttpError(403, "Forbidden")
        return {"data": self.get_payloads.get(endpoint, {})}

    def get_paginated(self, endpoint, params=None):
        self.calls.append(("get_paginated", endpoint, params))
        if endpoint in self.error_endpoints:
            raise AsanaHttpError(403, "Forbidden")
        for item in self.paginated_payloads.get(endpoint, []):
            yield item


class FetchProjectNameTest(TestCase):
    def test_returns_project_name_when_present(self) -> None:
        client = DummyClient(get_payloads={"/projects/proj_1": {"name": "Website"}})
        self.assertEqual(fetch_project_name(client, "proj_1"), "Website")

    def test_falls_back_to_gid_when_name_missing(self) -> None:
        client = DummyClient(get_payloads={"/projects/proj_1": {}})
        self.assertEqual(fetch_project_name(client, "proj_1"), "proj_1")

    def test_falls_back_to_gid_when_name_blank(self) -> None:
        client = DummyClient(get_payloads={"/projects/proj_1": {"name": ""}})
        self.assertEqual(fetch_project_name(client, "proj_1"), "proj_1")


class WalkProjectTasksTest(TestCase):
    def test_yields_top_level_tasks_as_task_type(self) -> None:
        client = DummyClient(
            paginated_payloads={
                "/projects/proj_1/tasks": [{"gid": "task_1"}, {"gid": "task_2"}],
            }
        )
        results = list(walk_project_tasks(client, "proj_1"))
        self.assertEqual([(t["gid"], kind) for t, kind in results], [
            ("task_1", "task"),
            ("task_2", "task"),
        ])

    def test_recurses_into_nested_subtasks_arbitrarily_deep(self) -> None:
        client = DummyClient(
            paginated_payloads={
                "/projects/proj_1/tasks": [{"gid": "task_1"}],
                "/tasks/task_1/subtasks": [{"gid": "sub_1"}],
                "/tasks/sub_1/subtasks": [{"gid": "sub_sub_1"}],
                "/tasks/sub_sub_1/subtasks": [],
            }
        )
        results = list(walk_project_tasks(client, "proj_1"))
        self.assertEqual(
            [(t["gid"], kind) for t, kind in results],
            [
                ("task_1", "task"),
                ("sub_1", "subtask"),
                ("sub_sub_1", "subtask"),
            ],
        )

    def test_seen_set_guards_against_duplicates_and_cycles(self) -> None:
        # sub_1 appears twice at the top level, and sub_1's own subtasks loop back to task_1.
        client = DummyClient(
            paginated_payloads={
                "/projects/proj_1/tasks": [{"gid": "task_1"}, {"gid": "task_1"}],
                "/tasks/task_1/subtasks": [{"gid": "sub_1"}, {"gid": "sub_1"}],
                "/tasks/sub_1/subtasks": [{"gid": "task_1"}],
            }
        )
        results = list(walk_project_tasks(client, "proj_1"))
        gids = [t["gid"] for t, _ in results]
        self.assertEqual(gids, ["task_1", "sub_1"])

    def test_skips_subtasks_on_api_error_instead_of_raising(self) -> None:
        client = DummyClient(
            paginated_payloads={
                "/projects/proj_1/tasks": [{"gid": "task_1"}, {"gid": "task_2"}],
                "/tasks/task_2/subtasks": [{"gid": "sub_2"}],
            },
            error_endpoints={"/tasks/task_1/subtasks"},
        )
        results = list(walk_project_tasks(client, "proj_1"))
        gids = [t["gid"] for t, _ in results]
        self.assertEqual(gids, ["task_1", "task_2", "sub_2"])

    def test_skips_tasks_missing_gid(self) -> None:
        client = DummyClient(
            paginated_payloads={
                "/projects/proj_1/tasks": [{"name": "no gid here"}, {"gid": "task_1"}],
            }
        )
        results = list(walk_project_tasks(client, "proj_1"))
        self.assertEqual([t["gid"] for t, _ in results], ["task_1"])


class IsRecurringTest(TestCase):
    def test_none_recurrence_is_not_recurring(self) -> None:
        self.assertFalse(is_recurring(None))

    def test_never_type_is_not_recurring(self) -> None:
        # Regression: Asana's real non-recurring sentinel is the literal string "never",
        # not null/absent — a plain truthiness check on `type` let every task through.
        self.assertFalse(is_recurring({"type": "never", "data": None}))

    def test_null_type_is_not_recurring(self) -> None:
        self.assertFalse(is_recurring({"type": None, "data": None}))

    def test_weekly_type_is_recurring(self) -> None:
        self.assertTrue(is_recurring({"type": "weekly", "data": {"frequency": 1}}))


class ExtractRecurrenceTypeAndFrequencyTest(TestCase):
    def test_none_recurrence_returns_blank_tuple(self) -> None:
        self.assertEqual(extract_recurrence_type_and_frequency(None), ("", ""))

    def test_empty_dict_recurrence_returns_blank_tuple(self) -> None:
        self.assertEqual(extract_recurrence_type_and_frequency({}), ("", ""))

    def test_returns_type_and_frequency_when_present(self) -> None:
        recurrence = {"type": "weekly", "data": {"frequency": 2}}
        self.assertEqual(extract_recurrence_type_and_frequency(recurrence), ("weekly", "2"))

    def test_missing_frequency_defaults_to_blank(self) -> None:
        recurrence = {"type": "weekly", "data": {}}
        self.assertEqual(extract_recurrence_type_and_frequency(recurrence), ("weekly", ""))

    def test_missing_data_key_defaults_to_blank_frequency(self) -> None:
        recurrence = {"type": "weekly"}
        self.assertEqual(extract_recurrence_type_and_frequency(recurrence), ("weekly", ""))


class CollectRecurringTaskInstancesTest(TestCase):
    def test_filters_to_tasks_with_recurrence_only(self) -> None:
        client = DummyClient(
            paginated_payloads={
                "/projects/proj_1/tasks": [
                    {"gid": "task_1", "name": "Weekly sync", "recurrence": {"type": "weekly", "data": {"frequency": 1}}},
                    {"gid": "task_2", "name": "One-off", "recurrence": None},
                    {"gid": "task_3", "name": "No recurrence key"},
                    {"gid": "task_4", "name": "Never recurs", "recurrence": {"type": "never", "data": None}},
                ],
                "/tasks/task_1/subtasks": [],
                "/tasks/task_2/subtasks": [],
                "/tasks/task_4/subtasks": [],
                "/tasks/task_3/subtasks": [],
            }
        )
        instances = collect_recurring_task_instances(client, "proj_1", "My Project")
        self.assertEqual(len(instances), 1)
        instance = instances[0]
        self.assertIsInstance(instance, RecurringTaskInstance)
        self.assertEqual(instance.task_gid, "task_1")
        self.assertEqual(instance.project_name, "My Project")
        self.assertEqual(instance.recurrence_type, "weekly")
        self.assertEqual(instance.recurrence_frequency, "1")
        self.assertEqual(instance.task_type, "task")

    def test_captures_parent_and_defaults_for_missing_fields(self) -> None:
        client = DummyClient(
            paginated_payloads={
                "/projects/proj_1/tasks": [{"gid": "task_1"}],
                "/tasks/task_1/subtasks": [
                    {
                        "gid": "sub_1",
                        "recurrence": {"type": "daily", "data": {"frequency": 1}},
                        "parent": {"gid": "task_1", "name": "Parent Task"},
                    }
                ],
                "/tasks/sub_1/subtasks": [],
            }
        )
        instances = collect_recurring_task_instances(client, "proj_1", "Proj")
        self.assertEqual(len(instances), 1)
        instance = instances[0]
        self.assertEqual(instance.task_type, "subtask")
        self.assertEqual(instance.parent_gid, "task_1")
        self.assertEqual(instance.parent_name, "Parent Task")
        self.assertEqual(instance.task_name, "")
        self.assertEqual(instance.due_on, "")
        self.assertFalse(instance.completed)
        self.assertEqual(instance.permalink_url, "")

    def test_excludes_non_null_recurrence_object_with_null_type(self) -> None:
        # Asana returns a non-null `recurrence` object on every task once requested via
        # opt_fields, even non-recurring ones (e.g. {"type": null, ...}) — a plain
        # truthiness check on the object would incorrectly treat every task as recurring.
        client = DummyClient(
            paginated_payloads={
                "/projects/proj_1/tasks": [
                    {"gid": "task_1", "name": "Not recurring", "recurrence": {"type": None, "data": None}},
                ],
                "/tasks/task_1/subtasks": [],
            }
        )
        instances = collect_recurring_task_instances(client, "proj_1", "Proj")
        self.assertEqual(instances, [])

    def test_no_recurring_tasks_returns_empty_list(self) -> None:
        client = DummyClient(
            paginated_payloads={
                "/projects/proj_1/tasks": [{"gid": "task_1"}],
                "/tasks/task_1/subtasks": [],
            }
        )
        instances = collect_recurring_task_instances(client, "proj_1", "Proj")
        self.assertEqual(instances, [])


def _make_instance(**overrides) -> RecurringTaskInstance:
    defaults = dict(
        project_gid="proj_1",
        project_name="Proj",
        task_gid="task_1",
        task_name="Weekly sync",
        task_type="task",
        parent_gid="",
        parent_name="",
        recurrence_type="weekly",
        recurrence_frequency="1",
        due_on="",
        completed=False,
        permalink_url="",
    )
    defaults.update(overrides)
    return RecurringTaskInstance(**defaults)


class GroupIntoSeriesTest(TestCase):
    def test_groups_by_project_name_type_and_frequency(self) -> None:
        instances = [
            _make_instance(task_gid="task_1", due_on="2026-01-01"),
            _make_instance(task_gid="task_2", due_on="2026-01-08"),
            _make_instance(task_gid="task_3", task_name="Other task"),
        ]
        series = group_into_series(instances)
        self.assertEqual(len(series), 2)
        weekly_sync = next(s for s in series if s.task_name == "Weekly sync")
        self.assertEqual(weekly_sync.occurrence_count, 2)
        self.assertEqual(weekly_sync.first_due_on, "2026-01-01")
        self.assertEqual(weekly_sync.last_due_on, "2026-01-08")

    def test_blank_due_on_instances_are_excluded_from_first_last(self) -> None:
        instances = [
            _make_instance(task_gid="task_1", due_on=""),
            _make_instance(task_gid="task_2", due_on=""),
        ]
        series = group_into_series(instances)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].occurrence_count, 2)
        self.assertEqual(series[0].first_due_on, "")
        self.assertEqual(series[0].last_due_on, "")

    def test_mixed_blank_and_present_due_on(self) -> None:
        instances = [
            _make_instance(task_gid="task_1", due_on=""),
            _make_instance(task_gid="task_2", due_on="2026-03-01"),
            _make_instance(task_gid="task_3", due_on="2026-02-01"),
        ]
        series = group_into_series(instances)
        self.assertEqual(len(series), 1)
        self.assertEqual(series[0].occurrence_count, 3)
        self.assertEqual(series[0].first_due_on, "2026-02-01")
        self.assertEqual(series[0].last_due_on, "2026-03-01")

    def test_different_recurrence_type_creates_separate_series(self) -> None:
        instances = [
            _make_instance(task_gid="task_1", recurrence_type="weekly"),
            _make_instance(task_gid="task_2", recurrence_type="daily"),
        ]
        series = group_into_series(instances)
        self.assertEqual(len(series), 2)

    def test_empty_instances_returns_empty_series(self) -> None:
        self.assertEqual(group_into_series([]), [])


class DbFixtureTestCase(TestCase):
    """Shared setUp for tests needing a real temp SQLite db."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.addCleanup(self.connection.close)

    def seed_project(self, gid: str, name: str) -> None:
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            (gid, name, "2025-10-31T00:00:00+00:00"),
        )
        self.connection.commit()

    def seed_task(
        self,
        gid: str,
        name: str,
        project_gid: str,
        recurrence=None,
        recurrence_checked_at=None,
        parent_gid=None,
        is_subtask=0,
        due_on=None,
        completed=0,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO tasks (
                gid, name, parent_gid, is_subtask, due_on, completed,
                recurrence, recurrence_checked_at, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gid,
                name,
                parent_gid,
                is_subtask,
                due_on,
                completed,
                json.dumps(recurrence) if recurrence else None,
                recurrence_checked_at,
                "2025-10-31T00:00:00+00:00",
            ),
        )
        self.connection.execute(
            "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
            (gid, project_gid, "2025-10-31T00:00:00+00:00"),
        )
        self.connection.commit()


class FetchProjectNameFromDbTest(DbFixtureTestCase):
    def test_returns_name_when_present(self) -> None:
        self.seed_project("proj_1", "Alpha")
        self.assertEqual(fetch_project_name_from_db(self.connection, "proj_1"), "Alpha")

    def test_returns_none_when_missing(self) -> None:
        self.assertIsNone(fetch_project_name_from_db(self.connection, "does_not_exist"))

    def test_returns_none_when_blank(self) -> None:
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("proj_blank", "", "2025-10-31T00:00:00+00:00"),
        )
        self.connection.commit()
        self.assertIsNone(fetch_project_name_from_db(self.connection, "proj_blank"))


class FetchProjectTaskRowsTest(DbFixtureTestCase):
    def test_returns_empty_list_for_unsynced_project(self) -> None:
        self.assertEqual(fetch_project_task_rows(self.connection, "proj_never_synced"), [])

    def test_returns_task_and_subtask_rows_with_parent_name(self) -> None:
        self.seed_project("proj_1", "Alpha")
        self.seed_task("task_1", "Parent task", "proj_1")
        self.seed_task(
            "sub_1",
            "Subtask",
            "proj_1",
            parent_gid="task_1",
            is_subtask=1,
        )
        rows = {row["gid"]: row for row in fetch_project_task_rows(self.connection, "proj_1")}
        self.assertEqual(set(rows), {"task_1", "sub_1"})
        self.assertEqual(rows["sub_1"]["parent_name"], "Parent task")
        self.assertEqual(rows["sub_1"]["is_subtask"], 1)
        self.assertEqual(rows["task_1"]["is_subtask"], 0)


class CollectRecurringTaskInstancesHybridTest(DbFixtureTestCase):
    def test_project_miss_falls_back_to_full_live_walk_and_writes_back(self) -> None:
        client = DummyClient(
            get_payloads={"/projects/proj_1": {"name": "Live Project"}},
            paginated_payloads={
                "/projects/proj_1/tasks": [
                    {
                        "gid": "task_1",
                        "name": "Weekly sync",
                        "recurrence": {"type": "weekly", "data": {"frequency": 1}},
                        "due_on": "2026-01-01",
                    },
                    {"gid": "task_2", "name": "One-off", "recurrence": {"type": None}},
                ],
                "/tasks/task_1/subtasks": [],
                "/tasks/task_2/subtasks": [],
            },
        )

        instances, errors = collect_recurring_task_instances_hybrid(client, self.database, self.connection, "proj_1")

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].task_gid, "task_1")
        self.assertEqual(instances[0].project_name, "Live Project")
        self.assertEqual(errors, [])

        # Both recurring and non-recurring tasks get their recurrence written back.
        row1 = self.connection.execute(
            "SELECT recurrence, recurrence_checked_at FROM tasks WHERE gid = ?", ("task_1",)
        ).fetchone()
        self.assertIsNotNone(row1["recurrence"])
        self.assertIsNotNone(row1["recurrence_checked_at"])
        row2 = self.connection.execute(
            "SELECT recurrence, recurrence_checked_at FROM tasks WHERE gid = ?", ("task_2",)
        ).fetchone()
        self.assertIsNone(row2["recurrence"])
        self.assertIsNotNone(row2["recurrence_checked_at"])

    def test_db_hit_uses_stored_recurrence_without_api_call(self) -> None:
        self.seed_project("proj_1", "Alpha")
        self.seed_task(
            "task_1",
            "Weekly sync",
            "proj_1",
            recurrence={"type": "weekly", "data": {"frequency": 1}},
            recurrence_checked_at="2025-10-31T00:00:00+00:00",
            due_on="2026-01-01",
        )
        client = DummyClient()

        instances, errors = collect_recurring_task_instances_hybrid(client, self.database, self.connection, "proj_1")

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].task_gid, "task_1")
        self.assertEqual(instances[0].project_name, "Alpha")
        self.assertEqual(client.calls, [])
        self.assertEqual(errors, [])

    def test_per_task_miss_fetches_live_detail_and_writes_back(self) -> None:
        self.seed_project("proj_1", "Alpha")
        self.seed_task("task_1", "Never checked", "proj_1")
        client = DummyClient(
            get_payloads={"/tasks/task_1": {"recurrence": {"type": "daily", "data": {"frequency": 1}}}},
        )

        instances, errors = collect_recurring_task_instances_hybrid(client, self.database, self.connection, "proj_1")

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].recurrence_type, "daily")
        self.assertEqual(errors, [])
        row = self.connection.execute(
            "SELECT recurrence, recurrence_checked_at FROM tasks WHERE gid = ?", ("task_1",)
        ).fetchone()
        self.assertIsNotNone(row["recurrence"])
        self.assertIsNotNone(row["recurrence_checked_at"])

    def test_per_task_miss_skips_task_on_api_error_instead_of_raising(self) -> None:
        # Regression: a 403/404 on one task's detail lookup (e.g. permissions, deleted task)
        # must not abort the whole report run for the other tasks in scope.
        self.seed_project("proj_1", "Alpha")
        self.seed_task("task_1", "Forbidden task", "proj_1")
        self.seed_task(
            "task_2",
            "Weekly sync",
            "proj_1",
            recurrence={"type": "weekly", "data": {"frequency": 1}},
            recurrence_checked_at="2025-10-31T00:00:00+00:00",
        )
        client = DummyClient(error_endpoints={"/tasks/task_1"})

        instances, errors = collect_recurring_task_instances_hybrid(client, self.database, self.connection, "proj_1")

        self.assertEqual([instance.task_gid for instance in instances], ["task_2"])
        row = self.connection.execute(
            "SELECT recurrence_checked_at FROM tasks WHERE gid = ?", ("task_1",)
        ).fetchone()
        self.assertIsNone(row["recurrence_checked_at"])
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].project_gid, "proj_1")
        self.assertEqual(errors[0].project_name, "Alpha")
        self.assertEqual(errors[0].task_gid, "task_1")
        self.assertEqual(errors[0].task_name, "Forbidden task")
        self.assertEqual(errors[0].status, "403")
        self.assertIn("Forbidden", errors[0].message)

    def test_multiple_unchecked_tasks_one_failing_reports_error_without_aborting_others(self) -> None:
        # Regression: a single failing per-task lookup among several unchecked tasks must
        # not abort the whole project — the other tasks' instances still get collected,
        # and the failure is surfaced in the errors list (not just the log).
        self.seed_project("proj_1", "Alpha")
        self.seed_task("task_ok_1", "Weekly sync", "proj_1")
        self.seed_task("task_forbidden", "Forbidden task", "proj_1")
        self.seed_task("task_ok_2", "Daily standup", "proj_1")
        client = DummyClient(
            get_payloads={
                "/tasks/task_ok_1": {"recurrence": {"type": "weekly", "data": {"frequency": 1}}},
                "/tasks/task_ok_2": {"recurrence": {"type": "daily", "data": {"frequency": 1}}},
            },
            error_endpoints={"/tasks/task_forbidden"},
        )

        instances, errors = collect_recurring_task_instances_hybrid(client, self.database, self.connection, "proj_1")

        self.assertEqual({i.task_gid for i in instances}, {"task_ok_1", "task_ok_2"})
        self.assertEqual(len(errors), 1)
        error = errors[0]
        self.assertEqual(error.project_gid, "proj_1")
        self.assertEqual(error.project_name, "Alpha")
        self.assertEqual(error.task_gid, "task_forbidden")
        self.assertEqual(error.task_name, "Forbidden task")
        self.assertEqual(error.status, "403")
        self.assertIn("Forbidden", error.message)

    def test_project_name_falls_back_to_live_when_db_name_missing(self) -> None:
        # Project row itself is absent from `projects`, but tasks were synced (edge case).
        self.seed_task("task_1", "Task", "proj_missing_name")
        client = DummyClient(get_payloads={"/projects/proj_missing_name": {"name": "Fetched Live"}})

        instances, errors = collect_recurring_task_instances_hybrid(client, self.database, self.connection, "proj_missing_name")

        self.assertEqual(instances, [])
        self.assertEqual(errors, [])
        self.assertIn(("get", "/projects/proj_missing_name", {"opt_fields": "name"}), client.calls)

    def test_mixed_checked_and_unchecked_rows_only_calls_api_for_unchecked(self) -> None:
        self.seed_project("proj_1", "Alpha")
        self.seed_task(
            "task_checked",
            "Checked",
            "proj_1",
            recurrence={"type": "weekly", "data": {"frequency": 1}},
            recurrence_checked_at="2025-10-31T00:00:00+00:00",
        )
        self.seed_task("task_unchecked", "Unchecked", "proj_1")
        client = DummyClient(get_payloads={"/tasks/task_unchecked": {"recurrence": None}})

        instances, errors = collect_recurring_task_instances_hybrid(client, self.database, self.connection, "proj_1")

        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].task_gid, "task_checked")
        self.assertEqual(errors, [])
        api_calls = [call for call in client.calls if call[0] == "get" and call[1].startswith("/tasks/")]
        self.assertEqual(len(api_calls), 1)
        self.assertEqual(api_calls[0][1], "/tasks/task_unchecked")
        self.assertIn("recurrence", api_calls[0][2]["opt_fields"].split(","))
