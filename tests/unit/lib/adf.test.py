from __future__ import annotations

from unittest import TestCase

from src.lib.adf import (
    BulletList,
    Heading,
    Paragraph,
    Run,
    render_blocks_adf,
    render_blocks_text,
    text_run,
    text_to_adf,
)


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


class TextRunTest(TestCase):
    def test_plain(self) -> None:
        self.assertEqual(text_run("hi"), {"type": "text", "text": "hi"})

    def test_bold_adds_strong_mark(self) -> None:
        node = text_run("hi", bold=True)
        self.assertEqual(node["marks"], [{"type": "strong"}])


class RenderBlocksTest(TestCase):
    BLOCKS = [
        Heading("Title"),
        Paragraph(["intro ", Run("bold", bold=True)]),
        BulletList([[Run("Active", bold=True), " — go"], ["Plain item"]]),
    ]

    def test_adf_structure(self) -> None:
        doc = render_blocks_adf(self.BLOCKS)
        self.assertEqual(doc["type"], "doc")
        kinds = [node["type"] for node in doc["content"]]
        self.assertEqual(kinds, ["heading", "paragraph", "bulletList"])
        # Heading carries a level attr.
        self.assertEqual(doc["content"][0]["attrs"]["level"], 2)
        # Bold run inside the paragraph keeps a strong mark.
        para_runs = doc["content"][1]["content"]
        self.assertEqual(para_runs[1]["marks"], [{"type": "strong"}])
        # Bullet list has two list items, each a paragraph.
        items = doc["content"][2]["content"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["type"], "listItem")
        self.assertEqual(items[0]["content"][0]["content"][0]["text"], "Active")

    def test_plain_text_shape(self) -> None:
        text = render_blocks_text(self.BLOCKS)
        self.assertEqual(
            text,
            "Title\n\nintro bold\n\n  • Active — go\n  • Plain item",
        )

    def test_empty_blocks_adf_has_empty_paragraph(self) -> None:
        doc = render_blocks_adf([])
        self.assertEqual(doc["content"], [{"type": "paragraph", "content": []}])
