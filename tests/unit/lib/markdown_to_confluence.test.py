from __future__ import annotations

from unittest import TestCase

from src.lib.markdown_to_confluence import (
    extract_title,
    markdown_to_storage,
    render_inline,
)


class RenderInlineTest(TestCase):
    def test_bold_italic_code(self) -> None:
        self.assertEqual(render_inline("**b**"), "<strong>b</strong>")
        self.assertEqual(render_inline("*i*"), "<em>i</em>")
        self.assertEqual(render_inline("`c`"), "<code>c</code>")

    def test_bold_wins_over_italic(self) -> None:
        self.assertEqual(render_inline("**bold**"), "<strong>bold</strong>")

    def test_link(self) -> None:
        self.assertEqual(
            render_inline("[label](https://example.com)"),
            '<a href="https://example.com">label</a>',
        )

    def test_code_span_is_not_emphasized(self) -> None:
        # A snake_case or starred token inside a code span stays literal.
        self.assertEqual(render_inline("`a_b *x*`"), "<code>a_b *x*</code>")

    def test_overlapping_emphasis_stays_well_formed(self) -> None:
        # Adversarial input (`**a *b***`) must not emit overlapping tags.
        import xml.etree.ElementTree as ET

        out = render_inline("**one phrase: *italic.***")
        ET.fromstring(f"<r>{out}</r>")  # raises if not well-formed
        self.assertNotIn("</strong></em>", out)

    def test_italic_can_contain_link(self) -> None:
        self.assertEqual(
            render_inline("*see [x](http://e.com)*"),
            '<em>see <a href="http://e.com">x</a></em>',
        )

    def test_bold_contains_nested_italic(self) -> None:
        self.assertEqual(
            render_inline("**a *b* c**"),
            "<strong>a <em>b</em> c</strong>",
        )

    def test_html_special_chars_escaped(self) -> None:
        self.assertEqual(render_inline("a < b & c > d"), "a &lt; b &amp; c &gt; d")

    def test_ampersand_in_url_escaped(self) -> None:
        self.assertEqual(
            render_inline("[x](https://e.com?a=1&b=2)"),
            '<a href="https://e.com?a=1&amp;b=2">x</a>',
        )


class BlockTest(TestCase):
    def test_headings(self) -> None:
        self.assertEqual(markdown_to_storage("# Title"), "<h1>Title</h1>")
        self.assertEqual(markdown_to_storage("### Sub"), "<h3>Sub</h3>")

    def test_paragraph_soft_wrap_joined(self) -> None:
        self.assertEqual(markdown_to_storage("one\ntwo"), "<p>one two</p>")

    def test_two_paragraphs(self) -> None:
        self.assertEqual(markdown_to_storage("a\n\nb"), "<p>a</p><p>b</p>")

    def test_horizontal_rule(self) -> None:
        self.assertEqual(markdown_to_storage("---"), "<hr/>")

    def test_fenced_code_with_language(self) -> None:
        out = markdown_to_storage("```python\nprint(1)\n```")
        self.assertIn('<ac:structured-macro ac:name="code">', out)
        self.assertIn('<ac:parameter ac:name="language">python</ac:parameter>', out)
        self.assertIn("<![CDATA[print(1)]]>", out)

    def test_fenced_code_does_not_escape_or_emphasize(self) -> None:
        out = markdown_to_storage("```\na < b **c**\n```")
        self.assertIn("a < b **c**", out)

    def test_unordered_list(self) -> None:
        self.assertEqual(markdown_to_storage("- a\n- b"), "<ul><li>a</li><li>b</li></ul>")

    def test_ordered_list(self) -> None:
        self.assertEqual(markdown_to_storage("1. a\n2. b"), "<ol><li>a</li><li>b</li></ol>")

    def test_nested_list(self) -> None:
        out = markdown_to_storage("- a\n  - b")
        self.assertEqual(out, "<ul><li>a<ul><li>b</li></ul></li></ul>")

    def test_task_list(self) -> None:
        out = markdown_to_storage("- [ ] todo\n- [x] done")
        self.assertIn("<ac:task-list>", out)
        self.assertIn("<ac:task-status>incomplete</ac:task-status>", out)
        self.assertIn("<ac:task-status>complete</ac:task-status>", out)
        self.assertIn("<ac:task-body>todo</ac:task-body>", out)

    def test_blockquote(self) -> None:
        out = markdown_to_storage("> quoted line")
        self.assertEqual(out, "<blockquote><p>quoted line</p></blockquote>")

    def test_table(self) -> None:
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        out = markdown_to_storage(md)
        self.assertEqual(
            out,
            "<table><tbody><tr><th>A</th><th>B</th></tr>"
            "<tr><td>1</td><td>2</td></tr></tbody></table>",
        )

    def test_table_cell_inline_formatting(self) -> None:
        md = "| H |\n|---|\n| **bold** |"
        out = markdown_to_storage(md)
        self.assertIn("<td><strong>bold</strong></td>", out)

    def test_heading_then_paragraph_not_merged(self) -> None:
        out = markdown_to_storage("# Title\nbody text")
        self.assertEqual(out, "<h1>Title</h1><p>body text</p>")


class ExtractTitleTest(TestCase):
    def test_extracts_and_strips_h1(self) -> None:
        title, body = extract_title("# My Page\n\nbody")
        self.assertEqual(title, "My Page")
        self.assertNotIn("# My Page", body)
        self.assertIn("body", body)

    def test_no_h1_returns_empty(self) -> None:
        title, body = extract_title("## Only sub\n\nbody")
        self.assertEqual(title, "")
        self.assertEqual(body, "## Only sub\n\nbody")
