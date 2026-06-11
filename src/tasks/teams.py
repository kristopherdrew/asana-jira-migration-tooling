"""Business logic for fetching teams and related memberships from Asana."""

from __future__ import annotations

from typing import Dict, Generator, Iterable, List, Optional

from src.lib.http_client import AsanaHttpClient


TEAM_FIELDS_DEFAULT = [
    "gid",
    "name",
    "description",
    "permalink_url",
    "organization",
    "workspace",
    "is_public",
    "created_at",
    "modified_at",
]

TEAM_MEMBERSHIP_FIELDS_DEFAULT = [
    "gid",
    "user.gid",
    "user.name",
    "user.email",
    "user.is_active",
    "team.gid",
    "is_guest",
    "is_admin",
    "is_limited_access",
]


def fetch_workspace_teams(
    client: AsanaHttpClient,
    workspace_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
    page_size: int = 100,
) -> Generator[Dict, None, None]:
    """Yield teams for the given workspace, handling pagination."""
    fields = ",".join(opt_fields or TEAM_FIELDS_DEFAULT)
    params = {"opt_fields": fields, "limit": str(page_size)}
    endpoint = f"/workspaces/{workspace_gid}/teams"
    yield from client.get_paginated(endpoint, params=params)


def fetch_team_memberships(
    client: AsanaHttpClient,
    team_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
    page_size: int = 50,
) -> List[Dict]:
    """Return a list of team memberships for a given team."""
    fields = ",".join(opt_fields or TEAM_MEMBERSHIP_FIELDS_DEFAULT)
    params = {"opt_fields": fields, "limit": str(page_size)}
    endpoint = f"/teams/{team_gid}/team_memberships"
    return list(client.get_paginated(endpoint, params=params))
