from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock

from scripts.export_recent_task_changes_api import (
    deduplicate_tasks,
    fetch_project_changes,
    resolve_cutoff,
    resolve_project_gids,
    write_outputs,
)


class RecentTaskChangesApiTest(TestCase):
    def test_resolve_cutoff_uses_exact_utc_window(self) -> None:
        now = datetime(2026, 9, 14, 15, 30, 45, tzinfo=timezone.utc)
        self.assertEqual(resolve_cutoff(None, 14, now), "2026-08-31T15:30:45Z")

    def test_resolve_cutoff_normalizes_explicit_timezone(self) -> None:
        self.assertEqual(resolve_cutoff("2026-09-01T08:00:00-04:00", 14), "2026-09-01T12:00:00Z")

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
        self.assertEqual(kwargs["params"]["modified_on.after"], "2026-09-01T00:00:00Z")
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

    def test_write_outputs_writes_csv_and_raw_json(self) -> None:
        with TemporaryDirectory() as temp_dir:
            task = {"gid": "t1", "name": "Task", "modified_at": "2026-09-03T00:00:00Z", "parent": {"gid": "p0", "name": "Parent"}, "_selected_project_gids": ["p1"], "_selected_project_names": ["One"], "_queried_project_gids": ["p1"]}
            csv_path, json_path = write_outputs(temp_dir, "2026-09-01T00:00:00Z", ["p1"], [task], [])
            with csv_path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["task_gid"], "t1")
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["cutoff"], "2026-09-01T00:00:00Z")
            self.assertEqual(payload["tasks"][0]["gid"], "t1")
            self.assertNotIn("_selected_project_gids", payload["tasks"][0])
