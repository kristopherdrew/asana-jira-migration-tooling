from __future__ import annotations

from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts.atlassian import add_service_user_to_teams as service_user_script
from src.lib.database import Database


class FakeClient:
    def __init__(self, failing_team_gid: str | None = None, me_payload=None, me_error=None):
        self.failing_team_gid = failing_team_gid
        self.me_payload = me_payload
        self.me_error = me_error
        self.calls: list[tuple[str, dict]] = []

    def post_json(self, endpoint: str, payload: dict) -> dict:
        self.calls.append((endpoint, payload))
        team_gid = endpoint.split("/")[2]
        if team_gid == self.failing_team_gid:
            raise RuntimeError("boom")
        return {"data": {"gid": f"membership_{team_gid}"}}

    def get(self, endpoint: str, params=None) -> dict:
        self.calls.append((endpoint, params))
        if self.me_error is not None:
            raise self.me_error
        return self.me_payload or {}


class AddServiceUserToTeamsTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"
        self.database = Database(str(self.db_path))
        self.now = datetime.now(timezone.utc)

    def _initialize_database(self) -> None:
        with self.database.connect() as connection:
            self.database.initialize(connection)
            self.database.upsert_team(connection, {"gid": "team_1", "name": "Team One"}, self.now)
            self.database.upsert_team(connection, {"gid": "team_2", "name": "Team Two"}, self.now)
            self.database.upsert_team(connection, {"gid": "team_3", "name": "Team Three"}, self.now)
            self.database.upsert_user(
                connection,
                {
                    "gid": "user_1",
                    "name": "Ada Lovelace",
                    "resource_type": "user",
                    "email": "ada@example.com",
                    "is_active": True,
                },
                self.now,
            )
            self.database.upsert_user(
                connection,
                {
                    "gid": "user_2",
                    "name": "Grace Hopper",
                    "resource_type": "user",
                    "email": "grace@example.com",
                    "is_active": True,
                },
                self.now,
            )
            connection.commit()

    def test_defaults_to_the_team_sync_worker_count(self) -> None:
        self.assertEqual(service_user_script.parse_args([]).workers, 5)
        self.assertEqual(service_user_script.parse_args(["--workers", "2"]).workers, 2)

    def test_add_service_user_to_teams_writes_failure_report(self) -> None:
        self._initialize_database()
        client = FakeClient(failing_team_gid="team_2")

        with self.database.connect() as connection:
            self.database.initialize(connection)
            success_count, failures = service_user_script.add_service_user_to_teams(connection, client, "user_1")

        self.assertEqual(success_count, 2)
        self.assertEqual(failures, [("team_2", "boom")])
        self.assertEqual(
            [endpoint for endpoint, _ in client.calls],
            [
                "/teams/team_1/addUser",
                "/teams/team_3/addUser",
                "/teams/team_2/addUser",
            ],
        )
        self.assertEqual(
            client.calls[0][1],
            {
                "data": {
                    "resource_type": "team_membership",
                    "user": {"gid": "user_1"},
                    "is_guest": False,
                    "is_limited_access": False,
                    "is_admin": True,
                }
            },
        )

        report_path = service_user_script.write_failure_report(self.workspace / "exports", failures)
        self.assertTrue(report_path.exists())
        self.assertEqual(
            report_path.read_text(encoding="utf-8"),
            "team_gid,error\nteam_2,boom\n",
        )

    def test_add_service_user_to_teams_skips_existing_memberships(self) -> None:
        self._initialize_database()
        client = FakeClient()

        with self.database.connect() as connection:
            self.database.initialize(connection)
            self.database.upsert_team_members(
                connection,
                "team_2",
                [{"user": {"gid": "user_1", "is_active": True}, "is_admin": True}],
                self.now,
            )
            success_count, failures = service_user_script.add_service_user_to_teams(
                connection, client, "user_1"
            )
            roster = connection.execute(
                "SELECT team_gid FROM team_members WHERE user_gid = ? ORDER BY team_gid",
                ("user_1",),
            ).fetchall()

        self.assertEqual(success_count, 2)
        self.assertEqual(failures, [])
        self.assertEqual(
            [endpoint for endpoint, _ in client.calls],
            ["/teams/team_1/addUser", "/teams/team_3/addUser"],
        )
        self.assertEqual([row[0] for row in roster], ["team_1", "team_2", "team_3"])

    def test_prompt_for_service_user_gid_searches_local_users(self) -> None:
        self._initialize_database()
        with self.database.connect() as connection:
            self.database.initialize(connection)
            with patch("builtins.input", side_effect=["ada", "1"]):
                gid = service_user_script.prompt_for_service_user_gid(connection, search_users=True)

        self.assertEqual(gid, "user_1")

    def _resolve(self, client, args: Namespace) -> str:
        self._initialize_database()
        with self.database.connect() as connection:
            self.database.initialize(connection)
            return service_user_script._resolve_service_user_gid(
                client, self.database, connection, args
            )

    def test_resolve_uses_explicit_user_gid(self) -> None:
        client = FakeClient()
        args = Namespace(user_gid="user_2", search_users=False)

        gid = self._resolve(client, args)

        self.assertEqual(gid, "user_2")
        self.assertEqual(client.calls, [])  # no /users/me lookup

    def test_resolve_defaults_to_token_account(self) -> None:
        client = FakeClient(me_payload={"data": {"gid": "svc_1", "name": "Atlassian-Migration"}})
        args = Namespace(user_gid=None, search_users=False)

        gid = self._resolve(client, args)

        self.assertEqual(gid, "svc_1")
        self.assertEqual(client.calls[0][0], "/users/me")

    def test_resolve_falls_back_to_prompt_when_me_fails(self) -> None:
        client = FakeClient(me_error=RuntimeError("network down"))
        args = Namespace(user_gid=None, search_users=False)

        # Fallback path first asks "Search current users?" (y), then search + pick.
        with patch("builtins.input", side_effect=["y", "grace", "1"]):
            gid = self._resolve(client, args)

        self.assertEqual(gid, "user_2")

    def test_resolve_search_users_flag_prompts(self) -> None:
        client = FakeClient(me_payload={"data": {"gid": "svc_1"}})
        args = Namespace(user_gid=None, search_users=True)

        with patch("builtins.input", side_effect=["ada", "1"]):
            gid = self._resolve(client, args)

        self.assertEqual(gid, "user_1")
        self.assertEqual(client.calls, [])  # search path never calls /users/me
