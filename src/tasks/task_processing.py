"""Shared helpers for task sync workflows."""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Set

from src.lib.database import Database
from src.lib.http_client import AsanaHttpClient
from src.tasks.custom_fields import fetch_custom_field


def collect_custom_field_gids(task: Dict) -> Set[str]:
    """Return the set of custom field GIDs referenced by the task payload."""
    gids: Set[str] = set()
    for custom_field in task.get("custom_fields") or []:
        if isinstance(custom_field, dict):
            gid = custom_field.get("gid")
            if gid:
                gids.add(gid)
    return gids


def merge_task_payload(listing: Dict, detail: Dict) -> Dict:
    """Combine list + detail payloads so downstream consumers see a single dict."""
    payload = dict(detail or {})
    for key in ("memberships", "custom_fields", "workspace", "parent"):
        if not payload.get(key) and listing.get(key):
            payload[key] = listing.get(key)
    for shallow_key in (
        "assignee",
        "assignee_status",
        "assignee_status_updated_at",
        "completed",
        "completed_at",
        "completed_by",
        "due_on",
        "due_at",
        "start_on",
        "start_at",
        "permalink_url",
        "actual_time_minutes",
        "liked",
        "num_likes",
    ):
        if shallow_key not in payload and shallow_key in listing:
            payload[shallow_key] = listing.get(shallow_key)
    return payload


def ensure_custom_fields(
    connection,
    database: Database,
    client: AsanaHttpClient,
    known_custom_fields: Set[str],
    field_gids: Set[str],
    sync_time,
) -> None:
    """Fetch and store any missing custom fields before inserting the task."""
    missing = [gid for gid in field_gids if gid not in known_custom_fields]
    for gid in missing:
        custom_field = fetch_custom_field(client, gid)
        database.upsert_custom_field(connection, custom_field, sync_time)
        known_custom_fields.add(gid)
