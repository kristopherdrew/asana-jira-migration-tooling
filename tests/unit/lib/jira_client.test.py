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
