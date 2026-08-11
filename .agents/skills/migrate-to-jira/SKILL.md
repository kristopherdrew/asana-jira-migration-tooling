---
name: migrate-to-jira
description: Use when migrating an Asana project or team into Jira — running the export → create-issues → import-keys → external-IDs → attachments chain, or re-running/cleaning up a wave. Codifies the ordering, the prerequisites (user/project maps), the two creation paths (CSV import vs. direct API), and the re-run/cleanup gotchas so a migration wave lands consistently.
---

# Migrate an Asana project/team into Jira

This is the operational runbook for the Jira side. The data must already be in
`data/asana.sqlite` (see the **refresh-asana-data** skill) before any of this runs.
Every Jira-bound script reads `ATLASSIAN_USERNAME` / `ATLASSIAN_TOKEN` and honors
`skip_ssl_verification` from the shared credential loader.

Two creation paths exist — pick one per wave, don't mix:
- **CSV import** (`export_*_jira_csv` → Jira import wizard → `import_jira_task_keys`) — you
  drive the Jira import UI; keys come back via the wizard's export.
- **Direct API** (`create_team_project_jira_issues`) — issues are POSTed straight to Jira
  and Asana→Jira mappings are persisted automatically (no `import_jira_task_keys` step).

## 0. Prerequisites (do these once per migration, before exporting)
1. **User map** — populate `user_atlassian_accounts` so comment authors / assignees resolve:
   `python3 -m scripts.update_atlassian_user_map --clear --atlassian-export <export-users.csv>`
   (matches Asana users to Atlassian accounts by email). Re-run whenever Atlassian
   assignments change — stale maps mean dropped assignees and unresolved comment authors.
2. **Jira project map** (CSV-import path, per-task export only) — populate `jira_project_name`/
   `jira_project_key` columns via
   `python3 -m scripts.atlassian.import_jira_project_map --csv <project_gid,project_name,project_key>.csv`.

## A. CSV-import path

### A1. Export
- **Per project / set / team:**
  `python3 -m scripts.export_project_to_jira_csv --project-gid <gid>`
  (or `--project-gids a,b` / `--team-gid <gid> [--archived-scope active|archived|all]`).
  Writes `<Project>-<ts>.csv` + a `_field_counts.csv` companion + a `_link_modifications.csv`
  audit. Use `--export-id <wave>` to dedup across runs (persists to `exported_tasks`; skips
  already-exported tasks) and group output under `exports/export-XX-<wave>/`.
  - `--stats-only` to scope without writing. `--custom-fields a,b` / `--no-custom-fields` to
    control fields. `--add-epics` / `--add-section-epics` to inject Epic ancestors.
  - For unattended runs pass `--jira-base-url <url> --no-jira-url-prompt --no-custom-field-prompt`.
- **Team→Project hierarchy (Team→Epic / Project→Task):**
  `python3 -m scripts.export_team_project_jira_csv`
  (skips zero-task projects unless `--include-empty-projects`; team-less projects land under a
  synthetic `No Team` epic).

### A2. Import in Jira
Run the CSV through Jira's external-import wizard. Map the **External issue ID** column to a
Jira custom field (this is the Asana task GID — the join key for everything downstream).

### A3. Bring keys back
`python3 -m scripts.import_jira_task_keys --csv <jira_export.csv>`
(expects headers `Issue key` + `External issue ID`; override with `--jira-column` /
`--task-column`). Upserts into `task_jira_mappings`. A task may map to several Jira keys
(one per Jira project), but a key belongs to one task.

## B. Direct-API path (alternative to A1–A3)
`python3 -m scripts.create_team_project_jira_issues --jira-base-url <url> --jira-project-key <KEY>`
Builds the same Team→Epic / Project→Task hierarchy via the REST API: resolves issue-type +
custom-field IDs via `createmeta`, creates Epics first, then links Tasks via `parent`.
Records `team_project_jira_mappings` so re-runs skip what exists.
- **Always preview first:** add `--dry-run` (prints payloads, creates nothing).
- `--pick-team` / `--team-gid <gid>` to scope to one team; `--limit <n>` for a cautious first
  pass; `--workers <n>` (default 4) for the task pass.
- `--map-fields` to interactively build a column→field map, save it, and reuse via
  `--field-map <path>` on later runs.
- Resilience is always on: unassignable assignees are retried unassigned; 429s are backed-off
  and requeued. Errored items stay unmapped so a later re-run picks them up.
- Writes `team_project_jira_create_<ts>.csv` with per-row status.

## C. Attachments (after issues exist + keys/mappings are stored)
`python3 -m scripts.sync_jira_attachments --project-gid <gid> --jira-base-url <url>`
(or `--project-gids a,b`). Downloads Asana attachments and uploads them to the Jira issues in
`task_jira_mappings`, matching the `!filename!` markers. Always `--dry-run` first. Tune with
`--max-workers` / `--throttle-seconds`; already-present files are `skipped-existing`.

## D. Re-runs & cleanup
- **Clear task→key mappings** before re-importing a wave:
  `python3 -m scripts.clear_jira_task_keys` (all), or `--task-gids` / `--project-gid` to scope.
- **Reset direct-API mappings** to recreate issues deleted in Jira:
  `create_team_project_jira_issues ... --reset` (honors `--team-gid`/`--pick-team`; preview with
  `--dry-run`).
- **Reset export-id dedup:** `export_project_to_jira_csv --clear-export-ids` (all) or
  `--clear-export-id <wave>`.
- **Repair personal-project URLs** (one-time, for issues created before My Tasks links existed):
  `python3 -m scripts.atlassian.update_personal_project_jira_urls --jira-base-url <url> --jira-project-key <KEY> --dry-run`.

## Gotchas
- The **External issue ID = Asana task GID** is the join key end-to-end; don't remap it.
- Refresh `user_atlassian_accounts` before *every* wave — it drives assignees and comment authors.
- The direct-API count fields (`Project Count (Asana)` on Epic; `Task Count (Asana)` on Epic+Task)
  must exist on the relevant Jira create screens or their values are warned-and-skipped.
- Most Jira scripts also live in the Interactive CLI **Atlassian** menu — same logic, prompted.
- Full per-flag detail: [docs/jira-integration.md](../../../docs/jira-integration.md).
