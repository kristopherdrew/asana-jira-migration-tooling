from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from src.lib.config import Credentials
from src.lib.database import Database, synced_at_timestamp
from scripts import get_portfolios


def _seed_users(db_path: str, gids) -> None:
    database = Database(db_path=db_path)
    connection = database.connect()
    database.initialize(connection)
    sync_time = synced_at_timestamp(datetime(2026, 1, 1, tzinfo=timezone.utc))
    for gid in gids:
        database.upsert_user(connection, {"gid": gid, "name": gid}, sync_time)
    connection.commit()
    connection.close()


class GetPortfoliosScriptTest(TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "asana.sqlite")
        self.credentials = Credentials(
            token="t", workspace_gid="ws_1", skip_ssl_verification=False
        )

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _run(self, owned_by_owner, items_by_portfolio, nested_records, argv):
        """Run main() with all network fetchers stubbed; return the call recorder."""
        calls = {"items_fetched": [], "record_fetched": []}

        def fake_user_portfolios(client, workspace_gid, owner_gid, page_size=100):
            return list(owned_by_owner.get(owner_gid, []))

        def fake_items(client, portfolio_gid, page_size=100):
            calls["items_fetched"].append(portfolio_gid)
            return list(items_by_portfolio.get(portfolio_gid, []))

        def fake_record(client, portfolio_gid, opt_fields=None):
            calls["record_fetched"].append(portfolio_gid)
            return nested_records.get(portfolio_gid, {"gid": portfolio_gid, "name": portfolio_gid})

        with patch.object(get_portfolios, "DEFAULT_DB_PATH", self.db_path), \
            patch.object(get_portfolios, "load_credentials", return_value=self.credentials), \
            patch.object(get_portfolios, "AsanaHttpClient", return_value=object()), \
            patch.object(get_portfolios, "fetch_user_portfolios", side_effect=fake_user_portfolios), \
            patch.object(get_portfolios, "fetch_portfolio_items", side_effect=fake_items), \
            patch.object(get_portfolios, "fetch_portfolio", side_effect=fake_record), \
            patch.object(get_portfolios, "fetch_portfolio_custom_field_settings", return_value=[]), \
            patch.object(get_portfolios, "fetch_portfolio_memberships", return_value=[]), \
            patch.object(get_portfolios, "fetch_portfolio_status_updates", return_value=[]):
            get_portfolios.main(argv)
        return calls

    def _portfolio_gids(self):
        database = Database(db_path=self.db_path)
        connection = database.connect()
        rows = connection.execute("SELECT gid FROM portfolios ORDER BY gid").fetchall()
        connection.close()
        return [r["gid"] for r in rows]

    def test_iterates_all_users_and_persists_owned_portfolios(self) -> None:
        _seed_users(self.db_path, ["u_1", "u_2"])
        owned = {
            "u_1": [{"gid": "pf_1", "name": "Alpha"}],
            "u_2": [{"gid": "pf_2", "name": "Beta"}],
        }
        items = {"pf_1": [{"gid": "proj_1", "resource_type": "project"}], "pf_2": []}

        calls = self._run(owned, items, {}, ["--workers", "1"])

        self.assertEqual(self._portfolio_gids(), ["pf_1", "pf_2"])
        self.assertCountEqual(calls["items_fetched"], ["pf_1", "pf_2"])
        self.assertEqual(calls["record_fetched"], [])  # no nesting

    def test_recurses_into_nested_portfolio_once(self) -> None:
        _seed_users(self.db_path, ["u_1"])
        owned = {"u_1": [{"gid": "pf_1", "name": "Top"}]}
        # pf_1 contains nested pf_child; pf_child contains a project (no further nesting).
        items = {
            "pf_1": [{"gid": "pf_child", "resource_type": "portfolio"}],
            "pf_child": [{"gid": "proj_9", "resource_type": "project"}],
        }
        nested_records = {"pf_child": {"gid": "pf_child", "name": "Child"}}

        calls = self._run(owned, items, nested_records, ["--owner-gid", "u_1", "--workers", "1"])

        self.assertEqual(self._portfolio_gids(), ["pf_1", "pf_child"])
        self.assertEqual(calls["record_fetched"], ["pf_child"])  # fetched exactly once
        self.assertCountEqual(calls["items_fetched"], ["pf_1", "pf_child"])

    def test_cycle_guard_does_not_loop_forever(self) -> None:
        _seed_users(self.db_path, ["u_1"])
        owned = {"u_1": [{"gid": "pf_a", "name": "A"}]}
        # pf_a -> pf_b -> pf_a (cycle). Each should be processed once.
        items = {
            "pf_a": [{"gid": "pf_b", "resource_type": "portfolio"}],
            "pf_b": [{"gid": "pf_a", "resource_type": "portfolio"}],
        }
        nested_records = {"pf_b": {"gid": "pf_b", "name": "B"}}

        calls = self._run(owned, items, nested_records, ["--workers", "1"])

        self.assertEqual(self._portfolio_gids(), ["pf_a", "pf_b"])
        self.assertEqual(calls["record_fetched"], ["pf_b"])
        self.assertCountEqual(calls["items_fetched"], ["pf_a", "pf_b"])

    def test_no_users_returns_without_error(self) -> None:
        _seed_users(self.db_path, [])
        calls = self._run({}, {}, {}, ["--workers", "1"])
        self.assertEqual(self._portfolio_gids(), [])
        self.assertEqual(calls["items_fetched"], [])
