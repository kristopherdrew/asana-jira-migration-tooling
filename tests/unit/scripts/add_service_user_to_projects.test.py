from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from scripts.atlassian import add_service_user_to_projects as service_user_script


class FakeClient:
    def __init__(self, failing_project_gid: str | None = None):
        self.failing_project_gid = failing_project_gid
        self.calls: list[tuple[str, dict]] = []

    def post_json(self, endpoint: str, payload: dict) -> dict:
        self.calls.append((endpoint, payload))
        project_gid = endpoint.split("/")[2]
        if project_gid == self.failing_project_gid:
            raise RuntimeError("boom")
        return {"data": {"gid": f"membership_{project_gid}"}}


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
