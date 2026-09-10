import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database


def utc_now():
    return datetime(2025, 10, 31, tzinfo=timezone.utc)


class DatabaseTasksTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(db_path=str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tmpdir.cleanup()

    def test_upsert_task_persists_relationships_and_custom_fields(self) -> None:
        sync_time = utc_now()
        custom_field = {
            "gid": "cf_people",
            "name": "Reviewer",
            "resource_type": "custom_field",
            "resource_subtype": "enum",
            "enabled": True,
            "is_global_to_workspace": False,
        }
        self.database.upsert_custom_field(self.connection, custom_field, sync_time)

        task = {
            "gid": "task_1",
            "name": "Example task",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "notes": "Plain text",
            "html_notes": "<body>Plain text</body>",
            "permalink_url": "https://app.asana.com/0/1/2",
            "assignee": {"gid": "user_1"},
            "assignee_status": "upcoming",
            "created_by": {"gid": "user_creator"},
            "completed_by": {"gid": "user_completer"},
            "parent": {"gid": "parent_task"},
            "workspace": {"gid": "ws_1", "name": "Workspace One"},
            "created_at": "2025-10-31T00:00:00.000Z",
            "modified_at": "2025-10-31T01:00:00.000Z",
            "start_on": "2025-11-01",
            "due_on": "2025-11-10",
            "completed": False,
            "memberships": [
                {
                    "project": {"gid": "proj_1", "name": "Project One"},
                    "section": {"gid": "sec_1", "name": "To Do"},
                    "is_primary": True,
                }
            ],
            "custom_fields": [
                {
                    "gid": "cf_people",
                    "type": "multi_enum",
                    "display_value": "Person A, Person B",
                    "multi_enum_values": [
                        {"gid": "enum_a", "name": "Person A", "enabled": True},
                        {"gid": "enum_b", "name": "Person B", "enabled": True},
                    ],
                    "is_value_set": True,
                }
            ],
            "dependencies": [{"gid": "task_dep"}],
            "dependents": [{"gid": "task_depended"}],
            "followers": [{"gid": "user_follower"}],
        }

        self.database.upsert_task(self.connection, task, sync_time)
        self.connection.commit()

        stored_task = self.connection.execute("SELECT * FROM tasks WHERE gid = ?", ("task_1",)).fetchone()
        self.assertIsNotNone(stored_task)
        self.assertEqual(stored_task["name"], "Example task")
        self.assertEqual(stored_task["assignee_gid"], "user_1")
        self.assertEqual(stored_task["workspace_gid"], "ws_1")
        self.assertEqual(stored_task["is_subtask"], 1)
        self.assertIsNone(stored_task["hierarchy_depth"])

        membership_rows = list(
            self.connection.execute(
                "SELECT project_gid, section_gid, is_primary FROM task_projects WHERE task_gid = ?",
                ("task_1",),
            )
        )
        self.assertEqual(len(membership_rows), 1)
        self.assertEqual(membership_rows[0]["project_gid"], "proj_1")
        self.assertEqual(membership_rows[0]["section_gid"], "sec_1")
        self.assertEqual(membership_rows[0]["is_primary"], 1)

        custom_field_row = self.connection.execute(
            """
            SELECT enum_option_gid, display_value, is_value_set
            FROM task_custom_fields
            WHERE task_gid = ? AND custom_field_gid = ?
            """,
            ("task_1", "cf_people"),
        ).fetchone()
        self.assertIsNotNone(custom_field_row)
        self.assertIsNone(custom_field_row["enum_option_gid"])
        self.assertEqual(custom_field_row["display_value"], "Person A, Person B")
        self.assertEqual(custom_field_row["is_value_set"], 1)

        multi_rows = list(
            self.connection.execute(
                """
                SELECT enum_option_gid, position
                FROM task_custom_field_multi_enum
                WHERE task_gid = ? AND custom_field_gid = ?
                ORDER BY position
                """,
                ("task_1", "cf_people"),
            )
        )
        self.assertEqual(len(multi_rows), 2)
        self.assertEqual(multi_rows[0]["enum_option_gid"], "enum_a")
        self.assertEqual(multi_rows[0]["position"], 0)
        self.assertEqual(multi_rows[1]["enum_option_gid"], "enum_b")
        self.assertEqual(multi_rows[1]["position"], 1)

        relationships = list(
            self.connection.execute(
                "SELECT related_task_gid, relation_type FROM task_relationships WHERE task_gid = ? ORDER BY relation_type",
                ("task_1",),
            )
        )
        self.assertEqual({row["related_task_gid"] for row in relationships}, {"task_dep", "task_depended"})
        self.assertEqual(len(relationships), 2)

        followers = list(
            self.connection.execute(
                "SELECT user_gid FROM task_followers WHERE task_gid = ?",
                ("task_1",),
            )
        )
        self.assertEqual([row["user_gid"] for row in followers], ["user_follower"])

    def test_hierarchy_depth_for_root_and_child_tasks(self) -> None:
        sync_time = utc_now()
        root_task = {
            "gid": "root_task",
            "name": "Root",
            "resource_type": "task",
            "resource_subtype": "default_task",
        }
        child_task = {
            "gid": "child_task",
            "name": "Child",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "parent": {"gid": "root_task"},
        }

        self.database.upsert_task(self.connection, root_task, sync_time)
        self.database.upsert_task(self.connection, child_task, sync_time)
        self.connection.commit()

        root_row = self.connection.execute("SELECT hierarchy_depth FROM tasks WHERE gid = ?", ("root_task",)).fetchone()
        child_row = self.connection.execute("SELECT hierarchy_depth FROM tasks WHERE gid = ?", ("child_task",)).fetchone()

        self.assertEqual(root_row["hierarchy_depth"], 0)
        self.assertEqual(child_row["hierarchy_depth"], 1)

    def test_hierarchy_depth_updates_when_parent_relinks(self) -> None:
        sync_time = utc_now()
        root_task = {
            "gid": "root_task",
            "name": "Root",
            "resource_type": "task",
            "resource_subtype": "default_task",
        }
        parent_task = {
            "gid": "parent_task",
            "name": "Parent",
            "resource_type": "task",
            "resource_subtype": "default_task",
        }
        child_task = {
            "gid": "child_task",
            "name": "Child",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "parent": {"gid": "parent_task"},
        }

        self.database.upsert_task(self.connection, root_task, sync_time)
        self.database.upsert_task(self.connection, parent_task, sync_time)
        self.database.upsert_task(self.connection, child_task, sync_time)
        self.connection.commit()

        initial_child_depth = self.connection.execute(
            "SELECT hierarchy_depth FROM tasks WHERE gid = ?",
            ("child_task",),
        ).fetchone()["hierarchy_depth"]
        self.assertEqual(initial_child_depth, 1)

        relinked_parent = dict(parent_task)
        relinked_parent["parent"] = {"gid": "root_task"}
        self.database.upsert_task(self.connection, relinked_parent, sync_time)
        self.connection.commit()

        parent_depth = self.connection.execute(
            "SELECT hierarchy_depth FROM tasks WHERE gid = ?",
            ("parent_task",),
        ).fetchone()["hierarchy_depth"]
        child_depth = self.connection.execute(
            "SELECT hierarchy_depth FROM tasks WHERE gid = ?",
            ("child_task",),
        ).fetchone()["hierarchy_depth"]

        self.assertEqual(parent_depth, 1)
        self.assertEqual(child_depth, 2)

    def test_hierarchy_depth_backfills_when_parent_arrives_later(self) -> None:
        sync_time = utc_now()
        orphan_child = {
            "gid": "child_only",
            "name": "Child",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "parent": {"gid": "later_parent"},
        }

        self.database.upsert_task(self.connection, orphan_child, sync_time)

        child_row = self.connection.execute(
            "SELECT hierarchy_depth FROM tasks WHERE gid = ?",
            ("child_only",),
        ).fetchone()
        self.assertIsNone(child_row["hierarchy_depth"])

        parent_task = {
            "gid": "later_parent",
            "name": "Parent",
            "resource_type": "task",
            "resource_subtype": "default_task",
        }

        self.database.upsert_task(self.connection, parent_task, sync_time)
        self.connection.commit()

        refreshed_child_row = self.connection.execute(
            "SELECT hierarchy_depth FROM tasks WHERE gid = ?",
            ("child_only",),
        ).fetchone()
        self.assertEqual(refreshed_child_row["hierarchy_depth"], 1)

    def test_rebuild_flattened_task_hierarchy_assigns_top_level_parent(self) -> None:
        sync_time = utc_now()
        self.database.upsert_task(
            self.connection,
            {"gid": "root_task", "name": "Root", "resource_type": "task", "resource_subtype": "default_task"},
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "child_task",
                "name": "Child",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "parent": {"gid": "root_task"},
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "grandchild_task",
                "name": "Grandchild",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "parent": {"gid": "child_task"},
            },
            sync_time,
        )
        stats = self.database.rebuild_flattened_task_hierarchy(self.connection)
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["flattened"], 1)
        self.assertEqual(stats["unresolved"], 0)

        rows = {
            row["task_gid"]: row
            for row in self.connection.execute("SELECT * FROM task_hierarchy_flattened").fetchall()
        }
        self.assertIsNone(rows["root_task"]["flattened_parent_gid"])
        self.assertEqual(rows["root_task"]["flattened_depth"], 0)
        self.assertEqual(rows["child_task"]["flattened_parent_gid"], "root_task")
        self.assertEqual(rows["child_task"]["flattened_depth"], 1)
        self.assertEqual(rows["grandchild_task"]["flattened_parent_gid"], "root_task")
        self.assertEqual(rows["grandchild_task"]["flattened_depth"], 1)

    def test_rebuild_flattened_task_hierarchy_counts_unresolved_when_missing_root(self) -> None:
        sync_time = utc_now()
        self.database.upsert_task(
            self.connection,
            {"gid": "root_task", "name": "Root", "resource_type": "task", "resource_subtype": "default_task"},
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "child_task",
                "name": "Child",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "parent": {"gid": "root_task"},
            },
            sync_time,
        )
        self.database.upsert_task(
            self.connection,
            {
                "gid": "grandchild_task",
                "name": "Grandchild",
                "resource_type": "task",
                "resource_subtype": "default_task",
                "parent": {"gid": "child_task"},
            },
            sync_time,
        )
        # Simulate missing top-level depth metadata
        self.connection.execute("UPDATE tasks SET hierarchy_depth = NULL WHERE gid = 'root_task'")
        stats = self.database.rebuild_flattened_task_hierarchy(self.connection)
        self.assertEqual(stats["unresolved"], 1)
        row = self.connection.execute(
            "SELECT flattened_parent_gid, flattened_depth FROM task_hierarchy_flattened WHERE task_gid = ?",
            ("grandchild_task",),
        ).fetchone()
        self.assertIsNone(row["flattened_parent_gid"])
        self.assertEqual(row["flattened_depth"], 1)

    def test_recalculate_all_task_depths_reports_progress(self) -> None:
        sync_time = utc_now()
        root_task = {
            "gid": "root",
            "name": "Root",
            "resource_type": "task",
            "resource_subtype": "default_task",
        }
        child_task = {
            "gid": "child",
            "name": "Child",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "parent": {"gid": "root"},
        }

        self.database.upsert_task(self.connection, root_task, sync_time)
        self.database.upsert_task(self.connection, child_task, sync_time)

        events = []

        stats = self.database.recalculate_all_task_depths(
            self.connection,
            progress_callback=lambda processed, total: events.append((processed, total)),
        )

        self.assertEqual(stats["total"], 2)
        self.assertIn((2, 2), events)

    def test_recalculate_all_task_depths_incremental_progress_tracks_missing_tasks(self) -> None:
        sync_time = utc_now()
        root_task = {
            "gid": "root_inc",
            "name": "Root",
            "resource_type": "task",
            "resource_subtype": "default_task",
        }
        child_task = {
            "gid": "child_inc",
            "name": "Child",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "parent": {"gid": "root_inc"},
        }

        self.database.upsert_task(self.connection, root_task, sync_time)
        self.database.upsert_task(self.connection, child_task, sync_time)
        self.database.recalculate_all_task_depths(self.connection)

        self.connection.execute(
            "UPDATE tasks SET hierarchy_depth = NULL WHERE gid = ?",
            ("child_inc",),
        )
        self.connection.commit()

        events = []

        stats = self.database.recalculate_all_task_depths(
            self.connection,
            progress_callback=lambda processed, total: events.append((processed, total)),
            incremental=True,
        )

        self.assertEqual(stats["total"], 2)
        self.assertIn((1, 1), events)
        child_depth = self.connection.execute(
            "SELECT hierarchy_depth FROM tasks WHERE gid = ?",
            ("child_inc",),
        ).fetchone()["hierarchy_depth"]
        self.assertEqual(child_depth, 1)

    def test_recalculate_all_task_depths_incremental_no_missing_depths(self) -> None:
        sync_time = utc_now()
        task = {
            "gid": "complete_task",
            "name": "Complete",
            "resource_type": "task",
            "resource_subtype": "default_task",
        }

        self.database.upsert_task(self.connection, task, sync_time)
        self.database.recalculate_all_task_depths(self.connection)

        events = []
        stats = self.database.recalculate_all_task_depths(
            self.connection,
            progress_callback=lambda processed, total: events.append((processed, total)),
            incremental=True,
        )

        self.assertEqual(stats["total"], 1)
        self.assertEqual(events[-1], (0, 0))

    def test_is_personal_flag_updates_with_subsequent_upserts(self) -> None:
        sync_time = utc_now()
        personal_task = {
            "gid": "personal_task",
            "name": "Inbox item",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "workspace": {"gid": "ws_1", "name": "Workspace One"},
            "assignee": {"gid": "user_1", "name": "User One"},
            "memberships": [
                {
                    "project": {"gid": "personal_user_1", "name": "User One"},
                    "is_primary": True,
                }
            ],
            "is_personal": True,
        }
        self.database.upsert_task(self.connection, personal_task, sync_time)
        self.connection.commit()

        stored = self.connection.execute(
            "SELECT is_personal FROM tasks WHERE gid = ?",
            ("personal_task",),
        ).fetchone()
        self.assertEqual(stored["is_personal"], 1)

        project_task = {
            "gid": "personal_task",
            "name": "Inbox item",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "workspace": {"gid": "ws_1", "name": "Workspace One"},
            "assignee": {"gid": "user_1", "name": "User One"},
            "memberships": [
                {
                    "project": {"gid": "proj_real", "name": "Real Project"},
                    "is_primary": True,
                }
            ],
        }
        self.database.upsert_task(self.connection, project_task, sync_time)
        self.connection.commit()

        updated = self.connection.execute(
            "SELECT is_personal FROM tasks WHERE gid = ?",
            ("personal_task",),
        ).fetchone()
        self.assertEqual(updated["is_personal"], 0)

    def test_upsert_task_stores_recurrence_when_present(self) -> None:
        sync_time = utc_now()
        task = {
            "gid": "recurring_task",
            "name": "Weekly sync",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "recurrence": {"type": "weekly", "data": {"frequency": 1}},
        }
        self.database.upsert_task(self.connection, task, sync_time)
        self.connection.commit()

        row = self.connection.execute(
            "SELECT recurrence, recurrence_checked_at FROM tasks WHERE gid = ?",
            ("recurring_task",),
        ).fetchone()
        self.assertEqual(json.loads(row["recurrence"]), {"type": "weekly", "data": {"frequency": 1}})
        self.assertEqual(row["recurrence_checked_at"], sync_time.isoformat())

    def test_upsert_task_null_type_recurrence_serializes_to_none(self) -> None:
        sync_time = utc_now()
        task = {
            "gid": "non_recurring_task",
            "name": "One-off",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "recurrence": {"type": None, "data": None},
        }
        self.database.upsert_task(self.connection, task, sync_time)
        self.connection.commit()

        row = self.connection.execute(
            "SELECT recurrence, recurrence_checked_at FROM tasks WHERE gid = ?",
            ("non_recurring_task",),
        ).fetchone()
        self.assertIsNone(row["recurrence"])
        # The `recurrence` key was present on the payload, so it was still "checked".
        self.assertEqual(row["recurrence_checked_at"], sync_time.isoformat())

    def test_upsert_task_never_type_recurrence_is_stored_verbatim(self) -> None:
        # Asana's real non-recurring sentinel is the literal string "never", not null.
        # It's stored as-is (not nulled out) so the value itself doubles as proof the
        # task was checked; report-layer consumers filter it via is_recurring().
        sync_time = utc_now()
        task = {
            "gid": "non_recurring_task_2",
            "name": "One-off",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "recurrence": {"type": "never", "data": None},
        }
        self.database.upsert_task(self.connection, task, sync_time)
        self.connection.commit()

        row = self.connection.execute(
            "SELECT recurrence, recurrence_checked_at FROM tasks WHERE gid = ?",
            ("non_recurring_task_2",),
        ).fetchone()
        self.assertEqual(json.loads(row["recurrence"]), {"type": "never", "data": None})
        self.assertEqual(row["recurrence_checked_at"], sync_time.isoformat())

    def test_upsert_task_without_recurrence_key_leaves_columns_null(self) -> None:
        sync_time = utc_now()
        task = {
            "gid": "never_checked_task",
            "name": "Legacy synced task",
            "resource_type": "task",
            "resource_subtype": "default_task",
        }
        self.database.upsert_task(self.connection, task, sync_time)
        self.connection.commit()

        row = self.connection.execute(
            "SELECT recurrence, recurrence_checked_at FROM tasks WHERE gid = ?",
            ("never_checked_task",),
        ).fetchone()
        self.assertIsNone(row["recurrence"])
        self.assertIsNone(row["recurrence_checked_at"])

    def test_update_task_recurrence_creates_stub_row_when_task_missing(self) -> None:
        checked_at = utc_now()
        self.database.update_task_recurrence(
            self.connection,
            "brand_new_task",
            "Newly discovered",
            {"type": "daily", "data": {"frequency": 2}},
            checked_at,
        )
        self.connection.commit()

        row = self.connection.execute(
            "SELECT name, recurrence, recurrence_checked_at, synced_at FROM tasks WHERE gid = ?",
            ("brand_new_task",),
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Newly discovered")
        self.assertEqual(json.loads(row["recurrence"]), {"type": "daily", "data": {"frequency": 2}})
        self.assertEqual(row["recurrence_checked_at"], checked_at.isoformat())
        self.assertEqual(row["synced_at"], checked_at.isoformat())

    def test_update_task_recurrence_updates_existing_row_without_touching_other_columns(self) -> None:
        sync_time = utc_now()
        task = {
            "gid": "existing_task",
            "name": "Existing",
            "resource_type": "task",
            "resource_subtype": "default_task",
            "notes": "Original notes",
        }
        self.database.upsert_task(self.connection, task, sync_time)
        self.connection.commit()

        checked_at = datetime(2025, 11, 1, tzinfo=timezone.utc)
        self.database.update_task_recurrence(
            self.connection,
            "existing_task",
            "Existing",
            {"type": "monthly", "data": {"frequency": 1}},
            checked_at,
        )
        self.connection.commit()

        row = self.connection.execute(
            "SELECT name, notes, recurrence, recurrence_checked_at, synced_at FROM tasks WHERE gid = ?",
            ("existing_task",),
        ).fetchone()
        self.assertEqual(row["name"], "Existing")
        self.assertEqual(row["notes"], "Original notes")
        self.assertEqual(json.loads(row["recurrence"]), {"type": "monthly", "data": {"frequency": 1}})
        self.assertEqual(row["recurrence_checked_at"], checked_at.isoformat())
        # synced_at should remain from the original upsert, not the recurrence-only update.
        self.assertEqual(row["synced_at"], sync_time.isoformat())

    def test_update_task_recurrence_null_type_serializes_to_none(self) -> None:
        checked_at = utc_now()
        self.database.update_task_recurrence(
            self.connection,
            "non_recurring_stub",
            "Stub",
            {"type": None, "data": None},
            checked_at,
        )
        self.connection.commit()

        row = self.connection.execute(
            "SELECT recurrence, recurrence_checked_at FROM tasks WHERE gid = ?",
            ("non_recurring_stub",),
        ).fetchone()
        self.assertIsNone(row["recurrence"])
        self.assertEqual(row["recurrence_checked_at"], checked_at.isoformat())
