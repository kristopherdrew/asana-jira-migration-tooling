from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from src.lib.database import Database
from src.lib.jira_csv_partition import CsvPart
from scripts.atlassian.export_archival_project_jira_csv import (
    ARCHIVAL_FIELDS_HEADING,
    build_archival_description,
    build_default_filename,
    export_archival_project,
    main,
    verify_csv_part_counts,
)
from src.lib.task_scope import parse_task_gids, resolve_task_projects


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
                "followers": [{"gid": "user_1", "name": "Alice Reviewer"}],
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
            workspace_gid="5153924692276",
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
        for column in (
            "Section",
            "parent_gid",
            "completed",
            "notes",
            "Comment",
            "Tags",
        ):
            self.assertIn(column, header)
        for excluded_column in (
            "assignee_atlassian_email",
            "creator_atlassian_email",
            "start_at",
        ):
            self.assertNotIn(excluded_column, header)
        # Native comment column keeps the Jira comment format (not folded away).
        self.assertEqual(
            by_gid["task_1"][header.index("Comment")],
            "04/Mar/25 9:05 AM;atl-alice; A comment stays native.",
        )
        # Subtask inherits the parent's section.
        self.assertEqual(by_gid["task_2"][header.index("Section")], "Backlog")
        self.assertEqual(by_gid["task_2"][header.index("parent_gid")], "task_1")
        self.assertNotIn("Collaborators", header)

    def test_adds_project_epic_and_reparents_top_level_tasks(self) -> None:
        header, by_gid = self._run_export()
        epic_gid = "proj_1::archival::epic"
        self.assertEqual(by_gid[epic_gid][header.index("task_name")], "Archive Me")
        self.assertEqual(by_gid[epic_gid][header.index("jira_hierarchy_depth")], "Epic")
        self.assertEqual(by_gid["task_1"][header.index("jira_hierarchy_depth")], "Task")
        self.assertEqual(by_gid["task_2"][header.index("jira_hierarchy_depth")], "Sub-task")
        self.assertEqual(by_gid["task_1"][header.index("jira_parent_gid")], epic_gid)
        self.assertEqual(by_gid["task_2"][header.index("jira_parent_gid")], "task_1")

    def test_adds_project_link_to_every_issue_and_collaborators_to_epic(self) -> None:
        header, by_gid = self._run_export()
        expected_link = "https://app.asana.com/1/5153924692276/project/proj_1"
        self.assertEqual(
            {row[header.index("Asana Project Link")] for row in by_gid.values()},
            {expected_link},
        )
        self.assertEqual(
            by_gid["task_1"][header.index("Asana Task Link")],
            "https://app.asana.com/1/5153924692276/task/task_1",
        )
        self.assertEqual(
            by_gid["task_2"][header.index("Asana Task Link")],
            "https://app.asana.com/1/5153924692276/task/task_2",
        )
        self.assertEqual(
            by_gid["proj_1::archival::epic"][header.index("Asana Task Link")],
            "",
        )
        epic_notes = by_gid["proj_1::archival::epic"][header.index("notes")]
        self.assertIn("Collaborators:\n- None recorded", epic_notes)
        self.assertNotIn("creator_count", epic_notes)

    def test_main_writes_consolidated_export_and_organizes_outputs(self) -> None:
        output_dir = self.workspace / "organized"
        argv = [
            "--project-gid",
            "proj_1",
            "--database",
            str(self.db_path),
            "--output-dir",
            str(output_dir),
            "--filename",
            "archive.csv",
        ]
        with patch(
            "scripts.atlassian.export_archival_project_jira_csv.load_credentials"
        ), patch(
            "scripts.atlassian.export_archival_project_jira_csv.resolve_jira_base_url",
            return_value=self.jira_base_url,
        ):
            main(argv)

        task_files = sorted((output_dir / "tasks").glob("*.csv"))
        self.assertEqual(len(task_files), 2)
        consolidated = next(path for path in task_files if path.name.startswith("_Consolidated-"))
        with consolidated.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 3)
        self.assertIn("proj_1::archival::epic", {row["External issue ID"] for row in rows})
        self.assertEqual(len(list((output_dir / "manifests").glob("*_manifest.csv"))), 1)
        self.assertTrue((output_dir / "link_modifications").is_dir())
        self.assertTrue((output_dir / "section_counts").is_dir())
        self.assertTrue((output_dir / "user_associations").is_dir())

    def test_main_prompts_to_map_unmapped_projects_for_selected_jira_site(self) -> None:
        output_dir = self.workspace / "mapped"
        with patch(
            "scripts.atlassian.export_archival_project_jira_csv.load_credentials"
        ), patch(
            "scripts.atlassian.export_archival_project_jira_csv.resolve_jira_base_url",
            return_value=self.jira_base_url,
        ), patch(
            "scripts.atlassian.export_archival_project_jira_csv.prompt_for_unmapped_jira_project",
            return_value=1,
        ) as prompt_for_mapping:
            main(
                [
                    "--project-gid",
                    "proj_1",
                    "--database",
                    str(self.db_path),
                    "--output-dir",
                    str(output_dir),
                    "--jira-site",
                    "sandbox",
                ]
            )

        self.assertEqual(
            prompt_for_mapping.call_args.args[1:],
            ([('proj_1', 'Archive Me')], 'sandbox'),
        )

    def test_main_keeps_duplicate_project_names_in_separate_exports(self) -> None:
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("proj_2", "Archive Me", self.sync_time.isoformat()),
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "task_3",
                "name": "Second Project Task",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "created_at": "2025-02-27T12:45:00Z",
                "modified_at": "2025-03-06T16:00:00Z",
                "completed": False,
                "memberships": [
                    {"project": {"gid": "proj_2", "name": "Archive Me"}}
                ],
            },
            self.sync_time,
        )
        self.connection.commit()
        output_dir = self.workspace / "duplicate-names"

        with patch(
            "scripts.atlassian.export_archival_project_jira_csv.load_credentials"
        ), patch(
            "scripts.atlassian.export_archival_project_jira_csv.resolve_jira_base_url",
            return_value=self.jira_base_url,
        ):
            main(
                [
                    "--project-gids",
                    "proj_1,proj_2",
                    "--database",
                    str(self.db_path),
                    "--output-dir",
                    str(output_dir),
                ]
            )

        project_files = sorted(
            path
            for path in (output_dir / "tasks").glob("Archive_Me-*-*_archival_tasks.csv")
            if not path.name.startswith("_Consolidated-")
        )
        self.assertEqual(len(project_files), 2)
        self.assertTrue(any("-proj_1-" in path.name for path in project_files))
        self.assertTrue(any("-proj_2-" in path.name for path in project_files))
        consolidated = next((output_dir / "tasks").glob("_Consolidated-*.csv"))
        with consolidated.open("r", newline="", encoding="utf-8") as handle:
            ids = {row["External issue ID"] for row in csv.DictReader(handle)}
        self.assertEqual(
            ids,
            {
                "proj_1::archival::epic",
                "task_1",
                "task_2",
                "proj_2::archival::epic",
                "task_3",
            },
        )

    def test_default_filename_includes_project_gid(self) -> None:
        first = build_default_filename("Archive Me", "proj_1")
        second = build_default_filename("Archive Me", "proj_2")
        self.assertIn("Archive_Me-proj_1-", first)
        self.assertIn("Archive_Me-proj_2-", second)
        self.assertNotEqual(first, second)

    def test_count_check_rejects_replaced_project_csv(self) -> None:
        path = self.workspace / "replaced.csv"
        path.write_text("task_gid\nonly-row\n", encoding="utf-8")
        with self.assertRaisesRegex(SystemExit, "expected 2 issues, found 1"):
            verify_csv_part_counts([CsvPart(path, 2)], "Per-project")

    def test_main_groups_default_output_by_export_id(self) -> None:
        grouped_dir = self.workspace / "exports" / "export-01-archive-wave"
        argv = [
            "--project-gid",
            "proj_1",
            "--database",
            str(self.db_path),
            "--export-id",
            "archive-wave",
        ]
        with patch(
            "scripts.atlassian.export_archival_project_jira_csv.load_credentials"
        ), patch(
            "scripts.atlassian.export_archival_project_jira_csv.resolve_jira_base_url",
            return_value=self.jira_base_url,
        ), patch(
            "scripts.atlassian.export_archival_project_jira_csv.next_export_dir",
            return_value=grouped_dir,
        ) as next_dir:
            main(argv)

        next_dir.assert_called_once_with(Path("exports"), "archive-wave")
        self.assertTrue((grouped_dir / "tasks").is_dir())
        self.assertTrue(
            any(
                path.name.startswith("_Consolidated-")
                for path in (grouped_dir / "tasks").glob("*.csv")
            )
        )

    def test_task_gid_scope_exports_only_requested_tasks(self) -> None:
        output_dir = self.workspace / "task-scope"
        argv = [
            "--task-gids",
            "task_2,task_2,missing_task",
            "--database",
            str(self.db_path),
            "--output-dir",
            str(output_dir),
        ]
        with patch(
            "scripts.atlassian.export_archival_project_jira_csv.load_credentials"
        ), patch(
            "scripts.atlassian.export_archival_project_jira_csv.resolve_jira_base_url",
            return_value=self.jira_base_url,
        ), self.assertLogs(
            "scripts.atlassian.export_archival_project_jira_csv", level="WARNING"
        ) as logs:
            main(argv)

        consolidated = next(
            path for path in (output_dir / "tasks").glob("_Consolidated-*.csv")
            if "manifest" not in path.name
        )
        with consolidated.open("r", newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        external_ids = {row["External issue ID"] for row in rows}
        self.assertEqual(external_ids, {"proj_1::archival::epic", "task_2"})
        self.assertIn("missing_task", "\n".join(logs.output))

    def test_task_gid_file_parser_deduplicates_and_preserves_order(self) -> None:
        path = self.workspace / "task-gids.csv"
        path.write_text("task_2\ntask_1, task_2\n", encoding="utf-8")
        self.assertEqual(
            parse_task_gids(task_gids_file=str(path)),
            ["task_2", "task_1"],
        )

    def test_resolve_task_projects_prefers_primary_membership(self) -> None:
        self.connection.execute(
            "UPDATE task_projects SET is_primary = 0 WHERE task_gid = ?",
            ("task_1",),
        )
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("proj_2", "Primary Project", self.sync_time.isoformat()),
        )
        self.connection.execute(
            "INSERT INTO task_projects "
            "(task_gid, project_gid, is_primary, synced_at) VALUES (?, ?, ?, ?)",
            ("task_1", "proj_2", 1, self.sync_time.isoformat()),
        )
        self.connection.commit()

        projects, filters = resolve_task_projects(
            self.connection,
            ["task_1"],
            logging.getLogger(__name__),
        )

        self.assertEqual(projects, [("proj_2", "Primary Project")])
        self.assertEqual(filters, {"proj_2": {"task_1"}})

    def test_export_id_records_tasks_and_skips_them_on_rerun(self) -> None:
        output_dir = self.workspace / "export-id"
        skipped_log = []
        first_path = export_archival_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            self.jira_base_url,
            filename="first.csv",
            export_id="archive-wave",
            skipped_log=skipped_log,
        )
        self.assertIsNotNone(first_path)

        second_path = export_archival_project(
            str(self.db_path),
            "proj_1",
            output_dir,
            self.jira_base_url,
            filename="second.csv",
            export_id="archive-wave",
            skipped_log=skipped_log,
        )
        self.assertIsNone(second_path)
        self.assertEqual(
            [entry["task_gid"] for entry in skipped_log],
            ["task_1", "task_2"],
        )
        recorded = list(
            self.connection.execute(
                "SELECT task_gid, project_gid FROM exported_tasks "
                "WHERE export_id = ? ORDER BY task_gid",
                ("archive-wave",),
            )
        )
        self.assertEqual([row["task_gid"] for row in recorded], ["task_1", "task_2"])
        self.assertEqual({row["project_gid"] for row in recorded}, {"proj_1"})

    def test_main_skips_empty_project_and_still_finalizes_batch(self) -> None:
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("proj_empty", "Empty Archive", self.sync_time.isoformat()),
        )
        self.connection.commit()
        output_dir = self.workspace / "mixed-batch"
        argv = [
            "--project-gids",
            "proj_1,proj_empty",
            "--database",
            str(self.db_path),
            "--output-dir",
            str(output_dir),
        ]
        with patch(
            "scripts.atlassian.export_archival_project_jira_csv.load_credentials"
        ), patch(
            "scripts.atlassian.export_archival_project_jira_csv.resolve_jira_base_url",
            return_value=self.jira_base_url,
        ), self.assertLogs(
            "scripts.atlassian.export_archival_project_jira_csv", level="WARNING"
        ) as logs:
            main(argv)

        task_files = list((output_dir / "tasks").glob("*.csv"))
        self.assertEqual(len(task_files), 2)
        self.assertTrue(any(path.name.startswith("_Consolidated-") for path in task_files))
        self.assertEqual(len(list((output_dir / "manifests").glob("*_manifest.csv"))), 1)
        self.assertIn("Empty Archive (proj_empty)", "\n".join(logs.output))

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
