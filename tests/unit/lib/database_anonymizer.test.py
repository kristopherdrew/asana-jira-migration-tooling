from __future__ import annotations

import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database, synced_at_timestamp
from src.lib.database_anonymizer import (
    anonymize_database,
    column_anonymization_policy,
    default_output_path,
)


class DatabaseAnonymizerTest(TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.workspace = Path(self.tmp_dir.name)
        self.source_path = self.workspace / "asana.sqlite"
        self.output_path = self.workspace / "asana_anonymized.sqlite"
        self.database = Database(str(self.source_path))
        self.connection = self.database.connect()
        self.connection.row_factory = sqlite3.Row
        self.database.initialize(self.connection)
        self._seed()

    def tearDown(self) -> None:
        self.connection.close()

    def _seed(self) -> None:
        synced_at = synced_at_timestamp().isoformat()
        self.connection.execute(
            """
            INSERT INTO users (gid, name, resource_type, email, is_active, user_task_list_gid, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("user_1", "Ada Lovelace", "user", "ada@example.com", 1, "utl_1", synced_at),
        )
        self.connection.execute(
            """
            INSERT INTO teams (gid, name, description, organization_gid, is_public, synced_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("team_1", "Secret Team", "Internal client launch", "org_1", 0, synced_at),
        )
        self.connection.execute(
            """
            INSERT INTO projects (
                gid, name, permalink_url, owner_gid, owner_name, team_gid, team_name,
                notes, html_notes, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "project_1",
                "Confidential Project",
                "https://app.asana.com/0/project_1/list",
                "user_1",
                "Ada Lovelace",
                "team_1",
                "Secret Team",
                "Discuss merger timeline",
                "<body>Discuss merger timeline</body>",
                synced_at,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO tasks (
                gid, name, notes, html_notes, permalink_url, assignee_gid, created_by_gid,
                workspace_gid, synced_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "task_1",
                "Call customer",
                "Email ada@example.com before visiting https://private.example.test/doc",
                "<body>Email Ada</body>",
                "https://app.asana.com/0/project_1/task_1",
                "user_1",
                "user_1",
                "workspace_1",
                synced_at,
            ),
        )
        self.connection.execute(
            """
            INSERT INTO task_projects (task_gid, project_gid, section_gid, section_name, synced_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("task_1", "project_1", "section_1", "Private Section", synced_at),
        )
        self.connection.execute(
            """
            INSERT INTO task_comments (gid, task_gid, created_by_gid, text, html_text, synced_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("comment_1", "task_1", "user_1", "Customer said yes", "<body>Customer said yes</body>", synced_at),
        )
        self.connection.execute(
            """
            INSERT INTO user_atlassian_accounts (
                user_gid, atlassian_account_id, atlassian_email, atlassian_status, synced_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            ("user_1", "atl_ada", "ada@company.test", "active", synced_at),
        )
        self.connection.execute(
            """
            INSERT INTO task_jira_mappings (task_gid, jira_key, synced_at, jira_site_key)
            VALUES (?, ?, ?, ?)
            """,
            ("task_1", "CLIENT-123", synced_at, "client-prod"),
        )
        self.connection.commit()

    def _connect_output(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.output_path)
        conn.row_factory = sqlite3.Row
        self.addCleanup(conn.close)
        return conn

    def test_anonymize_database_replaces_sensitive_values_and_preserves_joins(self) -> None:
        report = anonymize_database(self.source_path, self.output_path, salt="test-salt")

        self.assertTrue(self.output_path.exists())
        self.assertEqual(report.remaining_findings, ())
        self.assertGreater(report.updated_values, 0)

        conn = self._connect_output()
        user = conn.execute("SELECT * FROM users").fetchone()
        project = conn.execute("SELECT * FROM projects").fetchone()
        task = conn.execute("SELECT * FROM tasks").fetchone()
        membership = conn.execute("SELECT * FROM task_projects").fetchone()
        comment = conn.execute("SELECT * FROM task_comments").fetchone()
        jira = conn.execute("SELECT * FROM task_jira_mappings").fetchone()
        atlassian = conn.execute("SELECT * FROM user_atlassian_accounts").fetchone()

        self.assertNotEqual(user["gid"], "user_1")
        self.assertEqual(project["owner_gid"], user["gid"])
        self.assertEqual(task["assignee_gid"], user["gid"])
        self.assertEqual(membership["task_gid"], task["gid"])
        self.assertEqual(membership["project_gid"], project["gid"])
        self.assertEqual(comment["task_gid"], task["gid"])
        self.assertEqual(jira["task_gid"], task["gid"])
        self.assertEqual(atlassian["user_gid"], user["gid"])

        self.assertTrue(user["email"].endswith("@example.invalid"))
        self.assertNotIn("Ada", user["name"])
        self.assertNotIn("Confidential", project["name"])
        self.assertNotIn("customer", task["notes"].lower())
        self.assertNotIn("ada@example.com", task["notes"])
        self.assertTrue(task["permalink_url"].startswith("https://example.invalid/"))
        self.assertRegex(jira["jira_key"], r"^JIRA-\d{6}$")

    def test_repeatable_with_same_salt(self) -> None:
        first_output = self.workspace / "first.sqlite"
        second_output = self.workspace / "second.sqlite"

        anonymize_database(self.source_path, first_output, salt="stable")
        anonymize_database(self.source_path, second_output, salt="stable")

        first = sqlite3.connect(first_output)
        second = sqlite3.connect(second_output)
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first_gid = first.execute("SELECT gid FROM users").fetchone()[0]
        second_gid = second.execute("SELECT gid FROM users").fetchone()[0]
        self.assertEqual(first_gid, second_gid)

    def test_refuses_to_overwrite_without_flag(self) -> None:
        self.output_path.write_text("already here", encoding="utf-8")

        with self.assertRaises(FileExistsError):
            anonymize_database(self.source_path, self.output_path)

    def test_default_output_path_adds_anonymized_suffix(self) -> None:
        self.assertEqual(default_output_path(self.source_path), self.workspace / "asana_anonymized.sqlite")

    def test_column_anonymization_policy_classifies_representative_columns(self) -> None:
        cases = {
            "gid": ("yes", "identifier", "`gid_<hash>`"),
            "user_gid": ("yes", "identifier", "`gid_<hash>`"),
            "email": ("yes", "email", "`user_<hash>@example.invalid`"),
            "jira_key": ("yes", "Jira key", "`JIRA-<number>`"),
            "name": ("yes", "name", "`<Column Label> <hash>`"),
            "notes": ("yes", "text", "`redacted <hash>`"),
            "scope_json": ("yes", "JSON text", "valid JSON with string values redacted"),
            "synced_at": ("no", "none", "preserved"),
            "created_at": ("no", "none", "preserved"),
            "num_tasks": ("no", "none", "preserved"),
            "completed": ("no", "none", "preserved"),
            "status": ("no", "none", "preserved"),
        }

        for column, (anonymized, method, shape) in cases.items():
            with self.subTest(column=column):
                policy = column_anonymization_policy(column)
                self.assertEqual("yes" if policy.anonymized else "no", anonymized)
                self.assertEqual(policy.method, method)
                self.assertEqual(policy.replacement_shape, shape)
