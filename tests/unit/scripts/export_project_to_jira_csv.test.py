from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
from contextlib import redirect_stdout

from src.lib.database import Database
from scripts.export_project_to_jira_csv import (
    assign_unmapped_jira_project,
    compute_incremental_refresh_window,
    export_project,
    format_custom_field_names_line,
    main,
    normalize_jira_base_url,
    parse_project_list,
    prompt_and_run_incremental_refresh,
    print_project_stats,
)


class ExportProjectToJiraCsvTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.jira_base_url = "https://jira.example.atlassian.net"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        self._seed_custom_fields()

        # Seed project
        self.connection.execute(
            """
            INSERT INTO projects (
                gid, name, num_tasks, num_incomplete_tasks, num_completed_tasks,
                task_counts_captured_at, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "proj_1",
                "Example Project",
                10,
                4,
                6,
                "2025-01-01T00:00:00Z",
                self.sync_time.isoformat(),
            ),
        )
        self.connection.execute(
            """
            INSERT INTO projects (
                gid, name, num_tasks, num_incomplete_tasks, num_completed_tasks,
                task_counts_captured_at, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "proj_2",
                "Secondary Project",
                2,
                1,
                1,
                "2025-01-01T00:00:00Z",
                self.sync_time.isoformat(),
            ),
        )

        # Seed users
        self.database.upsert_user(
            self.connection,
            {
                "gid": "user_1",
                "name": "Alice Reviewer",
                "resource_type": "user",
                "email": "alice@example.com",
                "is_active": True,
                "workspaces": [],
            },
            self.sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "user_2",
                "name": "Bob Approver",
                "resource_type": "user",
                "email": "bob@example.com",
                "is_active": True,
                "workspaces": [],
            },
            self.sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": "user_inactive",
                "name": "Inactive Assignee",
                "resource_type": "user",
                "email": "inactive@example.com",
                "is_active": True,
                "workspaces": [],
            },
            self.sync_time,
        )

        # Seed tasks
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_1",
                "name": "First Task",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "workspace": {"gid": "ws_1", "name": "Demo Workspace"},
                "notes": "Task notes",
                "assignee": {"gid": "user_1", "name": "Alice Reviewer"},
                "created_by": {"gid": "user_1"},
                "followers": [
                    {"gid": "user_1", "name": "Alice Reviewer"},
                    {"gid": "user_2", "name": "Bob Approver"},
                ],
                "created_at": "2025-02-25T10:30:00Z",
                "modified_at": "2025-03-04T09:05:00Z",
                "completed_at": "2025-03-04T09:05:00Z",
                "start_on": "2025-03-01",
                "start_at": "2025-03-01T09:00:00Z",
                "due_on": "2025-03-10",
                "actual_time_minutes": 480,
                "custom_fields": [
                    {
                        "gid": "cf_text",
                        "type": "text",
                        "text_value": "Enterprise",
                        "display_value": "Enterprise",
                        "is_value_set": True,
                    },
                    {
                        "gid": "cf_multi",
                        "type": "multi_enum",
                        "multi_enum_values": [
                            {"gid": "opt_na", "name": "North America", "enabled": True},
                            {"gid": "opt_eu", "name": "EMEA", "enabled": True},
                        ],
                        "display_value": "North America, EMEA",
                        "is_value_set": True,
                    },
                ],
                "memberships": [
                    {
                        "project": {"gid": "proj_1", "name": "Example Project"},
                        "section": {"gid": "sec_1", "name": "Backlog"},
                    },
                    {
                        "project": {"gid": "proj_2", "name": "Secondary Project"},
                    },
                ],
                "tags": [
                    {"gid": "tag_1", "name": "Needs QA"},
                    {"gid": "tag_2", "name": "Customer Impact"},
                ],
            },
            self.sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_2",
                "name": "Second Task",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "workspace": {"gid": "ws_1", "name": "Demo Workspace"},
                "assignee": {"gid": "user_2", "name": "Bob Approver"},
                "created_by": {"gid": "user_2"},
                "created_at": "2025-02-26T12:45:00Z",
                "modified_at": "2025-03-05T16:00:00Z",
                "start_on": "2025-03-02",
                "start_at": "2025-03-02T13:30:00Z",
                "due_on": "2025-03-12",
                "actual_time_minutes": 120,
                "custom_fields": [
                    {
                        "gid": "cf_text",
                        "type": "text",
                        "text_value": "Starter",
                        "display_value": "Starter",
                        "is_value_set": True,
                    },
                    {
                        "gid": "cf_multi",
                        "type": "multi_enum",
                        "multi_enum_values": [
                            {"gid": "opt_na", "name": "North America", "enabled": True},
                        ],
                        "display_value": "North America",
                        "is_value_set": True,
                    },
                ],
                "memberships": [
                    {
                        "project": {"gid": "proj_1", "name": "Example Project"},
                        "section": {"gid": "sec_2", "name": "In Progress"},
                    }
                ],
                "tags": [
                    {"gid": "tag_2", "name": "Customer Impact"},
                ],
            },
            self.sync_time,
        )

        # Seed comments
        self.database.replace_task_comments(
            self.connection,
            "task_1",
            [
                {
                    "gid": "cmt_1",
                    "created_at": datetime(2025, 3, 4, 9, 5, tzinfo=timezone.utc).isoformat(),
                    "created_by": {"gid": "user_1"},
                    "text": "First comment",
                },
                {
                    "gid": "cmt_2",
                    "created_at": datetime(2025, 3, 4, 11, 45, tzinfo=timezone.utc).isoformat(),
                    "created_by": {"gid": "user_2"},
                    "text": "Second line\nwith newline",
                },
            ],
            self.sync_time,
        )
        self.database.replace_task_comments(
            self.connection,
            "task_2",
            [
                {
                    "gid": "cmt_3",
                    "created_at": datetime(2025, 3, 5, 16, 0, tzinfo=timezone.utc).isoformat(),
                    "created_by": {"gid": "user_1"},
                    "text": "Only comment",
                }
            ],
            self.sync_time,
        )

        self.connection.commit()
        self.database.replace_user_atlassian_accounts(
            self.connection,
            [
                {
                    "gid": "user_1",
                    "atlassianAccountId": "atl-account-alice",
                    "atlassianEmail": "alice.atl@example.com",
                    "atlassianStatus": "active",
                },
                {
                    "gid": "user_2",
                    "atlassianAccountId": "atl-account-bob",
                    "atlassianEmail": "bob.atl@example.com",
                    "atlassianStatus": "active",
                },
                {
                    "gid": "user_inactive",
                    "atlassianAccountId": "atl-account-inactive",
                    "atlassianEmail": "inactive.atl@example.com",
                    "atlassianStatus": "deactivated",
                },
            ],
            self.sync_time,
        )
        self.connection.commit()

    def tearDown(self) -> None:
        self.connection.close()

    def test_export_writes_comment_columns_with_atlassian_ids(self) -> None:
        output_dir = self.workspace / "exports"
        csv_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="project_proj_1_tasks.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
        )

        self.assertTrue(csv_path.exists())
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))

        header = rows[0]
        self.assertIn("Comment", header)
        section_columns = [index for index, name in enumerate(header) if name.startswith("Section")]
        self.assertEqual(len(section_columns), 1)
        comment_columns = [index for index, name in enumerate(header) if name == "Comment"]
        self.assertEqual(len(comment_columns), 2)
        asana_id_index = header.index("External issue ID")
        self.assertNotIn("completed", header)
        assignee_atl_index = header.index("assignee_atlassian_id")
        creator_index = header.index("creator")
        creator_email_index = header.index("creator_email")
        creator_atl_index = header.index("creator_atlassian_id")
        for excluded_column in (
            "assignee_atlassian_email",
            "creator_atlassian_email",
            "start_at",
        ):
            self.assertNotIn(excluded_column, header)
        current_project_index = header.index("current_project")
        project_link_index = header.index("Asana Project Link")
        task_link_index = header.index("Asana Task Link")
        for removed_column in (
            "project_num_tasks",
            "project_num_incomplete_tasks",
            "project_num_completed_tasks",
            "project_task_counts_captured_at",
            "project_last_task_created_at",
            "project_last_task_resolved_at",
            "project_last_task_updated_at",
        ):
            self.assertNotIn(removed_column, header)
        # modified_section is immediately followed by the raw Section column(s).
        self.assertEqual(header.index("modified_section") + 1, min(section_columns))
        # notes sits immediately before the comment column(s).
        self.assertEqual(header.index("notes") + 1, min(comment_columns))
        projects_index = header.index("projects")
        start_on_index = header.index("start_on")
        created_at_index = header.index("created_at")
        due_date_index = header.index("due_date")
        completed_at_index = header.index("completed_at")
        actual_time_index = header.index("actual_time_minutes")
        tags_columns = [index for index, name in enumerate(header) if name.startswith("Tags")]
        self.assertEqual(len(tags_columns), 2)

        gid_idx = header.index("External issue ID")
        task_rows = {row[gid_idx]: row for row in rows[1:]}
        first_task = task_rows["task_1"]
        second_task = task_rows["task_2"]

        first_comment = first_task[comment_columns[0]]
        second_comment = first_task[comment_columns[1]]
        self.assertEqual(first_comment, "04/Mar/25 9:05 AM;atl-account-alice; First comment")
        self.assertEqual(
            second_comment,
            "04/Mar/25 11:45 AM;atl-account-bob; Second line\nwith newline",
        )

        self.assertEqual(first_task[asana_id_index], "task_1")
        self.assertEqual(
            first_task[project_link_index],
            "https://app.asana.com/1/ws_1/project/proj_1",
        )
        self.assertEqual(
            first_task[task_link_index],
            "https://app.asana.com/1/ws_1/task/task_1",
        )
        self.assertEqual(first_task[section_columns[0]], "Backlog")
        self.assertEqual(first_task[assignee_atl_index], "atl-account-alice")
        self.assertEqual(first_task[creator_index], "Alice Reviewer")
        self.assertEqual(first_task[creator_email_index], "alice@example.com")
        self.assertEqual(first_task[creator_atl_index], "atl-account-alice")
        self.assertEqual(first_task[current_project_index], "Example Project (proj_1)")
        self.assertEqual(
            first_task[projects_index],
            "Example Project (proj_1); Secondary Project (proj_2)",
        )
        self.assertEqual(first_task[start_on_index], "01/Mar/25 12:00 am")
        self.assertEqual(first_task[created_at_index], "25/Feb/25 10:30 am")
        self.assertEqual(first_task[due_date_index], "10/Mar/25 12:00 am")
        self.assertEqual(first_task[completed_at_index], "04/Mar/25 9:05 am")
        self.assertEqual(first_task[actual_time_index], "480")
        self.assertEqual(first_task[tags_columns[0]], "Customer_Impact")
        self.assertEqual(first_task[tags_columns[1]], "Needs_QA")

        second_task_comment = second_task[comment_columns[0]]
        self.assertEqual(second_task_comment, "05/Mar/25 4:00 PM;atl-account-alice; Only comment")
        self.assertEqual(second_task[comment_columns[1]], "")
        self.assertEqual(second_task[section_columns[0]], "In Progress")
        self.assertEqual(second_task[assignee_atl_index], "atl-account-bob")
        self.assertEqual(second_task[creator_index], "Bob Approver")
        self.assertEqual(second_task[creator_email_index], "bob@example.com")
        self.assertEqual(second_task[creator_atl_index], "atl-account-bob")
        self.assertEqual(second_task[asana_id_index], "task_2")
        self.assertEqual(second_task[current_project_index], "Example Project (proj_1)")
        self.assertEqual(second_task[projects_index], "Example Project (proj_1)")
        self.assertEqual(second_task[start_on_index], "02/Mar/25 12:00 am")
        self.assertEqual(second_task[created_at_index], "26/Feb/25 12:45 pm")
        self.assertEqual(second_task[due_date_index], "12/Mar/25 12:00 am")
        self.assertEqual(second_task[completed_at_index], "")
        self.assertEqual(second_task[actual_time_index], "120")
        self.assertEqual(second_task[tags_columns[0]], "Customer_Impact")
        self.assertEqual(second_task[tags_columns[1]], "")

        summary_path = csv_path.with_name(f"{csv_path.stem}_field_counts.csv")
        self.assertTrue(summary_path.exists())
        with summary_path.open("r", newline="", encoding="utf-8") as handle:
            summary_rows = list(csv.reader(handle))
        self.assertEqual(
            summary_rows[0],
            ["custom_field_name", "custom_field_gid", "values_found", "included_in_export"],
        )
        self.assertEqual(summary_rows[1], ["Customer Tier", "cf_text", "2", "Yes"])
        self.assertEqual(summary_rows[2], ["Region", "cf_multi", "2", "Yes"])

    def test_add_section_epics_reparents_to_section_epic(self) -> None:
        output_dir = self.workspace / "exports"
        csv_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="section_epics.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
            add_section_epics=True,
        )

        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))

        header = rows[0]
        gid_idx = header.index("External issue ID")
        jira_parent_idx = header.index("jira_parent_gid")
        section_idx = next(index for index, name in enumerate(header) if name.startswith("Section"))
        rows_by_gid = {row[gid_idx]: row for row in rows[1:]}

        backlog_epic_gid = self._section_epic_gid("Backlog")
        in_progress_epic_gid = self._section_epic_gid("In Progress")

        self.assertIn(backlog_epic_gid, rows_by_gid)
        self.assertIn(in_progress_epic_gid, rows_by_gid)
        self.assertEqual(rows_by_gid[backlog_epic_gid][section_idx], "Backlog")
        self.assertEqual(rows_by_gid[in_progress_epic_gid][section_idx], "In Progress")

        self.assertEqual(rows_by_gid["task_1"][jira_parent_idx], backlog_epic_gid)
        self.assertEqual(rows_by_gid["task_2"][jira_parent_idx], in_progress_epic_gid)

    def test_html_hyperlinks_convert_to_jira_format(self) -> None:
        html_notes = (
            "<body>See <a href=\"https://docs.honeycomb.io/troubleshoot/product-lifecycle/"
            "experimental-features/metrics20/\">the new metrics beta</a> doc.</body>"
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_html_link",
                "name": "Task With HTML Link",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "notes": "See the new metrics beta doc.",
                "html_notes": html_notes,
                "assignee": {"gid": "user_1", "name": "Alice Reviewer"},
                "created_by": {"gid": "user_1"},
                "created_at": "2025-03-06T18:30:00Z",
                "memberships": [
                    {
                        "project": {"gid": "proj_1", "name": "Example Project"},
                    }
                ],
            },
            self.sync_time,
        )
        self.database.replace_task_comments(
            self.connection,
            "task_html_link",
            [
                {
                    "gid": "cmt_html_link",
                    "created_at": datetime(2025, 3, 7, 10, 15, tzinfo=timezone.utc).isoformat(),
                    "created_by": {"gid": "user_1"},
                    "text": "Reminder about metrics doc",
                    "html_text": "<body>Reminder <a href=\"https://example.com/spec\">spec draft</a></body>",
                }
            ],
            self.sync_time,
        )
        self.connection.commit()

        output_dir = self.workspace / "exports"
        csv_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="html_links.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
        )

        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        header = rows[0]
        notes_idx = header.index("notes")
        comment_columns = [index for index, name in enumerate(header) if name == "Comment"]
        gid_idx = header.index("External issue ID")
        task_rows = {row[gid_idx]: row for row in rows[1:]}

        self.assertIn("task_html_link", task_rows)
        html_row = task_rows["task_html_link"]
        expected_notes = (
            "See [the new metrics beta|https://docs.honeycomb.io/troubleshoot/product-lifecycle/"
            "experimental-features/metrics20/] doc."
        )
        self.assertEqual(html_row[notes_idx], expected_notes)
        expected_comment = "07/Mar/25 10:15 AM;atl-account-alice; Reminder [spec draft|https://example.com/spec]"
        self.assertEqual(html_row[comment_columns[0]], expected_comment)

    def test_html_anchor_prefers_label_over_plain_url(self) -> None:
        plain_notes = (
            "Let's research whether we want to enroll in (and whether we can still enroll in) "
            "https://docs.honeycomb.io/troubleshoot/product-lifecycle/experimental-features/metrics20/ "
            "—this would get us some improved functionality and they previously told us that we wouldn’t have to pay for those events. "
            "However, some https://dropbox.atlassian.net/wiki/spaces/PostSEV/pages/2666825882/SEV2+Sweltering+Donkey+Retroactive+Lock+contention+from+email+job+caused+major+DocSend+outage."
        )
        html_notes = (
            "<body>AC: Determine whether we should enroll in Metrics 2.0 (and if so, do it)\n\n"
            "Let's research whether we want to enroll in (and whether we can still enroll in) "
            "<a href=\"https://docs.honeycomb.io/troubleshoot/product-lifecycle/experimental-features/metrics20/\">the new metrics beta</a> "
            "—this would get us some improved functionality and they previously told us that we wouldn’t have to pay for those events. "
            "However, some <a href=\"https://dropbox.atlassian.net/wiki/spaces/PostSEV/pages/2666825882/SEV2+Sweltering+Donkey+Retroactive+Lock+contention+from+email+job+caused+major+DocSend+outage\">things are not supported</a>." \
            "</body>"
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_html_anchor",
                "name": "Task With Anchor Label",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "notes": plain_notes,
                "html_notes": html_notes,
                "assignee": {"gid": "user_1", "name": "Alice Reviewer"},
                "created_by": {"gid": "user_1"},
                "created_at": "2025-03-06T18:30:00Z",
                "memberships": [
                    {"project": {"gid": "proj_1", "name": "Example Project"}},
                ],
            },
            self.sync_time,
        )
        self.connection.commit()

        output_dir = self.workspace / "exports"
        csv_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="html_anchor_label.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
        )

        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        header = rows[0]
        notes_idx = header.index("notes")
        gid_idx = header.index("External issue ID")
        task_rows = {row[gid_idx]: row for row in rows[1:]}

        note_value = task_rows["task_html_anchor"][notes_idx]
        self.assertIn(
            "[the new metrics beta|https://docs.honeycomb.io/troubleshoot/product-lifecycle/experimental-features/metrics20/]",
            note_value,
        )
        self.assertIn(
            "[things are not supported|https://dropbox.atlassian.net/wiki/spaces/PostSEV/pages/2666825882/SEV2+Sweltering+Donkey+Retroactive+Lock+contention+from+email+job+caused+major+DocSend+outage]",
            note_value,
        )

    def test_export_id_skips_already_recorded_tasks(self) -> None:
        output_dir = self.workspace / "exports"
        first_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="batch_one.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
            export_id="batch-one",
        )
        self.assertTrue(first_path.exists())

        second_path = export_project(
            str(self.db_path),
            "proj_2",
            output_dir,
            filename="batch_one_again.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
            export_id="batch-one",
        )
        self.assertIsNone(second_path)

        rows = list(
            self.connection.execute(
                "SELECT task_gid, project_gid FROM exported_tasks WHERE export_id = ? ORDER BY task_gid",
                ("batch-one",),
            )
        )
        self.assertEqual([row["task_gid"] for row in rows], ["task_1", "task_2"])
        self.assertEqual({row["project_gid"] for row in rows}, {"proj_1"})

    def test_fallback_atlassian_id_used_when_mapping_missing(self) -> None:
        missing_gid = "user_missing"
        fallback_id = "atl-fallback"
        self.database.upsert_user(
            self.connection,
            {
                "gid": missing_gid,
                "name": "Missing Mapper",
                "resource_type": "user",
                "email": "missing@example.com",
                "is_active": True,
                "workspaces": [],
            },
            self.sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_fallback",
                "name": "Needs fallback",
                "resource_type": "task",
                "assignee": {"gid": missing_gid, "name": "Missing Mapper"},
                "created_by": {"gid": missing_gid},
                "created_at": "2025-03-07T12:00:00Z",
                "memberships": [{"project": {"gid": "proj_1", "name": "Example Project"}}],
            },
            self.sync_time,
        )
        self.database.replace_task_comments(
            self.connection,
            "task_fallback",
            [
                {
                    "gid": "cmt_fallback",
                    "created_at": datetime(2025, 3, 8, 9, 0, tzinfo=timezone.utc).isoformat(),
                    "created_by": {"gid": missing_gid},
                    "text": "Comment without mapping",
                }
            ],
            self.sync_time,
        )
        self.connection.commit()

        output_dir = self.workspace / "exports"
        csv_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="fallback.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
            fallback_atlassian_id=fallback_id,
        )
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        header = rows[0]
        gid_idx = header.index("External issue ID")
        task_rows = {row[gid_idx]: row for row in rows[1:]}
        row = task_rows["task_fallback"]

        assignee_atl_index = header.index("assignee_atlassian_id")
        comment_index = [idx for idx, name in enumerate(header) if name == "Comment"][0]

        self.assertEqual(row[assignee_atl_index], fallback_id)
        self.assertEqual(row[comment_index], "08/Mar/25 9:00 AM;atl-fallback; Comment without mapping")

    def test_inactive_assignee_uses_top_active_creator_for_owner_columns(self) -> None:
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_inactive_assignee",
                "name": "Inactive Assignee Task",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "assignee": {"gid": "user_inactive", "name": "Inactive Assignee"},
                "created_by": {"gid": "user_1"},
                "created_at": "2025-03-09T12:00:00Z",
                "modified_at": "2025-03-10T12:00:00Z",
                "memberships": [{"project": {"gid": "proj_1", "name": "Example Project"}}],
            },
            self.sync_time,
        )
        self.connection.commit()

        output_dir = self.workspace / "exports"
        csv_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="inactive_assignee.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
        )
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))

        header = rows[0]
        task_rows = {row[header.index("External issue ID")]: row for row in rows[1:]}
        row = task_rows["task_inactive_assignee"]

        assignee_idx = header.index("assignee")
        assignee_email_idx = header.index("assignee_email")
        assignee_atl_idx = header.index("assignee_atlassian_id")
        creator_idx = header.index("creator")
        creator_email_idx = header.index("creator_email")
        creator_atl_idx = header.index("creator_atlassian_id")
        self.assertNotIn("assignee_atlassian_email", header)
        self.assertNotIn("creator_atlassian_email", header)
        self.assertNotIn("start_at", header)

        self.assertEqual(row[assignee_idx], "Alice Reviewer")
        self.assertEqual(row[assignee_email_idx], "alice@example.com")
        self.assertEqual(row[assignee_atl_idx], "atl-account-alice")
        self.assertEqual(row[creator_idx], "Alice Reviewer")
        self.assertEqual(row[creator_email_idx], "alice@example.com")
        self.assertEqual(row[creator_atl_idx], "atl-account-alice")

    def test_exports_task_collaborators_and_records_them_in_association_report(self) -> None:
        output_dir = self.workspace / "exports"
        csv_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="collaborators.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
        )
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        header = rows[0]
        by_gid = {row[header.index("task_gid")]: row for row in rows[1:]}
        collaborator_index = header.index("Collaborators")
        self.assertEqual(header.count("Collaborators"), 1)
        self.assertEqual(
            by_gid["task_1"][collaborator_index : collaborator_index + 3],
            [
                "Bob Approver",
                "bob@example.com",
                "atl-account-bob",
            ],
        )

        assoc_path = csv_path.with_name(f"{csv_path.stem}_user_associations.csv")
        with assoc_path.open("r", newline="", encoding="utf-8") as handle:
            associations = {row["user_gid"]: row for row in csv.DictReader(handle)}
        self.assertEqual(associations["user_2"]["collaborator_count"], "1")
        self.assertEqual(associations["user_1"]["collaborator_count"], "0")

    def test_main_consolidates_collaborator_counts_in_user_association_report(self) -> None:
        output_dir = self.workspace / "consolidated"
        with patch("scripts.atlassian.export_project_to_jira_csv.load_credentials"), patch(
            "scripts.atlassian.export_project_to_jira_csv.resolve_jira_base_url",
            return_value=self.jira_base_url,
        ):
            main(
                [
                    "--project-gid",
                    "proj_1",
                    "--database",
                    str(self.db_path),
                    "--output-dir",
                    str(output_dir),
                    "--no-refresh-prompt",
                ]
            )
        reports = list(output_dir.rglob("*_user_associations.csv"))
        consolidated = next(path for path in reports if path.name.startswith("_Consolidated-"))
        with consolidated.open("r", newline="", encoding="utf-8") as handle:
            rows = {row["user_gid"]: row for row in csv.DictReader(handle)}
        self.assertEqual(rows["user_2"]["collaborator_count"], "1")

    def test_missing_user_snapshot_uses_unknown_user_label(self) -> None:
        missing_gid = "user_missing"
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_missing_user",
                "name": "Needs mapping",
                "resource_type": "task",
                "assignee": {"gid": missing_gid},
                "created_by": {"gid": missing_gid},
                "created_at": "2025-03-11T12:00:00Z",
                "memberships": [{"project": {"gid": "proj_1", "name": "Example Project"}}],
            },
            self.sync_time,
        )
        self.connection.commit()

        output_dir = self.workspace / "exports"
        csv_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="unknown_user.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
        )
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        header = rows[0]
        task_rows = {row[header.index("External issue ID")]: row for row in rows[1:]}
        row = task_rows["task_missing_user"]

        assignee_idx = header.index("assignee")
        creator_idx = header.index("creator")
        self.assertEqual(row[assignee_idx], "unknown_user user_missing")
        self.assertEqual(row[creator_idx], "unknown_user user_missing")

        assoc_path = csv_path.with_name(f"{csv_path.stem}_user_associations.csv")
        with assoc_path.open("r", newline="", encoding="utf-8") as handle:
            assoc_rows = list(csv.DictReader(handle))
        assoc_map = {row["user_gid"]: row for row in assoc_rows}
        self.assertEqual(assoc_map[missing_gid]["display_name"], "unknown_user user_missing")

    def test_missing_account_report_created_with_counts(self) -> None:
        missing_gid = "user_missing_report"
        missing_email = "missing.report@example.com"
        self.database.upsert_user(
            self.connection,
            {
                "gid": missing_gid,
                "name": "Missing Reporter",
                "resource_type": "user",
                "email": missing_email,
                "is_active": True,
                "workspaces": [],
            },
            self.sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_missing_report",
                "name": "Needs mapping report",
                "resource_type": "task",
                "assignee": {"gid": missing_gid, "name": "Missing Reporter"},
                "created_by": {"gid": missing_gid},
                "created_at": "2025-03-09T12:00:00Z",
                "memberships": [{"project": {"gid": "proj_1", "name": "Example Project"}}],
            },
            self.sync_time,
        )
        self.database.replace_task_comments(
            self.connection,
            "task_missing_report",
            [
                {
                    "gid": "cmt_missing",
                    "created_at": datetime(2025, 3, 10, 9, 0, tzinfo=timezone.utc).isoformat(),
                    "created_by": {"gid": missing_gid},
                    "text": "Comment without mapping",
                }
            ],
            self.sync_time,
        )
        self.connection.commit()

        output_dir = self.workspace / "exports"
        csv_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="missing_report.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
        )

        assoc_path = csv_path.with_name(f"{csv_path.stem}_user_associations.csv")
        self.assertTrue(assoc_path.exists())
        with assoc_path.open("r", newline="", encoding="utf-8") as handle:
            assoc_rows = list(csv.DictReader(handle))
        assoc_map = {row["user_gid"]: row for row in assoc_rows}
        self.assertIn(missing_gid, assoc_map)
        entry = assoc_map[missing_gid]
        self.assertEqual(entry["assignee_count"], "1")
        self.assertEqual(entry["creator_count"], "1")
        self.assertEqual(entry["commenter_count"], "1")
        self.assertEqual(entry["last_active_date"], "2025-03-10")

    def test_stats_only_writes_summary_files(self) -> None:
        output_dir = self.workspace / "exports"
        result = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="stats_only.csv",
            jira_base_url=self.jira_base_url,
            stats_only=True,
            prompt_for_custom_fields=False,
        )

        tasks_path = result
        section_path = result.with_name(f"{result.stem}_section_counts.csv")
        field_counts_path = result.with_name(f"{result.stem}_field_counts.csv")

        self.assertIsNotNone(result)
        self.assertFalse(tasks_path.exists())
        self.assertTrue(section_path.exists())
        self.assertTrue(field_counts_path.exists())

    def test_custom_field_filter_supports_names_and_gids(self) -> None:
        output_dir = self.workspace / "exports"
        csv_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="filtered_names.csv",
            jira_base_url=self.jira_base_url,
            custom_field_filters=["Customer Tier"],
            prompt_for_custom_fields=False,
        )
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        self.assertIn("Customer Tier", header)
        self.assertNotIn("Region", header)
        self.assertFalse(any(name.startswith("Region") for name in header))

        csv_path_gid = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="filtered_gid.csv",
            jira_base_url=self.jira_base_url,
            custom_field_filters=["cf_multi"],
            prompt_for_custom_fields=False,
        )
        with csv_path_gid.open("r", newline="", encoding="utf-8") as handle:
            header_gid = next(csv.reader(handle))
        self.assertTrue(any(name.startswith("Region") for name in header_gid))
        self.assertNotIn("Customer Tier", header_gid)

        summary_path = csv_path_gid.with_name(f"{csv_path_gid.stem}_field_counts.csv")
        with summary_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[1][-1], "No")
        self.assertEqual(rows[2][-1], "Yes")

    def test_asana_links_in_notes_and_comments_are_augmented(self) -> None:
        self.maxDiff = None
        migrated_profile_gid = "1201132360936580"
        unmapped_profile_gid = "1201132360936581"
        html_profile_url_gid = "1201512860287501"
        html_profile_actual_gid = "1201512838056458"
        html_list_url_gid = "1204000000000123"
        html_list_actual_gid = "1204000000000456"
        self.database.upsert_user(
            self.connection,
            {
                "gid": migrated_profile_gid,
                "name": "Migrated Example",
                "resource_type": "user",
                "email": "migrated@example.com",
                "is_active": True,
                "workspaces": [],
            },
            self.sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": unmapped_profile_gid,
                "name": "Casey Example",
                "resource_type": "user",
                "email": "casey@example.com",
                "is_active": True,
                "workspaces": [],
            },
            self.sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": html_profile_actual_gid,
                "name": "Isley Gao",
                "resource_type": "user",
                "email": "isley@example.com",
                "is_active": True,
                "workspaces": [],
            },
            self.sync_time,
        )
        self.database.upsert_user(
            self.connection,
            {
                "gid": html_list_actual_gid,
                "name": "Hazel Bachrach",
                "resource_type": "user",
                "email": "hazel@example.com",
                "is_active": True,
                "workspaces": [],
            },
            self.sync_time,
        )
        self.database.replace_user_atlassian_accounts(
            self.connection,
            [
                {
                    "gid": migrated_profile_gid,
                    "atlassianAccountId": "atl-account-zafar",
                },
                {
                    "gid": html_profile_actual_gid,
                    "atlassianAccountId": "atl-account-isley",
                },
                {
                    "gid": html_list_actual_gid,
                    "atlassianAccountId": "atl-account-hazel",
                },
            ],
            self.sync_time,
        )
        project_list_gid = "1202177026190132"
        self.connection.execute(
            """
            INSERT INTO projects (gid, name, synced_at)
            VALUES (?, ?, ?)
            """,
            (project_list_gid, "Stella's profile", self.sync_time.isoformat()),
        )
        attachment_gid = "1211776038456289"
        alt_task_project_gid = "164704154389947"
        alt_task_gid = "1206393554891721"
        notes_body = (
            "Task contains references\n"
            "Linked task: https://app.asana.com/1/8586366022814/project/1203643549286008/task/1211589352651816\n"
            f"Alt linked task: https://app.asana.com/0/{alt_task_project_gid}/{alt_task_gid}/f\n"
            "Migrated user: https://app.asana.com/0/profile/1201132360936580\n"
            "Missing user: https://app.asana.com/0/profile/1201132360936581\n"
            f"HTML tagged user: https://app.asana.com/0/profile/{html_profile_url_gid}\n"
            f"User list link: https://app.asana.com/0/{html_list_url_gid}/list\n"
            f"Attachment: https://app.asana.com/app/asana/-/get_asset?asset_id={attachment_gid}\n"
            "Can someone grant me those perms? Should this be an access request ticket? "
            "https://app.asana.com/0/1202177026190132/list\n"
        )
        html_notes = (
            "<body>Task contains references<br/>"
            "Linked task: <a href=\"https://app.asana.com/1/8586366022814/project/1203643549286008/task/1211589352651816\" "
            "data-asana-type=\"task\" data-asana-gid=\"1211589352651816\">Task Link</a><br/>"
            f"Alt linked task: <a href=\"https://app.asana.com/0/{alt_task_project_gid}/{alt_task_gid}/f\" "
            f"data-asana-type=\"task\" data-asana-gid=\"{alt_task_gid}\">Alt Task Link</a><br/>"
            "Migrated user: <a href=\"https://app.asana.com/0/profile/1201132360936580\" data-asana-type=\"user\" "
            "data-asana-gid=\"1201132360936580\">Migrated Example</a><br/>"
            "Missing user: <a href=\"https://app.asana.com/0/profile/1201132360936581\" data-asana-type=\"user\" "
            "data-asana-gid=\"1201132360936581\">Casey Example</a><br/>"
            f"HTML tagged user: <a href=\"https://app.asana.com/0/profile/{html_profile_url_gid}\" data-asana-type=\"user\" "
            f"data-asana-gid=\"{html_profile_actual_gid}\">Isley Gao</a><br/>"
            f"User list link: <a href=\"https://app.asana.com/0/{html_list_url_gid}/list\" data-asana-type=\"user\" "
            f"data-asana-gid=\"{html_list_actual_gid}\">Hazel Bachrach</a><br/>"
            "</body>"
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_links",
                "name": "Task With Links",
                "resource_type": "task",
                "notes": notes_body,
                "html_notes": html_notes,
                "assignee": {"gid": "user_1", "name": "Alice Reviewer"},
                "created_by": {"gid": "user_1"},
                "created_at": "2025-02-27T08:00:00Z",
                "memberships": [
                    {
                        "project": {"gid": "proj_1", "name": "Example Project"},
                    }
                ],
                "custom_fields": [],
                "tags": [],
            },
            self.sync_time,
        )
        self.database.replace_task_attachments(
            self.connection,
            "task_links",
            [
                {
                    "gid": attachment_gid,
                    "name": "image-20251119-221903.png",
                    "parent": {"gid": "task_links", "resource_type": "task"},
                    "resource_subtype": "asana",
                    "host": "asana",
                    "service": None,
                    "size": 1024,
                    "created_at": "2025-02-28T17:00:00Z",
                    "created_by": {"gid": "user_1"},
                    "permanent_url": f"https://app.asana.com/app/asana/-/get_asset?asset_id={attachment_gid}",
                    "view_url": f"https://app.asana.com/app/asana/-/get_asset?asset_id={attachment_gid}",
                }
            ],
            self.sync_time,
        )
        comment_text = (
            "See task https://app.asana.com/1/8586366022814/project/1203643549286008/task/9988776655443322"
            f" and alt https://app.asana.com/0/{alt_task_project_gid}/{alt_task_gid}/f"
            " with profile https://app.asana.com/0/profile/1201132360936581"
            f" and HTML user https://app.asana.com/0/profile/{html_profile_url_gid}oops"
            f" plus list https://app.asana.com/0/{html_list_url_gid}/list"
        )
        html_comment = (
            "<body>See task <a href=\"https://app.asana.com/1/8586366022814/project/1203643549286008/task/9988776655443322\" "
            "data-asana-type=\"task\" data-asana-gid=\"9988776655443322\">Linked Task</a> and alt "
            f"<a href=\"https://app.asana.com/0/{alt_task_project_gid}/{alt_task_gid}/f\" "
            f"data-asana-type=\"task\" data-asana-gid=\"{alt_task_gid}\">Alt Task</a> with profile "
            "<a href=\"https://app.asana.com/0/profile/1201132360936581\" data-asana-type=\"user\" "
            "data-asana-gid=\"1201132360936581\">Casey Example</a> and HTML user "
            f"<a href=\"https://app.asana.com/0/profile/{html_profile_url_gid}\" data-asana-type=\"user\" "
            f"data-asana-gid=\"{html_profile_actual_gid}\">Isley Gao</a> plus list "
            f"<a href=\"https://app.asana.com/0/{html_list_url_gid}/list\" data-asana-type=\"user\" "
            f"data-asana-gid=\"{html_list_actual_gid}\">Hazel Bachrach</a></body>"
        )
        self.database.replace_task_comments(
            self.connection,
            "task_links",
            [
                {
                    "gid": "cmt_links",
                    "created_at": datetime(2025, 3, 6, 14, 0, tzinfo=timezone.utc).isoformat(),
                    "created_by": {"gid": "user_1"},
                    "text": comment_text,
                    "html_text": html_comment,
                }
            ],
            self.sync_time,
        )
        self.connection.commit()

        output_dir = self.workspace / "exports"
        csv_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="links_augmented.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
        )
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        header = rows[0]
        notes_index = header.index("notes")
        comment_columns = [index for index, name in enumerate(header) if name == "Comment"]
        gid_idx = header.index("External issue ID")
        task_rows = {row[gid_idx]: row for row in rows[1:]}
        link_task_row = task_rows["task_links"]
        jira_base_url = self.jira_base_url

        expected_notes = (
            "Task contains references\n"
            "Linked task: [Task Link|https://app.asana.com/1/8586366022814/project/1203643549286008/task/1211589352651816]\n"
            f"{jira_base_url}/issues?jql=%22External%20issue%20ID%22%20~%20%221211589352651816%22\n"
            f"Alt linked task: [Alt Task Link|https://app.asana.com/0/{alt_task_project_gid}/{alt_task_gid}/f]\n"
            f"{jira_base_url}/issues?jql=%22External%20issue%20ID%22%20~%20%221206393554891721%22\n"
            "Migrated user: [Migrated Example|https://app.asana.com/0/profile/1201132360936580]\n"
            "[~accountid:atl-account-zafar]\n"
            "Missing user: [Casey Example|https://app.asana.com/0/profile/1201132360936581]\n"
            "Casey Example\n"
            f"HTML tagged user: [Isley Gao|https://app.asana.com/0/profile/{html_profile_url_gid}]\n"
            "[~accountid:atl-account-isley]\n"
            f"User list link: [Hazel Bachrach|https://app.asana.com/0/{html_list_url_gid}/list]\n"
            "[~accountid:atl-account-hazel]\n"
            f"Attachment: https://app.asana.com/app/asana/-/get_asset?asset_id={attachment_gid}\n"
            "!image-20251119-221903__1211776038456289.png!\n"
            "Can someone grant me those perms? Should this be an access request ticket? "
            "https://app.asana.com/0/1202177026190132/list\n"
            "Stella's profile\n"
        )
        self.assertEqual(link_task_row[notes_index], expected_notes)

        expected_comment = (
            "06/Mar/25 2:00 PM;atl-account-alice; "
            "See task [Linked Task|https://app.asana.com/1/8586366022814/project/1203643549286008/task/9988776655443322]\n"
            f"{jira_base_url}/issues?jql=%22External%20issue%20ID%22%20~%20%229988776655443322%22"
            f" and alt [Alt Task|https://app.asana.com/0/{alt_task_project_gid}/{alt_task_gid}/f]\n"
            f"{jira_base_url}/issues?jql=%22External%20issue%20ID%22%20~%20%221206393554891721%22"
            " with profile [Casey Example|https://app.asana.com/0/profile/1201132360936581]\n"
            "Casey Example and HTML user "
            f"[Isley Gao|https://app.asana.com/0/profile/{html_profile_url_gid}]\n"
            "[~accountid:atl-account-isley]oops plus list "
            f"[Hazel Bachrach|https://app.asana.com/0/{html_list_url_gid}/list]\n"
            "[~accountid:atl-account-hazel]"
        )
        self.assertEqual(link_task_row[comment_columns[0]], expected_comment)

        audit_candidates = [
            csv_path.with_name(f"{csv_path.stem}_link_modifications.csv"),
            csv_path.parent / "link_modifications" / f"{csv_path.stem}_link_modifications.csv",
        ]
        audit_path = next((p for p in audit_candidates if p.exists()), None)
        self.assertIsNotNone(audit_path)
        audit_contents = audit_path.read_text(encoding="utf-8")
        self.assertIn("task_links,comment", audit_contents)
        self.assertIn(f"https://app.asana.com/0/{html_list_url_gid}/list", audit_contents)
        self.assertIn("atl-account-isley", audit_contents)
        self.assertIn("atl-account-hazel", audit_contents)

    def test_default_filename_includes_project_name_and_timestamp(self) -> None:
        output_dir = self.workspace / "exports"
        fixed_time = datetime(2025, 3, 4, 15, 30, tzinfo=timezone.utc)
        with patch("scripts.atlassian.export_project_to_jira_csv.current_utc_time", return_value=fixed_time):
            csv_path = export_project(
                str(self.db_path),
                "proj_1",
                output_dir,
                jira_base_url=self.jira_base_url,
                prompt_for_custom_fields=False,
            )
        expected_name = "Example_Project-20250304-1530_tasks.csv"
        self.assertEqual(csv_path.name, expected_name)
        summary_path = csv_path.with_name(f"{csv_path.stem}_field_counts.csv")
        self.assertTrue(summary_path.exists())

    def test_subtasks_without_direct_project_membership_are_included(self) -> None:
        parent_gid = "parent_task"
        child_gid = "child_task"
        self.database.upsert_task(
            self.connection,
            {
                "gid": parent_gid,
                "name": "Parent Task",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "memberships": [
                    {
                        "project": {"gid": "proj_1", "name": "Example Project"},
                        "section": {"gid": "sec_extra", "name": "Ready"},
                    }
                ],
            },
            self.sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": child_gid,
                "name": "Child Task",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "parent": {"gid": parent_gid},
                "notes": "Nested work",
            },
            self.sync_time,
        )
        self.connection.commit()

        output_dir = self.workspace / "exports"
        csv_path = export_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            filename="subtasks.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
        )
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        header = rows[0]
        parent_index = header.index("parent_gid")
        current_project_index = header.index("current_project")
        gid_idx = header.index("External issue ID")
        task_rows = {row[gid_idx]: row for row in rows[1:]}

        self.assertIn(child_gid, task_rows)
        child_row = task_rows[child_gid]
        self.assertEqual(child_row[parent_index], parent_gid)
        self.assertEqual(child_row[current_project_index], "Example Project (proj_1)")
        parent_row = task_rows[parent_gid]
        self.assertEqual(parent_row[current_project_index], "Example Project (proj_1)")

    def test_project_stats_print_snapshot_counts(self) -> None:
        tasks = list(self.connection.execute("SELECT * FROM tasks WHERE gid IN (?, ?)", ("task_1", "task_2")))
        comments = list(self.connection.execute("SELECT * FROM task_comments WHERE task_gid IN (?, ?)", ("task_1", "task_2")))
        buffer = StringIO()
        with redirect_stdout(buffer):
            print_project_stats(
                "proj_1",
                tasks,
                comments,
                {},
                {},
                {},
                0,
                0,
                self.connection,
            )
        output = buffer.getvalue()
        self.assertIn("Project task count snapshot:", output)
        self.assertIn("Total tasks: 2", output)
        self.assertIn("Incomplete tasks: 4", output)
        self.assertIn("Completed tasks: 6", output)
        self.assertIn("Captured at: 2025-01-01T00:00:00Z", output)

    @patch("scripts.atlassian.export_project_to_jira_csv.prompt_and_run_incremental_refresh")
    def test_export_always_prompts_for_incremental_refresh(self, mock_refresh) -> None:
        export_project(
            str(self.db_path),
            "proj_1",
            self.workspace / "exports",
            filename="refresh_prompt.csv",
            jira_base_url=self.jira_base_url,
            prompt_for_custom_fields=False,
        )
        mock_refresh.assert_called_once()

    def test_format_custom_field_names_line(self) -> None:
        fields = [
            {"name": "Field One"},
            {"name": "Field Two"},
            {"name": ""},
        ]
        result = format_custom_field_names_line(fields)
        self.assertEqual(result, "Field One, Field Two")

    def test_compute_incremental_refresh_window_subtracts_one_day(self) -> None:
        reference_now = datetime(2025, 3, 10, tzinfo=timezone.utc)
        latest = datetime(2025, 3, 8, 15, 0, tzinfo=timezone.utc).isoformat()
        days_back, start_time = compute_incremental_refresh_window(latest, now=reference_now)
        self.assertEqual(start_time, datetime(2025, 3, 7, 15, 0, tzinfo=timezone.utc))
        self.assertEqual(days_back, 3)

    @patch("scripts.atlassian.export_project_to_jira_csv.subprocess.run")
    @patch("scripts.atlassian.export_project_to_jira_csv.input", return_value="y")
    def test_prompt_runs_incremental_refresh_when_user_accepts(
        self,
        mock_input: Mock,
        mock_run: Mock,
    ) -> None:
        with patch("scripts.atlassian.export_project_to_jira_csv.sys.stdin") as mock_stdin, patch.object(
            sys, "executable", "/usr/bin/python-test"
        ):
            mock_stdin.isatty.return_value = True
            prompt_and_run_incremental_refresh("proj_1", "2025-11-17T12:00:00Z", workers=4)
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("--project-gid", cmd)
        self.assertIn("proj_1", cmd)

    def test_normalize_jira_base_url_adds_scheme_and_strips_slash(self) -> None:
        self.assertEqual(
            normalize_jira_base_url("my-domain.atlassian.net/"),
            "https://my-domain.atlassian.net",
        )
        self.assertIsNone(normalize_jira_base_url("   "))

    def test_parse_project_list_explicit_gids_include_archived(self) -> None:
        self.connection.execute(
            """
            INSERT INTO projects (gid, name, archived, synced_at)
            VALUES (?, ?, ?, ?)
            """,
            ("proj_archived", "Archived Project", 1, self.sync_time.isoformat()),
        )
        self.connection.commit()

        result = parse_project_list(
            self.connection,
            project_gid=None,
            project_gids="proj_archived,proj_1",
            team_gid=None,
            archived_scope="active",
        )

        self.assertEqual(result, [("proj_archived", "Archived Project"), ("proj_1", "Example Project")])

    def test_assign_unmapped_jira_project_preserves_existing_associations(self) -> None:
        self.database.upsert_jira_project_mappings(
            self.connection,
            [{"project_gid": "proj_1", "project_name": "Existing Jira", "project_key": "OLD"}],
            self.sync_time,
        )

        added = assign_unmapped_jira_project(
            self.database,
            [("proj_1", "Example Project"), ("proj_2", "Secondary Project")],
            "MIG",
            "Migration",
        )

        self.assertEqual(added, 1)
        mappings = self.database.load_jira_project_mappings(self.connection)
        self.assertEqual(mappings["proj_1"]["jira_project_key"], "OLD")
        self.assertEqual(mappings["proj_1"]["jira_project_name"], "Existing Jira")
        self.assertEqual(mappings["proj_2"]["jira_project_key"], "MIG")
        self.assertEqual(mappings["proj_2"]["jira_project_name"], "Migration")

    def test_parse_project_list_reads_project_gid_column_from_csv(self) -> None:
        scope_file = self.workspace / "project-map.csv"
        scope_file.write_text(
            "project_gid,project_key,project_name\nproj_1,OPS-1,Example Project\n"
        )

        result = parse_project_list(
            self.connection,
            project_gid=None,
            project_gids_file=str(scope_file),
            team_gid=None,
            archived_scope="active",
        )

        self.assertEqual(result, [("proj_1", "Example Project")])

    def test_parse_project_list_team_gid_respects_archived_scope(self) -> None:
        self.connection.execute(
            """
            INSERT INTO projects (gid, name, archived, team_gid, synced_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("team_active", "Team Active", 0, "team_1", self.sync_time.isoformat()),
        )
        self.connection.execute(
            """
            INSERT INTO projects (gid, name, archived, team_gid, synced_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("team_archived", "Team Archived", 1, "team_1", self.sync_time.isoformat()),
        )
        self.connection.commit()

        active = parse_project_list(
            self.connection,
            project_gid=None,
            project_gids=None,
            team_gid="team_1",
            archived_scope="active",
        )
        archived = parse_project_list(
            self.connection,
            project_gid=None,
            project_gids=None,
            team_gid="team_1",
            archived_scope="archived",
        )
        all_projects = parse_project_list(
            self.connection,
            project_gid=None,
            project_gids=None,
            team_gid="team_1",
            archived_scope="all",
        )

        self.assertEqual({gid for gid, _ in active}, {"team_active"})
        self.assertEqual({gid for gid, _ in archived}, {"team_archived"})
        self.assertEqual({gid for gid, _ in all_projects}, {"team_active", "team_archived"})

    def test_exact_task_scope_exports_only_requested_task(self) -> None:
        output_path = export_project(
            str(self.db_path),
            "proj_1",
            self.workspace / "task-scope",
            filename="selected.csv",
            prompt_for_custom_fields=False,
            jira_base_url=self.jira_base_url,
            incremental_decision=False,
            task_gids={"task_2"},
        )

        self.assertIsNotNone(output_path)
        with output_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["task_gid"] for row in rows], ["task_2"])

    def _section_epic_gid(self, section_name: str) -> str:
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", section_name.strip())
        base = base.strip("_") or "section"
        return f"proj_1::section::{base}"

    def _seed_custom_fields(self) -> None:
        workspace = {"gid": "ws_1", "name": "Demo Workspace"}
        self.database.upsert_custom_field(
            self.connection,
            {
                "gid": "cf_text",
                "name": "Customer Tier",
                "resource_type": "custom_field",
                "resource_subtype": "text",
                "workspace": workspace,
            },
            self.sync_time,
        )
        self.database.upsert_custom_field(
            self.connection,
            {
                "gid": "cf_multi",
                "name": "Region",
                "resource_type": "custom_field",
                "resource_subtype": "multi_enum",
                "workspace": workspace,
                "enum_options": [
                    {"gid": "opt_na", "name": "North America", "color": "blue", "enabled": True},
                    {"gid": "opt_eu", "name": "EMEA", "color": "green", "enabled": True},
                ],
            },
            self.sync_time,
        )
