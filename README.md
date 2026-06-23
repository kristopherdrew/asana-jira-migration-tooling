# Asana Data Export Toolkit

Python-only automation scripts for exporting entities from the Asana API into a local SQLite database. All tooling relies on the standard library so it can run anywhere without third-party dependencies.

## Requirements
- Python 3.11 or later (use `python3` on macOS/Linux).
- Asana personal access token with read access to the target workspace.
- Credentials should live in an env-style file under your home directory, not in the project root. Supported keys are `ASANA_TOKEN`, `ASANA_WORKSPACE_GID`, `SKIP_SSL_VERIFICATION`, `ATLASSIAN_USERNAME`, and `ATLASSIAN_TOKEN`.
- For multi-client setups, keep one env file per client under your home directory and add a git-ignored `.asana_jira_env_path` file in the project root that points to the correct one for that copy of the repo. The resolution order is: process environment variables, env file named by `.asana_jira_env_path`, then default `~/.asana_jira_env`.

### Initial Setup
Create a client-specific env file:
```bash
cat > ~/.asana_jira_env_client_a <<'EOF'
export ASANA_TOKEN="your_asana_token"
export ASANA_WORKSPACE_GID="your_workspace_gid"
export SKIP_SSL_VERIFICATION="false"
export ATLASSIAN_USERNAME="you@example.com"
export ATLASSIAN_TOKEN="your_jira_api_token"
EOF
chmod 600 ~/.asana_jira_env_client_a
```

Point this project copy at that file:
```bash
echo "~/.asana_jira_env_client_a" > .asana_jira_env_path
```

Leave `SKIP_SSL_VERIFICATION` set to `false` for normal use. Only set it to `true` as a temporary local troubleshooting workaround for a broken trust store or certificate validation issue, then return it to `false` once the environment is fixed. Rotate tokens immediately if they are ever committed or shared.

## Project Layout
- `scripts/` — CLI entrypoints for each export task (kept runnable on their own even when wrapped by higher-level orchestration).
- `scripts/atlassian/` — Jira/Atlassian integration scripts; thin wrappers stay in `scripts/` so existing `python3 -m scripts.<name>` calls keep working.
- `scripts/deprecated/` — Parking spot for retired scripts so history remains discoverable without cluttering the primary CLI surface.
- `src/lib/` — Shared helpers (configuration loader, HTTP client, SQLite helpers).
- `src/tasks/` — Domain-specific logic per Asana entity.
- `tests/` — Unit tests mirroring the `src/` layout.
- `scripts/interactive_cli.py` — Menu-driven wrapper that chains the existing scripts without changing their standalone behavior.

## Usage Conventions
Run every CLI through the package module path so Python keeps `src/` on `sys.path`:
```bash
python3 -m scripts.get_teams --help
```

All entrypoints accept `--log-level` (`DEBUG`, `INFO`, `WARNING`, `ERROR`). If you prefer `python3 scripts/<name>.py`, export the project root on `PYTHONPATH` first; otherwise stick with `python3 -m scripts.<name>`. Each script remains runnable independently even when wrapped by consolidated flows.

## Interactive Orchestrator
Run the menu-driven wrapper to combine the existing entrypoints without losing standalone support:
```bash
python3 -m scripts.interactive_cli --log-level INFO
```
Menu highlights:
- Category 1 (Initialization): teams + memberships → users → add the service user to every Asana team → projects → custom fields.
- Category 2 (Tasks): tasks, task extras, personal tasks (full), incremental updates.
- Category 3 (Atlassian): export/import Jira keys, clear mappings, update Atlassian user map, sync attachments.
- Category 4 (Reports): project details, team summary, users, team memberships, custom field usage.
- Category 5 (Credentials): create/update the referenced env file or switch the project to a different one.

On first launch, if the interactive CLI cannot resolve a credentials file from the environment or `.asana_jira_env_path`, it starts a setup flow that asks for the Asana token, workspace GID, SSL verification flag, and optional Atlassian credentials, then writes the env file and project pointer for you.

When you run the Initialization flow, the projects step prompts for archived scope so you can choose active, archived, or all projects during the first data pull. The flow now adds the service user immediately after the user sync so any team-scoped metadata later in the run can see that membership. If any team update fails, the script writes `exports/service_user_team_add_failures_<timestamp>.csv` with the team GIDs and error messages.

## Core Sync Commands
### Sync Teams
Fetches all teams in the workspace and writes them into `data/asana.sqlite`.
```bash
python3 -m scripts.get_teams
```
Required flags:
- (none; uses `ASANA_WORKSPACE_GID` from the resolved credentials).

Optional flags:
- `--include-members` to fetch team memberships (fetched concurrently across teams).
- `--update-team-members` to skip the full sync and instead search for a single team already in the database (case-insensitive name search with a paginated picker) and refresh only its membership association.
- `--workspace-gid <gid>` to override the workspace ID from the resolved credentials.
- `--workers <n>` to set the number of concurrent membership fetch workers (default 5).

