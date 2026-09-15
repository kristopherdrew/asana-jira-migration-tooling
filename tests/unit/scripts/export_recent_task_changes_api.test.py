from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from scripts.export_recent_task_changes_api import (
    deduplicate_tasks,
    compare_tasks,
    fetch_project_changes,
    resolve_cutoff,
    resolve_project_gids,
    resolve_missing_in_api_tasks,
    write_outputs,
)


class RecentTaskChangesApiTest(TestCase):
    def test_resolve_cutoff_uses_utc_calendar_midnight(self) -> None:
        now = datetime(2026, 9, 14, 15, 30, 45, tzinfo=timezone.utc)
        self.assertEqual(resolve_cutoff(14, now), "2026-08-31T00:00:00Z")

    def test_resolve_cutoff_allows_today(self) -> None:
        now = datetime(2026, 9, 14, 1, 0, tzinfo=timezone.utc)
        self.assertEqual(resolve_cutoff(0, now), "2026-09-14T00:00:00Z")

    def test_resolve_project_gids_deduplicates_repeated_values(self) -> None:
        self.assertEqual(resolve_project_gids(["p1,p2", "p2,p3"], None), ["p1", "p2", "p3"])

    def test_fetch_project_changes_uses_workspace_search_filter(self) -> None:
        client = Mock()
        client.get_paginated.return_value = iter([{"gid": "task-1"}])
        result = fetch_project_changes(client, "workspace-1", "project-1", "2026-09-01T00:00:00Z", 25)
        self.assertEqual(result, [{"gid": "task-1"}])
        args, kwargs = client.get_paginated.call_args
        self.assertEqual(args[0], "/workspaces/workspace-1/tasks/search")
        self.assertEqual(kwargs["params"]["projects.any"], "project-1")
        self.assertEqual(kwargs["params"]["modified_at.after"], "2026-09-01T00:00:00Z")
        self.assertEqual(kwargs["params"]["limit"], "25")

    def test_deduplicate_tasks_preserves_multi_project_provenance(self) -> None:
        results = [
            ("p1", [{"gid": "t1", "name": "Task", "modified_at": "2026-09-03T00:00:00Z", "memberships": [{"project": {"gid": "p1", "name": "One"}}, {"project": {"gid": "p2", "name": "Two"}}]}]),
            ("p2", [{"gid": "t1", "name": "Task", "modified_at": "2026-09-03T00:00:00Z", "memberships": [{"project": {"gid": "p1", "name": "One"}}, {"project": {"gid": "p2", "name": "Two"}}]}]),
        ]
        tasks = deduplicate_tasks(results, ["p1", "p2"])
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["_selected_project_gids"], ["p1", "p2"])
        self.assertEqual(tasks[0]["_queried_project_gids"], ["p1", "p2"])

    def test_compare_tasks_returns_only_differences(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE task_projects (task_gid TEXT, project_gid TEXT, section_gid TEXT, section_name TEXT, is_primary INTEGER)")
        connection.execute("CREATE TABLE task_custom_fields (task_gid TEXT, custom_field_gid TEXT, text_value TEXT, number_value REAL, date_value TEXT, display_value TEXT, is_value_set INTEGER, enabled INTEGER, enum_option_gid TEXT)")
        connection.execute("CREATE TABLE task_custom_field_multi_enum (task_gid TEXT, custom_field_gid TEXT, enum_option_gid TEXT, position INTEGER)")
        connection.execute("CREATE TABLE task_followers (task_gid TEXT, user_gid TEXT)")
        connection.execute("CREATE TABLE task_relationships (task_gid TEXT, related_task_gid TEXT, relation_type TEXT)")
        connection.execute("CREATE TABLE task_tags (task_gid TEXT, tag_gid TEXT)")
        connection.execute("INSERT INTO task_projects VALUES ('t1', 'p1', NULL, NULL, 1)")
        db_tasks = {"t1": {"gid": "t1", "name": "Old name", "modified_at": "2026-09-03T00:00:00Z", "synced_at": "2026-09-03T01:00:00Z", "parent_gid": None, "is_subtask": 0}}
        api_tasks = [{"gid": "t1", "name": "New name", "modified_at": "2026-09-04T00:00:00Z", "memberships": [{"project": {"gid": "p1"}, "is_primary": True}]}]
        rows = compare_tasks(api_tasks, db_tasks, connection, ["p1"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["difference_types"], "name")
        connection.close()

    def test_compare_tasks_omits_modified_and_membership_only_changes(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE task_projects (task_gid TEXT, project_gid TEXT, section_gid TEXT, section_name TEXT, is_primary INTEGER)")
        connection.execute("CREATE TABLE task_custom_fields (task_gid TEXT, custom_field_gid TEXT, text_value TEXT, number_value REAL, date_value TEXT, display_value TEXT, is_value_set INTEGER, enabled INTEGER, enum_option_gid TEXT)")
        connection.execute("CREATE TABLE task_custom_field_multi_enum (task_gid TEXT, custom_field_gid TEXT, enum_option_gid TEXT, position INTEGER)")
        connection.execute("CREATE TABLE task_followers (task_gid TEXT, user_gid TEXT)")
        connection.execute("CREATE TABLE task_relationships (task_gid TEXT, related_task_gid TEXT, relation_type TEXT)")
        connection.execute("CREATE TABLE task_tags (task_gid TEXT, tag_gid TEXT)")
        connection.execute("INSERT INTO task_projects VALUES ('t1', 'p1', 'old-section', 'Old section', 1)")
        db_tasks = {"t1": {"gid": "t1", "name": "Task", "modified_at": "2026-09-03T00:00:00Z", "synced_at": "2026-09-03T01:00:00Z", "parent_gid": None, "is_subtask": 0}}
        api_tasks = [{"gid": "t1", "name": "Task", "modified_at": "2026-09-04T00:00:00Z", "memberships": [{"project": {"gid": "p1"}, "section": {"gid": "new-section", "name": "New section"}, "is_primary": True}]}]
        self.assertEqual(compare_tasks(api_tasks, db_tasks, connection, ["p1"]), [])
        connection.close()

    def test_compare_tasks_reports_missing_local_and_api_rows(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE task_projects (task_gid TEXT, project_gid TEXT, section_gid TEXT, section_name TEXT, is_primary INTEGER)")
        db_tasks = {"db-only": {"gid": "db-only", "name": "DB task", "modified_at": "2026-09-03T00:00:00Z", "synced_at": "2026-09-03T01:00:00Z"}}
        rows = compare_tasks([{"gid": "api-only", "name": "API task"}], db_tasks, connection, ["p1"])
        self.assertEqual({row["task_gid"] for row in rows}, {"api-only", "db-only"})
        self.assertEqual({row["difference_types"] for row in rows}, {"missing_in_db", "missing_in_api"})
        connection.close()

    def test_resolve_missing_in_api_tasks_directly_fetches_db_only_tasks(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute("CREATE TABLE task_projects (task_gid TEXT, project_gid TEXT, section_gid TEXT, section_name TEXT, is_primary INTEGER)")
        connection.execute("INSERT INTO task_projects VALUES ('subtask-1', 'project-1', NULL, NULL, 1)")
        client = Mock()
        db_tasks = {"subtask-1": {"gid": "subtask-1", "name": "Subtask"}}
        with patch(
            "scripts.export_recent_task_changes_api.fetch_task_detail",
            return_value={"gid": "subtask-1", "name": "Subtask", "modified_at": "2026-09-03T00:00:00Z"},
        ) as fetch:
            resolved = resolve_missing_in_api_tasks(client, [], db_tasks, connection, ["project-1"], 2)
        fetch.assert_called_once()
        self.assertIs(fetch.call_args.args[0], client)
        self.assertEqual(fetch.call_args.args[1], "subtask-1")
        self.assertEqual(resolved[0]["gid"], "subtask-1")
        self.assertEqual(resolved[0]["_selected_project_gids"], ["project-1"])
        connection.close()

    def test_write_outputs_writes_only_csv(self) -> None:
        with TemporaryDirectory() as temp_dir:
            row = {"task_gid": "t1", "difference_types": "name", "api_name": "New", "db_name": "Old", "api_modified_at": "", "db_modified_at": "", "db_synced_at": "", "selected_project_gids": "p1"}
            csv_path = write_outputs(temp_dir, [row])
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["task_gid"], "t1")
            self.assertEqual(csv_path.suffix, ".csv")
