from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from src.lib.database import Database
from src.lib.http_client import AsanaHttpError
from scripts.get_incremental_updates import (
    collect_workspace_summaries,
    ensure_memberships,
    fetch_workspace_modified_tasks,
)


class GetIncrementalUpdatesMembershipTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.sync_time = datetime(2025, 3, 5, tzinfo=timezone.utc)

        self.platform_project = {
            "gid": "proj_platform",
            "name": "Platform :: Backlog",
            "team": {"gid": "team_platform", "name": "Platform"},
        }
        self.database.upsert_project(self.connection, self.platform_project, self.sync_time)

        parent_payload = {
            "gid": "task_parent",
            "name": "Parent Task",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "memberships": [
                {
                    "project": {
                        "gid": self.platform_project["gid"],
                        "name": self.platform_project["name"],
                        "team": self.platform_project["team"],
                    },
                    "section": {"gid": "sec_backlog", "name": "Backlog"},
                    "is_primary": True,
                }
            ],
        }
        self.database.upsert_task(self.connection, parent_payload, self.sync_time)
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def test_ensure_memberships_clones_from_parent_in_db(self) -> None:
        payload = {
            "gid": "task_child",
            "name": "Child Task",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "parent": {"gid": "task_parent"},
        }
        membership_cache: dict[str, list[dict]] = {}
        with patch("scripts.get_incremental_updates.fetch_task_detail") as mock_fetch:
            ensure_memberships(self.connection, Mock(), payload, membership_cache)
            mock_fetch.assert_not_called()

        memberships = payload.get("memberships") or []
        self.assertTrue(memberships)
        self.assertEqual(memberships[0]["project"]["gid"], self.platform_project["gid"])
        self.assertEqual(memberships[0]["section"]["name"], "Backlog")

    def test_ensure_memberships_falls_back_to_api_parent(self) -> None:
        payload = {
            "gid": "task_child_missing",
            "name": "Detached Child",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "parent": {"gid": "api_parent"},
        }
        membership_cache: dict[str, list[dict]] = {}
        api_memberships = [
            {
                "project": {
                    "gid": self.platform_project["gid"],
                    "name": self.platform_project["name"],
                },
                "is_primary": True,
            }
        ]
        client = Mock()
        with patch(
            "scripts.get_incremental_updates.fetch_task_detail",
            return_value={"gid": "api_parent", "memberships": api_memberships},
        ) as mock_fetch:
            ensure_memberships(self.connection, client, payload, membership_cache)
            mock_fetch.assert_called_once()
            args, kwargs = mock_fetch.call_args
            self.assertIs(args[0], client)
            self.assertEqual(args[1], "api_parent")

        memberships = payload.get("memberships") or []
        self.assertEqual(memberships, api_memberships)

    def test_fetch_workspace_modified_tasks_adds_project_filter(self) -> None:
        client = _RecordingClient([[{"gid": "task_1"}]])
        list(
            fetch_workspace_modified_tasks(
                client,
                workspace_gid="ws",
                modified_since="2025-11-01",
                page_size=25,
                project_gid="proj-filter",
            )
        )
        self.assertEqual(len(client.calls), 1)
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/workspaces/ws/tasks/search")
        self.assertEqual(params["projects.any"], "proj-filter")

    def test_collect_workspace_summaries_merges_global_and_project_scans(self) -> None:
        responses = {
            None: [{"gid": "global_task"}],
            "proj-alpha": [{"gid": "global_task"}, {"gid": "project_task"}],
            "proj-beta": [{"gid": "beta_task"}],
        }
        client = _MappingClient(responses)
        projects = [
            {"project_gid": "proj-alpha"},
            {"project_gid": "proj-beta"},
        ]
        summaries = collect_workspace_summaries(
            client,
            workspace_gid="ws",
            modified_since="2025-11-01",
            page_size=50,
            projects=projects,
        )
        gids = sorted(summary["gid"] for summary in summaries)
        self.assertEqual(gids, ["beta_task", "global_task", "project_task"])
        self.assertEqual(len(client.calls), 3)
        self.assertNotIn("projects.any", client.calls[0][1])
        self.assertEqual(client.calls[1][1]["projects.any"], "proj-alpha")
        self.assertEqual(client.calls[2][1]["projects.any"], "proj-beta")

    def test_collect_workspace_summaries_skips_project_that_404s(self) -> None:
        responses = {
            None: [{"gid": "global_task"}],
            "proj-alpha": [{"gid": "alpha_task"}],
            "proj-beta": [{"gid": "beta_task"}],
        }
        client = _MappingClient(responses, error_on={"proj-alpha": 404})
        projects = [
            {"project_gid": "proj-alpha", "project_name": "Deleted Project"},
            {"project_gid": "proj-beta"},
        ]
        summaries = collect_workspace_summaries(
            client,
            workspace_gid="ws",
            modified_since="2025-11-01",
            page_size=50,
            projects=projects,
        )
        gids = sorted(summary["gid"] for summary in summaries)
        self.assertEqual(gids, ["beta_task", "global_task"])

    def test_collect_workspace_summaries_propagates_global_scan_error(self) -> None:
        client = _MappingClient({}, error_on={None: 500})
        with self.assertRaises(AsanaHttpError):
            collect_workspace_summaries(
                client,
                workspace_gid="ws",
                modified_since="2025-11-01",
                page_size=50,
                projects=[{"project_gid": "proj-alpha"}],
            )


class _RecordingClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get_paginated(self, endpoint, params=None):
        params = dict(params or {})
        self.calls.append((endpoint, params))
        batch = self.responses.pop(0)
        for item in batch:
            yield item


class _MappingClient:
    def __init__(self, responses, error_on=None):
        self.responses = responses
        self.error_on = error_on or {}
        self.calls = []

    def get_paginated(self, endpoint, params=None):
        params = dict(params or {})
        self.calls.append((endpoint, params))
        key = params.get("projects.any")
        if key in self.error_on:
            raise AsanaHttpError(self.error_on[key], "Not Found")
        for item in self.responses.get(key, []):
            yield item
