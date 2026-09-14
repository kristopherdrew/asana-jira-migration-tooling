from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.anonymize_database import main
from src.lib.database import Database, synced_at_timestamp


class AnonymizeDatabaseScriptTest(TestCase):
    def setUp(self) -> None:
        self.tmp_dir = TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.workspace = Path(self.tmp_dir.name)
        self.source = self.workspace / "asana.sqlite"
        self.output = self.workspace / "safe.sqlite"
        database = Database(str(self.source))
        with database.connect() as connection:
            database.initialize(connection)
            connection.execute(
                """
                INSERT INTO users (gid, name, resource_type, email, synced_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                ("user_1", "Private User", "user", "private@example.com", synced_at_timestamp().isoformat()),
            )
            connection.commit()

    def test_main_writes_anonymized_copy(self) -> None:
        main(
            [
                "--database",
                str(self.source),
                "--output",
                str(self.output),
                "--salt",
                "script-test",
                "--log-level",
                "ERROR",
            ]
        )

        self.assertTrue(self.output.exists())

    def test_main_refuses_in_place_output(self) -> None:
        with self.assertRaises(SystemExit):
            main(["--database", str(self.source), "--output", str(self.source), "--log-level", "ERROR"])
