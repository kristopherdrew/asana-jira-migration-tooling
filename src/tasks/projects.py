"""Business logic for fetching projects and related metadata from Asana."""

from __future__ import annotations

from typing import Dict, Generator, Iterable, Optional
from datetime import datetime, timezone

from src.lib.http_client import AsanaHttpClient


PROJECT_FIELDS_DEFAULT = [
    "gid",
    "name",
    "resource_subtype",
    "archived",
    "permalink_url",
    "created_at",
    "modified_at",
    "start_on",
    "due_on",
    "completed",
    "completed_at",
    "completed_by.gid",
    "owner.gid",
    "owner.name",
    "team.gid",
    "team.name",
    "members.gid",
    "members.name",
    "privacy_setting",
    "notes",
    "html_notes",
    "custom_fields.gid",
    "custom_fields.name",
    "custom_fields.type",
    "custom_fields.enum_value.gid",
    "custom_fields.enum_value.name",
    "custom_fields.number_value",
    "custom_fields.text_value",
    "custom_fields.people_value.gid",
    "custom_fields.people_value.name",
    "custom_fields.multi_enum_values.gid",
    "custom_fields.multi_enum_values.name",
    "custom_fields.enabled",
    "custom_field_settings.is_important",
    "custom_field_settings.custom_field.gid",
    "custom_field_settings.custom_field.name",
    "custom_field_settings.custom_field.type",
    "custom_field_settings.custom_field.is_global_to_workspace",
]

PROJECT_TASK_COUNT_FIELDS = [
    "num_tasks",
    "num_incomplete_tasks",
    "num_completed_tasks",
]


def fetch_workspace_projects(
    client: AsanaHttpClient,
    workspace_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
    page_size: int = 100,
    archived: Optional[bool] = None,
) -> Generator[Dict, None, None]:
    """Yield projects for the given workspace, handling pagination."""
    fields = ",".join(opt_fields or PROJECT_FIELDS_DEFAULT)
    params = {"opt_fields": fields, "limit": str(page_size)}
    if archived is not None:
        params["archived"] = "true" if archived else "false"
    endpoint = f"/workspaces/{workspace_gid}/projects"
    yield from client.get_paginated(endpoint, params=params)


def fetch_team_projects(
    client: AsanaHttpClient,
    team_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
    page_size: int = 100,
    archived: Optional[bool] = None,
) -> Generator[Dict, None, None]:
    """Yield projects for one team, handling pagination."""
    fields = ",".join(opt_fields or PROJECT_FIELDS_DEFAULT)
    params = {"opt_fields": fields, "limit": str(page_size)}
    if archived is not None:
        params["archived"] = "true" if archived else "false"
    yield from client.get_paginated(f"/teams/{team_gid}/projects", params=params)


def fetch_project_task_counts(client: AsanaHttpClient, project_gid: str) -> Dict[str, Optional[int] | str]:
    """Fetch the current task-count snapshot for a project."""
    endpoint = f"/projects/{project_gid}/task_counts"
    params = {"opt_fields": ",".join(PROJECT_TASK_COUNT_FIELDS)}
    payload = client.get(endpoint, params=params)
    data = payload.get("data") or {}
    return {
        "num_tasks": data.get("num_tasks"),
        "num_incomplete_tasks": data.get("num_incomplete_tasks"),
        "num_completed_tasks": data.get("num_completed_tasks"),
        "task_counts_captured_at": datetime.now(timezone.utc).isoformat(),
    }
