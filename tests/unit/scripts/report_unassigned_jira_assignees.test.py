from __future__ import annotations

from unittest import TestCase

from scripts.atlassian.report_unassigned_jira_assignees import (
    REPORT_COLUMNS,
    build_report_rows,
    build_workbook_sheets,
    derive_project_keys,
)


class DeriveProjectKeysTest(TestCase):
    def test_distinct_sorted_prefixes(self) -> None:
        keys = derive_project_keys(["ATJM-1", "ATJM-20", "FOO-3", "  ", "no-dash-but-has", "BAR-7"])
        self.assertEqual(keys, ["ATJM", "BAR", "FOO", "no"])

    def test_empty_and_none_are_ignored(self) -> None:
        self.assertEqual(derive_project_keys(["", None, "-5", "X-1"]), ["X"])


class BuildReportRowsTest(TestCase):
    def setUp(self) -> None:
        self.mappings = {
            "ATJM-1": ("project", "p1"),
            "ATJM-2": ("team", "t1"),
            "ATJM-3": ("project", "p_missing"),
        }
        self.revised = {
            "p1": {
                "Summary": "Proj One",
                "Owner": "Alice",
                "Owner Email": "alice@x.com",
                "Owner Atlassian ID": "atl-alice",
                "Owner Atlassian Status": "active",
                "Owner Source": "project_member",
            },
            "t1": {
                "Summary": "Team One",
                "Owner": "",
                "Owner Email": "",
                "Owner Atlassian ID": "",
                "Owner Atlassian Status": "",
                "Owner Source": "",
            },
        }
        self.issues = [
            {"key": "ATJM-1", "issue_type": "Task", "summary": "Task one"},
            {"key": "ATJM-2", "issue_type": "Epic", "summary": "Epic one"},
            {"key": "ATJM-3", "issue_type": "Task", "summary": "No revised row"},
            {"key": "ATJM-99", "issue_type": "Task", "summary": "Manually created"},
        ]

    def test_mapped_issue_with_active_member_would_get_assignee(self) -> None:
        rows, skipped = build_report_rows(self.issues, self.mappings, self.revised)
        by_key = {r["jira_key"]: r for r in rows}
        recovered = by_key["ATJM-1"]
        self.assertEqual(recovered["would_get_assignee"], "Yes")
        self.assertEqual(recovered["revised_owner_source"], "project_member")
        self.assertEqual(recovered["revised_owner_name"], "Alice")
        self.assertEqual(recovered["revised_owner_atlassian_id"], "atl-alice")
        self.assertEqual(recovered["asana_kind"], "project")
        self.assertEqual(recovered["asana_name"], "Proj One")

    def test_mapped_issue_without_active_candidate_stays_unassigned(self) -> None:
        rows, _ = build_report_rows(self.issues, self.mappings, self.revised)
        by_key = {r["jira_key"]: r for r in rows}
        self.assertEqual(by_key["ATJM-2"]["would_get_assignee"], "No")
        self.assertEqual(by_key["ATJM-2"]["revised_owner_source"], "none")

    def test_mapped_issue_missing_revised_row_is_none(self) -> None:
        rows, _ = build_report_rows(self.issues, self.mappings, self.revised)
        by_key = {r["jira_key"]: r for r in rows}
        self.assertEqual(by_key["ATJM-3"]["would_get_assignee"], "No")
        self.assertEqual(by_key["ATJM-3"]["asana_name"], "")

    def test_unmapped_issue_is_skipped_and_counted(self) -> None:
        rows, skipped = build_report_rows(self.issues, self.mappings, self.revised)
        self.assertEqual(skipped, 1)
        self.assertNotIn("ATJM-99", {r["jira_key"] for r in rows})

    def test_recovered_rows_sort_first(self) -> None:
        rows, _ = build_report_rows(self.issues, self.mappings, self.revised)
        self.assertEqual(rows[0]["jira_key"], "ATJM-1")  # the only "Yes"

    def test_workbook_sheets_have_expected_titles_and_header(self) -> None:
        rows, skipped = build_report_rows(self.issues, self.mappings, self.revised)
        sheets = build_workbook_sheets(
            rows,
            db_path="/tmp/asana.sqlite",
            jira_base_url="https://example.atlassian.net",
            project_keys=["ATJM"],
            skipped_unmapped=skipped,
            generated_at="2026-07-16T00:00:00Z",
        )
        titles = [title for title, _ in sheets]
        self.assertEqual(titles, ["Overview", "Unassigned Issues", "Summary"])
        unassigned_rows = dict(sheets)["Unassigned Issues"]
        self.assertEqual(unassigned_rows[0], list(REPORT_COLUMNS))
