# Core Sync Commands

CLI scripts that pull Asana entities into the local `data/asana.sqlite` snapshot. Run each through the package module path (`python3 -m scripts.<name>`) as described in the README's Usage Conventions.

← Back to [README](../README.md)

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

### Sync Portfolios
Captures Asana portfolios and everything attached to them. Because Asana cannot list portfolios workspace-wide (`GET /portfolios` requires an `owner`), the script iterates over every user in the `users` table (run `get_users` first) and lists the portfolios each one owns. For every discovered portfolio it stores the record (`portfolios`), its member projects and nested portfolios (`portfolio_items`), portfolio-scoped custom field settings (`portfolio_custom_field_settings`), memberships (`portfolio_memberships`), and full status-update history (`portfolio_updates`). Items whose `resource_type` is `portfolio` are recursed into and fetched even when no listed user owns them, with a cycle/dedup guard so each portfolio is captured once.
```bash
python3 -m scripts.get_portfolios
```
Required flags:
- (none; uses `ASANA_WORKSPACE_GID` from the resolved credentials).

Optional flags:
- `--workspace-gid <gid>` to override the workspace ID from the resolved credentials.
- `--owner-gid <gid>` to limit discovery to a single portfolio owner instead of every user.
- `--page-size <n>` to adjust the Asana pagination size (default 100).
- `--workers <n>` to set the number of concurrent fetch workers (default 5). DB writes always happen on the main thread.

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
Fetches every accessible user in the workspace, storing their identifiers, email, active status, and workspace memberships in `data/asana.sqlite`. After the user pass, a second concurrent pass captures each user's "My Tasks" list GID (`users.user_task_list_gid`) via `GET /users/{gid}/user_task_list`, enabling working My Tasks deep links (`https://app.asana.com/0/<user_task_list_gid>/list`). Another user's My Tasks is private, so unreadable lists are logged and left blank unless the token is an org-admin / service account; the run logs how many GIDs were captured vs. skipped.
```bash
python3 -m scripts.get_users
```
Required flags:
- (none; uses `ASANA_WORKSPACE_GID` from the resolved credentials).

Optional flags:
- `--workspace-gid <gid>` to override the workspace ID from the resolved credentials.
- `--page-size <n>` to adjust the Asana pagination size (default 100).
- `--workers <n>` concurrent workers for the My Tasks GID fetch pass (default 4).

