# CSV Reports

CSV exports for auditing and scoping the migration straight from the local `data/asana.sqlite` snapshot. Run each through the package module path (`python3 -m scripts.<name>`) as described in the README's Usage Conventions.

All report date columns export as `YYYY-MM-DD` with zero-padded month and day values.

← Back to [README](../README.md)

### Combined Reports Workbook (.xlsx)
Runs every non-Jira inventory report below and combines them into a single multi-tab `.xlsx` workbook — handy for handing the whole migration inventory to stakeholders in one file. Jira migration/export validation reports are intentionally separate. Tabs: an `Overview` index (generation time, source DB, per-tab row counts) followed by `Projects`, `Users`, `Team Memberships`, `Teams`, `Custom Field Usage`, `Portfolios`, and `Portfolio Items`. Each report is run into a temporary directory (reusing its own `main()`), so the data matches the standalone CSVs exactly. Headers are bold and frozen; numeric columns are typed as numbers, while long identifiers (Asana GIDs) are kept as text to avoid Excel's 15-digit precision loss. The reports run unfiltered (all teams).

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

### Anonymize Database
Creates a privacy-safer copy of the local SQLite snapshot for troubleshooting. The command copies the source database, then replaces sensitive values with deterministic fake values inside that copy: Asana/Jira identifiers, Jira keys, user names, emails, team/project/task/portfolio names, notes, comments, URLs, paths, custom-field text/display values, JSON blobs, and attachment metadata are rewritten while relationships and counts remain usable for joins and reports. Dates, booleans, numeric counts, statuses, and resource types are preserved. By default each run uses a fresh private salt; pass `--salt` only when you need repeatable anonymized values across runs. The script refuses to run in place.
```bash
python3 -m scripts.anonymize_database --database data/asana.sqlite
```
Required flags:
- (none; uses the active profile database by default).

Optional flags:
- `--output <path>` to choose the anonymized database path (defaults to `<database>_anonymized.sqlite`).
- `--overwrite` to replace an existing anonymized output database.
- `--salt <value>` to make generated fake values repeatable across runs.
- `--no-verify` to skip the post-run scan for obvious email, URL, and token-like values.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

In the interactive CLI, this command lives under **Advanced & Recovery** as “Create anonymized troubleshooting database.” For the exact table/column coverage, see [the anonymization map](anonymization-map.md), regenerated with `python3 -m scripts.describe_db --anonymization-markdown > docs/anonymization-map.md`.

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

### Project Last-Updated Report
Exports one row per project summarizing how recently its tasks were touched, so you can spot stale vs. active projects at a glance. Each row carries `project_gid`, `project_name`, `task_count` (number of tasks in the project, via the `task_projects` junction), `latest_task_update` (the most recent task `modified_at` in the project, formatted `YYYY-MM-DD`), and `latest_updated_task_gid`/`latest_updated_task_name` naming the single most recently modified task. Projects with no synced tasks appear with `task_count` `0` and blank date/task columns. Rows are sorted by `latest_task_update` descending (most recently active project first). The date columns stay blank until the task sync has populated `tasks` and `task_projects`.
```bash
python3 -m scripts.export_project_last_updated_report --database data/asana.sqlite
```
Required flags:
- (none; uses `data/asana.sqlite` by default).

Optional flags:
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `project_last_updated_<timestamp>.csv` filename.
- `--team-gid <gid>` to limit the report to one team’s projects.
- `--project-gids <gid1,gid2>` or `--project-gids-file <path>` to limit the report to an explicit project list.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

Use `--team-gid` to scope by team, or `--project-gids <gid1,gid2>` / `--project-gids-file <path>` for an explicit migration-wave project list. The interactive Assessment menu offers team lookup, pasted GIDs, a GID file, or the whole workspace.

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
- `--project-gids <gid1,gid2>` or `--project-gids-file <path>` to limit the report to an explicit project list.
- `--workspace-gid <gid>` to force the workspace used when building project URLs.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

Use `--team-gid` to scope by team, or `--project-gids <gid1,gid2>` / `--project-gids-file <path>` for an explicit migration-wave project list. The interactive Assessment menu offers team lookup, pasted GIDs, a GID file, or the whole workspace.

### Project Import Status Report
Exports one row per (project, Jira environment) combination, showing which Asana projects have been exported to Jira and the import coverage for each target. Rows are only emitted when either an explicit project mapping exists (from `jira_project_mappings`, populated by `scripts/atlassian/import_jira_project_map.py`) or when tasks from the project have been imported (detected via `task_jira_mappings`). For each export, the report derives the Jira project key by extracting the prefix from imported Jira issue keys (e.g., `ABC-123` → `ABC`) when no explicit mapping exists.

