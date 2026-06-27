from __future__ import annotations

import csv
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase

from src.lib.database import Database, synced_at_timestamp
from scripts.export_portfolio_report import export_portfolio_report


SYNC = synced_at_timestamp(datetime(2026, 1, 2, tzinfo=timezone.utc))


class ExportPortfolioReportTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "asana.sqlite")
        self.out_dir = Path(self.tmpdir.name) / "exports"
        self._seed()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _seed(self) -> None:
        db = Database(db_path=self.db_path)
        conn = db.connect()
        db.initialize(conn)
        # Users + Atlassian status for owner resolution.
        db.upsert_user(conn, {"gid": "u_owner", "name": "Olive Owner", "email": "olive@x.com"}, SYNC)
        conn.execute(
            "INSERT INTO user_atlassian_accounts (user_gid, atlassian_account_id, atlassian_status, synced_at) "
            "VALUES (?, ?, ?, ?)",
            ("u_owner", "acct_owner", "Active", SYNC.isoformat()),
        )
        # A synced project and a team for the items companion.
        conn.execute(
            "INSERT INTO teams (gid, name, synced_at) VALUES (?, ?, ?)",
            ("team_1", "Platform", SYNC.isoformat()),
        )
        db.upsert_project(
            conn,
            {"gid": "proj_1", "name": "Alpha", "team": {"gid": "team_1", "name": "Platform"},
             "archived": False, "num_tasks": 7},
            SYNC,
        )
        # Parent portfolio with one synced project, one unsynced project, one nested portfolio.
        db.upsert_portfolio(
            conn,
            {"gid": "pf_top", "name": "Top PF", "color": "blue", "public": True,
             "owner": {"gid": "u_owner", "name": "Olive Owner"},
             "created_by": {"gid": "u_owner", "name": "Olive Owner"},
             "current_status_update": {"status_type": "on_track", "title": "Good"},
             "custom_field_settings": [
                 {"is_important": True, "custom_field": {"gid": "cf_1", "name": "Priority", "type": "enum"}}
             ]},
            SYNC,
        )
        db.upsert_portfolio(conn, {"gid": "pf_child", "name": "Child PF"}, SYNC)
        db.replace_portfolio_items(
            conn,
            "pf_top",
            [
                {"gid": "proj_1", "name": "Alpha", "resource_type": "project"},
                {"gid": "proj_missing", "name": "Ghost", "resource_type": "project"},
                {"gid": "pf_child", "name": "Child PF", "resource_type": "portfolio"},
            ],
            SYNC,
        )
        db.replace_portfolio_memberships(
            conn, "pf_top", [{"user": {"gid": "u_owner", "name": "Olive Owner"}, "access_level": "admin"}], SYNC
        )
        db.replace_portfolio_updates(
            conn, "pf_top",
            [{"gid": "su_1", "kind": "status_update", "status_type": "on_track", "created_at": "2026-01-01T00:00:00.000Z"}],
            SYNC,
        )
        conn.commit()
        conn.close()

    def _read(self, path: Path):
        with path.open() as handle:
            return list(csv.DictReader(handle))

    def test_summary_rollups_and_hierarchy(self) -> None:
        summary_path, items_path = export_portfolio_report(
            database_path=self.db_path, output_dir=self.out_dir, workspace_gid="ws_1"
        )
        rows = {r["portfolio_gid"]: r for r in self._read(summary_path)}
        self.assertEqual(set(rows), {"pf_top", "pf_child"})

        top = rows["pf_top"]
        self.assertEqual(top["owner_name"], "Olive Owner")
        self.assertEqual(top["owner_email"], "olive@x.com")
        self.assertEqual(top["owner_atlassian_status"], "Active")
        self.assertEqual(top["portfolio_url"], "https://app.asana.com/1/ws_1/portfolio/pf_top")
        self.assertEqual(top["num_items"], "3")
        self.assertEqual(top["num_projects"], "2")
        self.assertEqual(top["num_active_projects"], "1")
        self.assertEqual(top["num_nested_portfolios"], "1")
        self.assertEqual(top["num_members"], "1")
        self.assertEqual(top["num_custom_field_settings"], "1")
        self.assertEqual(top["custom_fields"], "Priority")
        self.assertEqual(top["status_update_count"], "1")
        self.assertEqual(top["is_top_level"], "true")
        self.assertEqual(top["nesting_depth"], "0")

        child = rows["pf_child"]
        self.assertEqual(child["is_top_level"], "false")
        self.assertEqual(child["nesting_depth"], "1")
        self.assertEqual(child["parent_portfolio_names"], "Top PF")

        self.assertIsNotNone(items_path)

    def test_items_companion_in_scope_and_types(self) -> None:
        _, items_path = export_portfolio_report(
            database_path=self.db_path, output_dir=self.out_dir, workspace_gid="ws_1"
        )
        rows = self._read(items_path)
        by_item = {r["item_gid"]: r for r in rows}
        self.assertEqual(by_item["proj_1"]["item_type"], "project")
        self.assertEqual(by_item["proj_1"]["in_scope"], "true")
        self.assertEqual(by_item["proj_1"]["project_team_name"], "Platform")
        self.assertEqual(by_item["proj_1"]["project_num_tasks"], "7")
        self.assertEqual(by_item["proj_1"]["item_url"], "https://app.asana.com/1/ws_1/project/proj_1")
        # Unsynced project → in_scope false.
        self.assertEqual(by_item["proj_missing"]["in_scope"], "false")
        # Nested portfolio item → portfolio URL + in_scope true (we captured pf_child).
        self.assertEqual(by_item["pf_child"]["item_type"], "portfolio")
        self.assertEqual(by_item["pf_child"]["in_scope"], "true")
        self.assertEqual(by_item["pf_child"]["item_url"], "https://app.asana.com/1/ws_1/portfolio/pf_child")

    def test_summary_only_skips_companion(self) -> None:
        summary_path, items_path = export_portfolio_report(
            database_path=self.db_path, output_dir=self.out_dir, summary_only=True, workspace_gid="ws_1"
        )
        self.assertIsNone(items_path)
        self.assertTrue(summary_path.exists())

    def test_filename_override_derives_companion_name(self) -> None:
        summary_path, items_path = export_portfolio_report(
            database_path=self.db_path, output_dir=self.out_dir, filename="pf.csv", workspace_gid="ws_1"
        )
        self.assertEqual(summary_path.name, "pf.csv")
        self.assertEqual(items_path.name, "pf_items.csv")
