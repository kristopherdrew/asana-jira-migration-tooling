from __future__ import annotations

import io
import tempfile
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database

import scripts.manage_export_ids as script


class ManageExportIdsScriptTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(db_path=str(self.db_path))
        self.connection = self.database.connect()
        self.addCleanup(self.connection.close)
        self.database.initialize(self.connection)
        self._seed()

    def _seed(self) -> None:
        synced_at = datetime(2025, 1, 1, tzinfo=timezone.utc).isoformat()
        self.connection.execute(
            "INSERT INTO projects (gid, name, synced_at) VALUES (?, ?, ?)",
            ("proj_1", "Launch Plan", synced_at),
        )
        self.connection.executemany(
            """
            INSERT INTO exported_tasks (export_id, task_gid, project_gid, exported_at)
            VALUES (?, ?, ?, ?)
            """,
            [
                ("wave_one", "task_1", "proj_1", "2025-01-01T00:00:00+00:00"),
                ("wave_one", "task_2", "proj_1", "2025-01-02T00:00:00+00:00"),
                ("wave_two", "task_3", "proj_1", "2025-01-03T00:00:00+00:00"),
            ],
        )
        self.connection.commit()

    def _run(self, *args: str) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            script.main(["--database", str(self.db_path), "--log-level", "CRITICAL", *args])
        return output.getvalue()

    def test_list_prints_export_id_summary(self) -> None:
        output = self._run("--list")

        self.assertIn("Export ID | Tasks | Projects", output)
        self.assertIn("wave_one | 2 | 1", output)
        self.assertIn("wave_two | 1 | 1", output)

    def test_show_prints_project_breakdown(self) -> None:
        output = self._run("--show", "wave_one")

        self.assertIn("Export ID: wave_one", output)
        self.assertIn("Tasks: 2", output)
        self.assertIn("Launch Plan (proj_1): 2 tasks", output)

    def test_show_unknown_export_id_exits(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            self._run("--show", "missing")

        self.assertIn("Export ID not found: missing", str(raised.exception))

    def test_clear_one_export_id(self) -> None:
        output = self._run("--clear-export-id", "wave_one")

        self.assertIn("Cleared 2 recorded task mapping(s)", output)
        rows = self.connection.execute(
            "SELECT export_id FROM exported_tasks ORDER BY export_id"
        ).fetchall()
        self.assertEqual([row["export_id"] for row in rows], ["wave_two"])

    def test_clear_all_export_ids(self) -> None:
        output = self._run("--clear-all")

        self.assertIn("Cleared 3 recorded export-ID task mapping(s)", output)
        row = self.connection.execute("SELECT COUNT(*) AS total FROM exported_tasks").fetchone()
        self.assertEqual(row["total"], 0)