Each row carries `asana_project_gid`/`asana_project_name`, team info (`team_gid`, `team_name`), the target `jira_environment` (Jira site key; `'No Export'` for projects with no mappings), `jira_project_key`/`jira_project_name` (project name is blank when inferred from task keys), and task counts: `total_tasks` (non-deleted tasks in the project), `active_tasks` (non-deleted, non-completed), `completed_tasks`, `incomplete_tasks`, `imported_tasks` (tasks with Jira keys in this environment), `unimported_tasks`, and `import_coverage_percent` (100 × imported / total). Rows are sorted by team, then project name, then environment. The report stays empty until projects and tasks have been synced and Jira mappings created.
```bash
python3 -m scripts.export_project_import_status --database data/asana.sqlite
```
Required flags:
- (none; uses `data/asana.sqlite` by default).

Optional flags:
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `project_import_status_<timestamp>.csv` filename.
- `--team-gid <gid>` to limit the report to one team's projects.
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
- `--project-gids <gid1,gid2>` or `--project-gids-file <path>` to limit the report to an explicit project list.
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

Scope it to one admin's set of projects with `--team-gid` and/or `--owner-gid` (the owner is the project `owner_gid`), an explicit `--project-gids <gid1,gid2>` / `--project-gids-file <path>` list, or just the projects behind a set of migrated Jira Task keys with `--jira-keys`; omit all for the whole workspace. The workbook is named after the scope — `<Team> Migration Review.xlsx` (or the owner name when owner-scoped, `Selected Projects Migration Review.xlsx` when Jira-key-scoped, else `Workspace Migration Review.xlsx`) unless `--filename` overrides it. The tabs are:
- **Overview** — generation time, source DB, the scope applied, and a Tab→Rows index. Its title cell is the scope name + `Migration Review`.
- **Projects** — one row per in-scope project: team, `project_url`, the recorded `jira_migration_decision` (from `project_jira_decisions`; blank until `get_jira_migration_decisions` is run), the project's migrated `jira_key` (the Project→Task issue from `team_project_jira_mappings` where `kind='project'`; blank until the project is created in Jira), `archived`, Atlassian-aware project owner (`owner_name`/`owner_email`/`owner_atlassian_status`) and the effective **Team Admin** (resolved via `src/lib/team_analysis.py`: `team_admin`/`team_admin_email`/`team_admin_atlassian_status`/`team_admin_source`), and `task_count`/`section_count`/`custom_field_count` rollups.
- **Project Members** — one row per (project, member) membership record (from the `project_members` table): `team_name`, `project_name`, `project_url`, `archived`, an Atlassian-aware member column (`member_name`/`member_email`/`member_atlassian_status`), and `project_gid`/`user_gid`. Shares its builder with the standalone Project Members report.
- **Member Summary** — one row per member: `member_name`/`member_email`/`member_atlassian_status`, the `project_count` of in-scope projects they belong to, and a `;`-joined `project_names` list (sorted).
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
- `--project-gids <gid1,gid2>` or `--project-gids-file <path>` to scope the report to an explicit project list.
- `--jira-keys <keys>` to scope the report to the projects behind a list of migrated Jira Task keys (comma/space separated, or repeat the flag; case-insensitive). Keys are resolved to projects via `team_project_jira_mappings` (`kind='project'`); keys with no mapping are logged as a warning and, if none resolve, the report is empty.
- `--jira-keys-file <path>` to load Jira Task keys from a UTF-8 file containing comma-, space-, or newline-separated values.
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the scope-derived `<Team> Migration Review.xlsx` filename.
- `--workspace-gid <gid>` to force the workspace used when building project URLs.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

In the interactive Assessment menu, this export first asks whether the review should be team-centric, owner-centric, whole-workspace, by Jira key(s), or by an explicit project-GID list. The team/owner scopes search names from the local SQLite snapshot (falling back to a raw GID prompt). Both Jira-key and project-GID scopes accept pasted values or a selectable `.txt`, `.csv`, or `.list` file.

### Project Members Report (.xlsx)
A standalone multi-tab `.xlsx` listing project membership captured from Asana — the `members` array on each project, synced into the `project_members` table by `get_projects`. Scope it to one team's projects with `--team-gid`, or a migration-wave list with `--project-gids <gid1,gid2>` / `--project-gids-file <path>`; omit all scope flags for the whole workspace. The tabs are:
- **Overview** — generation time, source DB, the scope applied, and a Tab→Rows index.
- **Project Members** — one row per membership record (one project + one member): `team_name`, `project_name`, `project_url`, `archived`, an Atlassian-aware member column (`member_name`/`member_email`/`member_atlassian_status`), and the `project_gid`/`user_gid`. The member name/email come from the `users` snapshot when available (falling back to the name embedded on the membership when the user is not synced); `member_atlassian_status` stays blank without a `user_atlassian_accounts` mapping.
- **Member Summary** — one row per member: `member_name`/`member_email`/`member_atlassian_status`, the `project_count` of projects they belong to, and a `;`-joined `project_names` list (sorted). Sorted by most projects first.

