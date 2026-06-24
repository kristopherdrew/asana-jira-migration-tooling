from unittest import TestCase

from src.lib.http_client import AsanaHttpError
from src.tasks.users import (
    USER_FIELDS_REQUIRED,
    fetch_user_task_list,
    fetch_workspace_users,
)


class DummyClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_paginated(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        for item in self.payload:
            yield item


class GetClient:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error
        self.calls = []

    def get(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        if self.error is not None:
            raise self.error
        return self.payload


class FetchWorkspaceUsersTest(TestCase):
    def test_uses_workspace_endpoint_with_required_fields(self) -> None:
        client = DummyClient(payload=[{"gid": "user"}])

        users = list(fetch_workspace_users(client, "123"))

        self.assertEqual(users[0]["gid"], "user")
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/workspaces/123/users")
        self.assertEqual(params["limit"], "100")
        for field in USER_FIELDS_REQUIRED:
            self.assertIn(field, params["opt_fields"])

    def test_allows_custom_field_override(self) -> None:
        client = DummyClient(payload=[])
        custom_fields = ["gid", "email"]

        list(fetch_workspace_users(client, "456", opt_fields=custom_fields, page_size=25))

        _, params = client.calls[0]
        self.assertEqual(params["opt_fields"], "gid,email")
        self.assertEqual(params["limit"], "25")


class FetchUserTaskListTest(TestCase):
    def test_returns_gid_from_user_task_list_endpoint(self) -> None:
        client = GetClient(payload={"data": {"gid": "utl_1"}})

        gid = fetch_user_task_list(client, "user_1", "ws_1")

        self.assertEqual(gid, "utl_1")
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/users/user_1/user_task_list")
        self.assertEqual(params["workspace"], "ws_1")
        self.assertEqual(params["opt_fields"], "gid")

    def test_returns_none_when_inaccessible(self) -> None:
        client = GetClient(error=AsanaHttpError(403, "Forbidden"))

        gid = fetch_user_task_list(client, "user_2", "ws_1")

        self.assertIsNone(gid)

    def test_returns_none_when_payload_missing_gid(self) -> None:
        client = GetClient(payload={"data": {}})

        self.assertIsNone(fetch_user_task_list(client, "user_3", "ws_1"))
