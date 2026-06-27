---
name: refresh-asana-data
description: Use when pulling or refreshing Asana data into data/asana.sqlite — a first full sync, re-syncing a team/project, or an incremental top-up. Codifies the correct ordering (metadata before tasks before extras), the dependencies between syncs, the scope flags shared across scripts, and when to run the backfills.
---

# Refresh the Asana SQLite snapshot

All sync scripts write `data/asana.sqlite`, read credentials via `src/lib/config.py`
(`ASANA_WORKSPACE_GID` + token), and honor `skip_ssl_verification`. Run via the module path
(`python3 -m scripts.<name>`). The **Initialization** and **Tasks** menus of
`scripts/interactive_cli.py` wrap most of this with prompts — use them for guided runs.

## Ordering matters (later steps depend on earlier ones)
Run the metadata layer before tasks, and tasks before anything task-derived (extras, reports,
exports). Skipping order leaves blank columns, not errors.

### 1. Metadata (fixed pipeline — this is the Initialization menu)
1. `python3 -m scripts.get_teams` (add `--include-members` for memberships)
2. `python3 -m scripts.get_users` — also captures each user's My Tasks GID
   (`users.user_task_list_gid`); **required before** `get_portfolios`, personal-task `--all-users`,
   and the personal-project URL repair.
3. Service-user access (so the token can read every project's tasks):
   `python3 -m scripts.add_service_user_to_teams` then
   `python3 -m scripts.add_service_user_to_projects --user-gid <gid> --project-gids a,b`
4. `python3 -m scripts.get_projects` (`--archived-scope active|archived|all`) — captures the
   per-project task-count snapshot.
5. `python3 -m scripts.get_custom_fields`

### 2. Tasks (the bulk pull)
- `python3 -m scripts.get_tasks` with one scope: `--project-gid` / `--project-gids` /
  `--team` (interactive) / `--team-gids` / `--all-teams [--exclude-team-gids ...]`.
  - `--all-teams` supports `--resume-all-teams` (state in `exports/all_team_sync_state.json`).
  - `--workers <n>` (default 5), `--project-archived-scope active|archived|all`.
  - Interactive runs record projects with a positive metadata count but zero imported rows to
    `exports/missing_service_user_projects.json` and offer to add the service user + retry —
    the usual fix for an under-permissioned token.

### 3. Task-derived layers (same scope flags as get_tasks)
- `python3 -m scripts.get_task_extras --team-gids a,b` — comments + attachment metadata
  (human comments only). Needed before attachment sync and comment columns in exports.
- `python3 -m scripts.get_project_status_updates --project-gid <gid>` — project-level status
  updates/comments into `project_updates`; captures projects that have narrative but **no tasks**.

### 4. Optional surfaces
- **Portfolios:** `python3 -m scripts.get_portfolios` — iterates every user as owner (needs
  `get_users` first); `--owner-gid` to scope, `--workers`.
- **Personal tasks (My Tasks):** `python3 -m scripts.get_personal_tasks --assignee me|<gid>`
  (`--all-users` to iterate everyone). Synthetic `personal_<user_gid>` projects.

### 5. Backfills (run after the relevant sync / any schema change)
- `python3 -m scripts.backfill_task_hierarchy_depth` — rebuilds `tasks.hierarchy_depth`
  (top-level = 0, each subtask +1). Run after task syncs and schema changes.
- `python3 -m scripts.backfill_child_task_projects` — fixes child-task project association.

## Incremental top-ups (after a full sync exists)
- **Preferred:** `python3 -m scripts.get_incremental_updates --days-back 3 <scope>` — replays
  the task+extras pipeline for anything `modified_at` within the window; workspace-wide scope
  uses a single `tasks/search` (Asana caps search at 60/min). Deleted comments/attachments are
  flagged `is_deleted=1`, not removed.
- Personal-task delta (`get_personal_tasks_delta`) is **deprecated** — prefer
  `get_incremental_updates`.

## Diagnostics
- `python3 -m scripts.inspect_project_status_data --project-gid <gid>` — read-only probe of a
  project's status/story endpoints (dumps raw JSON to `exports/`).
- `python3 -m scripts.describe_db [--table <name>]` — DB introspection / row counts / latest
  `synced_at` to confirm what's populated.

## Gotchas
- Blank task-derived columns in reports/exports usually mean a missing sync step, not a bug —
  check the ordering above.
- Concurrent-fetch scripts expose `--workers`; single-request/export/diagnostic scripts don't.
- Full per-flag detail: [docs/sync-commands.md](../../../docs/sync-commands.md).
