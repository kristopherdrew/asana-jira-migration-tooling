# CSV Reports

CSV exports for auditing and scoping the migration straight from the local `data/asana.sqlite` snapshot. Run each through the package module path (`python3 -m scripts.<name>`) as described in the README's Usage Conventions.

All report date columns export as `YYYY-MM-DD` with zero-padded month and day values.

← Back to [README](../README.md)

### Combined Reports Workbook (.xlsx)
Runs every report below **except** the Team→Project Jira CSV and combines them into a single multi-tab `.xlsx` workbook — handy for handing the whole migration inventory to stakeholders in one file. Tabs: an `Overview` index (generation time, source DB, per-tab row counts) followed by `Projects`, `Users`, `Team Memberships`, `Teams`, `Custom Field Usage`, `Portfolios`, and `Portfolio Items`. Each report is run into a temporary directory (reusing its own `main()`), so the data matches the standalone CSVs exactly. Headers are bold and frozen; numeric columns are typed as numbers, while long identifiers (Asana GIDs) are kept as text to avoid Excel's 15-digit precision loss. The reports run unfiltered (all teams).

The workbook is written by the dependency-free `src/lib/xlsx_writer.py` (which hand-writes OOXML via `zipfile`), so this report — like every other script here — needs only the Python standard library and runs anywhere Python does.
```bash
python3 -m scripts.export_all_reports_xlsx --database data/asana.sqlite
```
Required flags:
- (none; uses `data/asana.sqlite` by default).

Optional flags:
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `all_reports_<timestamp>.xlsx` filename.
- `--workspace-gid <gid>` to force the workspace used when building URLs in the reports.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

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
Exports one row per project from SQLite with the project metadata, a direct Asana project URL, the team URL, the project owner as `owner_name`/`owner_email`/`owner_atlassian_status` (resolved from the `users` and `user_atlassian_accounts` tables; the Asana GID is no longer emitted), a per-project effective owner as `effective_owner_source`/`effective_owner_name`/`effective_owner_email`/`effective_owner_atlassian_status` (the project owner when their Atlassian account is active, otherwise the top active task creator; `effective_owner_source` is `project_owner`, `task_creator`, or blank), the current snapshot counts stored on `projects` (`num_tasks`, `num_incomplete_tasks`, `num_completed_tasks`), and task activity dates derived from synced task rows (`last_task_created_at`, `last_task_resolved_at`, `last_task_updated_at`). A separate `actual_task_count` column (with `actual_task_data_present` set to `true` when live task data exists) sits immediately before `num_tasks` for easy comparison. The export also surfaces the active Atlassian user who created the most tasks in each project as `top_task_creator_name`/`top_task_creator_email`/`top_task_creator_atlassian_status`/`top_task_creator_task_count` (inactive Atlassian creators are excluded, so these stay blank when no active creator exists). A `jira_migration_decision` column carries the "Asana Migration Decision" value pulled back from each project's Jira issue (populated by `scripts.atlassian.get_jira_migration_decisions`; blank until that pull runs), and `task_counts_captured_at` is emitted as the final column. The task activity and top-creator columns remain blank until the task sync has populated `tasks`.
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

### Portfolio Report
Exports portfolios from SQLite for migration scoping. Writes two CSVs: a **summary** (`portfolio_details_<timestamp>.csv`, one row per portfolio) and an **items companion** (`portfolio_details_<timestamp>_items.csv`, one row per portfolio item) — pass `--summary-only` to skip the companion.

The summary carries `portfolio_gid`/`portfolio_name`/`portfolio_url`, `color`, `public`, the owner and creator each resolved to `*_name`/`*_email`/`*_atlassian_status` (from the `users` and `user_atlassian_accounts` tables; blank without a mapping), dates (`created_at`, `start_on`, `due_on`, `last_status_update_at`) as `YYYY-MM-DD`, the latest status snapshot (`current_status_type`, `current_status_title`) plus `status_update_count`, and content rollups from `portfolio_items` joined to `projects`: `num_items`, `num_nested_portfolios`, `num_projects`, `num_active_projects`, and `num_archived_projects`. It also includes `num_members`, `num_custom_field_settings` with a `;`-joined `custom_fields` name list, and hierarchy placement: `is_top_level`, `nesting_depth` (0 = top-level; computed cycle-safe from the nesting graph), and a `;`-joined `parent_portfolio_names`. `synced_at` is the final column.