### Sync Projects
Fetches all projects in the workspace—including lifecycle metadata and custom field definitions/settings—and stores them in `data/asana.sqlite`. Each project row also captures a point-in-time task count snapshot from Asana's `/projects/{project_gid}/task_counts` endpoint so you can see the current task total before the full task sync runs.
```bash
python3 -m scripts.get_projects
```
Required flags:
- (none; uses `ASANA_WORKSPACE_GID` from the resolved credentials).

Optional flags:
- `--workspace-gid <gid>` to override the workspace ID from the resolved credentials.
- `--page-size <n>` to adjust the Asana pagination size (default 100).
- `--archived-scope <active|archived|all>` to decide which lifecycle state(s) to sync.

### Sync Custom Fields
Fetches every custom field definition available in the workspace—including enum option catalogs, formatting metadata, and any project-local (non-global) fields discovered via project settings—and stores it in `data/asana.sqlite`. Ownership data (creator, owner) is intentionally excluded.
```bash
python3 -m scripts.get_custom_fields
```
Required flags:
- (none; uses `ASANA_WORKSPACE_GID` from the resolved credentials).

Optional flags:
- `--workspace-gid <gid>` to override the workspace ID from the resolved credentials.
- `--page-size <n>` to adjust the Asana pagination size (default 100).

### Sync Users
Fetches every accessible user in the workspace, storing their identifiers, email, active status, and workspace memberships in `data/asana.sqlite`.
```bash
python3 -m scripts.get_users
```
Required flags:
- (none; uses `ASANA_WORKSPACE_GID` from the resolved credentials).

Optional flags:
- `--workspace-gid <gid>` to override the workspace ID from the resolved credentials.
- `--page-size <n>` to adjust the Asana pagination size (default 100).

### Add Service User to Teams
Adds one service user to every Asana team stored in SQLite using Asana's `addUser` endpoint. When launched interactively, the script can search the current `users` table before falling back to a raw GID entry.
```bash
python3 -m scripts.add_service_user_to_teams
```
Required flags:
- (none; uses `data/asana.sqlite` and the resolved credentials by default).

Optional flags:
- `--database <path>` to point at a different SQLite snapshot (default `data/asana.sqlite`).
- `--output-dir <path>` to choose where failure exports are written (default `exports/`).
- `--user-gid <gid>` to bypass the prompt and use a specific service user.
- `--search-users` to force the interactive user search flow instead of prompting for a raw GID first.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

If one or more team updates fail, the script writes `service_user_team_add_failures_<timestamp>.csv` into the output directory with the failed team GIDs and the captured error text.

### Add Service User to Projects
Adds one service user to a selected list of Asana project GIDs using Asana's `addMembers` endpoint.
```bash
python3 -m scripts.add_service_user_to_projects --user-gid <service_user_gid> --project-gids <gid1,gid2>
```
Required flags:
- `--user-gid <gid>` to identify the service user to add, or omit it to be prompted interactively.
- One scope selector: `--project-gid <gid>` repeated as needed, or `--project-gids <gid1,gid2>`.

Optional flags:
- `--output-dir <path>` to choose where failure exports are written (default `exports/`).
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

If one or more project updates fail, the script writes `service_user_project_add_failures_<timestamp>.csv` into the output directory with the failed project GIDs and the captured error text.

### Sync Tasks
Recursively fetches tasks (and subtasks) for selected projects, backfilling missing user profiles or custom field definitions on the fly. Persists task metadata, memberships, dependencies, followers, and custom field values into `data/asana.sqlite`. Run with either `--project-gid` for a focused pull or `--team` to drive selection interactively.
```bash
python3 -m scripts.get_tasks
```
Required flags:
- One of `--project-gid <gid>`, `--team`, `--team-gids <gid1,gid2>`, or `--all-teams` (optionally with `--exclude-team-gids <gid1,gid2>`).

Optional flags:
- `--project-gid <gid>` – bypasses the default project and syncs only the specified project.
- `--project-gids <gid1,gid2>` – sync a comma-separated list of projects sequentially.
- `--team` – launches an interactive selector that lists ten teams at a time, sorted first by “never imported” (no task rows present) and then by descending project count. Use `n`/`p` to page, enter the row number to run that team, or `q` to exit.
- `--team-gids <gid1,gid2>` – syncs the listed teams sequentially without prompting.
- `--all-teams` – walks every team found in SQLite, skipping any you omit via `--exclude-team-gids <gid1,gid2>`.
- `--resume-all-teams` with `--all-teams` – resumes from the JSON progress file at `exports/all_team_sync_state.json` by default. The file records `completed_team_gids` plus an `updated_at` value written as an ISO 8601 UTC timestamp.
- `--all-teams-state-file <path>` – overrides the progress file location if you want to store the resume state elsewhere.
- `--page-size <n>` – adjusts the Asana pagination limit for task listings and subtask fetches (default 100).
- `--workers <n>` – controls the number of concurrent `/tasks/{gid}` detail fetches (default 5).
- `--project-archived-scope <active|archived|all>` – choose whether to sync only active projects (default), only archived ones, or both.

