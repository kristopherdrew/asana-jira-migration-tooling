from unittest import TestCase

from src.tasks.project_extras import (
    fetch_project_comments,
    fetch_project_status_updates,
    merge_project_updates,
)


class DummyClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_paginated(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        for item in self.payload:
            yield item


class ProjectExtrasFetchTest(TestCase):
    def test_fetch_status_updates_passes_parent_param(self) -> None:
        client = DummyClient([{"gid": "su_1"}, {"gid": "su_2"}])

        updates = fetch_project_status_updates(client, "project_1")

        self.assertEqual([u["gid"] for u in updates], ["su_1", "su_2"])
        endpoint, params = client.calls[0]
        self.assertEqual(endpoint, "/status_updates")
        self.assertEqual(params["parent"], "project_1")

    def test_fetch_project_comments_filters_non_comments(self) -> None:
        client = DummyClient(
            [
                {"gid": "1", "resource_subtype": "comment_added"},
                {"gid": "2", "resource_subtype": "owner_set"},
                {"gid": "3", "resource_subtype": "comment_added"},
            ]
        )

        comments = fetch_project_comments(client, "project_1")

        self.assertEqual({c["gid"] for c in comments}, {"1", "3"})
        endpoint, _ = client.calls[0]
        self.assertEqual(endpoint, "/projects/project_1/stories")


class MergeProjectUpdatesTest(TestCase):
    def test_drops_comment_mirroring_status_update(self) -> None:
        status_updates = [
            {
                "gid": "su_1",
                "title": "Status update - May 16",
                "status_type": "on_track",
                "text": "All good",
                "html_text": "<body>All good</body>",
                "created_at": "2025-05-16T23:56:22.076Z",
                "created_by": {"gid": "user_a"},
                "modified_at": "2025-05-16T23:56:22.667Z",
                "resource_subtype": "project_status_update",
            }
        ]
        # Auto-generated mirror: same author + created_at, different gid.
        comments = [
            {
                "gid": "story_1",
                "text": "Summary\nAll good",
                "created_at": "2025-05-16T23:56:22.076Z",
                "created_by": {"gid": "user_a"},
                "resource_subtype": "comment_added",
            }
        ]

        rows = merge_project_updates(status_updates, comments)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["gid"], "su_1")
        self.assertEqual(rows[0]["kind"], "status_update")
        self.assertEqual(rows[0]["status_type"], "on_track")
        self.assertEqual(rows[0]["created_by_gid"], "user_a")

    def test_keeps_standalone_comment_and_sorts_chronologically(self) -> None:
        status_updates = [
            {
                "gid": "su_late",
                "text": "later",
                "created_at": "2025-05-16T00:00:00.000Z",
                "created_by": {"gid": "user_a"},
            }
        ]
        comments = [
            {
                "gid": "comment_early",
                "text": "a real comment",
                "is_edited": True,
                "created_at": "2025-01-01T00:00:00.000Z",
                "created_by": {"gid": "user_b"},
                "resource_subtype": "comment_added",
            }
        ]

        rows = merge_project_updates(status_updates, comments)

        self.assertEqual([r["gid"] for r in rows], ["comment_early", "su_late"])
        self.assertEqual(rows[0]["kind"], "comment")
        self.assertEqual(rows[0]["is_edited"], True)
        self.assertEqual(rows[0]["title"], None)
        self.assertEqual(rows[1]["kind"], "status_update")

    def test_skips_records_without_gid(self) -> None:
        rows = merge_project_updates(
            [{"text": "no gid"}],
            [{"text": "no gid", "resource_subtype": "comment_added"}],
        )
        self.assertEqual(rows, [])
