from __future__ import annotations

from unittest import TestCase

from scripts.upload_jira_attachments import parse_args


class UploadJiraAttachmentsArgsTest(TestCase):
    def test_no_weblinks_flag_is_optional_escape_hatch(self) -> None:
        args = parse_args(["--manifest", "archive/manifest.json", "--no-weblinks"])

        self.assertEqual(args.manifest, "archive/manifest.json")
        self.assertTrue(args.no_weblinks)