During every run the script:
- Iterates the selected projects, recursing through subtasks so the hierarchy is complete in one pass.
- Lazily backfills users and custom fields the first time they appear.
- Writes multi-select custom field values into a normalized link table so Jira CSV exports can replicate fields with repeated column names.
- Surfaces progress every ~25 tasks and records a new `synced_at` timestamp for each row.
- Processes multi-homed tasks for each project to keep every team’s view complete.

During an interactive task sync, the CLI also records any project that had a positive `num_tasks` count from the project metadata pull but produced zero imported task rows. The candidate list is written to `exports/missing_service_user_projects.json`, then the CLI offers to add a service user to those projects and optionally retry the missed project list immediately.

### Sync Task Extras
Pulls task comments and attachment metadata for the selected projects (using the same scope flags as above) and stores them in `task_comments` and `task_attachments` tables. Only human-authored comments (`resource_subtype = comment_added`) are captured; system events are excluded.
```bash
python3 -m scripts.get_task_extras --team-gids <gid1,gid2>
```
Required flags:
- One of `--project-gid`, `--team`, `--team-gids`, `--all-teams`, or the personal-task flags (`--personal`/`--personal-assignees`).

Optional flags (mirror `get_tasks.py`):
- `--project-gid <gid>` for a single project.
- `--team` for the interactive picker.
- `--team-gids <gid1,gid2>` or `--all-teams`/`--exclude-team-gids <gid>` for unattended batches.
- `--workers <n>` to tune concurrency when fetching stories/attachments (default 5).
- `--personal` to sync extras for the synthetic `personal_<user_gid>` projects that `get_personal_tasks` writes.
- `--personal-assignees <gid1,gid2>` (implies `--personal`) to scope the run to specific assignees’ `personal_` projects.

Each project run re-fetches every task’s comments/attachments, backfills missing authors, replaces stored rows, and logs progress every ~25 tasks.

### Sync Personal Tasks
Pulls every task assigned to the selected user that does **not** belong to any project (true “My Tasks” items) and stores them with `tasks.is_personal = 1`. Each personal task is linked to a synthetic project named after the assignee (e.g., `personal_<user_gid>`) and that pseudo-project is flagged via `projects.is_personal_project = 1`.
```bash
python3 -m scripts.get_personal_tasks --assignee me
```
Required flags:
- (none; defaults to `--assignee me` and workspace from the resolved credentials).

Optional flags:
- `--workspace-gid <gid>` to override the workspace from the resolved credentials.
- `--assignee <gid|me>` to target a specific user (defaults to `me`).
- `--page-size <n>` (default 100).
- `--workers <n>` (default 5).
- `--modified-since <iso8601>` to limit the pull to tasks updated after a timestamp.
- `--completed-since <iso8601>` to match Asana’s completion filter.
- `--all-users` to iterate over every user stored in SQLite (requires `get_users` run first).
- `--log-level <LEVEL>` to adjust logging verbosity.

In the interactive CLI, the personal-task assignee prompt can search users by name or email from the local SQLite snapshot before falling back to a raw GID entry.

### Sync Personal Tasks (Incremental)
Runs the same personal-task pipeline but only fetches tasks updated since the last successful run (cursor stored in `personal_task_cursors`). Pass `--since` to override the stored cursor for one-off replays.
```bash
python3 -m scripts.get_personal_tasks_delta --assignee me
```
Required flags:
- (none; defaults to `--assignee me`; can be widened with `--all-users`).

Optional flags:
- `--workspace-gid <gid>` to override the workspace.
- `--assignee <gid|me>` to choose the user key used for both fetching and cursor storage.
- `--since <iso8601>` to seed the cursor for this run.
- `--page-size <n>` / `--workers <n>` / `--log-level <LEVEL>` as described above.
- `--all-users` to run incremental syncs for every stored user, honoring individual cursors.

> **Deprecated:** prefer `python3 -m scripts.get_incremental_updates` for ongoing changes. This incremental personal-tasks script remains for legacy compatibility and is parked under `scripts/deprecated/` (a thin wrapper lives in `scripts/` to avoid breaking existing calls).

### Incremental Updates
Replays the task + extras pipeline for anything touched in the last _N_ days. The script looks at `modified_at`, so any change to the task (field edits, comments, attachments, subtask activity) will trigger a refresh. Updated rows overwrite the previous snapshot, missing comments/attachments are kept but flagged with `is_deleted = 1`, and when you target the whole workspace the script issues a single `tasks/search` query instead of iterating every historical project (note: Asana caps search requests at 60/min).
```bash
python3 -m scripts.get_incremental_updates --days-back 3 --all-teams --exclude-team-gids <gid>
```
Required flags:
- `--days-back <n>` to set the lookback window (defaults to 3 if omitted).
- One scope selector: `--project-gid`, `--team`, `--team-gids`, `--all-teams`, or `--personal`/`--personal-assignees`.