### Add Service User to Teams
Adds one service user to every Asana team stored in SQLite using Asana's `addUser` endpoint. By default the service user is resolved automatically as the token's own account (`GET /users/me`) and upserted into the `users` table so it becomes searchable; no prompt is shown. Pass `--user-gid` to override, or `--search-users` to pick from the local `users` table interactively. If `/users/me` cannot be reached, the script falls back to the interactive search/GID prompt.
```bash
python3 -m scripts.add_service_user_to_teams
```
Required flags:
- (none; uses `data/asana.sqlite`, the resolved credentials, and the token's own account by default).

Optional flags:
- `--database <path>` to point at a different SQLite snapshot (default `data/asana.sqlite`).
- `--output-dir <path>` to choose where failure exports are written (default `exports/`).
- `--user-gid <gid>` to override the token account and use a specific service user.
- `--search-users` to pick the service user from the local `users` table interactively instead of using the token account.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

If one or more team updates fail, the script writes `service_user_team_add_failures_<timestamp>.csv` into the output directory with the failed team GIDs and the captured error text.

### Add Service User to Projects
Adds one service user to a selected list of Asana project GIDs using Asana's `addMembers` endpoint. Like the teams script, the service user defaults to the token's own account (`GET /users/me`), upserted into the `users` table; pass `--user-gid` to override, and if `/users/me` fails the script falls back to a raw GID prompt.
```bash
python3 -m scripts.add_service_user_to_projects --project-gids <gid1,gid2>
```
Required flags:
- One scope selector: `--project-gid <gid>` repeated as needed, or `--project-gids <gid1,gid2>`.

Optional flags:
- `--database <path>` to point at a different SQLite snapshot (default `data/asana.sqlite`); used to upsert the resolved token account.
- `--user-gid <gid>` to override the token account and use a specific service user.
- `--output-dir <path>` to choose where failure exports are written (default `exports/`).
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

If one or more project updates fail, the script writes `service_user_project_add_failures_<timestamp>.csv` into the output directory with the failed project GIDs and the captured error text.

### Sync Tasks
Recursively fetches tasks (and subtasks) for selected projects, backfilling missing user profiles or custom field definitions on the fly. Persists task metadata, memberships, dependencies, followers, and custom field values into `data/asana.sqlite`. Run with either `--project-gid` for a focused pull or `--team` to drive selection interactively.
```bash
python3 -m scripts.get_tasks
```
Required flags:
- One of `--project-gid <gid>`, `--project-gids <gid1,gid2>`, `--project-gids-file <path>`, `--team`, `--team-gids <gid1,gid2>`, or `--all-teams` (optionally with `--exclude-team-gids <gid1,gid2>`).

Optional flags:
- `--project-gid <gid>` – bypasses the default project and syncs only the specified project.
- `--project-gids <gid1,gid2>` – sync a comma-separated list of projects sequentially.
- `--project-gids-file <path>` – load project GIDs from a UTF-8 file containing newline- or comma-separated values.
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

During an interactive task sync, the CLI also records any project that had a positive `num_tasks` count from the project metadata pull but produced zero imported task rows. The candidate list is written to `exports/missing_service_user_projects.json`, then the CLI offers to add a service user (resolved automatically as the token's own account) to those projects and optionally retry the missed project list immediately.

### Sync Task Extras
Pulls task comments and attachment metadata for the selected projects (using the same scope flags as above) and stores them in `task_comments` and `task_attachments` tables. Only human-authored comments (`resource_subtype = comment_added`) are captured; system events are excluded.
```bash
python3 -m scripts.get_task_extras --team-gids <gid1,gid2>
```
Required flags:
- One of `--project-gid`, `--project-gids`, `--project-gids-file`, `--team`, `--team-gids`, `--all-teams`, or the personal-task flags (`--personal`/`--personal-assignees`).

Optional flags (mirror `get_tasks.py`):
- `--project-gid <gid>` for a single project.
- `--project-gids <gid1,gid2>` to sync a comma-separated list of projects sequentially.
- `--project-gids-file <path>` to load newline- or comma-separated project GIDs from a UTF-8 file.
- `--team` for the interactive picker.
- `--team-gids <gid1,gid2>` or `--all-teams`/`--exclude-team-gids <gid>` for unattended batches.
- `--workers <n>` to tune concurrency when fetching stories/attachments (default 5).
- `--personal` to sync extras for the synthetic `personal_<user_gid>` projects that `get_personal_tasks` writes.
- `--personal-assignees <gid1,gid2>` (implies `--personal`) to scope the run to specific assignees’ `personal_` projects.

Each project run re-fetches every task’s comments/attachments, backfills missing authors, replaces stored rows, and logs progress every ~25 tasks.

### Sync Project Status Updates
Captures the narrative content that lives on a project itself—status updates and project comments—and stores it in the unified `project_updates` table. This covers projects that hold status updates/comments but have **no tasks** (otherwise invisible to the migration). Two Asana sources are merged: structured status updates (`GET /status_updates?parent=<project_gid>`) and human-authored project comments (`comment_added` stories from `GET /projects/<project_gid>/stories`). Each row is tagged with `kind` (`status_update` or `comment`); status updates keep their `title`, `status_type`, and `html_text`. Asana auto-generates a `comment_added` story mirroring each status update, so any comment whose `(created_by, created_at)` matches a status update is dropped in favor of the richer status-update row. System stories (owner changes, custom-field edits, etc.) are excluded, and rows are stored chronologically.
```bash
python3 -m scripts.get_project_status_updates --project-gid <gid>
```
Required flags:
- One of `--project-gid`, `--project-gids`, `--project-gids-file`, `--team`, `--team-gids`, or `--all-teams`.

Optional flags:
- `--project-gid <gid>` / `--project-gids <gid1,gid2>` / `--project-gids-file <path>` for one or more specific projects; files accept newline- or comma-separated values.
- `--team` for the interactive picker, or `--team-gids <gid1,gid2>` / `--all-teams` (+ `--exclude-team-gids <gid>`) for batches.
- `--workers <n>` to tune per-project fetch concurrency (default 5).
- `--workspace-gid <gid>` (logging only) and `--log-level <LEVEL>`.

Each project run re-fetches its status updates and comments, dedups mirrors, backfills missing authors, soft-deletes rows that are no longer present, and replaces stored rows. Re-runs are idempotent.

### Inspect Project Status Data (Diagnostic)
Read-only probe (no DB writes) for understanding what a single project's status-update/story endpoints actually return before changing storage. For the given `--project-gid` it calls a set of candidate Asana endpoints — `GET /status_updates?parent=<gid>`, per-update detail `GET /status_updates/<su_gid>`, nested `GET /status_updates/<su_gid>/stories`, `GET /projects/<gid>/stories`, and the deprecated `GET /projects/<gid>/project_statuses` — wrapping each so one failure (e.g. a 404 route) doesn't abort the rest. It prints an endpoint → HTTP-status → record-count summary and dumps the raw JSON to `exports/project_status_probe_<gid>_<timestamp>.json`.
```bash
python3 -m scripts.inspect_project_status_data --project-gid <gid>
```
Required flags:
- `--project-gid <gid>`.

Optional flags:
- `--output-dir <path>` for the JSON dump (default `exports`).
- `--detail-limit <n>` max status updates to fetch full detail / nested stories for (default 5).
- `--log-level <LEVEL>`.

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
- One scope selector: `--project-gid`, `--project-gids`, `--project-gids-file`, `--team`, `--team-gids`, `--all-teams`, or `--personal`/`--personal-assignees`.

Optional flags mirror the other scripts (`--project-gid`, `--project-gids`, `--project-gids-file`, `--team`, `--team-gids`, `--all-teams`, `--exclude-team-gids`, `--workers`, `--page-size`). The summary reports how many tasks, comments, and attachments were analyzed, created/updated, or marked as deleted (with per-team totals and optional per-project detail).

For multi-project scopes, projects that return HTTP 404 because they were deleted or are no longer accessible are logged and skipped so the remaining projects can finish. Other API errors still stop the run.
