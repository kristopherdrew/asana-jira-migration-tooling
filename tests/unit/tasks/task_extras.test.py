from unittest import TestCase

from src.tasks.task_extras import fetch_task_attachments, fetch_task_comments


class DummyClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get_paginated(self, endpoint, params=None):
        self.calls.append((endpoint, params))
        for item in self.payload:
            yield item


class TaskExtrasFetchTest(TestCase):
    def test_fetch_task_comments_filters_non_comments(self) -> None:
        client = DummyClient(
            [
                {"gid": "1", "resource_subtype": "comment_added"},
                {"gid": "2", "resource_subtype": "assigned"},
            ]
        )

        comments = fetch_task_comments(client, "task_1")

        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["gid"], "1")

    def test_fetch_task_attachments_returns_all(self) -> None:
        payload = [{"gid": "att_1"}, {"gid": "att_2"}]
        client = DummyClient(payload)

        attachments = fetch_task_attachments(client, "task_1")

        self.assertEqual(len(attachments), 2)
        self.assertEqual({att["gid"] for att in attachments}, {"att_1", "att_2"})
