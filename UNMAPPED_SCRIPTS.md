# Unmapped Scripts — Interactive CLI Coverage Reference

A quick reference of CLI scripts that are **not** reachable from the interactive
CLI (`scripts/interactive_cli.py`), plus scripts that *are* mapped but don't expose
all of their command-line flags.

> **Maintenance:** Update this file whenever a script is added, wired into / removed
> from `scripts/interactive_cli.py`, or has flags added/exposed. To verify the
> mapped set, grep the invocation points:
> ```bash
> grep -noE "run_script\([a-z_]+(\.[a-z_]+)*\.main" scripts/interactive_cli.py | sort -u
> ```
> Also check `run_initial_metadata` (the metadata batch wires several mains via a
> `steps` list rather than direct `run_script(...)` calls).

_Last reviewed: 2026-06-23_

---

## 1. Scripts NOT mapped to the Interactive CLI

Real CLI entrypoints with no reachable path from the interactive CLI.

| Script (module path) | Purpose | Category |
|---|---|---|
| `scripts.atlassian.create_team_project_jira_issues` | Create Jira work items (Team→Epic, Project→Task) directly via the REST API. | **User-facing gap** |
| `scripts.atlassian.search_jira_external_ids` | JQL search → harvest external IDs (e.g. Asana task GID) → upsert Jira↔Asana mappings. | **User-facing gap** |
| `scripts.get_personal_tasks_delta` | Incremental personal-task sync via last-modified cursor. | **Dead import** — imported at `interactive_cli.py:31` but never invoked. |
| `scripts.backfill_child_task_projects` | Backfill child-task project memberships from parents. | Maintenance utility |
| `scripts.debug_fetch_task_comments` | Diagnostic: compare a task's raw stories vs the filtered comment list. | Debug tool |
| `scripts.deprecated.backfill_task_hierarchy_depth` | Legacy rebuild of `tasks.hierarchy_depth`. | Deprecated |
| `scripts.deprecated.get_personal_tasks_delta` | Legacy delta personal-task sync. | Deprecated |

**Notes**
- The two genuine user-facing gaps are `create_team_project_jira_issues` and
  `search_jira_external_ids`.
- `get_personal_tasks_delta` is imported as if intended for use but is never called
  (no `run_script(get_personal_tasks_delta.main, ...)`), so it is effectively unmapped.
- The remaining entries are backfill/debug/deprecated utilities that arguably do not
  belong in the interactive menu.

---

## 2. Mapped scripts with flags NOT exposed by the Interactive CLI

### Ubiquitous, by-design (not treated as gaps)
Almost no mapped script exposes these — the CLI hardcodes sensible defaults / relies
on resolved credentials:
`--database`, `--workspace-gid`, `--output-dir`, `--filename`, `--page-size`, `--workers`.

### Behaviorally meaningful gaps
Flags that change *what the script does* but cannot be reached from the interactive flow:

| Mapped script | Unreachable flags |
|---|---|
| `scripts.atlassian.export_project_to_jira_csv` | `--custom-fields` (granular selection; only all/none offered), `--add-epics`, `--add-section-epics`, `--also-per-project`, `--consolidated-filename`, `--clear-export-id` (single) |
| `scripts.atlassian.update_atlassian_user_map` | `--atlassian-export` (raw Atlassian-export ingest mode), `--clear` — both documented primary inputs, yet only `--csv` is prompted |
| `scripts.atlassian.sync_jira_attachments` | `--task-gid`, `--max-workers`, `--throttle-seconds`, `--keep-downloads` |
| `scripts.get_tasks` | `--project-gids`, `--team-gids`, `--exclude-team-gids`, `--project-archived-scope` |
| `scripts.get_personal_tasks` | `--completed-since`, `--modified-since` |
| `scripts.get_teams` | `--update-team-members` |
| `scripts.get_incremental_updates` | `--workers`, `--page-size` |

**Notes**
- No mapped script exposes 100% of its flags.
- Largest functional under-exposure: `export_project_to_jira_csv` (epic-generation
  strategies and granular custom-field selection are unreachable).
- Most surprising: `update_atlassian_user_map` — the `--atlassian-export` ingest mode
  (a documented main feature) is not offered interactively.
