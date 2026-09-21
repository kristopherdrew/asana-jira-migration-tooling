from __future__ import annotations

import io
from contextlib import redirect_stdout
from unittest import TestCase
from unittest.mock import MagicMock, patch

from scripts import debug_fetch_task_comments
from src.lib.config import Credentials


def _story(gid: str, subtype: str, text: str, name: str = "Someone") -> dict:
    return {
        "gid": gid,
        "resource_subtype": subtype,
        "created_at": "2026-01-01T00:00:00Z",
        "created_by": {"name": name},
        "text": text,
    }


class DebugFetchTaskCommentsScriptTest(TestCase):
    def setUp(self) -> None:
        self.credentials = Credentials(
            token="asana-token",
            workspace_gid="workspace_1",
            skip_ssl_verification=True,
        )
        self.client = MagicMock()

    def test_main_prints_raw_and_filtered_stories(self) -> None:
        raw_stories = [
            _story("story_1", "comment_added", "Hello world"),
            _story("story_2", "assigned", "System changed assignee"),
        ]
        filtered_comments = [raw_stories[0]]
        self.client.get_paginated.return_value = raw_stories

        with patch.object(
            debug_fetch_task_comments, "load_credentials", return_value=self.credentials
        ), patch.object(
            debug_fetch_task_comments, "AsanaHttpClient", return_value=self.client
        ) as client_cls, patch.object(
            debug_fetch_task_comments, "fetch_task_comments", return_value=filtered_comments
        ) as fetch_comments, patch(
            "sys.argv",
            ["debug_fetch_task_comments.py", "--task-gid", "task_1", "--page-size", "50"],
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                debug_fetch_task_comments.main()

        output = stdout.getvalue()

        client_cls.assert_called_once_with(token="asana-token", skip_ssl_verification=True)
        self.client.get_paginated.assert_called_once()
        endpoint, kwargs = self.client.get_paginated.call_args
        self.assertEqual(endpoint[0], "/tasks/task_1/stories")
        self.assertEqual(kwargs["params"]["limit"], "50")
        fetch_comments.assert_called_once_with(self.client, "task_1", page_size=50)

        self.assertIn("RAW story_1 | comment_added", output)
        self.assertIn("RAW story_2 | assigned", output)
        self.assertIn("FILTERED story_1 | comment_added", output)
        self.assertNotIn("FILTERED story_2", output)
        self.assertNotIn("No filtered comments returned.", output)

    def test_main_prints_message_when_no_filtered_comments(self) -> None:
        raw_stories = [_story("story_1", "assigned", "System changed assignee")]
        self.client.get_paginated.return_value = raw_stories

        with patch.object(
            debug_fetch_task_comments, "load_credentials", return_value=self.credentials
        ), patch.object(
            debug_fetch_task_comments, "AsanaHttpClient", return_value=self.client
        ), patch.object(
            debug_fetch_task_comments, "fetch_task_comments", return_value=[]
        ), patch(
            "sys.argv", ["debug_fetch_task_comments.py", "--task-gid", "task_2"]
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                debug_fetch_task_comments.main()

        output = stdout.getvalue()
        self.assertIn("No filtered comments returned.", output)
        self.assertNotIn("FILTERED", output)

    def test_preview_length_truncates_long_text(self) -> None:
        long_text = "word " * 100
        raw_stories = [_story("story_1", "comment_added", long_text)]
        self.client.get_paginated.return_value = raw_stories

        with patch.object(
            debug_fetch_task_comments, "load_credentials", return_value=self.credentials
        ), patch.object(
            debug_fetch_task_comments, "AsanaHttpClient", return_value=self.client
        ), patch.object(
            debug_fetch_task_comments, "fetch_task_comments", return_value=[]
        ), patch(
            "sys.argv",
            [
                "debug_fetch_task_comments.py",
                "--task-gid",
                "task_3",
                "--preview-length",
                "20",
            ],
        ):
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                debug_fetch_task_comments.main()

        output = stdout.getvalue()
        self.assertNotIn(long_text.strip(), output)
        self.assertIn(" …", output)
        raw_line = next(line for line in output.splitlines() if line.startswith("RAW"))
        snippet = raw_line.split(" | ")[-1]
        self.assertLessEqual(len(snippet), 20)

    def test_parse_args_defaults(self) -> None:
        with patch("sys.argv", ["debug_fetch_task_comments.py"]):
            args = debug_fetch_task_comments.parse_args()

        self.assertEqual(args.task_gid, "1210826389244773")
        self.assertEqual(args.page_size, 100)
        self.assertEqual(args.preview_length, 200)
