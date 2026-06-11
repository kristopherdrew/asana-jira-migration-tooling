# Working Session Notes

## Environment & Constraints
- Scripts run from repo root; all state persists locally.
- API credentials are loaded from environment variables or a client-specific env file, with optional project-local pointers via `.asana_jira_env_path`.
- Web search is available for documentation lookups, but API calls still need to be executed manually outside the agent runtime.
- Use only Python standard library (no third-party dependencies).
- When `skip_ssl_verification` is true, scripts must disable SSL verification to work around the local certificate mismatch; keep the flag configurable per selected credential source.

## Rate Limiting & Concurrency
- Follow Asana rate-limit guidance (paid domain): up to 1500 requests/min, search limited to 60/min, concurrent GET ≤50, concurrent writes ≤15, and duplication/instantiation/export jobs limited to 5 concurrent executions per user. citeturn0search0
- Honor `Retry-After` headers on 429 responses and implement exponential backoff.
- Default to 5 worker threads for API fetches; CLI flag `--workers` can override.

## Project Structure & Shared Utilities
- Runtime modules now live under `src/` (`src/tasks`, `src/projects`, etc.), with reusable helpers consolidated in `src/lib/` (`config`, `database`, `http_client`, `logging_utils`, `project_scope`, `team_picker`, `user_backfill`).
- SQLite remains the normalized store for every entity; all scripts share the `Database` helper for schema initialization and writes.
- CLI entrypoints sit in `scripts/` and are intended to run with `python3 -m scripts.<name>` so the repo root stays on `sys.path`.
- Exports land in `exports/`; logs are emitted to stdout via the shared logging helper so no dedicated `logs/` directory is required.

## Script Order & Responsibilities
1. `get_teams.py` – fetch workspace teams, optional membership details.
2. `get_projects.py`
3. `get_users.py`
4. `get_custom_fields.py`
5. `get_tasks.py`
6. `get_task_extras.py`
7. `export_project_to_jira_csv.py`
8. Optional: `get_incremental_updates.py` for targeted delta refreshes.

## Schema Overview
- `Database.initialize` (see `src/lib/database.py`) creates all tables, indexes, and views required by the current scripts: teams, team_members, projects, project_custom_fields, project_custom_field_settings, users, user_workspaces, tasks, task_projects, task_custom_fields, task_custom_field_multi_enum, task_relationships, task_followers, task_comments, task_attachments, `personal_task_cursors`, plus the `view_project_custom_field_configurations` and `view_project_task_hierarchy_counts` views.
- Tasks now include an `is_personal` flag, and projects carry `is_personal_project` so pseudo-projects (one per assignee) can represent personal tasks without colliding with real projects. These pseudo rows use the assignee’s name and live in the standard `projects` table to keep downstream queries simple.
- Column backfill and safety helpers (`_ensure_column`, `_ensure_indexes`, `_ensure_views`) keep older databases aligned with the latest schema without manual migrations.
- Each script relies on the shared `Database` helpers to upsert data, ensuring schema consistency across the toolchain.

## Implementation Status

### Completed Decisions
- Database schema (tables, indexes, views) is finalized in `src/lib/database.py`; initialization and idempotent migrations run automatically when scripts connect.
- Shared utility modules (`src/lib/config.py`, `database.py`, `http_client.py`, `logging_utils.py`, `project_scope.py`, `team_picker.py`, `user_backfill.py`) are in place and imported across all CLI entrypoints.
- Logging standardization and checkpointing are implemented: `logging_utils.configure_logging` centralizes formatting, and long-running syncs commit progress every `COMMIT_INTERVAL` tasks.
- Every script reads `skip_ssl_verification` from the shared credential loader when constructing the HTTP client, and task pipelines backfill unknown users/custom fields via `UserBackfill` and `fetch_custom_field` helpers.
- End-to-end data coverage is live: teams, projects, users, custom fields, tasks, task extras, incremental updates, and Jira CSV export scripts all run via `python3 -m scripts.<name>` with shared scope flags (`--project-gid`, `--team`, `--team-gids`, `--all-teams`, `--exclude-team-gids`, `--workers`).
- Personal task coverage is handled by `scripts.get_personal_tasks` (full) and `scripts.get_personal_tasks_delta` (incremental). Both reuse the shared database helpers, attach personal tasks to synthetic projects keyed by the assignee, and maintain cursors in `personal_task_cursors`.

### Remaining Follow-ups
- Validate whether downstream consumers need special handling for deeply nested subtasks (beyond parent/child relationships surfaced today). `tasks.hierarchy_depth` now captures the nesting level, but we still need to confirm whether exports should flatten or preserve multi-level chains.