The items companion carries `portfolio_gid`/`portfolio_name`/`portfolio_url`, `position`, `item_type` (`project`|`portfolio`), `item_gid`/`item_name`/`item_url`, and—for project items—`project_team_name`, `project_archived`, and `project_num_tasks`. `in_scope` is `true` when the item was captured in the local snapshot (its gid exists in `projects` or `portfolios`) and `false` otherwise (e.g. archived/other-workspace projects not synced). Run `get_portfolios` first to populate the portfolio tables.
```bash
python3 -m scripts.export_portfolio_report --database data/asana.sqlite
```
Required flags:
- (none; uses `data/asana.sqlite` by default).

Optional flags:
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the auto-generated summary filename (the companion derives `<stem>_items.csv` from it).
- `--summary-only` to write only the per-portfolio summary and skip the items companion.
- `--workspace-gid <gid>` to force the workspace used when building portfolio/project URLs.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

### Field & Section Migration Review Report (.xlsx)
A standalone multi-tab `.xlsx` built for the migration-scoping conversations with Team Admins and Project Owners. It lays out, per project, how each project actually uses **Sections** and **Custom Fields** — down to individual enum options and task counts — plus two cross-project "catalog" tabs that surface differently-named-but-same-purpose fields and sections side-by-side, so the team can decide a sensible Jira status/field model. "Status" is intentionally not a separate dimension: in Asana it is an outcome of how Sections and (enum) Custom Fields are used, which this report exposes.

Scope it to one admin's set of projects with `--team-gid` and/or `--owner-gid` (the owner is the project `owner_gid`); omit both for the whole workspace. The tabs are:
- **Overview** — generation time, source DB, the scope applied, and a Tab→Rows index.
- **Projects** — one row per in-scope project: team, `project_url`, the recorded `jira_migration_decision` (from `project_jira_decisions`; blank until `get_jira_migration_decisions` is run), `archived`, Atlassian-aware project owner (`owner_name`/`owner_email`/`owner_atlassian_status`) and the effective **Team Admin** (resolved via `src/lib/team_analysis.py`: `team_admin`/`team_admin_email`/`team_admin_atlassian_status`/`team_admin_source`), and `task_count`/`section_count`/`custom_field_count` rollups.
- **Sections** — one row per (project, section): `section_name`, `task_count`, and `completed_task_count` (sections often *are* the de-facto workflow → candidate Jira statuses/board columns).
- **Custom Fields** — one row per (project, field) actually populated on a task: `field_type`, `is_important`, `is_global_to_workspace`, `tasks_with_values`, `project_task_count`, `usage_pct`, and `distinct_values`.
- **Custom Field Values** — value-level breakdown: one row per (project, field, **enum option**) with a task count for `enum`/`multi_enum` fields. Non-enum fields (text/number/date) collapse to a single summary row per (project, field) showing `(N distinct values)` and a `;`-joined `sample_values` list (top 5 by frequency), since their values are not a fixed option set.
- **Field Catalog** — one row per distinct custom field across the scope: `field_type`, `project_count`, `distinct_options`, the full `option_set`, and the `;`-joined `project_names` using it.
- **Section Catalog** — one row per distinct section name (case-insensitive) across the scope: `project_count`, `task_count`, and the `;`-joined `team_names`/`project_names`.

Run the Asana syncs first (`get_teams`, `get_users`, `get_projects`, `get_custom_fields`, `get_tasks`) so sections, custom-field values, and ownership are populated; Atlassian columns stay blank without a `user_atlassian_accounts` mapping. The workbook is written by the dependency-free `src/lib/xlsx_writer.py`, so it needs only the Python standard library.
```bash
python3 -m scripts.export_field_section_review_report --database data/asana.sqlite
```
Required flags:
- (none; uses `data/asana.sqlite` by default).

Optional flags:
- `--team-gid <gid>` to scope the report to one team's projects.
- `--owner-gid <gid>` to scope the report to one project owner's projects.
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `field_section_review_<timestamp>.xlsx` filename.
- `--workspace-gid <gid>` to force the workspace used when building project URLs.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

In the interactive Reports menu, this export first asks whether the review should be team-centric, owner-centric, or whole-workspace, then searches team or project-owner names from the local SQLite snapshot (falling back to a raw GID prompt) for the chosen scope only.

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
