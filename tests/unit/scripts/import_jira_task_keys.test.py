from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from src.lib.database import Database

from scripts import import_jira_task_keys


def sync_time() -> str:
    return datetime(2025, 3, 1, tzinfo=timezone.utc).isoformat()


class ImportJiraTaskKeysTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self._seed_projects_and_tasks()

    def tearDown(self) -> None:
        self.connection.close()

    def _seed_projects_and_tasks(self) -> None:
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("proj_1", "Example", sync_time()),
        )
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("proj_2", "Second", sync_time()),
        )
        for task_gid in ("task_1", "task_2", "task_other"):
            self.connection.execute(
                "INSERT INTO tasks (gid, name, synced_at) VALUES (?, ?, ?)",
                (task_gid, f"Task {task_gid}", sync_time()),
            )
        self.connection.executemany(
            "INSERT INTO task_projects (task_gid, project_gid, synced_at) VALUES (?, ?, ?)",
            [
                ("task_1", "proj_1", sync_time()),
                ("task_2", "proj_1", sync_time()),
                ("task_other", "proj_2", sync_time()),
            ],
        )
        self.connection.commit()

    def _write_csv(
        self,
        rows: list[tuple[str, str]],
        task_header: str | None = None,
        jira_header: str | None = None,
        bom: bool = False,
    ) -> Path:
        csv_path = self.workspace / "jira_keys.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            header = [
                jira_header or import_jira_task_keys.DEFAULT_JIRA_COLUMN,
                task_header or import_jira_task_keys.DEFAULT_TASK_COLUMN,
            ]
            if bom:
                header[0] = "\ufeff" + header[0]
            writer.writerow(header)
            writer.writerows(rows)
        return csv_path

    def test_import_writes_mappings(self) -> None:
        csv_path = self._write_csv([
            ("ENG-1", "task_1"),
            ("ENG-2", "task_2"),
        ])

        summary = import_jira_task_keys.import_jira_keys_from_csv(
            database_path=str(self.db_path),
            csv_path=csv_path,
        )

        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["written"], 2)
        rows = self.connection.execute(
            "SELECT jira_key, task_gid FROM task_jira_mappings ORDER BY jira_key",
        ).fetchall()
        self.assertEqual([(row["jira_key"], row["task_gid"]) for row in rows], [("ENG-1", "task_1"), ("ENG-2", "task_2")])

    def test_import_skips_tasks_not_present(self) -> None:
        csv_path = self._write_csv([
            ("ENG-1", "task_1"),
            ("ENG-999", "missing_task"),
        ])

        summary = import_jira_task_keys.import_jira_keys_from_csv(
            database_path=str(self.db_path),
            csv_path=csv_path,
        )

        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["written"], 1)
        rows = self.connection.execute(
            "SELECT jira_key FROM task_jira_mappings ORDER BY jira_key",
        ).fetchall()
        self.assertEqual([row["jira_key"] for row in rows], ["ENG-1"])

    def test_import_rejects_duplicate_keys_for_different_tasks(self) -> None:
        csv_existing = self._write_csv([( "ENG-1", "task_other" )])
        import_jira_task_keys.import_jira_keys_from_csv(
            database_path=str(self.db_path),
            csv_path=csv_existing,
        )

        csv_path = self._write_csv([( "ENG-1", "task_1" )])
        with self.assertRaises(ValueError):
            import_jira_task_keys.import_jira_keys_from_csv(
                database_path=str(self.db_path),
                csv_path=csv_path,
            )

    def test_import_supports_custom_task_header(self) -> None:
        custom_header = "Custom field (Asana Task ID)"
        csv_path = self._write_csv(
            rows=[("ENG-1", "task_1")],
            task_header=custom_header,
        )

        summary = import_jira_task_keys.import_jira_keys_from_csv(
            database_path=str(self.db_path),
            csv_path=csv_path,
            task_column=custom_header,
        )

        self.assertEqual(summary["written"], 1)
        row = self.connection.execute(
            "SELECT jira_key, task_gid FROM task_jira_mappings",
        ).fetchone()
        self.assertEqual((row["jira_key"], row["task_gid"]), ("ENG-1", "task_1"))

    def test_import_supports_custom_jira_header(self) -> None:
        custom_header = "Issue Key"
        csv_path = self._write_csv(
            rows=[("ENG-1", "task_1")],
            jira_header=custom_header,
        )

        summary = import_jira_task_keys.import_jira_keys_from_csv(
            database_path=str(self.db_path),
            csv_path=csv_path,
            jira_column=custom_header,
        )

        self.assertEqual(summary["written"], 1)
        row = self.connection.execute(
            "SELECT jira_key, task_gid FROM task_jira_mappings",
        ).fetchone()
        self.assertEqual((row["jira_key"], row["task_gid"]), ("ENG-1", "task_1"))

    def test_import_handles_bom_in_header(self) -> None:
        csv_path = self._write_csv(
            rows=[("ENG-1", "task_1")],
            bom=True,
        )

        summary = import_jira_task_keys.import_jira_keys_from_csv(
            database_path=str(self.db_path),
            csv_path=csv_path,
        )

        self.assertEqual(summary["written"], 1)