Optional flags mirror the other scripts (`--project-gid`, `--team`, `--team-gids`, `--all-teams`, `--exclude-team-gids`, `--workers`, `--page-size`). The summary reports how many tasks, comments, and attachments were analyzed, created/updated, or marked as deleted (with per-team totals and optional per-project detail).

## Exports & Atlassian Integrations
All report date columns now export as `YYYY-MM-DD` with zero-padded month and day values. Jira comment timestamps in the Jira CSV export remain in Jira's required comment format.
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

### Update Atlassian User Map
Maintains the `user_atlassian_accounts` lookup table that translates Asana user GIDs into Atlassian account IDs, emails, and user status so Jira exports can resolve comment authors. There are two ways to populate it.

**Option A — raw Atlassian user export (recommended).** Feed an Atlassian export directly and let the script match rows to Asana users by email, with no manual mapping step. Only the `User id`, `email`, and `User status` columns are read; everything else is ignored.
```bash
python3 -m scripts.update_atlassian_user_map --clear --atlassian-export path/to/export-users.csv
```
Email matching is case-insensitive, and only Asana users whose email appears in the export are mapped. Each run logs how many rows were scanned, how many Asana users matched, and how many Asana users with an email had no match.

**Option B — pre-mapped CSV.** Provide a CSV with `gid`, `atlassianAccountId`, and optionally `atlassianEmail` and `atlassianStatus` (or `status`). Start from the [template](Sample%20Data/atlassian_user_map_template.csv) if you are building one from scratch.
```bash
python3 -m scripts.update_atlassian_user_map --csv path/to/mapping.csv
```

Both options upsert by Asana GID. Add `--clear` to delete every existing mapping first — use it alone to wipe the table, or alongside an input file to fully replace the contents.

Flags (supply at least one of `--csv`, `--atlassian-export`, or `--clear`):
- `--atlassian-export <path>` — load a raw Atlassian user export and match Asana users by email.
- `--csv <path>` — load a pre-mapped user file.
- `--clear` — delete all rows from `user_atlassian_accounts` before loading.
- `--database <path>` — point at a different SQLite file (default `data/asana.sqlite`).
- `--log-level <LEVEL>` — adjust logging verbosity (default `INFO`).

### Search Jira For External IDs
Fetch Jira issues using the v3 `POST /rest/api/3/search/jql` endpoint (nextPageToken pagination), pull the external ID field (e.g., an Asana task GID stored in `customfield_<id>`), and upsert the Jira→Asana mapping into SQLite. The query is always forced to `ORDER BY created ASC, issuekey ASC` to keep pagination deterministic. Requires `ATLASSIAN_USERNAME` and `ATLASSIAN_TOKEN` in the resolved credentials.
```bash
python3 -m scripts.search_jira_external_ids \\
  --jira-base-url https://example.atlassian.net \\
  --jql 'project = ENG AND "External issue ID" is not EMPTY' \\
  --external-id-field customfield_12345 \\
  --max-results 500
```
Required flags:
- `--jira-base-url <url>` for the Jira Cloud instance.
- `--jql <expression>` to pick the issue set (order is forced to `created ASC`).
- `--external-id-field <key>` identifying the field that holds the external/task ID (e.g., `customfield_12345`).

Optional flags:
- `--max-results <n>` page size per search call (default 500; clamped to Jira’s cap).
- `--database <path>` SQLite file to update (default `data/asana.sqlite`).
- `--dry-run` to print mappings without writing to SQLite.
- `--log-level <LEVEL>` to adjust verbosity (default `INFO`).

### Export Project Tasks To Jira CSV
Transforms normalized SQLite data for a project into a Jira-compatible CSV (one row per task/subtask). Includes parent IDs, assignee name/email, due date, completion status/timestamp, task description, user comments, populated custom fields (multi-select values appear in sequential columns with the same name), and the current project snapshot counts from `projects` on every row (`project_num_tasks`, `project_num_incomplete_tasks`, `project_num_completed_tasks`, and `project_task_counts_captured_at`). If a task’s assignee is not an active Atlassian user, the export substitutes the active user who created the most tasks in that project for the assignee-facing columns. It also surfaces task-derived project activity columns (`project_last_task_created_at`, `project_last_task_resolved_at`, `project_last_task_updated_at`), which remain blank until task rows exist in SQLite.
```bash
python3 -m scripts.export_project_to_jira_csv --project-gid <gid>
# multiple projects
python3 -m scripts.export_project_to_jira_csv --project-gids <gid1,gid2>
# all projects for a team
python3 -m scripts.export_project_to_jira_csv --team-gid <team_gid> [--archived-scope active|archived|all]
```
Required flags:
- One scope selector: `--project-gid`, `--project-gids`, or `--team-gid` (optionally with `--archived-scope`).

