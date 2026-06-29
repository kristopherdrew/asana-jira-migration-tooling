"""Convert GitHub-flavored Markdown into Confluence storage format (XHTML).

This is the piece that lets us publish the already-written docs to Confluence
*without routing their content through a language model*. A model-driven publish
re-generates every page body as output tokens (slow + expensive); this converter
does the markdown -> storage-format translation deterministically and locally, so
the publisher only spends network time on the REST calls.

Scope is intentionally the subset of Markdown the repo's docs actually use:
ATX headings, fenced code blocks (with language), pipe tables, blockquotes,
ordered/unordered/nested lists, GitHub task lists, horizontal rules, and the
inline run of bold/italic/inline-code/links. It is not a general CommonMark
implementation; it covers the playbook and report docs faithfully and degrades
gracefully (unknown constructs fall through as paragraph text).

Public entrypoint: :func:`markdown_to_storage`.
"""

from __future__ import annotations

import re
from typing import List, Tuple

__all__ = ["markdown_to_storage", "extract_title"]

# ---------------------------------------------------------------------------
# Inline rendering
# ---------------------------------------------------------------------------

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# Italic content excludes the tag characters ``< >`` so an italic span can never
# straddle a ``<strong>`` boundary produced by the bold pass (which would emit
# overlapping, non-well-formed tags for inputs like ``**a *b***``).
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)([^*<>]+?)(?<!\s)\*(?!\*)")


