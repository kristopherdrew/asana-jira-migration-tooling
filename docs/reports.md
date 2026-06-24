# CSV Reports

CSV exports for auditing and scoping the migration straight from the local `data/asana.sqlite` snapshot. Run each through the package module path (`python3 -m scripts.<name>`) as described in the README's Usage Conventions.

All report date columns export as `YYYY-MM-DD` with zero-padded month and day values.

← Back to [README](../README.md)

### Export Users
Writes the `users` table (plus workspace memberships) to a CSV for quick auditing or downstream reporting. Includes GID, name, email, resource type, active flag, Atlassian account id, Atlassian status, per-user task activity counts (`tasks_created`, `tasks_completed`, `tasks_commented`), a `last_active_date`, a direct Asana user URL, last sync date, and a semicolon-separated list of workspaces. Atlassian id/status come from the `user_atlassian_accounts` table (keyed by Asana user GID) and are left blank for users without a mapping. The activity counts come from synced task data: `tasks_created` counts tasks where the user is `created_by`, `tasks_completed` counts tasks where the user is `completed_by`, and `tasks_commented` counts the distinct tasks on which the user authored at least one non-deleted comment (not the number of comments). `last_active_date` (formatted `YYYY-MM-DD`) is the most recent of the user's last task creation, last task completion, or last non-deleted comment. All counts default to `0` and `last_active_date` is blank until the task and comment syncs have populated `tasks` and `task_comments`.
```bash
python3 -m scripts.export_users --database data/asana.sqlite
```
Required flags:
- (none; uses `data/asana.sqlite` by default).

Optional flags:
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `users_<timestamp>.csv` filename.
- `--workspace-gid <gid>` to force the workspace used when building user URLs.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

### Project Details Report
Exports one row per project from SQLite with the project metadata, a direct Asana project URL, the team URL, the project owner as `owner_name`/`owner_email`/`owner_atlassian_status` (resolved from the `users` and `user_atlassian_accounts` tables; the Asana GID is no longer emitted), a per-project effective owner as `effective_owner_source`/`effective_owner_name`/`effective_owner_email`/`effective_owner_atlassian_status` (the project owner when their Atlassian account is active, otherwise the top active task creator; `effective_owner_source` is `project_owner`, `task_creator`, or blank), the current snapshot counts stored on `projects` (`num_tasks`, `num_incomplete_tasks`, `num_completed_tasks`), and task activity dates derived from synced task rows (`last_task_created_at`, `last_task_resolved_at`, `last_task_updated_at`). A separate `actual_task_count` column (with `actual_task_data_present` set to `true` when live task data exists) sits immediately before `num_tasks` for easy comparison. The export also surfaces the active Atlassian user who created the most tasks in each project as `top_task_creator_name`/`top_task_creator_email`/`top_task_creator_atlassian_status`/`top_task_creator_task_count` (inactive Atlassian creators are excluded, so these stay blank when no active creator exists). `task_counts_captured_at` is emitted as the final column. The task activity and top-creator columns remain blank until the task sync has populated `tasks`.
```bash
python3 -m scripts.export_project_details_report --database data/asana.sqlite
```
Required flags:
- (none; uses `data/asana.sqlite` by default).

Optional flags:
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `project_details_<timestamp>.csv` filename.
- `--team-gid <gid>` to limit the report to one team’s projects.
- `--workspace-gid <gid>` to force the workspace used when building project URLs.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

In the interactive Reports menu, this export can search team names from the local SQLite snapshot before falling back to a raw team GID prompt.

### Custom Field Usage Report
Exports one row per (project, custom field) so you can see which custom fields are actually in use per project — and of what kind — before deciding what to migrate into Jira. Only fields populated on at least one task in the project are emitted (a field enabled on a project but never filled in is omitted). Each row carries `project_gid`/`project_name`/`project_url`, `team_gid`/`team_name`, `archived`, `custom_field_gid`/`custom_field_name`, `field_type` (the field's `resource_subtype`: text/number/enum/multi_enum/date/people), `is_global_to_workspace`, `is_important` (from the project's custom field settings), `tasks_with_values` (distinct tasks in that project with a value set for the field), `project_task_count`, and `usage_pct` (`tasks_with_values / project_task_count`). A field counts as populated when it has a non-empty single value or any multi-enum selection; this matches the `values_found` semantic used by the `export_project_to_jira_csv.py` field-counts companion. The report stays empty until the task sync has populated `tasks`/`task_custom_fields`.
```bash
python3 -m scripts.export_custom_field_usage_report --database data/asana.sqlite
```
Required flags:
- (none; uses `data/asana.sqlite` by default).

Optional flags:
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `custom_field_usage_<timestamp>.csv` filename.
- `--team-gid <gid>` to limit the report to one team’s projects.
- `--workspace-gid <gid>` to force the workspace used when building project URLs.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

### Team Report
Exports one row per team from SQLite with the team metadata, a direct Asana team URL, the total number of projects associated with that team, those projects split into active and archived counts, and a sum of the actual task-table counts when task rows are available. The report also records the effective admin chosen from the Atlassian-aware analysis chain: first active team admin, then the top active project owner if no active admin exists, then the top active task creator if the top owner is not active. Immediately after `actual_task_count_total` the export emits `effective_admin_source` followed by the winning `effective_admin_name` (Asana display name), `effective_admin_email`, and `effective_admin_atlassian_status`. Each user section follows the same `name`/`email`/`atlassian_status` shape (no Asana GID): `first_admin_*` (with `additional_admin_emails` listing the remaining admins as a status-free email list), `most_projects_owner_*` plus `most_projects_owner_project_count` (the user who owns the greatest number of projects), and `task_creator_*` plus `task_creator_task_count`. The team sync column is emitted as a date only.
```bash
python3 -m scripts.export_team_report --database data/asana.sqlite
```
Required flags:
- (none; uses `data/asana.sqlite` by default).

Optional flags:
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `team_report_<timestamp>.csv` filename.
- `--team-gid <gid>` to limit the report to one team.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

In the interactive Reports menu, this export can search team names from the local SQLite snapshot before falling back to a raw team GID prompt.

### Team Memberships Report
Exports the `view_team_memberships` database view to CSV so you can review teams, users, roles, admin flags, direct Asana team/user URLs, and sync dates in one place. If the user snapshot is missing, the report still keeps the membership row and falls back to `unknown_user <gid>`.
```bash
python3 -m scripts.export_team_memberships --database data/asana.sqlite
```
Required flags:
- (none; uses `data/asana.sqlite` by default).

Optional flags:
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `team_memberships_<timestamp>.csv` filename.
- `--team-gid <gid>` to limit the report to one team.
- `--workspace-gid <gid>` to force the workspace used when building user URLs.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

In the interactive Reports menu, this export can also search team names from the local SQLite snapshot before falling back to a raw team GID prompt.
