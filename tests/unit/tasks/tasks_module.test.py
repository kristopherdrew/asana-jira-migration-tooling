from unittest import TestCase

from src.tasks.tasks import (
    TASK_DETAIL_FIELDS,
    TASK_LIST_FIELDS,
    collect_user_gids_from_task,
    fetch_project_tasks,
    fetch_subtasks,
    fetch_task_detail,
)


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


class TaskModuleTest(TestCase):
    def test_fetch_project_tasks_uses_project_endpoint(self) -> None:
        client = DummyClient(payload=[{"gid": "task_1"}])
        tasks = list(fetch_project_tasks(client, "proj_1"))
        self.assertEqual(tasks[0]["gid"], "task_1")
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/projects/proj_1/tasks")
        for field in TASK_LIST_FIELDS:
            self.assertIn(field, params["opt_fields"].split(","))

    def test_fetch_task_detail_uses_detail_endpoint(self) -> None:
        client = DummyClient(payload={"gid": "task_detail"})
        result = fetch_task_detail(client, "task_detail")
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/tasks/task_detail")
        for field in TASK_DETAIL_FIELDS:
            self.assertIn(field, params["opt_fields"].split(","))
        self.assertEqual(result["gid"], "task_detail")

    def test_fetch_subtasks_uses_subtask_endpoint(self) -> None:
        client = DummyClient(payload=[])
        list(fetch_subtasks(client, "task_parent"))
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/tasks/task_parent/subtasks")
        self.assertIn("opt_fields", params)

    def test_collect_user_gids_from_task_returns_expected_users(self) -> None:
        task = {
            "assignee": {"gid": "user_assignee"},
            "created_by": {"gid": "user_creator"},
            "completed_by": {"gid": "user_completer"},
            "followers": [{"gid": "user_follower"}],
            "custom_fields": [
                {"people_value": [{"gid": "user_cf1"}, {"gid": "user_cf2"}]},
                {"people_value": {"gid": "user_cf3"}},
            ],
        }
        gids = collect_user_gids_from_task(task)
        self.assertEqual(
            gids,
            {
                "user_assignee",
                "user_creator",
                "user_completer",
                "user_follower",
                "user_cf1",
                "user_cf2",
                "user_cf3",
            },
        )
