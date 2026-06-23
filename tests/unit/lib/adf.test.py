from __future__ import annotations

from unittest import TestCase

from src.lib.adf import text_to_adf


class TextToAdfTest(TestCase):
    def test_single_line(self) -> None:
        doc = text_to_adf("hello world")
        self.assertEqual(doc["type"], "doc")
        self.assertEqual(doc["version"], 1)
        self.assertEqual(len(doc["content"]), 1)
        self.assertEqual(doc["content"][0]["content"][0]["text"], "hello world")

    def test_multi_line_drops_blank_lines(self) -> None:
        doc = text_to_adf("first\n\nsecond\n")
        texts = [node["content"][0]["text"] for node in doc["content"]]
        self.assertEqual(texts, ["first", "second"])

    def test_empty_yields_single_empty_paragraph(self) -> None:
        for value in ("", "   ", None):  # type: ignore[arg-type]
            doc = text_to_adf(value)  # type: ignore[arg-type]
            self.assertEqual(len(doc["content"]), 1)
            self.assertEqual(doc["content"][0]["content"], [])
