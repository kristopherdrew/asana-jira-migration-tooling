import tempfile
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database
from src.lib.table_metadata import GROUP_ORDER, TABLE_METADATA


class TableMetadataRegistryTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tmpdir.cleanup()

    def _schema_objects(self) -> set:
        rows = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return {row[0] for row in rows}

    def test_registry_matches_schema_exactly(self) -> None:
        schema = self._schema_objects()
        registry = set(TABLE_METADATA)
        missing_from_registry = schema - registry
        stale_in_registry = registry - schema
        self.assertEqual(
            missing_from_registry,
            set(),
            f"Tables/views in the schema but missing from TABLE_METADATA: {sorted(missing_from_registry)}",
        )
        self.assertEqual(
            stale_in_registry,
            set(),
            f"Entries in TABLE_METADATA that no longer exist in the schema: {sorted(stale_in_registry)}",
        )

    def test_every_entry_uses_a_known_group(self) -> None:
        for name, meta in TABLE_METADATA.items():
            self.assertIn(meta.group, GROUP_ORDER, f"{name} uses unlisted group '{meta.group}'")

    def test_entries_have_required_context(self) -> None:
        for name, meta in TABLE_METADATA.items():
            self.assertTrue(meta.purpose, f"{name} missing purpose")
            self.assertTrue(meta.source, f"{name} missing source")
            self.assertTrue(meta.scripts, f"{name} missing populating scripts")
            self.assertTrue(meta.writer, f"{name} missing writer")
            self.assertTrue(meta.write_semantics, f"{name} missing write semantics")
