"""Helpers for fetching task comments and attachments from Asana."""

from __future__ import annotations

from typing import Dict, Iterable, List

from src.lib.http_client import AsanaHttpClient


COMMENT_FIELDS = [
    "gid",
    "created_at",
    "created_by.gid",
    "created_by.name",
    "text",
    "html_text",
    "is_edited",
    "resource_subtype",
]

ATTACHMENT_FIELDS = [
    "gid",
    "name",
    "resource_subtype",
    "host",
    "service",
    "size",
    "created_at",
    "created_by.gid",
    "created_by.name",
    "permanent_url",
    "view_url",
    "parent.gid",
    "parent.resource_type",
]


def fetch_task_comments(
    client: AsanaHttpClient,
    task_gid: str,
    page_size: int = 100,
) -> List[Dict]:
    """Return user-authored comments for a task."""
    params = {
        "opt_fields": ",".join(COMMENT_FIELDS),
        "limit": str(page_size),
    }
    endpoint = f"/tasks/{task_gid}/stories"
    comments: List[Dict] = []
    for story in client.get_paginated(endpoint, params=params):
        if story.get("resource_subtype") == "comment_added":
            comments.append(story)
    return comments


def fetch_task_attachments(
    client: AsanaHttpClient,
    task_gid: str,
    page_size: int = 100,
) -> List[Dict]:
    """Return attachments linked to a task."""
    params = {
        "opt_fields": ",".join(ATTACHMENT_FIELDS),
        "limit": str(page_size),
    }
    endpoint = f"/tasks/{task_gid}/attachments"
    return list(client.get_paginated(endpoint, params=params))