Run the Asana syncs first (`get_teams`, `get_users`, `get_projects`) so membership, member identities, and team names are populated. The workbook is written by the dependency-free `src/lib/xlsx_writer.py`, so it needs only the Python standard library.
```bash
python3 -m scripts.export_project_members_report --database data/asana.sqlite
```
Required flags:
- (none; uses `data/asana.sqlite` by default).

Optional flags:
- `--team-gid <gid>` to scope the report to one team's projects.
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `project_members_<timestamp>.xlsx` filename.
- `--workspace-gid <gid>` to force the workspace used when building project URLs.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

In the interactive Reports menu, this export searches team names from the local SQLite snapshot (falling back to a raw team GID prompt); leave the scope blank for all teams.

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
### Jira Task Migration Validation

Validates the distinct Asana tasks belonging to projects in the selected site's Asana
Project → Jira Space associations. It compares those task GIDs to `task_jira_mappings` and
prints the associated task total, valid Jira-key count, missing count, and migration coverage
percentage. Shared tasks are counted once. The command makes no Jira API calls; run the Jira
key recovery command first so the database reflects current External IDs.

```bash
python3 -m scripts.export_jira_migration_validation --jira-site production_jira
python3 -m scripts.export_jira_migration_validation --jira-site production_jira --export-missing
python3 -m scripts.export_jira_migration_validation \
  --jira-site production_jira \
  --project-gids-file inputs/wave-07-project-gids.txt \
  --export-missing
```

When `--export-missing` is supplied, the command writes a detailed CSV containing
`task_gid`, task name, completion state, and associated project IDs/names. It also writes a
`<stem>_task_gids.txt` companion that can be passed directly to:

```bash
python3 -m scripts.export_project_to_jira_csv --task-gids-file exports/jira_missing_tasks_<timestamp>_task_gids.txt
```

Optional flags:
- `--jira-site <key>` selects the Jira site; it is inferred when associations exist for exactly one site.
- `--team-gid <gid>` validates distinct tasks in that team's Jira-associated projects.
- `--project-gids <gid1,gid2>` or `--project-gids-file <path>` limits validation to the exact projects in one migration wave. Every requested project must exist locally and have a Jira Space association for the selected site; omit both for cumulative validation of every associated project.
- `--task-gids <gid1,gid2>` or `--task-gids-file <path>` validates an exact task set. Every requested task must exist locally and belong to at least one project associated with the selected Jira site.
- `--export-missing` writes the missing-task CSV and focused-reimport GID list.
- `--database <path>`, `--output-dir <path>`, `--filename <name>`, and `--log-level <LEVEL>` use the standard report conventions.

The Interactive CLI offers fuzzy team lookup, project GIDs pasted or loaded from a file,
task GIDs pasted or loaded from a file, and cumulative all-associated-project validation.

### Recurring Tasks Report

Unlike every other report in this file, this one can both read **and** write the local SQLite snapshot in addition to querying live Asana — it's DB-first with a live-Asana fallback for gaps. `tasks.recurrence`/`tasks.recurrence_checked_at` (populated by the normal task sync — see [sync-commands](sync-commands.md) and [data-model](data-model.md)) are used when already captured; live Asana is only queried to fill gaps: a project with no synced tasks falls back to a full live walk of every task and nested subtask (requesting the undocumented `recurrence` field), while a synced task whose `recurrence_checked_at` is still `NULL` (synced before this column existed) gets a single-task live lookup. Any value discovered live is written back into `tasks.recurrence`/`recurrence_checked_at`, so re-running the report against the same projects later needs no further API calls. Writes two CSVs: one row per recurring task instance found, and a best-effort series summary. Asana exposes no true "series id" for a recurrence, so the summary groups instances by `(project_gid, task_name, recurrence_type, recurrence_frequency)` — two unrelated tasks that happen to share a name and schedule would incorrectly merge into one series row.

```bash
python3 -m scripts.export_recurring_tasks_report --project-gids 1201234567890,1209876543210
python3 -m scripts.export_recurring_tasks_report --project-gids-file inputs/scope-project-gids.txt
```

Required flags:
- `--project-gids <gid1,gid2>` or `--project-gids-file <path>` (exactly one; there is no team/workspace-wide scope for this report).

Optional flags:
- `--database <path>` to point at a different SQLite database (default `data/asana.sqlite`). Recurrence is read from here when already synced, and live-queried gaps are written back to it.
- `--output-dir <path>` to choose the destination directory (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `recurring_tasks_instances_<timestamp>.csv` filename (the series summary is derived from it, e.g. `<stem>_series_summary.csv`).
- `--page-size <n>` for task/subtask pagination, used only when falling back to a live walk for an unsynced project (default `100`).
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

Instances CSV columns: `project_gid, project_name, task_gid, task_name, task_type (task|subtask), parent_gid, parent_name, recurrence_type, recurrence_frequency, due_on, completed, permalink_url`.
Series summary columns: `project_gid, project_name, task_name, recurrence_type, recurrence_frequency, occurrence_count, first_due_on, last_due_on`.
