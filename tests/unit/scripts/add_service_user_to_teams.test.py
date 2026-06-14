from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts.atlassian import add_service_user_to_teams as service_user_script
from src.lib.database import Database


class FakeClient:
    def __init__(self, failing_team_gid: str | None = None):
        self.failing_team_gid = failing_team_gid
        self.calls: list[tuple[str, dict]] = []

    def post_json(self, endpoint: str, payload: dict) -> dict:
        self.calls.append((endpoint, payload))
        team_gid = endpoint.split("/")[2]
        if team_gid == self.failing_team_gid:
            raise RuntimeError("boom")
        return {"data": {"gid": f"membership_{team_gid}"}}


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

    def test_prompt_for_service_user_gid_searches_local_users(self) -> None:
        self._initialize_database()
        with self.database.connect() as connection:
            self.database.initialize(connection)
            with patch("builtins.input", side_effect=["ada", "1"]):
                gid = service_user_script.prompt_for_service_user_gid(connection, search_users=True)

        self.assertEqual(gid, "user_1")
