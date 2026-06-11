"""Attachment-related helpers for Jira exports and uploads."""

from __future__ import annotations

import os
import re
from typing import Optional

# Characters that can cause issues in filenames across platforms.
UNSAFE_CHARS_PATTERN = re.compile(r"[\\/:*?\"<>|]")


def jira_unique_attachment_name(original_name: Optional[str], attachment_gid: str) -> str:
    """Return a Jira-safe, unique filename by appending the attachment gid.

    - Preserves the original extension when present (e.g., foo.png -> foo__gid.png).
    - Falls back to the gid when no name is provided.
    - Normalizes path separators and strips characters Jira/Windows disallow.
    """

    base = (original_name or "").strip() or attachment_gid
    # Normalize path separators to avoid directory traversal in temp paths.
    base = base.replace(os.sep, "_").replace("/", "_")
    base = UNSAFE_CHARS_PATTERN.sub("_", base)

    # Preserve the final extension if there is a stem.
    if "." in base and not base.startswith("."):
        stem, ext = base.rsplit(".", 1)
        stem = stem or attachment_gid
        return f"{stem}__{attachment_gid}.{ext}"

    return f"{base}__{attachment_gid}"
