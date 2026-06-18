from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts import get_teams
from src.lib.database import Database


class FakeClient:
    """Returns canned memberships per team and can fail a chosen team."""

    def __init__(self, memberships_by_team: dict, failing_team_gid: str | None = None):
        self.memberships_by_team = memberships_by_team
        self.failing_team_gid = failing_team_gid
        self.calls: list[str] = []

    def get_paginated(self, endpoint, params=None):
        team_gid = endpoint.split("/")[2]
        self.calls.append(team_gid)
        if team_gid == self.failing_team_gid:
            raise RuntimeError("boom")
        for member in self.memberships_by_team.get(team_gid, []):
            yield member


def _membership(user_gid: str, *, is_admin: bool = False) -> dict:
    return {
        "gid": f"membership_{user_gid}",
        "user": {"gid": user_gid, "name": user_gid, "is_active": True},
        "is_admin": is_admin,
        "role": "member",
    }


class SyncTeamMembershipsTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.now = datetime.now(timezone.utc)

    def _seed_teams(self, connection) -> None:
        self.database.initialize(connection)
        self.database.upsert_team(connection, {"gid": "team_1", "name": "Alpha"}, self.now)
        self.database.upsert_team(connection, {"gid": "team_2", "name": "Beta"}, self.now)
        connection.commit()

    def test_fetches_and_upserts_memberships_for_each_team(self) -> None:
        client = FakeClient(
            {
                "team_1": [_membership("user_1"), _membership("user_2", is_admin=True)],
                "team_2": [_membership("user_3")],
            }
        )
        with self.database.connect() as connection:
            self._seed_teams(connection)
            total = get_teams.sync_team_memberships(
                client=client,
                database=self.database,
                connection=connection,
                team_gids=["team_1", "team_2"],
                workers=3,
                sync_time=self.now,
            )
            connection.commit()
            rows = list(
                connection.execute(
                    "SELECT team_gid, user_gid FROM team_members ORDER BY team_gid, user_gid"
                )
            )

        self.assertEqual(total, 3)
        self.assertEqual(sorted(client.calls), ["team_1", "team_2"])
        self.assertEqual(
            [(row["team_gid"], row["user_gid"]) for row in rows],
            [("team_1", "user_1"), ("team_1", "user_2"), ("team_2", "user_3")],
        )

    def test_skips_team_when_fetch_fails(self) -> None:
        client = FakeClient(
            {"team_1": [_membership("user_1")], "team_2": [_membership("user_3")]},
            failing_team_gid="team_2",
        )
        with self.database.connect() as connection:
            self._seed_teams(connection)
            total = get_teams.sync_team_memberships(
                client=client,
                database=self.database,
                connection=connection,
                team_gids=["team_1", "team_2"],
                workers=2,
                sync_time=self.now,
            )
            connection.commit()
            rows = list(connection.execute("SELECT team_gid FROM team_members"))

        self.assertEqual(total, 1)
        self.assertEqual([row["team_gid"] for row in rows], ["team_1"])

    def test_empty_team_list_is_noop(self) -> None:
        client = FakeClient({})
        with self.database.connect() as connection:
            self._seed_teams(connection)
            total = get_teams.sync_team_memberships(
                client=client,
                database=self.database,
                connection=connection,
                team_gids=[],
                workers=2,
                sync_time=self.now,
            )
        self.assertEqual(total, 0)
        self.assertEqual(client.calls, [])


class UpdateSingleTeamMembersTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.now = datetime.now(timezone.utc)
        with self.database.connect() as connection:
            self.database.initialize(connection)
            self.database.upsert_team(connection, {"gid": "team_1", "name": "Alpha"}, self.now)
            connection.commit()

    def test_refreshes_only_selected_team(self) -> None:
        client = FakeClient({"team_1": [_membership("user_1"), _membership("user_2")]})
        with patch("scripts.get_teams.search_team", return_value=("team_1", "Alpha")):
            get_teams.update_single_team_members(
                client=client, database=self.database, workers=2
            )

        self.assertEqual(client.calls, ["team_1"])
        with self.database.connect() as connection:
            rows = list(connection.execute("SELECT user_gid FROM team_members ORDER BY user_gid"))
        self.assertEqual([row["user_gid"] for row in rows], ["user_1", "user_2"])

    def test_no_selection_makes_no_calls(self) -> None:
        client = FakeClient({"team_1": [_membership("user_1")]})
        with patch("scripts.get_teams.search_team", return_value=None):
            get_teams.update_single_team_members(
                client=client, database=self.database, workers=2
            )
        self.assertEqual(client.calls, [])


class ParseArgsTest(TestCase):
    def test_defaults(self) -> None:
        args = get_teams.parse_args([])
        self.assertEqual(args.workers, get_teams.DEFAULT_WORKERS)
        self.assertFalse(args.update_team_members)
        self.assertFalse(args.include_members)

    def test_overrides(self) -> None:
        args = get_teams.parse_args(["--workers", "8", "--update-team-members"])
        self.assertEqual(args.workers, 8)
        self.assertTrue(args.update_team_members)
