"""Business logic for fetching users from Asana workspaces."""

from __future__ import annotations

from typing import Dict, Generator, Iterable, Optional

from src.lib.http_client import AsanaHttpClient


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
