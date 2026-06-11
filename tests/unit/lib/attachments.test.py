from __future__ import annotations

from unittest import TestCase

from src.lib.attachments import jira_unique_attachment_name


class JiraUniqueAttachmentNameTest(TestCase):
    def test_preserves_extension_and_appends_gid(self) -> None:
        result = jira_unique_attachment_name("example.png", "att_123")
        self.assertEqual(result, "example__att_123.png")

    def test_falls_back_to_gid_when_name_missing(self) -> None:
        result = jira_unique_attachment_name(None, "att_456")
        self.assertEqual(result, "att_456__att_456")

    def test_strips_unsafe_characters_and_separators(self) -> None:
        result = jira_unique_attachment_name("dir/name:with*chars.pdf", "att_789")
        self.assertEqual(result, "dir_name_with_chars__att_789.pdf")

    def test_handles_hidden_files_without_stem(self) -> None:
        result = jira_unique_attachment_name(".env", "att_000")
        self.assertEqual(result, ".env__att_000")
