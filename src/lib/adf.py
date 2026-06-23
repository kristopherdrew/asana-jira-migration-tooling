"""Helpers for building Atlassian Document Format (ADF) payloads.

The Jira Cloud REST v3 API expects rich-text fields (e.g. ``description``) as ADF
JSON documents rather than the plain strings the CSV import wizard accepts. This
module offers two layers:

* ``text_to_adf`` — convert plain multi-line text into flat ADF paragraphs.
* A small structured-block model (``Heading``/``Paragraph``/``BulletList`` built from
  ``Run`` text spans) that renders to BOTH rich ADF (``render_blocks_adf``) and plain
  text (``render_blocks_text``), so the same content can feed the API (rich) and the
  CSV export (plain) without drifting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Union


def text_to_adf(text: str) -> Dict[str, object]:
    """Convert plain text into a minimal ADF ``doc``.

    Each non-empty line becomes its own paragraph node. Empty lines act as
    separators and are dropped (ADF paragraphs cannot contain empty text nodes).
    An empty or whitespace-only input yields a document with a single empty
    paragraph so the field still validates.
    """
    content: List[Dict[str, object]] = []
    for line in (text or "").splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue
        content.append(
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": stripped}],
            }
        )

    if not content:
        content.append({"type": "paragraph", "content": []})

    return {
        "type": "doc",
        "version": 1,
        "content": content,
    }


# ----------------------------------------------------------------------------
# Structured blocks → rich ADF or plain text
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class Run:
    """An inline text span, optionally bold."""

    text: str
    bold: bool = False


# A run-like value: a Run, or a plain string (treated as non-bold).
RunLike = Union[Run, str]


@dataclass(frozen=True)
class Heading:
    text: str
    level: int = 2


@dataclass(frozen=True)
class Paragraph:
    runs: Sequence[RunLike] = field(default_factory=tuple)


@dataclass(frozen=True)
class BulletList:
    items: Sequence[Sequence[RunLike]] = field(default_factory=tuple)


Block = Union[Heading, Paragraph, BulletList]


def _as_runs(runs: Sequence[RunLike]) -> List[Run]:
    return [Run(run) if isinstance(run, str) else run for run in runs]


def text_run(text: str, bold: bool = False) -> Dict[str, object]:
    """Build an ADF text node, adding a ``strong`` mark when ``bold``."""
    node: Dict[str, object] = {"type": "text", "text": text}
    if bold:
        node["marks"] = [{"type": "strong"}]
    return node


def _runs_to_nodes(runs: Sequence[RunLike]) -> List[Dict[str, object]]:
    return [text_run(run.text, run.bold) for run in _as_runs(runs)]


def heading(text: str, level: int = 2) -> Dict[str, object]:
    return {
        "type": "heading",
        "attrs": {"level": level},
        "content": [text_run(text)],
    }


def paragraph(runs: Sequence[RunLike]) -> Dict[str, object]:
    return {"type": "paragraph", "content": _runs_to_nodes(runs)}


def bullet_list(items: Sequence[Sequence[RunLike]]) -> Dict[str, object]:
    return {
        "type": "bulletList",
        "content": [
            {
                "type": "listItem",
                "content": [{"type": "paragraph", "content": _runs_to_nodes(item)}],
            }
            for item in items
        ],
    }


def render_blocks_adf(blocks: Sequence[Block]) -> Dict[str, object]:
    """Render structured blocks into a rich ADF ``doc``."""
    content: List[Dict[str, object]] = []
    for block in blocks:
        if isinstance(block, Heading):
            content.append(heading(block.text, block.level))
        elif isinstance(block, Paragraph):
            content.append(paragraph(block.runs))
        elif isinstance(block, BulletList):
            content.append(bullet_list(block.items))
        else:  # pragma: no cover - defensive
            raise TypeError(f"Unsupported block type: {type(block)!r}")
    if not content:
        content.append({"type": "paragraph", "content": []})
    return {"type": "doc", "version": 1, "content": content}


def render_blocks_text(blocks: Sequence[Block]) -> str:
    """Render structured blocks into plain text (for CSV / non-rich fallback).

    Headings sit on their own line, bullets use a ``  • `` prefix, bold runs render
    as their plain text, and blocks are separated by a blank line.
    """
    chunks: List[str] = []
    for block in blocks:
        if isinstance(block, Heading):
            chunks.append(block.text)
        elif isinstance(block, Paragraph):
            chunks.append("".join(run.text for run in _as_runs(block.runs)))
        elif isinstance(block, BulletList):
            lines = [
                "  • " + "".join(run.text for run in _as_runs(item))
                for item in block.items
            ]
            chunks.append("\n".join(lines))
        else:  # pragma: no cover - defensive
            raise TypeError(f"Unsupported block type: {type(block)!r}")
    return "\n\n".join(chunks)
