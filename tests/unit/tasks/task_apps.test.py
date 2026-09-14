from unittest import TestCase

from src.lib.http_client import AsanaHttpError
from src.tasks.task_apps import fetch_task_app_attachment_probe, summarize_app_attachments


class DummyClient:
    def __init__(self, attachments=None, details=None, detail_errors=None):
        self.attachments = attachments or []
        self.details = details or {}
        self.detail_errors = detail_errors or {}
        self.calls = []

    def get_paginated(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        for item in self.attachments:
            yield item

    def get(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        gid = endpoint.rsplit("/", 1)[-1]
        if gid in self.detail_errors:
            raise self.detail_errors[gid]
        return {"data": self.details.get(gid, {"gid": gid})}


class TaskAppsProbeTest(TestCase):
    def test_fetch_probe_reads_attachment_list_and_details(self) -> None:
        client = DummyClient(
            attachments=[
                {
                    "gid": "att_1",
                    "name": "Design doc",
                    "resource_subtype": "gdrive",
                    "host": "gdrive",
                    "connected_to_app": True,
                }
            ],
            details={"att_1": {"gid": "att_1", "view_url": "https://docs.google.com/document/d/1"}},
        )

        result = fetch_task_app_attachment_probe(client, "task_1")

        self.assertEqual(result["task_gid"], "task_1")
        self.assertEqual(result["summary"]["total_attachments"], 1)
        self.assertEqual(result["summary"]["app_like_attachments"], 1)
        self.assertEqual(result["summary"]["connected_to_app"], 1)
        self.assertEqual(result["attachment_details"][0]["data"]["view_url"], "https://docs.google.com/document/d/1")
        list_endpoint, list_params = client.calls[0]
        self.assertEqual(list_endpoint, "/attachments")
        self.assertEqual(list_params["parent"], "task_1")
        self.assertIn("connected_to_app", list_params["opt_fields"].split(","))
        detail_endpoint, detail_params = client.calls[1]
        self.assertEqual(detail_endpoint, "/attachments/att_1")
        self.assertIn("view_url", detail_params["opt_fields"].split(","))

    def test_fetch_probe_can_skip_details(self) -> None:
        client = DummyClient(attachments=[{"gid": "att_1"}])

        result = fetch_task_app_attachment_probe(client, "task_1", include_details=False)

        self.assertEqual(result["attachment_details"], [])
        self.assertEqual(len(client.calls), 1)

    def test_detail_errors_are_recorded_without_aborting(self) -> None:
        client = DummyClient(
            attachments=[{"gid": "att_1"}],
            detail_errors={"att_1": AsanaHttpError(403, "Forbidden", {"errors": [{"message": "nope"}]})},
        )

        result = fetch_task_app_attachment_probe(client, "task_1")

        self.assertFalse(result["attachment_details"][0]["ok"])
        self.assertEqual(result["attachment_details"][0]["status"], 403)

    def test_summary_counts_subtypes_hosts_and_connected_flag(self) -> None:
        summary = summarize_app_attachments(
            [
                {"resource_subtype": "asana", "host": "asana"},
                {"resource_subtype": "external", "host": "external", "connected_to_app": True},
                {"resource_subtype": "gdrive", "host": "gdrive"},
            ]
        )

        self.assertEqual(summary["total_attachments"], 3)
        self.assertEqual(summary["app_like_attachments"], 2)
        self.assertEqual(summary["connected_to_app"], 1)
        self.assertEqual(summary["by_resource_subtype"]["gdrive"], 1)
