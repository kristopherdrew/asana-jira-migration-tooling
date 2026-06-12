from unittest import TestCase

from src.tasks.projects import fetch_project_task_counts, fetch_workspace_projects


class DummyClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_paginated(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        for item in self.payload:
            yield item

    def get(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        return {"data": self.payload}


class FetchWorkspaceProjectsTest(TestCase):
    def test_uses_workspace_endpoint_with_default_fields(self) -> None:
        client = DummyClient(payload=[{"gid": "project"}])

        projects = list(fetch_workspace_projects(client, "123"))

        self.assertEqual(projects[0]["gid"], "project")
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/workspaces/123/projects")
        self.assertEqual(params["limit"], "100")
        self.assertIn("opt_fields", params)


class FetchProjectTaskCountsTest(TestCase):
    def test_uses_project_task_counts_endpoint(self) -> None:
        client = DummyClient(
            payload={
                "num_tasks": 10,
                "num_incomplete_tasks": 4,
                "num_completed_tasks": 6,
            }
        )

        counts = fetch_project_task_counts(client, "proj_1")

        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/projects/proj_1/task_counts")
        self.assertEqual(params["opt_fields"], "num_tasks,num_incomplete_tasks,num_completed_tasks")
        self.assertEqual(counts["num_tasks"], 10)
        self.assertEqual(counts["num_incomplete_tasks"], 4)
        self.assertEqual(counts["num_completed_tasks"], 6)
        self.assertIn("task_counts_captured_at", counts)
