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

_Last reviewed: 2026-08-12_

---

## 1. Scripts NOT mapped to the Interactive CLI

Real CLI entrypoints with no reachable path from the interactive CLI.

| Script (module path) | Purpose | Category |
|---|---|---|
| `scripts.get_personal_tasks_delta` | Incremental personal-task sync via last-modified cursor. | **Dead import** — imported by `interactive_cli.py` but never invoked. |
| `scripts.backfill_child_task_projects` | Backfill child-task project memberships from parents. | Maintenance utility |
| `scripts.backfill_task_hierarchy_depth` | Compatibility entrypoint for rebuilding `tasks.hierarchy_depth`. | Maintenance utility |
| `scripts.describe_db` | Inspect the live SQLite schema or regenerate the data dictionary. | Maintenance / developer utility |
| `scripts.debug_fetch_task_comments` | Diagnostic: compare a task's raw stories vs the filtered comment list. | Debug tool |
| `scripts.inspect_project_status_data` | Probe project status-update APIs and dump raw response data. | Debug tool |
| `scripts.atlassian.publish_markdown_to_confluence` | Publish or update local Markdown files as Confluence pages. | Specialized user-facing utility |
| `scripts.atlassian.report_migration_coverage` | Reconcile foreign-migrated Asana projects against Jira by task name. | Specialized diagnostic |
| `scripts.atlassian.report_unassigned_jira_assignees` | Report unassigned migrated Jira issues against current owner resolution. | Specialized diagnostic |
| `scripts.atlassian.update_personal_project_jira_urls` | Repair migrated personal-project Jira links in place. | One-time repair utility |
| `scripts.deprecated.backfill_task_hierarchy_depth` | Legacy rebuild of `tasks.hierarchy_depth`. | Deprecated |
| `scripts.deprecated.get_personal_tasks_delta` | Legacy delta personal-task sync. | Deprecated |

**Notes**
- `create_team_project_jira_issues` is mapped under **Prepare Jira**, and
  `search_jira_external_ids` is mapped under **Migrate Projects** and
  **Maintain & Validate**.
- `get_personal_tasks_delta` is imported as if intended for use but is never called
  (no `run_script(get_personal_tasks_delta.main, ...)`), so it is effectively unmapped.
- Top-level compatibility wrappers for mapped `scripts.atlassian.*` modules are not
  listed separately. `scripts.personal_tasks_cli` is a helper module rather than a
  standalone `main()` entrypoint.
- Most remaining entries are specialized, backfill, debug, repair, or deprecated
  utilities that may not belong in the general interactive menu.

---

## 2. Mapped scripts with flags NOT exposed by the Interactive CLI

### Ubiquitous, by-design (not treated as gaps)
Many mapped scripts do not expose these — the CLI hardcodes sensible defaults / relies
on resolved credentials, although selected workflows prompt for workers or output paths:
`--database`, `--workspace-gid`, `--output-dir`, `--filename`, `--page-size`, `--workers`.

### Behaviorally meaningful gaps
Flags that change *what the script does* but cannot be reached from the interactive flow:

| Mapped script | Unreachable flags |
|---|---|
| `scripts.atlassian.export_project_to_jira_csv` | `--add-epics`, `--add-section-epics`, `--consolidated-output`, `--also-per-project`, `--consolidated-filename`, `--clear-export-id` (single). Granular custom-field selection is available through the exporter's own prompt, though the outer CLI does not construct `--custom-fields` directly. |
| `scripts.atlassian.update_atlassian_user_map` | `--atlassian-export` (raw Atlassian-export ingest mode), `--clear` — both documented primary inputs, yet only `--csv` is prompted |
| `scripts.atlassian.sync_jira_attachments` | `--task-gid`, `--max-workers`, `--throttle-seconds`, `--keep-downloads` |
| `scripts.get_tasks` | `--exclude-team-gids`, `--project-archived-scope`, `--all-teams-state-file` |
| `scripts.get_personal_tasks` | `--completed-since`, `--modified-since` |
| `scripts.get_teams` | `--update-team-members` |
| `scripts.get_incremental_updates` | `--workers`, `--page-size` |

**Notes**
- Largest functional under-exposure: `export_project_to_jira_csv` (epic-generation
  strategies and its native consolidated-output modes are unreachable; the interactive
  workflow instead performs its own post-export merge for multi-project runs).
- Most surprising: `update_atlassian_user_map` — the `--atlassian-export` ingest mode
  (a documented main feature) is not offered interactively.
