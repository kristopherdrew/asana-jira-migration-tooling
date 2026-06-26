from __future__ import annotations

from datetime import datetime, timezone
from unittest import TestCase

from src.lib.database import Database


class DatabasePortfoliosTest(TestCase):
    def setUp(self) -> None:
        self.database = Database(":memory:")
        self.connection = self.database.connect()
        self.database.initialize(self.connection)
        self.sync_time = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self.connection.close()

    def test_upsert_portfolio_flattens_nested_objects(self) -> None:
        portfolio = {
            "gid": "pf_1",
            "name": "Roadmap",
            "color": "blue",
            "permalink_url": "https://app.asana.com/0/portfolio/pf_1",
            "public": True,
            "owner": {"gid": "u_owner", "name": "Owner One"},
            "created_by": {"gid": "u_creator", "name": "Creator One"},
            "created_at": "2025-12-01T00:00:00.000Z",
            "start_on": "2026-01-01",
            "due_on": "2026-06-30",
            "workspace": {"gid": "ws_1"},
            "current_status_update": {"status_type": "on_track", "title": "All good"},
        }
        self.database.upsert_portfolio(self.connection, portfolio, self.sync_time)

        row = self.connection.execute("SELECT * FROM portfolios WHERE gid = 'pf_1'").fetchone()
        self.assertEqual(row["name"], "Roadmap")
        self.assertEqual(row["public"], 1)
        self.assertEqual(row["owner_gid"], "u_owner")
        self.assertEqual(row["owner_name"], "Owner One")
        self.assertEqual(row["created_by_gid"], "u_creator")
        self.assertEqual(row["workspace_gid"], "ws_1")
        self.assertEqual(row["current_status_type"], "on_track")
        self.assertEqual(row["current_status_title"], "All good")

    def test_upsert_portfolio_writes_custom_field_settings_when_present(self) -> None:
        portfolio = {
            "gid": "pf_1",
            "name": "Roadmap",
            "custom_field_settings": [
                {
                    "is_important": True,
                    "custom_field": {
                        "gid": "cf_1",
                        "name": "Priority",
                        "type": "enum",
                        "is_global_to_workspace": True,
                    },
                }
            ],
        }
        self.database.upsert_portfolio(self.connection, portfolio, self.sync_time)

        rows = self.connection.execute(
            "SELECT * FROM portfolio_custom_field_settings WHERE portfolio_gid = 'pf_1'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["custom_field_name"], "Priority")
        self.assertEqual(rows[0]["is_important"], 1)

    def test_upsert_portfolio_without_settings_key_leaves_existing_untouched(self) -> None:
        self.database.upsert_portfolio(
            self.connection,
            {"gid": "pf_1", "name": "Roadmap", "custom_field_settings": [
                {"is_important": False, "custom_field": {"gid": "cf_1", "name": "P"}}
            ]},
            self.sync_time,
        )
        # Re-upsert the record fields only (no settings key) — settings must persist.
        self.database.upsert_portfolio(self.connection, {"gid": "pf_1", "name": "Roadmap v2"}, self.sync_time)

        rows = self.connection.execute(
            "SELECT custom_field_gid FROM portfolio_custom_field_settings WHERE portfolio_gid = 'pf_1'"
        ).fetchall()
        self.assertEqual(len(rows), 1)
        name = self.connection.execute("SELECT name FROM portfolios WHERE gid = 'pf_1'").fetchone()["name"]
        self.assertEqual(name, "Roadmap v2")

    def test_replace_portfolio_items_full_replace_with_position(self) -> None:
        self.database.upsert_portfolio(self.connection, {"gid": "pf_1", "name": "P"}, self.sync_time)
        items = [
            {"gid": "proj_1", "name": "Alpha", "resource_type": "project"},
            {"gid": "pf_2", "name": "Sub", "resource_type": "portfolio"},
        ]
        count = self.database.replace_portfolio_items(self.connection, "pf_1", items, self.sync_time)
        self.assertEqual(count, 2)

        rows = self.connection.execute(
            "SELECT item_gid, resource_type, position FROM portfolio_items WHERE portfolio_gid = 'pf_1' ORDER BY position"
        ).fetchall()
        self.assertEqual([r["item_gid"] for r in rows], ["proj_1", "pf_2"])
        self.assertEqual([r["position"] for r in rows], [0, 1])

        # Re-running with fewer items fully replaces the prior set.
        self.database.replace_portfolio_items(
            self.connection, "pf_1", [{"gid": "proj_9", "resource_type": "project"}], self.sync_time
        )
        rows = self.connection.execute(
            "SELECT item_gid FROM portfolio_items WHERE portfolio_gid = 'pf_1'"
        ).fetchall()
        self.assertEqual([r["item_gid"] for r in rows], ["proj_9"])

    def test_replace_portfolio_memberships_dedupes_users(self) -> None:
        self.database.upsert_portfolio(self.connection, {"gid": "pf_1", "name": "P"}, self.sync_time)
        memberships = [
            {"user": {"gid": "u_1", "name": "One"}, "access_level": "admin"},
            {"user": {"gid": "u_1", "name": "One"}, "access_level": "editor"},
            {"user": {"gid": "u_2", "name": "Two"}, "access_level": "viewer"},
        ]
        count = self.database.replace_portfolio_memberships(self.connection, "pf_1", memberships, self.sync_time)
        self.assertEqual(count, 2)
        rows = self.connection.execute(
            "SELECT user_gid FROM portfolio_memberships WHERE portfolio_gid = 'pf_1' ORDER BY user_gid"
        ).fetchall()
        self.assertEqual([r["user_gid"] for r in rows], ["u_1", "u_2"])

    def test_replace_portfolio_updates_soft_delete_and_stats(self) -> None:
        self.database.upsert_portfolio(self.connection, {"gid": "pf_1", "name": "P"}, self.sync_time)
        first = self.database.replace_portfolio_updates(
            self.connection,
            "pf_1",
            [
                {"gid": "su_1", "kind": "status_update", "status_type": "on_track", "title": "T1"},
                {"gid": "su_2", "kind": "status_update", "status_type": "at_risk", "title": "T2"},
            ],
            self.sync_time,
        )
        self.assertEqual(first["created"], 2)
        self.assertEqual(first["updated"], 0)

        # Second run keeps su_1, drops su_2 (soft-deleted), adds su_3.
        second = self.database.replace_portfolio_updates(
            self.connection,
            "pf_1",
            [
                {"gid": "su_1", "kind": "status_update", "title": "T1 edited"},
                {"gid": "su_3", "kind": "status_update", "title": "T3"},
            ],
            self.sync_time,
        )
        self.assertEqual(second["updated"], 1)
        self.assertEqual(second["created"], 1)
        self.assertEqual(second["deleted"], 1)

        active = self.connection.execute(
            "SELECT gid FROM portfolio_updates WHERE portfolio_gid = 'pf_1' AND is_deleted = 0 ORDER BY gid"
        ).fetchall()
        self.assertEqual([r["gid"] for r in active], ["su_1", "su_3"])
        deleted = self.connection.execute(
            "SELECT gid FROM portfolio_updates WHERE portfolio_gid = 'pf_1' AND is_deleted = 1"
        ).fetchall()
        self.assertEqual([r["gid"] for r in deleted], ["su_2"])
