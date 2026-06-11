from unittest import TestCase

from src.tasks.custom_fields import (
    CUSTOM_FIELD_FIELDS_REQUIRED,
    fetch_custom_field,
    fetch_workspace_custom_fields,
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


class FetchWorkspaceCustomFieldsTest(TestCase):
    def test_uses_custom_field_endpoint_with_required_fields(self) -> None:
        client = DummyClient(payload=[{"gid": "cf"}])

        fields = list(fetch_workspace_custom_fields(client, "789"))

        self.assertEqual(fields[0]["gid"], "cf")
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/workspaces/789/custom_fields")
        self.assertEqual(params["limit"], "100")
        for field in CUSTOM_FIELD_FIELDS_REQUIRED:
            self.assertIn(field, params["opt_fields"])

    def test_allows_opt_field_override(self) -> None:
        client = DummyClient(payload=[])
        overrides = ["gid", "name"]

        list(fetch_workspace_custom_fields(client, "111", opt_fields=overrides, page_size=25))

        _, params = client.calls[0]
        self.assertEqual(params["opt_fields"], "gid,name")
        self.assertEqual(params["limit"], "25")

    def test_fetch_custom_field_uses_single_endpoint(self) -> None:
        client = DummyClient(payload={"gid": "cf_123"})

        result = fetch_custom_field(client, "cf_123")

        endpoint, params = client.calls[-1]
        self.assertEqual(endpoint, "/custom_fields/cf_123")
        self.assertIn("opt_fields", params)
        self.assertEqual(result["gid"], "cf_123")
