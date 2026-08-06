from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database
from scripts.atlassian.export_archival_project_jira_csv import (
    ARCHIVAL_FIELDS_HEADING,
    build_archival_description,
    export_archival_project,
)


class ExportArchivalProjectJiraCsvTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.jira_base_url = "https://jira.example.atlassian.net"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.addCleanup(self.connection.close)
        self.database.initialize(self.connection)
        self.sync_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

        self._seed_custom_fields()

        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("proj_1", "Archive Me", self.sync_time.isoformat()),
        )

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

        # Parent task: populated text + multi-enum custom fields, section, tag, comment.
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_1",
                "name": "Parent Task",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "notes": "Original description.",
                "assignee": {"gid": "user_1", "name": "Alice Reviewer"},
                "created_by": {"gid": "user_1"},
                "created_at": "2025-02-25T10:30:00Z",
                "modified_at": "2025-03-04T09:05:00Z",
                "completed": True,
                "completed_at": "2025-03-04T09:05:00Z",
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
                    # Present on the task but with no value -> must be omitted from the block.
                    {
                        "gid": "cf_empty",
                        "type": "text",
                        "text_value": None,
                        "display_value": None,
                        "is_value_set": False,
                    },
                ],
                "memberships": [
                    {
                        "project": {"gid": "proj_1", "name": "Archive Me"},
                        "section": {"gid": "sec_1", "name": "Backlog"},
                    }
                ],
                "tags": [{"gid": "tag_1", "name": "Needs QA"}],
            },
            self.sync_time,
        )

        # Subtask: no custom fields -> notes must be unchanged (no block); inherits section.
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_2",
                "name": "Child Task",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "notes": "Just notes, no fields.",
                "parent": {"gid": "task_1"},
                "assignee": {"gid": "user_1", "name": "Alice Reviewer"},
                "created_by": {"gid": "user_1"},
                "created_at": "2025-02-26T12:45:00Z",
                "modified_at": "2025-03-05T16:00:00Z",
                "completed": False,
                "memberships": [{"project": {"gid": "proj_1", "name": "Archive Me"}}],
            },
            self.sync_time,
        )

        self.database.replace_task_comments(
            self.connection,
            "task_1",
            [
                {
                    "gid": "cmt_1",
                    "created_at": datetime(2025, 3, 4, 9, 5, tzinfo=timezone.utc).isoformat(),
                    "created_by": {"gid": "user_1"},
                    "text": "A comment stays native.",
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
                    "atlassianAccountId": "atl-alice",
                    "atlassianEmail": "alice.atl@example.com",
                    "atlassianStatus": "active",
                }
            ],
            self.sync_time,
        )
        self.connection.commit()

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
        self.database.upsert_custom_field(
            self.connection,
            {
                "gid": "cf_empty",
                "name": "Unused Field",
                "resource_type": "custom_field",
                "resource_subtype": "text",
                "workspace": workspace,
            },
            self.sync_time,
        )

    def _run_export(self):
        output_dir = self.workspace / "exports"
        csv_path = export_archival_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            self.jira_base_url,
            filename="archive_proj_1.csv",
        )
        self.assertIsNotNone(csv_path)
        with csv_path.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.reader(handle))
        header = rows[0]
        by_gid = {row[header.index("External issue ID")]: row for row in rows[1:]}
        return header, by_gid

    def test_custom_fields_are_not_columns(self) -> None:
        header, _ = self._run_export()
        self.assertNotIn("Customer Tier", header)
        self.assertNotIn("Region", header)
        self.assertNotIn("Unused Field", header)

    def test_native_columns_retained(self) -> None:
        header, by_gid = self._run_export()
        for column in ("Section", "parent_gid", "completed", "notes", "Comment", "Tags"):
            self.assertIn(column, header)
        # Native comment column keeps the Jira comment format (not folded away).
        self.assertEqual(
            by_gid["task_1"][header.index("Comment")],
            "04/Mar/25 9:05 AM;atl-alice; A comment stays native.",
        )
        # Subtask inherits the parent's section.
        self.assertEqual(by_gid["task_2"][header.index("Section")], "Backlog")
        self.assertEqual(by_gid["task_2"][header.index("parent_gid")], "task_1")

    def test_populated_fields_folded_into_description(self) -> None:
        header, by_gid = self._run_export()
        notes = by_gid["task_1"][header.index("notes")]
        self.assertIn("Original description.", notes)
        self.assertIn(ARCHIVAL_FIELDS_HEADING, notes)
        self.assertIn("Customer Tier: Enterprise", notes)
        # Multi-enum joined with ", ".
        self.assertIn("Region: North America, EMEA", notes)
        # Empty-valued field is omitted.
        self.assertNotIn("Unused Field", notes)
        # The block follows the original notes.
        self.assertLess(notes.index("Original description."), notes.index(ARCHIVAL_FIELDS_HEADING))

    def test_task_without_fields_keeps_notes_unchanged(self) -> None:
        header, by_gid = self._run_export()
        self.assertEqual(by_gid["task_2"][header.index("notes")], "Just notes, no fields.")

    def test_build_archival_description_no_fields_returns_notes(self) -> None:
        self.assertEqual(build_archival_description("hello", [], "t", {}, {}, {}), "hello")

    def test_build_archival_description_block_only_when_notes_empty(self) -> None:
        field_meta = [{"gid": "cf_text", "name": "Customer Tier", "type": "text", "max_multi": 0}]
        single_map = {
            ("t", "cf_text"): {
                "enum_option_gid": None,
                "display_value": "Enterprise",
                "text_value": "Enterprise",
                "number_value": None,
                "date_value": None,
            }
        }
        result = build_archival_description("", field_meta, "t", single_map, {}, {})
        self.assertTrue(result.startswith(ARCHIVAL_FIELDS_HEADING))
        self.assertIn("Customer Tier: Enterprise", result)
