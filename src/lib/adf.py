"""Helpers for building Atlassian Document Format (ADF) payloads.

The Jira Cloud REST v3 API expects rich-text fields (e.g. ``description``) as ADF
JSON documents rather than the plain strings the CSV import wizard accepts. These
helpers convert plain multi-line text into a minimal ADF document.
"""

from __future__ import annotations

from typing import Dict, List


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
