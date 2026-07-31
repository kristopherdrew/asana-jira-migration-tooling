"""Business logic for fetching users from Asana workspaces."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Dict, Generator, Iterable, Optional

import logging

from src.lib.http_client import AsanaHttpClient, AsanaHttpError

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids import cycle
    import sqlite3

    from src.lib.database import Database

logger = logging.getLogger(__name__)


USER_FIELDS_REQUIRED = [
    "gid",
    "name",
    "resource_type",
    "email",
    "is_active",
    "workspaces",
]


def fetch_workspace_users(
    client: AsanaHttpClient,
    workspace_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
    page_size: int = 100,
) -> Generator[Dict, None, None]:
    """Yield users visible within the workspace, handling pagination."""
    fields = ",".join(opt_fields or USER_FIELDS_REQUIRED)
    params = {"opt_fields": fields, "limit": str(page_size)}
    endpoint = f"/workspaces/{workspace_gid}/users"
    yield from client.get_paginated(endpoint, params=params)


def fetch_user(
    client: AsanaHttpClient,
    user_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
) -> Dict:
    """Return the full user profile for the given GID."""
    fields = ",".join(opt_fields or USER_FIELDS_REQUIRED)
    params = {"opt_fields": fields}
    endpoint = f"/users/{user_gid}"
    payload = client.get(endpoint, params=params)
    return payload.get("data", payload)


def fetch_current_user(
    client: AsanaHttpClient,
    opt_fields: Optional[Iterable[str]] = None,
) -> Dict:
    """Return the full user profile for the token's own account (``GET /users/me``)."""
    fields = ",".join(opt_fields or USER_FIELDS_REQUIRED)
    params = {"opt_fields": fields}
    payload = client.get("/users/me", params=params)
    return payload.get("data", payload)


def resolve_service_user_gid(
    client: AsanaHttpClient,
    database: "Database",
    connection: "sqlite3.Connection",
    synced_at: datetime,
) -> str:
    """Resolve the service user as the token's own account and persist it.

    Fetches ``GET /users/me`` and upserts the record into the ``users`` table so
    the service account (which is often absent from the workspace user list) is
    searchable afterwards. Returns the resolved GID; raises ``ValueError`` when the
    API returns no GID.
    """
    me = fetch_current_user(client)
    gid = (me or {}).get("gid")
    if not gid:
        raise ValueError("Asana /users/me returned no user GID; the token may be invalid.")
    database.upsert_user(connection, me, synced_at)
    return str(gid)


def fetch_user_task_list(
    client: AsanaHttpClient,
    user_gid: str,
    workspace_gid: str,
) -> Optional[str]:
    """Return the GID of the user's "My Tasks" list within the workspace.

    Another user's My Tasks is private and only readable with an org-admin /
    service-account token; when it is inaccessible (e.g. 403/404) we log and
    return ``None`` so a single unreadable user never aborts the sync.
    """
    endpoint = f"/users/{user_gid}/user_task_list"
    params = {"workspace": workspace_gid, "opt_fields": "gid"}
    try:
        payload = client.get(endpoint, params=params)
    except AsanaHttpError as exc:
        logger.warning(
            "Could not fetch user task list for user %s: %s", user_gid, exc
        )
        return None
    data = payload.get("data", payload) or {}
    return data.get("gid")
