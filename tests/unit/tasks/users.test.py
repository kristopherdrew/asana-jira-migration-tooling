from unittest import TestCase

from src.tasks.users import USER_FIELDS_REQUIRED, fetch_workspace_users


class DummyClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_paginated(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        for item in self.payload:
            yield item


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