Optional flags:
- `--database <path>` to point at a different SQLite file.
- `--output-dir <path>` to change where the CSV is written (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `<ProjectName>-<YYYYMMDD-HHMM>.csv` filename.
- `--stats-only` to print project statistics without writing a CSV.
- `--export-id <value>` to record every exported task under a shared identifier and skip any task already exported with the same ID.
- `--clear-export-ids` to wipe all recorded export/task mappings from SQLite and exit.
- `--clear-export-id <value>` to delete recorded mappings for a specific export ID and exit.
- `--fallback-atlassian-id <account_id>` to substitute an Atlassian account ID anywhere a mapping is missing.
- `--incremental-refresh [prompt|yes|no]` to control per-project refresh prompts.
- `--custom-fields <name_or_gid,...>` to restrict the export to the specified custom fields.
- `--no-custom-fields` to omit all custom fields from the export.
- `--no-custom-field-prompt` to skip the interactive selector.
- `--jira-base-url <url>` to set the Jira instance used when constructing the “External issue ID” JQL search links.
- `--no-jira-url-prompt` to skip the Jira base URL question in unattended runs; requires `--jira-base-url`.
- `--add-epics` to inject a synthetic project-level epic row, place it in the `Open` section, and re-parent top-level tasks to it for Jira imports that expect an Epic ancestor.
- `--add-section-epics` to inject one synthetic epic per section and re-parent top-level tasks to the first section they belong to.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

When you provide `--export-id`, every exported task GID is persisted to the `exported_tasks` table along with the project it came from. Subsequent runs that reuse the same `--export-id` omit any task already logged. Use `--clear-export-ids` before a new migration wave if you want a clean slate.

When `--export-id` is present and you're writing to the default `exports/` directory, each run (even single-project exports) now uses a dedicated `exports/export-XX-<export-id>` folder so the task CSV, field counts, link modifications, and skipped-task reports stay grouped together.

When launched from an interactive terminal and `--custom-fields` isn’t provided, the script pauses after listing detected fields (with per-field value counts) so you can export all fields or enter a comma-separated subset. It also prints a single comma-separated line you can copy, prune, and paste back into the prompt. Provide `--jira-base-url` to set the JQL link host up front; otherwise the script prompts for it once. In unattended runs, pass both `--jira-base-url` and `--no-jira-url-prompt`.

Each user-authored comment appears in its own `Comment` column (the header repeats for every additional comment). Comment cells follow Jira’s expected format `dd/MMM/yy h:mm AM/PM;<atlassian_account_id>; <comment text>` and preserve multiline content via CSV quoting. Comment author IDs are resolved through the `user_atlassian_accounts` table using the task creator’s Asana GID; refresh it via `scripts.update_atlassian_user_map` whenever Atlassian account assignments change.

Jira project metadata columns (`jira_project_name`, `jira_project_key`) come from `scripts.atlassian.import_jira_project_map`; populate that table before exporting to carry the Jira identifiers alongside each task row.
`jira_project_type` is emitted as `software` for every exported row so Jira imports have the project type context inline.

Every export also writes a `<filename>_link_modifications.csv` companion that lists every Asana URL the script augmented (plus full field contents when relevant). Each entry includes `task_gid`, field, original, modified, and optional full content.

### Export Team Project Jira CSV
Builds a single Jira import CSV for the team-to-project hierarchy. The file is ordered with all Epic rows first, then all Project rows. Epic rows represent Asana Teams with at least one associated Project and include `Issue Type`, `Task ID`, `Summary`, `Owner`, `Owner Email`, `Owner Atlassian ID`, `Owner Atlassian Status`, three `Labels` columns, `Parent Id` (blank), `Project Status` (blank), `Project Count (Asana)` and `Task Count (Asana)` (the team's total project count and total actual-task count), and a multiline `Description`. The `Owner` column carries the Asana display name. Three columns share the `Labels` header so Atlassian merges them into multiple label values: the first holds the owner source (e.g. `team_admin`, `project_owner`, `task_creator`), the second holds the `last_active_$yyyy` label derived from `last_task_updated_at` (blank on Epic rows), and the third holds the project type (`personal_project` or `team_project`, blank on Epic rows). Project rows use `Issue Type = Task`, point `Parent Id` at the owning Epic’s `Task ID`, and set `Project Status` from the project archive flag. The `Owner`/`Owner Email`/`Owner Atlassian ID`/`Owner Atlassian Status` columns and the owner-source `Labels` value carry the project's **effective owner** (the project owner when their Atlassian account is active, otherwise the project's top active task creator, so the source is `project_owner` or `task_creator`; all blank when neither applies — the raw project owner is still listed in the description). Project rows populate only `Task Count (Asana)` (the project's actual task count) and leave `Project Count (Asana)` blank. Each project `Description` is organized into three bullet-listed sections under bold headings (rich ADF in the direct-API path, plain text in the CSV): **Primary Details** (`actual_task_count`, `owner_name`, `owner_email`, `owner_atlassian_status`, `project_created_at`, `project_completed`, `project_completed_at`), **Secondary Details** (`num_incomplete_tasks`, `num_completed_tasks`, `last_task_created_at`, `last_task_resolved_at`, `last_task_updated_at`), and **Implied Details** — the effective owner (`effective_owner_source`/`_name`/`_email`/`_atlassian_status` — the project owner when their Atlassian account is active, otherwise the project's top active task creator) and the most-created-tasks creator (`top_task_creator_name`/`_email`/`_atlassian_status`/`_task_count`). Field names render bold; the project's own date/flag fields are prefixed `project_` to distinguish them from task-derived dates. Each Epic `Description` uses the same bold-keyed, bulleted, rich-ADF style across two sections: **Primary Details** (`first_admin_email`, `additional_admin_emails`, `project_count_total`, `project_count_active`, `project_count_archived`, `actual_task_count_total`) and **Implied Details** (`effective_admin`/`_email`/`_source`, the team's majority project owner `majority_project_owner`/`_email`/`_atlassian_status`/`_project_count`, and the most-created-tasks creator `task_creator`/`_email`/`_atlassian_status`/`_task_count`). The team-level owner fields follow the same Atlassian-aware selection order as the Team Report. Epic descriptions report owners by display name and omit the per-user Atlassian id/email and `first_admin_gid` fields. Projects without a team association are attached to a synthetic `No Team` Epic that has no owner or description but still carries `Project Count (Asana)`/`Task Count (Asana)` aggregated over the team-less projects it parents. The project type is exported as a `Labels` value (`personal_project` for personal task trackers, `team_project` otherwise; blank on Epic rows) rather than a standalone column.
```bash
python3 -m scripts.export_team_project_jira_csv
```
Optional flags:
- `--database <path>` to point at a different SQLite file (default `data/asana.sqlite`).
- `--output-dir <path>` to change where the CSV is written (default `exports/`).
- `--filename <name>` to override the auto-generated `team_project_jira_import_<timestamp>.csv` filename.
- `--workspace-gid <gid>` to force the workspace used when building project URLs.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

### Create Team/Project Jira Issues (Direct API)
A direct alternative to the CSV import above: instead of generating a CSV for the Jira import wizard, this creates the same Team→Epic and Project→Task hierarchy by POSTing to the Jira Cloud REST API. It reuses the exact row-building logic of `export_team_project_jira_csv` (same owner resolution, descriptions, and `No Team` epic), then for each row resolves issue-type and custom-field IDs via `createmeta`, creates all Epics first, captures their real Jira keys, and links each Task to its team's Epic via `parent`. Standard fields map directly (`summary`; `description` as ADF; `assignee` by Atlassian accountId when the (effective) owner's account is active — for Tasks the owner is the *effective* owner: the project owner when their Atlassian account is active, otherwise the project's top active task creator, matching how Epics resolve their effective admin; `labels` from the owner source (`project_owner`/`task_creator`/…) + last-active label + project type (`personal_project`/`team_project`), spaces stripped); the remaining columns (`Team URL`, `Project URL`, `Instructions`, `Project Status`, plus `Project Count (Asana)` on Epics and `Task Count (Asana)` on both Epics and Tasks) map to Jira custom fields resolved by field name (override the column→field-name map with `--field-map`). The two count fields must exist on the relevant Jira create screens (`Project Count (Asana)` on Epic; `Task Count (Asana)` on Epic + Task) or their values are warned-and-skipped. Created issues are recorded in the `team_project_jira_mappings` table, so re-runs skip anything already created (no duplicates). Requires `ATLASSIAN_USERNAME`/`ATLASSIAN_TOKEN` in the resolved credentials.
```bash
# Preview the payloads without creating anything
python3 -m scripts.create_team_project_jira_issues --jira-base-url https://example.atlassian.net --jira-project-key MIG --dry-run
# Create issues (prompts for confirmation; use --yes to skip)
python3 -m scripts.create_team_project_jira_issues --jira-base-url https://example.atlassian.net --jira-project-key MIG
# Create issues for a single team only (interactively search for the team by name)
python3 -m scripts.create_team_project_jira_issues --jira-base-url https://example.atlassian.net --jira-project-key MIG --pick-team
```
Required flags:
- `--jira-base-url <url>` for the Jira site (e.g., `https://example.atlassian.net`).
- `--jira-project-key <KEY>` the target Jira project where Epics/Tasks are created.

Optional flags:
- `--epic-issue-type <name>` / `--task-issue-type <name>` issue-type names to resolve via `createmeta` (defaults `Epic` / `Task`).
- `--field-map <path>` JSON object overriding column → Jira custom field name mappings.
- `--workers <n>` concurrent workers for the task-creation pass (default `4`; epics are created sequentially first).
- `--limit <n>` cap the number of issues created this run (epics counted first) for a cautious first pass; skips are not counted.
- `--team-gid <gid>` restrict the run to a single team — its Epic plus that team's project Tasks (the synthetic `No Team` epic is excluded). Mutually exclusive with `--pick-team`.
- `--pick-team` launch the interactive picker to search teams by name and create issues only for the selected team.
- `--dry-run` resolve fields and print the JSON payloads without creating issues.
- `--yes` skip the confirmation prompt.
- `--database <path>`, `--workspace-gid <gid>`, `--output-dir <path>`, `--log-level <LEVEL>` as elsewhere.

A run report CSV (`team_project_jira_create_<timestamp>.csv`) records each entry's `kind`, `asana_gid`, `summary`, resulting `jira_key`, and `status` (`created`, `skipped-existing`, `skipped-limit`, `dry-run`, or `error`).

### Import Jira Task Keys
Persists Jira issue keys (after a Jira import) back to SQLite so future exports know which tasks already exist in Jira. Expects Jira export headers `Issue key` and `External issue ID` by default; override column names with `--jira-column` and/or `--task-column` if your export labels them differently (e.g., `Issue Key`, `Custom field (Asana Task ID)`). BOMs and surrounding whitespace in headers are ignored.
```bash
python3 -m scripts.import_jira_task_keys --csv /path/to/jira_export.csv
# Example overriding column names
python3 -m scripts.import_jira_task_keys --csv /path/to/jira_export.csv --jira-column "Issue Key" --task-column "Custom field (Asana Task ID)"
```
Required flags:
- `--csv <path>` pointing to the Jira export with `Issue key` (or your chosen Jira column) and whichever task ID column you specify (defaults to `External issue ID`).

Optional flags:
- `--jira-column <header>` to use a differently named Jira issue key column.
- `--task-column <header>` to use a differently named Asana task ID column.
- `--database <path>` to point at a different SQLite snapshot (default `data/asana.sqlite`).
- `--log-level <LEVEL>` to adjust verbosity (default `INFO`).

Each import upserts the mappings found in the CSV. A single Asana task may accumulate multiple Jira keys (one per Jira project you imported it into), but a single Jira key can only belong to one task; attempting to reuse a key for a different task raises an error. Rows whose task IDs don’t exist in SQLite are skipped with a warning.

### Clear Jira Task Keys
Removes stored Jira mappings when you need a fresh slate before re-importing.
```bash
python3 -m scripts.clear_jira_task_keys
```
Required flags:
- (none).
Optional flags:
- `--database <path>`: point to a different SQLite snapshot (default: `data/asana.sqlite`).
- `--task-gids <gid1,gid2>` to clear only specific tasks (by default every mapping is deleted).
- `--project-gid <project_gid>` to clear mappings for all tasks that belong to that Asana project (ignored when `--task-gids` is provided).
- `--log-level <LEVEL>` for verbosity.

Only the `task_jira_mappings` table is affected, so your Asana data remains intact for subsequent exports or attachment syncs.

### Import Jira Project Associations
Stores Jira project metadata (name/key) by project GID so exports can include them:
```bash
python3 -m scripts.atlassian.import_jira_project_map --csv path/to/file.csv
```
Required flags:
- `--csv <path>` containing `project_gid,project_name,project_key`.

Optional flags:
- `--database <path>`: SQLite snapshot to read/write (default `data/asana.sqlite`).
- `--log-level <LEVEL>` for verbosity.

The CSV must contain `project_gid,project_name,project_key`. Once imported, Jira project name/key are added as columns in the Jira CSV export.

The exporter also walks each task’s description and comments to augment embedded Asana links so Jira reviewers see context immediately:
- Task URLs gain a newline with the Jira JQL search.
- Profile URLs resolve to `[~accountid:...]` mentions when an Atlassian mapping exists, otherwise display `<Display Name> - not migrated` (with HTML inspection to recover obfuscated profile IDs).
- Project list URLs are replaced with the project’s friendly name or user mention, depending on the Asana HTML hints.
- Inline attachment URLs receive the stored filename on the next line, wrapped in `!filename!`.

When `--stats-only` is supplied, the script prints summary information to stdout instead of writing a file: the project snapshot counts from `projects`, total task count, counts by hierarchy depth, a roster of users referenced by the project (including Atlassian IDs/emails when available), and custom field usage counts.

Task-centric user columns pair the human-readable details with Atlassian IDs and Atlassian emails (e.g., `assignee`, `assignee_email`, `assignee_atlassian_id`, `assignee_atlassian_email`, and the same quartet for `creator`). The export also surfaces scheduling and scope metadata (`current_project`, aggregated `projects`, `start_on`, `start_at`, `created_at`, `due_date`, `actual_time_minutes`) and includes semicolon-separated `Tags` columns. Every export also writes a companion `<filename>_field_counts.csv` listing each detected custom field, how many tasks populated it, and whether it was included in the primary CSV.

### Sync Jira Attachments
Downloads every attachment referenced by the tasks in a project and uploads those files to the Jira issues linked via `task_jira_mappings`, keeping filenames consistent with the `!filename!` markers embedded in notes/comments.
```bash
python3 -m scripts.sync_jira_attachments --project-gid <gid> --jira-base-url https://example.atlassian.net
# or
python3 -m scripts.sync_jira_attachments --project-gids <gid1,gid2,...> --jira-base-url https://example.atlassian.net
```
Required flags:
- One scope selector: `--project-gid <gid>` or `--project-gids <gid1,gid2,...>`.
- `--jira-base-url <url>` for the target Jira site.

Optional flags:
- `--database <path>`: SQLite snapshot to read from (default `data/asana.sqlite`).
- `--output-dir <path>`: Where to write the CSV audit report (default `exports/`). Each project run produces `<ProjectName>_attachment_sync_<timestamp>.csv`.
- `--max-workers <n>` / `--throttle-seconds <s>`: Control concurrency and insert a small delay between uploads to respect Jira’s rate limits (defaults: 2 workers, 0.5 seconds).
- `--dry-run`: Lists all prospective downloads/uploads without touching Asana or Jira.
- `--keep-downloads`: Leave the temporary files on disk after uploading.
- `--task-gid <gid>`: Limit the run to a single Asana task.
- `--log-level <LEVEL>` for verbosity tuning.

When writing to the default `exports/` directory, each attachment sync run creates its own `attachment-export-XX` folder. Multi-project runs also write a consolidated `attachments_consolidated_<timestamp>.csv` alongside the per-project reports.

Requirements:
- The resolved credentials must include `ATLASSIAN_USERNAME` and `ATLASSIAN_TOKEN` (Jira API token) alongside the Asana fields.
- The Jira base URL is required so the script knows which site to call.
- Attachments are downloaded under `exports/tmp_attachments/` (one temp folder per run). Each file is fetched once per Asana attachment id, cached locally, and uploaded per Jira issue key. If a Jira issue already has a file with the same name, the script skips re-uploading and records `skipped-existing` in the report.

## Sensitive Local Data
This toolkit intentionally writes migration artifacts to local disk. Treat the following as potentially sensitive:
- `data/asana.sqlite` — normalized snapshot of tasks, comments, attachments metadata, users, emails, and related entities.
- `exports/` — Jira CSVs, user exports, audit reports, link-modification reports, and attachment sync reports.
- `exports/tmp_attachments/` — temporary attachment downloads created during Jira attachment syncs.

Attachment temp folders are deleted automatically on normal completion unless `--keep-downloads` is used. Interrupted or failed runs can still leave residual temp files behind, so review `exports/tmp_attachments/` during cleanup. SQLite snapshots and export files persist until you remove or archive them according to the client’s retention policy.

Suggested post-migration cleanup:
```bash
rm -rf exports
rm -f data/asana.sqlite
rm -f .asana_jira_env_path
```

If access is no longer needed, also remove or rotate the client-specific env file stored under your home directory.

## Testing
Run the full unit test suite:
```bash
python3 -m unittest discover tests
```
Target ≥90% coverage and add regression tests for every bug fix.

Generate a statement-coverage report when preparing releases:
```bash
python3 -m trace --count --file trace.out -m unittest discover tests
```
Inspect `trace.out` to verify critical modules stay within the ≥90% target. Integration smoke tests live under `tests/integration/`; they exercise CLI entrypoints end-to-end with in-memory SQLite databases and mocked HTTP clients.

## Virtual Environment
Create and activate a project-local virtual environment to keep dependencies isolated and avoid conflicts with system/global Python packages:
```bash
python3 -m venv .venv
source .venv/bin/activate
```
This keeps any future dependency pins scoped to the project, prevents global packages from shadowing modules, and lets you remove the environment cleanly by deleting `.venv`.

## Operations Runbook
- **Credentials**: keep client-specific env files under your home directory, point each repo copy at the right one with `.asana_jira_env_path`, and rotate personal access tokens immediately if they are ever committed or shared.
- **Logging**: every script accepts `--log-level` (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Use `DEBUG` to trace API pagination or rate-limit retries; default `INFO` is suitable for unattended runs.
- **Sync recovery**: rerun the relevant script with the same scope. Upserts are idempotent, and incremental sync summaries confirm whether rows were analyzed, updated, or re-created.
- **SQLite maintenance**: new indexes on task linkage tables keep large syncs responsive. Vacuum occasionally (`sqlite3 data/asana.sqlite 'VACUUM;'`) after pruning old data.
- **Exports**: Jira CSVs accumulate under `exports/` (git-ignored). Rotate or archive large batches to avoid local storage bloat.

## Rate Limit Awareness
The shared HTTP client surfaces the most recent Asana rate limit headers via `AsanaHttpClient.last_rate_limit`. Inspect this metadata after requests to monitor remaining capacity and defer heavy jobs when approaching the limit.

## Open Question
- `tasks.hierarchy_depth` now records nested subtask depth, but we still need to confirm whether downstream exports should flatten deeper chains or preserve the full hierarchy as-is.
