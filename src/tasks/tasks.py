"""Business logic for fetching tasks, details, and subtasks from Asana."""

from __future__ import annotations

from typing import Dict, Generator, Iterable, Optional, Set

from src.lib.http_client import AsanaHttpClient


TASK_LIST_FIELDS = [
    "gid",
    "name",
    "resource_type",
    "resource_subtype",
    "completed",
    "completed_at",
    "completed_by.gid",
    "completed_by.name",
    "assignee.gid",
    "assignee.name",
    "assignee_status",
    "assignee_status_updated_at",
    "created_at",
    "modified_at",
    "start_on",
    "start_at",
    "due_on",
    "due_at",
    "permalink_url",
    "parent.gid",
    "parent.name",
    "workspace.gid",
    "workspace.name",
    "actual_time_minutes",
    "liked",
    "num_likes",
    "custom_fields.gid",
    "custom_fields.type",
    "custom_fields.enabled",
    "custom_fields.display_value",
    "custom_fields.is_value_set",
    "custom_fields.text_value",
    "custom_fields.number_value",
    "custom_fields.date_value",
    "custom_fields.enum_value.gid",
    "custom_fields.multi_enum_values.gid",
    "custom_fields.multi_enum_values.name",
    "custom_fields.multi_enum_values.enabled",
    "custom_fields.people_value.gid",
    "custom_fields.people_value.name",
    "memberships.project.gid",
    "memberships.project.name",
    "memberships.is_primary",
    "memberships.section.gid",
    "memberships.section.name",
    "memberships.project.team.gid",
    "memberships.project.team.name",
    "tags.gid",
    "tags.name",
]

TASK_DETAIL_FIELDS = [
    "gid",
    "name",
    "resource_type",
    "resource_subtype",
    "notes",
    "html_notes",
    "created_at",
    "created_by.gid",
    "created_by.name",
    "modified_at",
    "start_on",
    "start_at",
    "due_on",
    "due_at",
    "completed",
    "completed_at",
    "completed_by.gid",
    "completed_by.name",
    "assignee.gid",
    "assignee.name",
    "assignee_status",
    "assignee_status_updated_at",
    "permalink_url",
    "parent.gid",
    "parent.name",
    "workspace.gid",
    "workspace.name",
    "actual_time_minutes",
    "liked",
    "num_likes",
    "followers.gid",
    "followers.name",
    "dependencies.gid",
    "dependencies.name",
    "dependents.gid",
    "dependents.name",
    "recurrence",
    "custom_fields.gid",
    "custom_fields.type",
    "custom_fields.enabled",
    "custom_fields.display_value",
    "custom_fields.is_value_set",
    "custom_fields.text_value",
    "custom_fields.number_value",
    "custom_fields.date_value",
    "custom_fields.enum_value.gid",
    "custom_fields.multi_enum_values.gid",
    "custom_fields.multi_enum_values.name",
    "custom_fields.multi_enum_values.enabled",
    "custom_fields.people_value.gid",
    "custom_fields.people_value.name",
    "memberships.project.gid",
    "memberships.project.name",
    "memberships.is_primary",
    "memberships.section.gid",
    "memberships.section.name",
    "memberships.project.team.gid",
    "memberships.project.team.name",
    "tags.gid",
    "tags.name",
]

SUBTASK_FIELDS = TASK_LIST_FIELDS


def fetch_project_tasks(
    client: AsanaHttpClient,
    project_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
    page_size: int = 100,
    modified_since: Optional[str] = None,
) -> Generator[Dict, None, None]:
    """Yield tasks for a given project using the listing endpoint."""
    fields = ",".join(opt_fields or TASK_LIST_FIELDS)
    params = {"opt_fields": fields, "limit": str(page_size)}
    if modified_since:
        params["modified_since"] = modified_since
    endpoint = f"/projects/{project_gid}/tasks"
    yield from client.get_paginated(endpoint, params=params)


def fetch_task_detail(
    client: AsanaHttpClient,
    task_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
) -> Dict:
    """Retrieve the full task record with additional metadata."""
    fields = ",".join(opt_fields or TASK_DETAIL_FIELDS)
    params = {"opt_fields": fields}
    endpoint = f"/tasks/{task_gid}"
    payload = client.get(endpoint, params=params)
    return payload.get("data", payload)


def fetch_subtasks(
    client: AsanaHttpClient,
    task_gid: str,
    opt_fields: Optional[Iterable[str]] = None,
    page_size: int = 100,
) -> Generator[Dict, None, None]:
    """Yield subtasks for a parent task."""
    fields = ",".join(opt_fields or SUBTASK_FIELDS)
    params = {"opt_fields": fields, "limit": str(page_size)}
    endpoint = f"/tasks/{task_gid}/subtasks"
    yield from client.get_paginated(endpoint, params=params)


def fetch_workspace_tasks(
    client: AsanaHttpClient,
    workspace_gid: str,
    assignee: str = "me",
    opt_fields: Optional[Iterable[str]] = None,
    page_size: int = 100,
    modified_since: Optional[str] = None,
    completed_since: Optional[str] = None,
) -> Generator[Dict, None, None]:
    """Yield tasks scoped to a workspace+assignee combination."""
    fields = ",".join(opt_fields or TASK_LIST_FIELDS)
    params = {
        "workspace": workspace_gid,
        "assignee": assignee,
        "opt_fields": fields,
        "limit": str(page_size),
    }
    if modified_since:
        params["modified_since"] = modified_since
    if completed_since:
        params["completed_since"] = completed_since
    endpoint = "/tasks"
    yield from client.get_paginated(endpoint, params=params)


def collect_user_gids_from_task(task: Dict) -> Set[str]:
    """Collect all user identifiers referenced directly in the task payload."""
    gids: Set[str] = set()
    for key in ("assignee", "created_by", "completed_by"):
        user = task.get(key) or {}
        gid = user.get("gid")
        if gid:
            gids.add(gid)
    for follower in task.get("followers") or []:
        if isinstance(follower, dict):
            gid = follower.get("gid")
            if gid:
                gids.add(gid)
    for custom_field in task.get("custom_fields") or []:
        if not isinstance(custom_field, dict):
            continue
        people_value = custom_field.get("people_value")
        if isinstance(people_value, dict):
            gid = people_value.get("gid")
            if gid:
                gids.add(gid)
        elif isinstance(people_value, list):
            for person in people_value:
                if isinstance(person, dict):
                    gid = person.get("gid")
                    if gid:
                        gids.add(gid)
    return gids
