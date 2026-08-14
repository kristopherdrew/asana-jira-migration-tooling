from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts import export_jira_migration_validation as report
from src.lib.database import Database
from src.tasks.jira_migration_validation import fetch_task_validation


class JiraMigrationValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = Path(self.temp.name) / "asana.sqlite"
        self.database = Database(str(self.db_path))
        now = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as connection:
            self.database.initialize(connection)
            connection.execute("INSERT INTO teams (gid, name, synced_at) VALUES ('team1', 'Team One', ?)", (now,))
            connection.execute("INSERT INTO projects (gid, name, team_gid, synced_at) VALUES ('p1', 'One', 'team1', ?)", (now,))
            connection.executemany(
                "INSERT INTO tasks (gid, name, completed, synced_at) VALUES (?, ?, ?, ?)",
                [("t1", "Found", 0, now), ("t2", "Missing", 1, now)],
            )
            connection.executemany(
                "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, 'p1', ?)",
                [("t1", now), ("t2", now)],
            )
            connection.execute(
                "INSERT INTO jira_project_mappings (project_gid, jira_project_key, synced_at, jira_site_key) VALUES ('p1', 'ONE', ?, 'site')",
                (now,),
            )
            connection.execute(
                "INSERT INTO task_jira_mappings (task_gid, jira_key, synced_at, jira_site_key) VALUES ('t1', 'ONE-1', ?, 'site')",
                (now,),
            )
            connection.commit()

    def test_fetches_coverage_without_double_counting(self) -> None:
        with self.database.connect() as connection:
            site, rows, summary = fetch_task_validation(connection)
        self.assertEqual(site, "site")
        self.assertEqual(summary.total, 2)
        self.assertEqual(summary.found, 1)
        self.assertEqual(summary.missing, 1)
        self.assertEqual(summary.coverage_percent, 50.0)
        self.assertEqual([row["task_gid"] for row in rows if not row["jira_key"]], ["t2"])

    def test_wave_scope_uses_explicit_projects(self) -> None:
        with self.database.connect() as connection:
            site, rows, summary = fetch_task_validation(connection, project_gids=["p1"])
        self.assertEqual(site, "site")
        self.assertEqual(summary.total, 2)
        self.assertEqual({row["task_gid"] for row in rows}, {"t1", "t2"})

    def test_wave_scope_requires_project_association(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as connection:
            connection.execute("INSERT INTO projects (gid, name, synced_at) VALUES ('p2', 'Two', ?)", (now,))
            connection.commit()
            with self.assertRaisesRegex(ValueError, "no Jira Space association"):
                fetch_task_validation(connection, "site", ["p2"])

    def test_team_scope_validates_associated_team_tasks(self) -> None:
        with self.database.connect() as connection:
            site, rows, summary = fetch_task_validation(connection, team_gid="team1")
        self.assertEqual(site, "site")
        self.assertEqual(summary.total, 2)
        self.assertEqual({row["task_gid"] for row in rows}, {"t1", "t2"})

    def test_exact_task_scope(self) -> None:
        with self.database.connect() as connection:
            _site, rows, summary = fetch_task_validation(connection, task_gids=["t2"])
        self.assertEqual(summary.total, 1)
        self.assertEqual(summary.missing, 1)
        self.assertEqual([row["task_gid"] for row in rows], ["t2"])

    def test_exact_task_scope_rejects_unknown_task(self) -> None:
        with self.database.connect() as connection:
            with self.assertRaisesRegex(ValueError, "not found"):
                fetch_task_validation(connection, "site", task_gids=["nope"])

    def test_exports_missing_csv_and_reimport_gid_list(self) -> None:
        output = Path(self.temp.name) / "exports"
        report.main([
            "--database", str(self.db_path), "--jira-site", "site",
            "--export-missing", "--output-dir", str(output), "--filename", "missing.csv",
        ])
        with (output / "missing.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual([row["task_gid"] for row in rows], ["t2"])
        self.assertEqual((output / "missing_task_gids.txt").read_text(encoding="utf-8"), "t2\n")

    def test_project_gid_file_scopes_cli_report(self) -> None:
        project_file = Path(self.temp.name) / "wave.txt"
        project_file.write_text("p1\n", encoding="utf-8")
        report.main([
            "--database", str(self.db_path), "--jira-site", "site",
            "--project-gids-file", str(project_file),
        ])

    def test_requires_site_when_associations_span_sites(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.database.connect() as connection:
            connection.execute("INSERT INTO projects (gid, name, synced_at) VALUES ('p2', 'Two', ?)", (now,))
            connection.execute(
                "INSERT INTO jira_project_mappings (project_gid, jira_project_key, synced_at, jira_site_key) VALUES ('p2', 'TWO', ?, 'other')",
                (now,),
            )
            connection.commit()
            with self.assertRaisesRegex(ValueError, "Multiple Jira sites"):
                fetch_task_validation(connection)


if __name__ == "__main__":
    unittest.main()
