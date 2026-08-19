from __future__ import annotations

import json
from unittest import TestCase

from src.lib.jira_client import JiraApiError, JiraClient


class _RecordingClient(JiraClient):
    """JiraClient with a stubbed transport that records calls and replays bodies."""

    def __init__(self, responses):
        super().__init__(base_url="https://example.atlassian.net", username="u", token="t")
        self._responses = responses
        self.calls = []

    def _request(self, method, path, headers=None, data=None, max_retries=5, backoff=1.5):
        self.calls.append({"method": method, "path": path, "data": data})
        body = self._responses.pop(0)
        if isinstance(body, Exception):
            raise body
        return body


class CreateIssueTest(TestCase):
    def test_create_issue_posts_payload_and_parses_response(self) -> None:
        client = _RecordingClient([b'{"id":"100","key":"MIG-1"}'])
        result = client.create_issue({"fields": {"summary": "hi"}})
        self.assertEqual(result["key"], "MIG-1")
        call = client.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertEqual(call["path"], "/rest/api/3/issue")
        self.assertEqual(json.loads(call["data"].decode("utf-8")), {"fields": {"summary": "hi"}})

    def test_invalid_json_raises(self) -> None:
        client = _RecordingClient([b"not json"])
        with self.assertRaises(JiraApiError):
            client.create_issue({"fields": {}})

    def test_get_myself_hits_myself_endpoint(self) -> None:
        client = _RecordingClient([b'{"displayName":"Jane Q","accountId":"abc"}'])
        result = client.get_myself()
        self.assertEqual(result["displayName"], "Jane Q")
        call = client.calls[0]
        self.assertEqual(call["method"], "GET")
        self.assertEqual(call["path"], "/rest/api/3/myself")

    def test_get_project_reads_exact_project_key(self) -> None:
        client = _RecordingClient([b'{"key":"MIG","name":"Migration Space"}'])
        project = client.get_project("MIG")
        self.assertEqual(project["name"], "Migration Space")
        self.assertEqual(client.calls[0]["path"], "/rest/api/3/project/MIG")

    def test_field_and_resolution_catalog_endpoints(self) -> None:
        client = _RecordingClient([
            b'[{"id":"customfield_1","name":"Section"}]',
            b'[{"id":"10000","name":"Done"}]',
        ])
        self.assertEqual(client.get_fields()[0]["name"], "Section")
        self.assertEqual(client.get_resolutions()[0]["name"], "Done")
        self.assertEqual(
            [call["path"] for call in client.calls],
            ["/rest/api/3/field", "/rest/api/3/resolution"],
        )

    def test_create_custom_field_posts_payload(self) -> None:
        client = _RecordingClient([b'{"id":"customfield_10001","name":"Asana Section"}'])
        payload = {"name": "Asana Section", "type": "textfield"}
        result = client.create_custom_field(payload)
        self.assertEqual(result["id"], "customfield_10001")
        self.assertEqual(client.calls[0]["method"], "POST")
        self.assertEqual(client.calls[0]["path"], "/rest/api/3/field")
        self.assertEqual(json.loads(client.calls[0]["data"].decode("utf-8")), payload)

    def test_get_custom_fields_reads_every_admin_catalog_page(self) -> None:
        client = _RecordingClient([
            b'{"startAt":0,"maxResults":1,"total":2,"isLast":false,'
            b'"values":[{"id":"customfield_1","name":"Asana Section"}]}',
            b'{"startAt":1,"maxResults":1,"total":2,"isLast":true,'
            b'"values":[{"id":"customfield_2","name":"Asana Task Link"}]}',
        ])
        fields = client.get_custom_fields()
        self.assertEqual([field["id"] for field in fields], ["customfield_1", "customfield_2"])
        self.assertEqual(
            [call["path"] for call in client.calls],
            [
                "/rest/api/3/field/search?type=custom&orderBy=name&startAt=0&maxResults=100",
                "/rest/api/3/field/search?type=custom&orderBy=name&startAt=1&maxResults=100",
            ],
        )

    def test_custom_field_context_and_option_endpoints(self) -> None:
        client = _RecordingClient([
            b'{"values":[{"id":"10000"}]}',
            b'{"values":[{"id":"1","value":"Active"}]}',
            b'{"options":[{"id":"2","value":"Archived"}]}',
        ])
        self.assertEqual(client.get_custom_field_contexts("customfield_1")[0]["id"], "10000")
        self.assertEqual(client.get_custom_field_options("customfield_1", "10000")[0]["value"], "Active")
        client.create_custom_field_options("customfield_1", "10000", ["Archived"])
        self.assertEqual(
            [call["path"] for call in client.calls],
            [
                "/rest/api/3/field/customfield_1/context",
                "/rest/api/3/field/customfield_1/context/10000/option",
                "/rest/api/3/field/customfield_1/context/10000/option",
            ],
        )
        self.assertEqual(
            json.loads(client.calls[2]["data"].decode("utf-8")),
            {"options": [{"value": "Archived"}]},
        )


class JiraApiErrorTest(TestCase):
    def test_status_and_body_are_accessible(self) -> None:
        body = '{"errors": {"assignee": "User x cannot be assigned issues."}}'
        err = JiraApiError(400, body)
        self.assertEqual(err.status, 400)
        self.assertEqual(err.body, body)
        self.assertIn("Jira API error 400", str(err))


class CreateMetaTest(TestCase):
    def test_issue_types(self) -> None:
        client = _RecordingClient([
            json.dumps({"issueTypes": [{"id": "1", "name": "Epic"}, {"id": "2", "name": "Task"}]}).encode("utf-8")
        ])
        types = client.get_createmeta_issue_types("MIG")
        self.assertEqual([t["name"] for t in types], ["Epic", "Task"])
        self.assertIn("/rest/api/3/issue/createmeta/MIG/issuetypes", client.calls[0]["path"])

    def test_fields_keyed_by_name_single_page(self) -> None:
        body = {
            "startAt": 0,
            "maxResults": 50,
            "total": 2,
            "fields": [
                {"name": "Summary", "fieldId": "summary", "schema": {"type": "string"}},
                {"name": "Team URL", "fieldId": "customfield_10001", "schema": {"type": "string"}},
            ],
        }
        client = _RecordingClient([json.dumps(body).encode("utf-8")])
        fields = client.get_createmeta_fields("MIG", "1")
        self.assertEqual(set(fields), {"Summary", "Team URL"})
        self.assertEqual(fields["Team URL"]["fieldId"], "customfield_10001")
        self.assertEqual(len(client.calls), 1)
