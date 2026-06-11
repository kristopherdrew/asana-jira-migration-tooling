import tempfile
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database, synced_at_timestamp


class DatabaseCustomFieldsTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        db_path = Path(self.tmpdir.name) / "asana.sqlite"
        self.database = Database(db_path=str(db_path))
        self.connection = self.database.connect()
        self.database.initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.tmpdir.cleanup()

    def test_upsert_custom_field_persists_definition_and_enum_options(self) -> None:
        custom_field = {
            "gid": "cf_1",
            "name": "Priority",
            "resource_type": "custom_field",
            "resource_subtype": "enum",
            "description": "Priority level",
            "enabled": True,
            "is_global_to_workspace": True,
            "workspace": {"gid": "ws_1", "name": "Workspace One"},
            "format": "enum",
            "precision": None,
            "currency_code": None,
            "custom_label": None,
            "custom_label_position": None,
            "is_formula_field": False,
            "modified_at": "2023-01-01T00:00:00.000Z",
            "enum_options": [
                {"gid": "opt_high", "name": "High", "color": "red", "enabled": True},
                {"gid": "opt_low", "name": "Low", "color": "green", "enabled": True},
            ],
        }

        sync_time = synced_at_timestamp()
        self.database.upsert_custom_field(self.connection, custom_field, sync_time)
        self.connection.commit()

        row = self.connection.execute("SELECT * FROM custom_fields WHERE gid = ?", ("cf_1",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Priority")
        self.assertEqual(row["resource_subtype"], "enum")
        self.assertEqual(row["is_global_to_workspace"], 1)
        self.assertEqual(row["workspace_gid"], "ws_1")
        self.assertEqual(row["is_formula_field"], 0)

        options = list(
            self.connection.execute(
                """
                SELECT enum_option_gid, name, color, enabled, position
                FROM custom_field_enum_options
                WHERE custom_field_gid = ?
                ORDER BY position
                """,
                ("cf_1",),
            )
        )
        self.assertEqual(len(options), 2)
        self.assertEqual(options[0]["enum_option_gid"], "opt_high")
        self.assertEqual(options[0]["enabled"], 1)
        self.assertEqual(options[1]["enum_option_gid"], "opt_low")
        self.assertEqual(options[1]["position"], 1)

    def test_upsert_custom_field_replaces_enum_options(self) -> None:
        custom_field = {
            "gid": "cf_replace",
            "name": "Status",
            "resource_type": "custom_field",
            "resource_subtype": "enum",
            "enum_options": [{"gid": "opt_old", "name": "Old", "color": "blue", "enabled": True}],
        }
        sync_time = synced_at_timestamp()
        self.database.upsert_custom_field(self.connection, custom_field, sync_time)
        self.connection.commit()

        updated_field = {
            "gid": "cf_replace",
            "name": "Status",
            "resource_type": "custom_field",
            "resource_subtype": "enum",
            "enum_options": [{"gid": "opt_new", "name": "New", "color": "yellow", "enabled": False}],
        }
        later_sync = synced_at_timestamp()
        self.database.upsert_custom_field(self.connection, updated_field, later_sync)
        self.connection.commit()

        options = list(
            self.connection.execute(
                "SELECT enum_option_gid FROM custom_field_enum_options WHERE custom_field_gid = ?",
                ("cf_replace",),
            )
        )
        self.assertEqual(len(options), 1)
        self.assertEqual(options[0]["enum_option_gid"], "opt_new")
