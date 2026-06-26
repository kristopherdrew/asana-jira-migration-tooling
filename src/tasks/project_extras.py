"""Helpers for fetching project-level status updates and comments from Asana.

In Asana a project can carry narrative content that is independent of any task:

* **Status updates** (`GET /status_updates?parent={project_gid}`) — structured posts
  with a ``title``, ``status_type`` (e.g. ``on_track``), and rich text.
* **Comments** on the project itself, surfaced as ``comment_added`` stories in the
  project activity feed (`GET /projects/{project_gid}/stories`).

Status updates are also echoed into the story feed as ``comment_added`` mirrors (same
author + ``created_at``). :func:`merge_project_updates` folds both sources into a single
normalized list tagged with ``kind`` (``status_update`` / ``comment``), keeping the richer
status-update row whenever a comment merely mirrors it.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from src.lib.http_client import AsanaHttpClient


STATUS_UPDATE_FIELDS = [
    "gid",
    "title",
    "status_type",
    "text",
    "html_text",
    "created_at",
    "created_by.gid",
    "created_by.name",
    "modified_at",
    "resource_subtype",
]

PROJECT_COMMENT_FIELDS = [
    "gid",
    "created_at",
    "created_by.gid",
    "created_by.name",
    "text",
    "html_text",
    "is_edited",
    "resource_subtype",
]

KIND_STATUS_UPDATE = "status_update"
KIND_COMMENT = "comment"


def fetch_project_status_updates(
    client: AsanaHttpClient,
    project_gid: str,
    page_size: int = 100,
) -> List[Dict]:
    """Return the status updates posted on a project."""
    params = {
        "parent": project_gid,
        "opt_fields": ",".join(STATUS_UPDATE_FIELDS),
        "limit": str(page_size),
    }
    return list(client.get_paginated("/status_updates", params=params))


def fetch_project_comments(
    client: AsanaHttpClient,
    project_gid: str,
    page_size: int = 100,
) -> List[Dict]:
    """Return user-authored comments from a project's activity feed."""
    params = {
        "opt_fields": ",".join(PROJECT_COMMENT_FIELDS),
        "limit": str(page_size),
    }
    endpoint = f"/projects/{project_gid}/stories"
    comments: List[Dict] = []
    for story in client.get_paginated(endpoint, params=params):
        if story.get("resource_subtype") == "comment_added":
            comments.append(story)
    return comments


def _mirror_key(record: Dict) -> tuple[Optional[str], Optional[str]]:
    """Identity used to detect a comment that merely mirrors a status update."""
    created_by = record.get("created_by") or {}
    return created_by.get("gid"), record.get("created_at")


def _normalize_status_update(update: Dict) -> Dict:
    created_by = update.get("created_by") or {}
    return {
        "gid": update.get("gid"),
        "kind": KIND_STATUS_UPDATE,
        "title": update.get("title"),
        "status_type": update.get("status_type"),
        "text": update.get("text"),
        "html_text": update.get("html_text"),
        "created_at": update.get("created_at"),
        "created_by_gid": created_by.get("gid"),
        "modified_at": update.get("modified_at"),
        "resource_subtype": update.get("resource_subtype"),
        "is_edited": None,
    }


def _normalize_comment(comment: Dict) -> Dict:
    created_by = comment.get("created_by") or {}
    return {
        "gid": comment.get("gid"),
        "kind": KIND_COMMENT,
        "title": None,
        "status_type": None,
        "text": comment.get("text"),
        "html_text": comment.get("html_text"),
        "created_at": comment.get("created_at"),
        "created_by_gid": created_by.get("gid"),
        "modified_at": None,
        "resource_subtype": comment.get("resource_subtype"),
        "is_edited": comment.get("is_edited"),
    }


def merge_project_updates(
    status_updates: List[Dict],
    comments: List[Dict],
) -> List[Dict]:
    """Fold status updates and project comments into one normalized, deduped list.

    A comment whose ``(created_by_gid, created_at)`` matches a status update is an
    auto-generated mirror of that update and is dropped in favor of the richer
    status-update row. The remaining rows are returned sorted by ``created_at``.
    """
    status_keys = {
        _mirror_key(update)
        for update in status_updates
        if isinstance(update, dict) and update.get("gid")
    }

    rows: List[Dict] = []
    for update in status_updates:
        if isinstance(update, dict) and update.get("gid"):
            rows.append(_normalize_status_update(update))

    for comment in comments:
        if not isinstance(comment, dict) or not comment.get("gid"):
            continue
        if _mirror_key(comment) in status_keys:
            continue
        rows.append(_normalize_comment(comment))

    rows.sort(key=lambda row: (row.get("created_at") or "", row.get("gid") or ""))
    return rows