def _escape(text: str) -> str:
    """Escape the XML-significant characters for storage-format text/attributes."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _split_code_spans(text: str) -> List[Tuple[bool, str]]:
    """Split text into ``(is_code, segment)`` pieces, honoring backtick runs.

    A code span opens with a run of N backticks and closes with the next run of
    exactly N backticks. Unbalanced backticks are emitted as literal text."""
    segments: List[Tuple[bool, str]] = []
    pattern = re.compile(r"`+")
    pos = 0
    while True:
        match = pattern.search(text, pos)
        if not match:
            segments.append((False, text[pos:]))
            return segments
        ticks = match.group(0)
        close = text.find(ticks, match.end())
        if close == -1:
            # No matching closer; treat the backticks as literal text.
            segments.append((False, text[pos:match.end()]))
            pos = match.end()
            continue
        if match.start() > pos:
            segments.append((False, text[pos:match.start()]))
        segments.append((True, text[match.end():close].strip()))
        pos = close + len(ticks)


def _render_emphasis_and_links(escaped: str) -> str:
    """Apply bold, then italic, then links to already-escaped text.

    Emphasis runs first, while the text holds no tags, so a span can only contain
    plain characters — keeping every ``<strong>``/``<em>`` pair balanced. Links are
    substituted last; a label that already contains emphasis tags still matches the
    link regex (``[^\\]]+``). Operating on escaped text is safe because none of
    ``* [ ] ( )`` are escaped, while any ``&`` inside a URL becomes a valid
    ``&amp;`` for the href."""
    def link_sub(match: "re.Match[str]") -> str:
        label, href = match.group(1), match.group(2)
        return f'<a href="{href}">{label}</a>'

    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC_RE.sub(r"<em>\1</em>", escaped)
    escaped = _LINK_RE.sub(link_sub, escaped)
    return escaped


def render_inline(text: str) -> str:
    """Render a single line of inline Markdown to storage-format XHTML."""
    out: List[str] = []
    for is_code, segment in _split_code_spans(text):
        if is_code:
            out.append(f"<code>{_escape(segment)}</code>")
        else:
            out.append(_render_emphasis_and_links(_escape(segment)))
    return "".join(out)


# ---------------------------------------------------------------------------
# Block-level detection
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_FENCE_RE = re.compile(r"^(```|~~~)(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^(```|~~~)\s*$")
_HR_RE = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_LIST_ITEM_RE = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
_TASK_RE = re.compile(r"^\[([ xX])\]\s+(.*)$")
_TABLE_SEP_CELL_RE = re.compile(r"^:?-+:?$")


def _is_table_separator(line: str) -> bool:
    """True for a GFM table separator row (``|---|---|``), any column count."""
    stripped = line.strip()
    if "|" not in stripped:
        return False
    cells = [cell.strip() for cell in _split_table_row(stripped)]
    non_empty = [cell for cell in cells if cell]
    return bool(non_empty) and all(_TABLE_SEP_CELL_RE.match(cell) for cell in non_empty)


def _is_table_start(lines: List[str], index: int) -> bool:
    return (
        "|" in lines[index]
        and index + 1 < len(lines)
        and _is_table_separator(lines[index + 1])
    )


def _starts_block(lines: List[str], index: int) -> bool:
    """True when the line begins a non-paragraph block (so paragraphs stop)."""
    line = lines[index]
    return bool(
        _HEADING_RE.match(line)
        or _FENCE_RE.match(line)
        or _HR_RE.match(line)
        or _LIST_ITEM_RE.match(line)
        or line.lstrip().startswith(">")
        or _is_table_start(lines, index)
    )


# ---------------------------------------------------------------------------
# Block renderers
# ---------------------------------------------------------------------------

def _code_macro(language: str, code: str) -> str:
    language = language.strip()
    lang_param = (
        f'<ac:parameter ac:name="language">{_escape(language)}</ac:parameter>'
        if language
        else ""
    )
    # CDATA can't contain the literal "]]>" sequence; split it if present.
    safe_code = code.replace("]]>", "]]]]><![CDATA[>")
    return (
        '<ac:structured-macro ac:name="code">'
        f"{lang_param}"
        f"<ac:plain-text-body><![CDATA[{safe_code}]]></ac:plain-text-body>"
        "</ac:structured-macro>"
    )


def _split_table_row(line: str) -> List[str]:
    line = line.strip()
    # Protect escaped pipes before splitting on cell separators.
    line = line.replace(r"\|", "\0")
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip().replace("\0", "|") for cell in line.split("|")]


def _render_table(table_lines: List[str]) -> str:
    rows = [_split_table_row(line) for line in table_lines]
    header = rows[0]
    body = rows[2:]  # rows[1] is the `---|---` separator
    parts = ["<table><tbody>"]
    parts.append(
        "<tr>" + "".join(f"<th>{render_inline(cell)}</th>" for cell in header) + "</tr>"
    )
    for row in body:
        parts.append(
            "<tr>" + "".join(f"<td>{render_inline(cell)}</td>" for cell in row) + "</tr>"
        )
    parts.append("</tbody></table>")
    return "".join(parts)


def _render_li(item: dict) -> str:
    body = render_inline(item["text"])
    if item["task"] is not None:
        status = "complete" if item["task"] else "incomplete"
        return (
            "<ac:task><ac:task-status>"
            f"{status}</ac:task-status><ac:task-body>{body}</ac:task-body></ac:task>"
        )
    return f"<li>{body}</li>"


def _li_close(is_task: bool) -> str:
    return "</ac:task>" if is_task else "</li>"


def _build_list(items: List[dict], idx: int) -> Tuple[str, int]:
    """Build one list (and its nested children) starting at ``items[idx]``.

    Items carry an ``indent`` (column count); a deeper indent than the current
    level becomes a nested list grafted into the preceding item."""
    base_indent = items[idx]["indent"]
    is_task = items[idx]["task"] is not None
    if is_task:
        open_tag, close_tag = "<ac:task-list>", "</ac:task-list>"
    else:
        tag = "ol" if items[idx]["ordered"] else "ul"
        open_tag, close_tag = f"<{tag}>", f"</{tag}>"

    out = [open_tag]
    while idx < len(items) and items[idx]["indent"] >= base_indent:
        if items[idx]["indent"] > base_indent:
            child, idx = _build_list(items, idx)
            # Graft the nested list inside the previous item, before its close tag.
            closer = _li_close(is_task)
            out[-1] = out[-1][: -len(closer)] + child + closer
            continue
        out.append(_render_li(items[idx]))
        idx += 1
    out.append(close_tag)
    return "".join(out), idx


def _render_list(block_lines: List[str]) -> str:
    items: List[dict] = []
    for line in block_lines:
        match = _LIST_ITEM_RE.match(line)
        if not match:
            # A non-marker indented line continues the previous item's text.
            if items:
                items[-1]["text"] += " " + line.strip()
            continue
        indent = len(match.group(1).replace("\t", "    "))
        marker = match.group(2)
        text = match.group(3)
        ordered = bool(re.match(r"\d+\.", marker))
        task = None
        task_match = _TASK_RE.match(text)
        if task_match:
            task = task_match.group(1).lower() == "x"
            text = task_match.group(2)
        items.append({"indent": indent, "ordered": ordered, "task": task, "text": text})
    if not items:
        return ""
    html, _ = _build_list(items, 0)
    return html


# ---------------------------------------------------------------------------
# Top-level conversion
# ---------------------------------------------------------------------------

def markdown_to_storage(markdown: str) -> str:
    """Convert a Markdown document to Confluence storage-format XHTML."""
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: List[str] = []
    i = 0
    total = len(lines)

    while i < total:
        line = lines[i]

        if not line.strip():
            i += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            language = fence.group(2)
            i += 1
            code_lines: List[str] = []
            while i < total and not _FENCE_CLOSE_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            i += 1  # consume the closing fence (if present)
            blocks.append(_code_macro(language, "\n".join(code_lines)))
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            text = heading.group(2).strip().rstrip("#").strip()
            blocks.append(f"<h{level}>{render_inline(text)}</h{level}>")
            i += 1
            continue

        if _HR_RE.match(line):
            blocks.append("<hr/>")
            i += 1
            continue

        if line.lstrip().startswith(">"):
            quote_lines: List[str] = []
            while i < total and lines[i].lstrip().startswith(">"):
                quote_lines.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            inner = markdown_to_storage("\n".join(quote_lines))
            blocks.append(f"<blockquote>{inner}</blockquote>")
            continue

        if _is_table_start(lines, i):
            table_lines: List[str] = []
            while i < total and lines[i].strip() and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            blocks.append(_render_table(table_lines))
            continue

        if _LIST_ITEM_RE.match(line):
            list_lines: List[str] = []
            # Gather contiguous list lines plus their indented continuations.
            while i < total and lines[i].strip() and (
                _LIST_ITEM_RE.match(lines[i]) or lines[i].startswith((" ", "\t"))
            ):
                list_lines.append(lines[i])
                i += 1
            blocks.append(_render_list(list_lines))
            continue

        # Paragraph: gather soft-wrapped lines until a blank line or a new block.
        para: List[str] = []
        while i < total and lines[i].strip() and not _starts_block(lines, i):
            para.append(lines[i].strip())
            i += 1
        blocks.append(f"<p>{render_inline(' '.join(para))}</p>")

    return "".join(blocks)


def extract_title(markdown: str) -> Tuple[str, str]:
    """Return ``(title, body_without_title)`` using the first ATX ``# `` heading.

    Falls back to ``("", markdown)`` when the document has no level-1 heading, so
    the caller can supply its own title (e.g. from the filename)."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    for index, line in enumerate(lines):
        match = re.match(r"^#\s+(.*)$", line)
        if match:
            title = match.group(1).strip().rstrip("#").strip()
            remaining = lines[:index] + lines[index + 1:]
            return title, "\n".join(remaining)
        if line.strip():
            # First non-blank line isn't an H1; don't hunt deeper.
            break
    return "", markdown
