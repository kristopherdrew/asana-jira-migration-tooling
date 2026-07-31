from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scripts.atlassian import add_service_user_to_projects as service_user_script
from src.lib.database import Database


class FakeClient:
    def __init__(self, failing_project_gid: str | None = None, me_payload=None, me_error=None):
        self.failing_project_gid = failing_project_gid
        self.me_payload = me_payload
        self.me_error = me_error
        self.calls: list[tuple[str, dict]] = []

    def post_json(self, endpoint: str, payload: dict) -> dict:
        self.calls.append((endpoint, payload))
        project_gid = endpoint.split("/")[2]
        if project_gid == self.failing_project_gid:
            raise RuntimeError("boom")
        return {"data": {"gid": f"membership_{project_gid}"}}

    def get(self, endpoint: str, params=None) -> dict:
        self.calls.append((endpoint, params))
        if self.me_error is not None:
            raise self.me_error
        return self.me_payload or {}


class FakeCredentials:
    token = "token"
    skip_ssl_verification = False


class AddServiceUserToProjectsTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)

    def test_resolve_project_gids_dedupes_and_requires_values(self) -> None:
        gids = service_user_script.resolve_project_gids(["proj_1", "proj_2", "proj_1"], "proj_2, proj_3")
        self.assertEqual(gids, ["proj_2", "proj_3", "proj_1"])

        with self.assertRaises(SystemExit):
            service_user_script.resolve_project_gids([], "")

    def test_add_service_user_to_projects_writes_failure_report(self) -> None:
        client = FakeClient(failing_project_gid="proj_2")

        success_count, failures = service_user_script.add_service_user_to_projects(
            client,
            "user_1",
            ["proj_1", "proj_2", "proj_3"],
        )

        self.assertEqual(success_count, 2)
        self.assertEqual(failures, [("proj_2", "boom")])
        self.assertEqual(
            [endpoint for endpoint, _ in client.calls],
            [
                "/projects/proj_1/addMembers",
                "/projects/proj_2/addMembers",
                "/projects/proj_3/addMembers",
            ],
        )
        self.assertEqual(client.calls[0][1], {"data": {"members": "user_1"}})

        report_path = service_user_script.write_failure_report(self.workspace / "exports", failures)
        self.assertTrue(report_path.exists())
        self.assertEqual(
            report_path.read_text(encoding="utf-8"),
            "project_gid,error\nproj_2,boom\n",
        )


class AddServiceUserToProjectsMainTest(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.workspace = Path(self.temp_dir.name)
        self.db_path = self.workspace / "asana.sqlite"

    def _run_main(self, client: FakeClient, extra_args: list[str]) -> None:
        argv = [
            "--database",
            str(self.db_path),
            "--project-gids",
            "proj_1,proj_2",
            "--output-dir",
            str(self.workspace / "exports"),
            *extra_args,
        ]
        with patch.object(service_user_script, "load_credentials", return_value=FakeCredentials()), patch.object(
            service_user_script, "AsanaHttpClient", return_value=client
        ):
            service_user_script.main(argv)

    def test_defaults_to_token_account_and_upserts(self) -> None:
        client = FakeClient(
            me_payload={"data": {"gid": "svc_1", "name": "Atlassian-Migration", "is_active": True}}
        )

        self._run_main(client, extra_args=[])

        self.assertEqual(client.calls[0][0], "/users/me")
        add_calls = [c for c in client.calls if c[0].endswith("/addMembers")]
        self.assertEqual(len(add_calls), 2)
        self.assertEqual(add_calls[0][1], {"data": {"members": "svc_1"}})

        database = Database(str(self.db_path))
        with database.connect() as connection:
            row = connection.execute("SELECT name FROM users WHERE gid = ?", ("svc_1",)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Atlassian-Migration")

    def test_explicit_user_gid_skips_lookup(self) -> None:
        client = FakeClient()

        self._run_main(client, extra_args=["--user-gid", "given_1"])

        self.assertNotIn("/users/me", [c[0] for c in client.calls])
        add_calls = [c for c in client.calls if c[0].endswith("/addMembers")]
        self.assertEqual(add_calls[0][1], {"data": {"members": "given_1"}})

    def test_falls_back_to_prompt_when_me_fails(self) -> None:
        client = FakeClient(me_error=RuntimeError("network down"))

        with patch("builtins.input", side_effect=["prompted_1"]):
            self._run_main(client, extra_args=[])

        add_calls = [c for c in client.calls if c[0].endswith("/addMembers")]
        self.assertEqual(add_calls[0][1], {"data": {"members": "prompted_1"}})
