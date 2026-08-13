from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone

from scripts.atlassian import search_jira_external_ids as search_mod
from src.lib.database import Database


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def search(self, jql, fields, max_results, next_page_token=None):
        self.calls.append(
            {"jql": jql, "fields": tuple(fields), "max_results": max_results, "next_page_token": next_page_token}
        )
        return self.responses.pop(0) if self.responses else {"issues": []}


class SearchJiraExternalIdsTests(unittest.TestCase):
    def test_build_jql_places_cursor_before_order_clause(self) -> None:
        cursor = search_mod.JiraIssue(
            key="ENG-2",
            created="2024-02-01T12:00:00.000+0000",
            external_id="task_2",
            meta={"keys_at_ts": ["ENG-1", "ENG-2"]},
        )
        result = search_mod.build_jql("project = ENG ORDER BY priority DESC", cursor)
        self.assertIn("project = ENG", result)
        self.assertIn('created > "2024-02-01T12:00:00.000+0000"', result)
        # cursor clause should appear before ORDER BY
        cursor_pos = result.index("created >")
        order_pos = result.lower().index("order by")
        self.assertLess(cursor_pos, order_pos)

    def test_collect_external_ids_pages_and_batches_with_cursor(self) -> None:
        responses = [
            {
                "issues": [
                    {
                        "key": "ENG-1",
                        "fields": {
                            "created": "2024-01-01T00:00:00.000+0000",
                            "customfield_123": "task_1",
                        },
                    }
                ],
                "nextPageToken": None,
            },
            {
                "issues": [
                    {
                        "key": "ENG-2",
                        "fields": {
                            "created": "2024-01-02T00:00:00.000+0000",
                            "customfield_123": "task_2",
                        },
                    }
                ],
                "nextPageToken": None,
            },
        ]
        client = FakeClient(responses)
        issues = search_mod.collect_external_ids(client, "project = ENG", "customfield_123", max_results=1)
        self.assertEqual([issue.key for issue in issues], ["ENG-1", "ENG-2"])
        # first batch page, empty page to end batch, second batch page
        self.assertEqual(len(client.calls), 3)
        self.assertTrue(all(call["max_results"] == 1 for call in client.calls))
        # ensure cursor exclusion clause uses NOT IN instead of issuekey >
        cursor_call = client.calls[1]  # the first call of second batch
        self.assertIn("issuekey NOT IN", cursor_call["jql"])

    def test_upsert_mappings_skips_unknown_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/asana.sqlite"
            database = Database(db_path=db_path)
            with database.connect() as conn:
                database.initialize(conn)
                conn.execute(
                    "INSERT INTO tasks (gid, name, synced_at) VALUES (?, ?, ?)",
                    ("task_1", "Task 1", datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()

            issues = [
                search_mod.JiraIssue(key="ENG-1", created="2024-01-01T00:00:00.000+0000", external_id="task_1"),
                search_mod.JiraIssue(key="ENG-2", created="2024-01-01T01:00:00.000+0000", external_id="missing_task"),
            ]
            summary = search_mod.upsert_mappings(db_path, issues)
            self.assertEqual(summary["written"], 1)
            self.assertEqual(summary["processed"], 2)

    def test_upsert_mappings_records_selected_site(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = f"{temp_dir}/asana.sqlite"
            database = Database(db_path=db_path)
            with database.connect() as conn:
                database.initialize(conn)
                conn.execute(
                    "INSERT INTO tasks (gid, name, synced_at) VALUES (?, ?, ?)",
                    ("task_1", "Task 1", datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
            search_mod.upsert_mappings(
                db_path,
                [search_mod.JiraIssue("ENG-1", "2024-01-01", "task_1")],
                jira_site_key="client_jira",
            )
            with database.connect() as conn:
                row = conn.execute("SELECT jira_site_key FROM task_jira_mappings").fetchone()
            self.assertEqual(row["jira_site_key"], "client_jira")

    def test_resolve_external_field_and_build_mapped_jql(self) -> None:
        fields = [{"id": "customfield_123", "name": "External issue ID"}]
        field_id = search_mod.resolve_external_field_id(fields, "external ISSUE id")
        self.assertEqual(field_id, "customfield_123")
        self.assertEqual(
            search_mod.build_mapped_projects_jql(["ABC", "XYZ"], field_id),
            'project IN ("ABC", "XYZ") AND "customfield_123" is not EMPTY',
        )

    def test_clamp_max_results_respects_cap(self) -> None:
        self.assertEqual(search_mod.clamp_max_results(search_mod.JIRA_MAX_RESULTS_CAP + 5), search_mod.JIRA_MAX_RESULTS_CAP)
        self.assertEqual(search_mod.clamp_max_results(50), 50)

    def test_list_fields_uses_singular_field_endpoint(self) -> None:
        # Regression: Jira Cloud's "Get fields" endpoint is GET /rest/api/3/field
        # (singular). The plural form 404s, which only surfaces on a live call.
        client = search_mod.JiraSearchClient(base_url="https://example.atlassian.net", username="u", token="t")
        captured = {}

        def fake_request(method, path, headers=None, data=None, **kwargs):
            captured["method"] = method
            captured["path"] = path
            return [{"id": "customfield_1", "name": "Asana Migration Decision"}]

        client._request = fake_request  # type: ignore[method-assign]
        fields = client.list_fields()
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["path"], "/rest/api/3/field")
        self.assertEqual(fields[0]["id"], "customfield_1")


if __name__ == "__main__":
    unittest.main()
