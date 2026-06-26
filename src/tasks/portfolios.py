"""Business logic for fetching portfolios and related metadata from Asana.

Asana portfolios group projects (and other portfolios) outside the team/project
hierarchy. Unlike projects, portfolios cannot be listed workspace-wide: the
``GET /portfolios`` endpoint requires an ``owner``, so callers enumerate portfolios
per owner. Each portfolio carries several sub-resources fetched here:

* **Items** (`GET /portfolios/{gid}/items`) — the member projects and nested
  portfolios, returned in display order.
* **Custom field settings** (`GET /portfolios/{gid}/custom_field_settings`) — the
  portfolio-scoped custom fields.
* **Memberships** (`GET /portfolio_memberships?portfolio={gid}`) — users with access.
* **Status updates** (`GET /status_updates?parent={gid}`) — the same endpoint used
  for project status updates; reuses :data:`STATUS_UPDATE_FIELDS`.
"""

from __future__ import annotations

from typing import Dict, Generator, Iterable, List, Optional

from src.lib.http_client import AsanaHttpClient
from src.tasks.project_extras import STATUS_UPDATE_FIELDS


PORTFOLIO_FIELDS_DEFAULT = [
    "gid",
    "name",
    "color",
    "permalink_url",
    "public",
    "owner.gid",
    "owner.name",
    "created_by.gid",
    "created_by.name",
    "created_at",
    "start_on",
    "due_on",
    "workspace.gid",
    "current_status_update.status_type",
    "current_status_update.title",
]

PORTFOLIO_ITEM_FIELDS = [
    "gid",
    "name",
    "resource_type",
]

PORTFOLIO_CUSTOM_FIELD_SETTING_FIELDS = [
    "is_important",
    "custom_field.gid",
    "custom_field.name",
    "custom_field.type",
    "custom_field.is_global_to_workspace",
]

PORTFOLIO_MEMBERSHIP_FIELDS = [
    "user.gid",
    "user.name",
    "access_level",
]


def fetch_user_portfolios(
    client: AsanaHttpClient,
    workspace_gid: str,
    owner_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
    page_size: int = 100,
) -> Generator[Dict, None, None]:
    """Yield the portfolios owned by ``owner_gid`` in the workspace."""
    fields = ",".join(opt_fields or PORTFOLIO_FIELDS_DEFAULT)
    params = {
        "workspace": workspace_gid,
        "owner": owner_gid,
        "opt_fields": fields,
        "limit": str(page_size),
    }
    yield from client.get_paginated("/portfolios", params=params)


def fetch_portfolio(
    client: AsanaHttpClient,
    portfolio_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
) -> Dict:
    """Fetch a single portfolio record (used when recursing into nested portfolios)."""
    fields = ",".join(opt_fields or PORTFOLIO_FIELDS_DEFAULT)
    payload = client.get(f"/portfolios/{portfolio_gid}", params={"opt_fields": fields})
    return payload.get("data") or {}


def fetch_portfolio_items(
    client: AsanaHttpClient,
    portfolio_gid: str,
    page_size: int = 100,
) -> List[Dict]:
    """Return the items (projects and nested portfolios) contained in a portfolio."""
    params = {
        "opt_fields": ",".join(PORTFOLIO_ITEM_FIELDS),
        "limit": str(page_size),
    }
    return list(client.get_paginated(f"/portfolios/{portfolio_gid}/items", params=params))


def fetch_portfolio_custom_field_settings(
    client: AsanaHttpClient,
    portfolio_gid: str,
    page_size: int = 100,
) -> List[Dict]:
    """Return the portfolio-scoped custom field settings."""
    params = {
        "opt_fields": ",".join(PORTFOLIO_CUSTOM_FIELD_SETTING_FIELDS),
        "limit": str(page_size),
    }
    return list(client.get_paginated(f"/portfolios/{portfolio_gid}/custom_field_settings", params=params))


def fetch_portfolio_memberships(
    client: AsanaHttpClient,
    portfolio_gid: str,
    page_size: int = 100,
) -> List[Dict]:
    """Return the memberships (users with access) for a portfolio."""
    params = {
        "portfolio": portfolio_gid,
        "opt_fields": ",".join(PORTFOLIO_MEMBERSHIP_FIELDS),
        "limit": str(page_size),
    }
    return list(client.get_paginated("/portfolio_memberships", params=params))


def fetch_portfolio_status_updates(
    client: AsanaHttpClient,
    portfolio_gid: str,
    page_size: int = 100,
) -> List[Dict]:
    """Return the status updates posted on a portfolio."""
    params = {
        "parent": portfolio_gid,
        "opt_fields": ",".join(STATUS_UPDATE_FIELDS),
        "limit": str(page_size),
    }
    return list(client.get_paginated("/status_updates", params=params))
