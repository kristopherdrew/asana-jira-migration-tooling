"""Single source of truth for SQLite table/view context and provenance.

This registry holds the *human* context for each table and view — what it stores,
which script populates it, which Asana/Jira API endpoint (or CSV / derivation) the
data comes from, the `Database` method that writes it, and the write semantics.

Column types, foreign keys, and indexes are intentionally NOT duplicated here — those
are introspected live from the schema by `scripts/describe_db.py` so they can never
drift. This registry is the prose layer; the schema in `src/lib/database.py` is the
structural layer. A drift-guard test asserts every created table/view has an entry here.

Both `scripts/describe_db.py` (console + `--markdown` data dictionary) read from this
registry, so documentation and the introspection tool stay in lockstep.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class TableMeta:
    """Context/provenance for one table or view."""

    name: str
    group: str
    purpose: str
    source: str  # API endpoint, "CSV import", "derived in SQL", etc.
    scripts: List[str]  # populating script module paths
    writer: str  # Database.<method> that writes it (or "view" / "n/a")
    write_semantics: str
    is_view: bool = False


# Display order for groups in the data dictionary / describe output.
GROUP_ORDER: List[str] = [
    "Core entities",
    "Custom fields & tags",
    "Task sub-resources",
    "Project sub-resources",
    "Portfolio sub-resources",
    "Membership & identity",
    "Jira / Atlassian mapping",
    "Sync bookkeeping",
    "Views",
]


def _m(*args, **kwargs) -> TableMeta:
    return TableMeta(*args, **kwargs)


TABLE_METADATA: Dict[str, TableMeta] = {
    # ---------------- Core entities ----------------
    "teams": _m(
        "teams",
        "Core entities",
        "Asana team definitions in the workspace.",
        "GET /workspaces/{workspace_gid}/teams",
        ["scripts/get_teams.py"],
        "upsert_team",
        "Upsert by gid (PK).",
    ),
    "users": _m(
        "users",
        "Core entities",
        "User accounts visible in the workspace, plus their My Tasks list GID.",
        "GET /workspaces/{workspace_gid}/users; GET /users/{gid}/user_task_list",
        ["scripts/get_users.py", "scripts/get_teams.py", "(UserBackfill in task/extras syncs)"],
        "upsert_user",
        "Upsert by gid (PK); user_task_list_gid set by update_user_task_list_gid.",
    ),
    "projects": _m(
        "projects",
        "Core entities",
        "Project definitions, lifecycle metadata, and task-count snapshot; project_classification ('personal' or 'work') identifies personal projects vs team/work projects for migration tracking.",
        "GET /workspaces/{workspace_gid}/projects; GET /projects/{gid}/task_counts",
        ["scripts/get_projects.py", "scripts/get_personal_tasks.py", "scripts/get_incremental_updates.py"],
        "upsert_project",
        "Upsert by gid (PK); also writes project_custom_fields and project_custom_field_settings. project_classification is set via resource_subtype='personal_project_placeholder' → 'personal', defaults to 'work'.",
    ),
    "tasks": _m(
        "tasks",
        "Core entities",
        "Core task records (and subtasks); hierarchy_depth and is_personal flags support export; is_deleted flag marks tasks no longer found in syncs. "
        "recurrence is a JSON-serialized copy of Asana's undocumented per-task recurrence object, "
        "stored verbatim including the literal type 'never' Asana uses for explicitly non-recurring "
        "tasks (query via is_recurring()/type != 'never' rather than a NULL check to find actual "
        "recurrences — see src/tasks/recurring_tasks.py); NULL means the field was never populated. "
        "recurrence_checked_at is the ISO timestamp of the last sync whose payload included the "
        "recurrence field, distinguishing 'checked' (recurrence_checked_at set, whatever recurrence "
        "holds) from 'never checked' (both NULL). deleted_at is the ISO timestamp when the task was "
        "first marked as deleted; NULL for active tasks.",
        "GET /projects/{gid}/tasks; GET /tasks; GET /tasks/{gid}; GET /tasks/{gid}/subtasks",
        ["scripts/get_tasks.py", "scripts/get_personal_tasks.py", "scripts/get_incremental_updates.py"],
        "upsert_task; mark_tasks_as_deleted",
        "Upsert by gid (PK); also writes task_projects / task_custom_fields / task_custom_field_multi_enum / task_relationships / task_followers / task_tags / tags. "
        "recurrence / recurrence_checked_at are also updatable in isolation via Database.update_task_recurrence "
        "(single-column UPDATE) for live-API gap-fill backfills that don't have a full task payload. "
        "is_deleted / deleted_at are set by mark_tasks_as_deleted when tasks are no longer found in sync.",
    ),
    "portfolios": _m(
        "portfolios",
        "Core entities",
        "Portfolio definitions (groupings of projects/nested portfolios), owner, dates, and latest status snapshot.",
        "GET /portfolios?workspace={gid}&owner={user_gid} (per owner); GET /portfolios/{gid} (nested recursion)",
        ["scripts/get_portfolios.py"],
        "upsert_portfolio",
        "Upsert by gid (PK); also writes portfolio_custom_field_settings when settings are supplied.",
    ),
    # ---------------- Custom fields & tags ----------------
    "custom_fields": _m(
        "custom_fields",
        "Custom fields & tags",
        "Custom field definitions available in the workspace.",
        "GET /workspaces/{workspace_gid}/custom_fields",
        ["scripts/get_custom_fields.py", "scripts/get_incremental_updates.py"],
        "upsert_custom_field",
        "Upsert by gid (PK); also writes custom_field_enum_options.",
    ),
    "custom_field_enum_options": _m(
        "custom_field_enum_options",
        "Custom fields & tags",
        "Enum option catalog (dropdown choices) for enum/multi-enum custom fields.",
        "Derived from the enum_options array of GET /workspaces/{gid}/custom_fields",
        ["scripts/get_custom_fields.py", "scripts/get_incremental_updates.py"],
        "_replace_custom_field_enum_options (via upsert_custom_field)",
        "Full-replace per custom_field_gid. PK (custom_field_gid, enum_option_gid).",
    ),
    "tags": _m(
        "tags",
        "Custom fields & tags",
        "Tag definitions referenced by task_tags.",
        "Derived from each task's tags array during task sync.",
        ["scripts/get_tasks.py", "scripts/get_personal_tasks.py", "scripts/get_incremental_updates.py"],
        "replace_task_tags (via upsert_task)",
        "Upsert by gid (PK).",
    ),
    # ---------------- Task sub-resources ----------------
    "task_projects": _m(
        "task_projects",
        "Task sub-resources",
        "Junction: which projects/sections contain each task.",
        "Derived from each task's memberships array during task sync.",
        ["scripts/get_tasks.py", "scripts/get_personal_tasks.py", "scripts/get_incremental_updates.py", "scripts/backfill_child_task_projects.py"],
        "_replace_task_projects (via upsert_task)",
        "Full-replace per task_gid. PK (task_gid, project_gid). FK→tasks, FK→projects.",
    ),
    "task_custom_fields": _m(
        "task_custom_fields",
        "Task sub-resources",
        "Single-value custom field values on tasks (text/number/date/enum).",
        "Derived from each task's custom_fields array during task sync.",
        ["scripts/get_tasks.py", "scripts/get_personal_tasks.py", "scripts/get_incremental_updates.py"],
        "_replace_task_custom_fields (via upsert_task)",
        "Full-replace per task_gid. PK (task_gid, custom_field_gid).",
    ),
    "task_custom_field_multi_enum": _m(
        "task_custom_field_multi_enum",
        "Task sub-resources",
        "Multi-select custom field values on tasks (one row per selected option).",
        "Derived from each task's custom_fields[*].multi_enum_values during task sync.",
        ["scripts/get_tasks.py", "scripts/get_personal_tasks.py", "scripts/get_incremental_updates.py"],
        "_replace_task_custom_fields (via upsert_task)",
        "Full-replace per task_gid. PK (task_gid, custom_field_gid, enum_option_gid).",
    ),
    "task_relationships": _m(
        "task_relationships",
        "Task sub-resources",
        "Task dependencies and dependents (blocking relationships).",
        "Derived from each task's dependencies/dependents arrays during task sync.",
        ["scripts/get_tasks.py", "scripts/get_personal_tasks.py", "scripts/get_incremental_updates.py"],
        "_replace_task_relationships (via upsert_task)",
        "Full-replace per (task_gid, relation_type). PK (task_gid, related_task_gid, relation_type).",
    ),
    "task_followers": _m(
        "task_followers",
        "Task sub-resources",
        "Users following each task.",
        "Derived from each task's followers array during task sync.",
        ["scripts/get_tasks.py", "scripts/get_personal_tasks.py", "scripts/get_incremental_updates.py"],
        "_replace_task_followers (via upsert_task)",
        "Full-replace per task_gid. PK (task_gid, user_gid).",
    ),
    "task_comments": _m(
        "task_comments",
        "Task sub-resources",
        "Human-authored comments on tasks (comment_added stories).",
        "GET /tasks/{gid}/stories filtered to resource_subtype=comment_added",
        ["scripts/get_task_extras.py", "scripts/get_incremental_updates.py"],
        "replace_task_comments",
        "Soft-delete per task_gid then upsert by gid (PK); is_deleted flag.",
    ),
    "task_attachments": _m(
        "task_attachments",
        "Task sub-resources",
        "Files attached to tasks.",
        "GET /tasks/{gid}/attachments",
        ["scripts/get_task_extras.py", "scripts/get_incremental_updates.py"],
        "replace_task_attachments",
        "Soft-delete per task_gid then upsert by gid (PK); is_deleted flag.",
    ),
    "attachment_archives": _m(
        "attachment_archives",
        "Jira / Atlassian mapping",
        "Durable local extraction waves for delayed Asana-to-Jira attachment migration.",
        "Derived from active Asana-hosted rows in task_attachments and local archive output.",
        ["scripts/download_asana_attachments.py"],
        "create_archive",
        "Upsert by archive_id; completion totals and manifest checksum are refreshed on resume.",
    ),
    "attachment_archive_files": _m(
        "attachment_archive_files",
        "Jira / Atlassian mapping",
        "Per-file local path, byte count, checksum, and verification state for an archive wave.",
        "Downloaded bytes plus task_attachments source metadata.",
        ["scripts/download_asana_attachments.py"],
        "create_archive",
        "Upsert by (archive_id, attachment_gid); resumable and checksum verified.",
    ),
    "attachment_jira_uploads": _m(
        "attachment_jira_uploads",
        "Jira / Atlassian mapping",
        "Per-destination delivery state for archived files uploaded to Jira work items.",
        "Archive manifest joined to current task_jira_mappings and Jira attachment API responses.",
        ["scripts/upload_jira_attachments.py"],
        "upload_archive",
        "Upsert by (archive_id, attachment_gid, jira_site_key, jira_key); records retries and Jira attachment IDs.",
    ),
    "attachment_jira_weblinks": _m(
        "attachment_jira_weblinks",
        "Jira / Atlassian mapping",
        "Per-destination delivery state for Jira remote web links created from non-Asana task attachment URLs.",
        "Active non-Asana URL/app rows in task_attachments joined to current task_jira_mappings and Jira remote-link API responses.",
        ["scripts/upload_jira_attachments.py"],
        "upload_attachment_weblinks",
        "Upsert by (attachment_gid, jira_site_key, jira_key); records idempotent globalId, Jira remote-link ID, status, and retry errors.",
    ),
    "task_tags": _m(
        "task_tags",
        "Task sub-resources",
        "Junction: tags assigned to each task.",
        "Derived from each task's tags array during task sync.",
        ["scripts/get_tasks.py", "scripts/get_personal_tasks.py", "scripts/get_incremental_updates.py"],
        "replace_task_tags (via upsert_task)",
        "Full-replace per task_gid. PK (task_gid, tag_gid).",
    ),
    "task_hierarchy_flattened": _m(
        "task_hierarchy_flattened",
        "Task sub-resources",
        "Computed flattened parent reference per task for Jira-friendly hierarchy export.",
        "Derived in SQL from tasks.parent_gid / tasks.hierarchy_depth.",
        ["scripts/backfill_task_hierarchy_depth.py"],
        "rebuild_flattened_task_hierarchy",
        "Full-replace. PK task_gid. FK→tasks.",
    ),
    # ---------------- Project sub-resources ----------------
    "project_custom_fields": _m(
        "project_custom_fields",
        "Project sub-resources",
        "Custom field values set on the project itself.",
        "Derived from each project's custom_fields array during project sync.",
        ["scripts/get_projects.py", "scripts/get_incremental_updates.py"],
        "_replace_project_custom_fields (via upsert_project)",
        "Full-replace per project_gid. PK (project_gid, custom_field_gid).",
    ),
    "project_custom_field_settings": _m(
        "project_custom_field_settings",
        "Project sub-resources",
        "Per-project custom field configuration (importance, global-to-workspace).",
        "Derived from each project's custom_field_settings array during project sync.",
        ["scripts/get_projects.py", "scripts/get_incremental_updates.py"],
        "_replace_project_custom_field_settings (via upsert_project)",
        "Full-replace per project_gid. PK (project_gid, custom_field_gid).",
    ),
    "project_updates": _m(
        "project_updates",
        "Project sub-resources",
        "Project-level status updates and comments (covers task-less projects), kind-tagged.",
        "GET /status_updates?parent={gid} + GET /projects/{gid}/stories (comment_added), merged & deduped by merge_project_updates",
        ["scripts/get_project_status_updates.py", "scripts/get_incremental_updates.py"],
        "replace_project_updates",
        "Soft-delete per project_gid then upsert by gid (PK); is_deleted flag.",
    ),
    # ---------------- Portfolio sub-resources ----------------
    "portfolio_items": _m(
        "portfolio_items",
        "Portfolio sub-resources",
        "Junction: the projects and nested portfolios contained in each portfolio, in display order.",
        "GET /portfolios/{gid}/items",
        ["scripts/get_portfolios.py"],
        "replace_portfolio_items",
        "Full-replace per portfolio_gid. PK (portfolio_gid, item_gid); resource_type is project|portfolio.",
    ),
    "portfolio_custom_field_settings": _m(
        "portfolio_custom_field_settings",
        "Portfolio sub-resources",
        "Per-portfolio custom field configuration (importance, global-to-workspace).",
        "GET /portfolios/{gid}/custom_field_settings",
        ["scripts/get_portfolios.py"],
        "_replace_portfolio_custom_field_settings (via upsert_portfolio)",
        "Full-replace per portfolio_gid. PK (portfolio_gid, custom_field_gid).",
    ),
    "portfolio_memberships": _m(
        "portfolio_memberships",
        "Portfolio sub-resources",
        "Users with access to each portfolio (distinct from owner) and their access level.",
        "GET /portfolio_memberships?portfolio={gid}",
        ["scripts/get_portfolios.py"],
        "replace_portfolio_memberships",
        "Full-replace per portfolio_gid. PK (portfolio_gid, user_gid). FK→portfolios, FK→users.",
    ),
    "portfolio_updates": _m(
        "portfolio_updates",
        "Portfolio sub-resources",
        "Portfolio-level status updates (kind-tagged status_update).",
        "GET /status_updates?parent={gid}, normalized by merge_project_updates",
        ["scripts/get_portfolios.py"],
        "replace_portfolio_updates",
        "Soft-delete per portfolio_gid then upsert by gid (PK); is_deleted flag.",
    ),
    # ---------------- Membership & identity ----------------
    "project_members": _m(
        "project_members",
        "Membership & identity",
        "Users who are members of each project (roster only; no access level).",
        "GET /workspaces/{gid}/projects opt_fields=members.gid,members.name",
        ["scripts/get_projects.py"],
        "upsert_project",
        "Full-replace per project_gid via _replace_project_members. PK (project_gid, user_gid). FK->projects, FK->users.",
    ),
    "team_members": _m(
        "team_members",
        "Membership & identity",
        "Team membership roster with role / admin / active flags.",
        "GET /teams/{gid}/team_memberships",
        ["scripts/get_teams.py"],
        "upsert_team_members",
        "Upsert by (team_gid, user_gid) PK.",
    ),
    "user_workspaces": _m(
        "user_workspaces",
        "Membership & identity",
        "Which workspaces each user belongs to.",
        "Derived from each user's workspaces array during user sync.",
        ["scripts/get_users.py"],
        "_replace_user_workspaces (via upsert_user)",
        "Full-replace per user_gid. PK (user_gid, workspace_gid).",
    ),
    "user_atlassian_accounts": _m(
        "user_atlassian_accounts",
        "Membership & identity",
        "Maps Asana user GID → Atlassian account id/email/status so Jira exports can resolve authors.",
        "CSV import (mapping CSV) or raw Atlassian user export matched by email — not an Asana API.",
        ["scripts/update_atlassian_user_map.py"],
        "replace_user_atlassian_accounts",
        "Upsert by Asana gid (PK); --clear wipes all rows first.",
    ),
    # ---------------- Jira / Atlassian mapping ----------------
    "task_jira_mappings": _m(
        "task_jira_mappings",
        "Jira / Atlassian mapping",
        "Maps Asana task GID ↔ Jira issue key.",
        "Jira search across associated project/space keys for an external-id field — not an Asana API.",
        ["scripts/atlassian/search_jira_external_ids.py"],
        "upsert_task_jira_mappings",
        "One row per (jira_site_key, task_gid); UNIQUE(jira_site_key, jira_key). FK→tasks.",
    ),
    "jira_project_mappings": _m(
        "jira_project_mappings",
        "Jira / Atlassian mapping",
        "Maps Asana project GID → Jira project name/key for export columns.",
        "CSV import (project_gid,project_name,project_key) — not an Asana API.",
        ["scripts/atlassian/import_jira_project_map.py"],
        "upsert_jira_project_mappings",
        "Upsert by (jira_site_key, project_gid).",
    ),
    "team_project_jira_mappings": _m(
        "team_project_jira_mappings",
        "Jira / Atlassian mapping",
        "Maps Asana team/project → created Jira Epic/Task key so re-runs skip existing issues.",
        "Recorded during direct-API Jira issue creation (kind = team/project/no_team).",
        ["scripts/atlassian/create_team_project_jira_issues.py"],
        "upsert_team_project_jira_mappings (delete_team_project_jira_mappings for --reset)",
        "Upsert by (jira_site_key, asana_gid, kind); UNIQUE(jira_site_key, jira_key).",
    ),
    "project_jira_decisions": _m(
        "project_jira_decisions",
        "Jira / Atlassian mapping",
        "Stores the 'Asana Migration Decision' custom-field value read back from each project's Jira Task issue, keyed by Asana project GID.",
        "Jira JQL search for the decision field (POST /rest/api/3/search/jql) + field-id lookup (GET /rest/api/3/field); jira_key resolved to project via team_project_jira_mappings (kind='project') — not an Asana API.",
        ["scripts/atlassian/get_jira_migration_decisions.py"],
        "upsert_project_jira_decisions",
        "Upsert by (jira_site_key, project_gid); UNIQUE(jira_site_key, jira_key). FK→projects.",
    ),
    "exported_tasks": _m(
        "exported_tasks",
        "Jira / Atlassian mapping",
        "Audit trail of which tasks were written in each Jira-export batch (--export-id).",
        "Computed during export_project_to_jira_csv runs.",
        ["scripts/atlassian/export_project_to_jira_csv.py"],
        "record_exported_tasks",
        "INSERT OR IGNORE by (export_id, task_gid) PK — append-only, no updates.",
    ),
    # ---------------- Sync bookkeeping ----------------
    "personal_task_cursors": _m(
        "personal_task_cursors",
        "Sync bookkeeping",
        "Per-assignee modified_at checkpoint for incremental personal-task pagination.",
        "Local sync checkpoint written after a successful run — not an API.",
        ["scripts/get_personal_tasks.py", "scripts/get_incremental_updates.py"],
        "upsert_personal_task_cursor",
        "Upsert by assignee_gid (PK).",
    ),
    # ---------------- Views ----------------
    "view_project_custom_field_configurations": _m(
        "view_project_custom_field_configurations",
        "Views",
        "Per-(project, custom field) config with a count of tasks that actually have a value set.",
        "Joins project_custom_field_settings → projects → teams; counts values from task_custom_fields + task_custom_field_multi_enum.",
        ["(defined in src/lib/database.py)"],
        "view",
        "Read-only view.",
        is_view=True,
    ),
    "view_project_task_hierarchy_counts": _m(
        "view_project_task_hierarchy_counts",
        "Views",
        "Per-project task hierarchy stats (counts by depth level, created/modified date ranges).",
        "Joins task_projects → projects → tasks → teams; aggregates by project and hierarchy_depth.",
        ["(defined in src/lib/database.py)"],
        "view",
        "Read-only view.",
        is_view=True,
    ),
    "view_team_memberships": _m(
        "view_team_memberships",
        "Views",
        "Denormalized team membership roster joined to team and user details.",
        "INNER JOIN team_members → teams, LEFT JOIN users.",
        ["(defined in src/lib/database.py)"],
        "view",
        "Read-only view.",
        is_view=True,
    ),
}


def tables_in_group(group: str) -> List[TableMeta]:
    """Return registry entries for a group, in insertion order."""
    return [meta for meta in TABLE_METADATA.values() if meta.group == group]
