"""Business logic for fetching custom field definitions from Asana."""

from __future__ import annotations

from typing import Dict, Generator, Iterable, Optional

from src.lib.http_client import AsanaHttpClient


CUSTOM_FIELD_FIELDS_REQUIRED = [
    "gid",
    "name",
    "resource_type",
    "resource_subtype",
    "description",
    "enabled",
    "is_global_to_workspace",
    "workspace.gid",
    "workspace.name",
    "format",
    "precision",
    "currency_code",
    "custom_label",
    "custom_label_position",
    "is_formula_field",
    "modified_at",
    "enum_options.gid",
    "enum_options.name",
    "enum_options.color",
    "enum_options.enabled",
]


def fetch_workspace_custom_fields(
    client: AsanaHttpClient,
    workspace_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
    page_size: int = 100,
) -> Generator[Dict, None, None]:
    """Yield custom field definitions for the workspace, handling pagination."""
    fields = ",".join(opt_fields or CUSTOM_FIELD_FIELDS_REQUIRED)
    params = {"opt_fields": fields, "limit": str(page_size)}
    endpoint = f"/workspaces/{workspace_gid}/custom_fields"
    yield from client.get_paginated(endpoint, params=params)


def fetch_custom_field(
    client: AsanaHttpClient,
    custom_field_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
) -> Dict:
    """Return the full definition for a single custom field."""
    fields = ",".join(opt_fields or CUSTOM_FIELD_FIELDS_REQUIRED)
    params = {"opt_fields": fields}
    endpoint = f"/custom_fields/{custom_field_gid}"
    payload = client.get(endpoint, params=params)
    return payload.get("data", payload)
