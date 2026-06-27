# Atlassian / Jira Integration

Scripts that map Asana users to Atlassian accounts, generate Jira import CSVs, create Jira issues directly via the REST API, and sync attachments. Run each through the package module path (`python3 -m scripts.<name>`) as described in the README's Usage Conventions.

Jira-bound scripts read `ATLASSIAN_USERNAME` and `ATLASSIAN_TOKEN` from the resolved credentials. Jira comment timestamps in the Jira CSV export use Jira's required comment format; all other report date columns export as `YYYY-MM-DD`.

← Back to [README](../README.md)

### Update Atlassian User Map
Maintains the `user_atlassian_accounts` lookup table that translates Asana user GIDs into Atlassian account IDs, emails, and user status so Jira exports can resolve comment authors. There are two ways to populate it.

**Option A — raw Atlassian user export (recommended).** Feed an Atlassian export directly and let the script match rows to Asana users by email, with no manual mapping step. Only the `User id`, `email`, and `User status` columns are read; everything else is ignored.
```bash
python3 -m scripts.update_atlassian_user_map --clear --atlassian-export path/to/export-users.csv
```
Email matching is case-insensitive, and only Asana users whose email appears in the export are mapped. Each run logs how many rows were scanned, how many Asana users matched, and how many Asana users with an email had no match.

**Option B — pre-mapped CSV.** Provide a CSV with `gid`, `atlassianAccountId`, and optionally `atlassianEmail` and `atlassianStatus` (or `status`). Start from the [template](../Sample%20Data/atlassian_user_map_template.csv) if you are building one from scratch.
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

### Pull Asana Migration Decisions From Jira
Read the **"Asana Migration Decision"** custom field back from the project-level Jira issues (the Task issues created by `create_team_project_jira_issues`) and store each value in the `project_jira_decisions` table, keyed by Asana project GID. The field id is resolved by name at runtime via `GET /rest/api/3/fields` (no hard-coded `customfield_<id>`), issues are paged with `POST /rest/api/3/search/jql` (deterministic `ORDER BY created ASC, issuekey ASC`), and each `jira_key` is mapped to its project through `team_project_jira_mappings` (`kind='project'`). The decision then appears as the `jira_migration_decision` column in the project details report (and the combined `.xlsx` Projects tab). Requires `ATLASSIAN_USERNAME` and `ATLASSIAN_TOKEN` in the resolved credentials.

`--jql` is optional: when omitted, the search is derived from the project-level Jira keys already stored in `team_project_jira_mappings`. The distinct project keys are pulled out of those issue keys (e.g. `MIG-12` → `MIG`) and combined into `project IN (<derived keys>) AND issuetype = "Task" AND "<field-name>" is not EMPTY`. So a fresh pull usually needs only the base URL.
```bash
# derive the JQL from stored keys (no --jql needed)
python3 -m scripts.atlassian.get_jira_migration_decisions \\
  --jira-base-url https://example.atlassian.net \\
  --dry-run
# write the values
python3 -m scripts.atlassian.get_jira_migration_decisions \\
  --jira-base-url https://example.atlassian.net
# or scope explicitly
python3 -m scripts.atlassian.get_jira_migration_decisions \\
  --jira-base-url https://example.atlassian.net \\
  --jql 'project = MIG AND issuetype = "Task"'
```
Required flags:
- `--jira-base-url <url>` for the Jira Cloud instance.

Optional flags:
- `--jql <expression>` to pick the issue set explicitly (order is forced to `created ASC`). Omit to derive the default from stored project keys.
- `--field-name <name>` display name of the decision field (default `Asana Migration Decision`; resolved case-insensitively and used in the derived JQL).
- `--max-results <n>` page size per search call (default 500; clamped to Jira’s cap).
- `--include-empty` to store mapped issues even when the decision field is blank (default skips blanks).
- `--database <path>` SQLite file to update (default `data/asana.sqlite`).
- `--dry-run` to print decisions without writing to SQLite.
- `--log-level <LEVEL>` to adjust verbosity (default `INFO`).

Issues whose `jira_key` has no project-level mapping are skipped and counted (run `create_team_project_jira_issues` first if they should map). If `--jql` is omitted and there are no project-level mappings yet, the run exits with guidance to create the issues first or pass `--jql`. One decision is stored per project (latest write wins); `UNIQUE(jira_key)` guards against a Jira issue mapping to two projects.

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
Builds a single Jira import CSV for the team-to-project hierarchy. The file is ordered with all Epic rows first, then all Project rows. Epic rows represent Asana Teams with at least one associated Project and include `Issue Type`, `Task ID`, `Summary`, `Owner`, `Owner Email`, `Owner Atlassian ID`, `Owner Atlassian Status`, three `Labels` columns, `Parent Id` (blank), `Project Status` (blank), `Project Count (Asana)` and `Task Count (Asana)` (the team's total project count and total actual-task count), and a multiline `Description`. The `Owner` column carries the Asana display name. Three columns share the `Labels` header so Atlassian merges them into multiple label values: the first holds the owner source (e.g. `team_admin`, `project_owner`, `task_creator`), the second holds the `last_active_$yyyy` label derived from `last_task_updated_at` (blank on Epic rows), and the third holds the project type (`personal_project` or `team_project`, blank on Epic rows). Project rows use `Issue Type = Task`, point `Parent Id` at the owning Epic’s `Task ID`, and set `Project Status` from the project archive flag. The `Project URL` is the project's Asana deep link, except for personal task trackers (synthetic `personal_<user_gid>` projects with no real Asana project): those use the owner's **My Tasks** list link (`https://app.asana.com/0/<user_task_list_gid>/list`, sourced from `users.user_task_list_gid` captured by `get_users`), or are left blank when the My Tasks GID is unknown (the dead synthetic URL is never emitted). The `Owner`/`Owner Email`/`Owner Atlassian ID`/`Owner Atlassian Status` columns and the owner-source `Labels` value carry the project's **effective owner** (the project owner when their Atlassian account is active, otherwise the project's top active task creator, so the source is `project_owner` or `task_creator`; all blank when neither applies — the raw project owner is still listed in the description). Project rows populate only `Task Count (Asana)` (the project's actual task count) and leave `Project Count (Asana)` blank. Each project `Description` is organized into three bullet-listed sections under bold headings (rich ADF in the direct-API path, plain text in the CSV): **Primary Details** (`actual_task_count`, `owner_name`, `owner_email`, `owner_atlassian_status`, `project_created_at`, `project_completed`, `project_completed_at`), **Secondary Details** (`num_incomplete_tasks`, `num_completed_tasks`, `last_task_created_at`, `last_task_resolved_at`, `last_task_updated_at`), and **Implied Details** — the effective owner (`effective_owner_source`/`_name`/`_email`/`_atlassian_status` — the project owner when their Atlassian account is active, otherwise the project's top active task creator) and the most-created-tasks creator (`top_task_creator_name`/`_email`/`_atlassian_status`/`_task_count`). Field names render bold; the project's own date/flag fields are prefixed `project_` to distinguish them from task-derived dates. Each Epic `Description` uses the same bold-keyed, bulleted, rich-ADF style across two sections: **Primary Details** (`first_admin_email`, `additional_admin_emails`, `project_count_total`, `project_count_active`, `project_count_archived`, `actual_task_count_total`) and **Implied Details** (`effective_admin`/`_email`/`_source`, the team's majority project owner `majority_project_owner`/`_email`/`_atlassian_status`/`_project_count`, and the most-created-tasks creator `task_creator`/`_email`/`_atlassian_status`/`_task_count`). The team-level owner fields follow the same Atlassian-aware selection order as the Team Report. Epic descriptions report owners by display name and omit the per-user Atlassian id/email and `first_admin_gid` fields. Projects without a team association are attached to a synthetic `No Team` Epic that has no owner or description but still carries `Project Count (Asana)`/`Task Count (Asana)` aggregated over the team-less projects it parents. The project type is exported as a `Labels` value (`personal_project` for personal task trackers, `team_project` otherwise; blank on Epic rows) rather than a standalone column.
```bash
python3 -m scripts.export_team_project_jira_csv
```
By default, projects with **zero synced tasks** are skipped (no Project→Task row is emitted for them), so the export only covers projects that have task data. Pass `--include-empty-projects` to emit a Project→Task row for every project regardless of task count; empty projects render `Task Count (Asana) = 0` (and team-less empty projects join the `No Team` Epic).

Optional flags:
- `--database <path>` to point at a different SQLite file (default `data/asana.sqlite`).
- `--output-dir <path>` to change where the CSV is written (default `exports/`).
- `--filename <name>` to override the auto-generated `team_project_jira_import_<timestamp>.csv` filename.
- `--workspace-gid <gid>` to force the workspace used when building project URLs.
- `--include-empty-projects` create a Project→Task row for projects with zero synced tasks (default: skip empty projects).
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

### Create Team/Project Jira Issues (Direct API)
A direct alternative to the CSV import above: instead of generating a CSV for the Jira import wizard, this creates the same Team→Epic and Project→Task hierarchy by POSTing to the Jira Cloud REST API. It reuses the exact row-building logic of `export_team_project_jira_csv` (same owner resolution, descriptions, and `No Team` epic), then for each row resolves issue-type and custom-field IDs via `createmeta`, creates all Epics first, captures their real Jira keys, and links each Task to its team's Epic via `parent`. Standard fields map directly (`summary`; `description` as ADF; `assignee` by Atlassian accountId when the (effective) owner's account is active — for Tasks the owner is the *effective* owner: the project owner when their Atlassian account is active, otherwise the project's top active task creator, matching how Epics resolve their effective admin; `labels` from the owner source (`project_owner`/`task_creator`/…) + last-active label + project type (`personal_project`/`team_project`), spaces stripped); the remaining columns map to Jira custom fields resolved by field name (override the column→field-name map with `--field-map`): by default `Team URL`→`Team (Asana)`, `Project URL`→`Project (Asana)`, `Instructions`→`Testing Instructions`, `Project Status`→`Asana Current Project Status`, plus `Project Count (Asana)` (Epics) and `Task Count (Asana)` (Epics and Tasks). The two count fields must exist on the relevant Jira create screens (`Project Count (Asana)` on Epic; `Task Count (Asana)` on Epic + Task) or their values are warned-and-skipped. Created issues are recorded in the `team_project_jira_mappings` table, so re-runs skip anything already created (no duplicates). Requires `ATLASSIAN_USERNAME`/`ATLASSIAN_TOKEN` in the resolved credentials.
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
- `--field-map <path>` JSON object overriding column → Jira custom field mappings. Each value may be a Jira field **name** or a field **id**.
- `--map-fields` interactively build the field map: for each managed column it shows the default field name and the field id it resolves to via `createmeta`, then prompts for a field id to use (press Enter to keep resolving by name). When finished it offers to save the map to a JSON file (default `field_map.json` in the current directory) so future runs can reuse it via `--field-map`.
- `--workers <n>` concurrent workers for the task-creation pass (default `4`; epics are created sequentially first).
- `--limit <n>` cap the number of issues created this run (epics counted first) for a cautious first pass; skips are not counted.
- `--reset` delete the persisted Asana→Jira mappings for the in-scope rows before creating, so issues you removed in Jira get recreated. Honors `--team-gid`/`--pick-team` (resets just that team's Epic and project Tasks); without a team scope it resets every mapping. Combine with `--dry-run` first to preview what would be recreated.
- `--team-gid <gid>` restrict the run to a single team — its Epic plus that team's project Tasks (the synthetic `No Team` epic is excluded). Mutually exclusive with `--pick-team`.
- `--pick-team` launch the interactive picker to search teams by name and create issues only for the selected team.
- `--include-empty-projects` create an issue for projects with zero synced tasks too (default: skip empty projects, matching the CSV exporter).

Also available in the Interactive CLI **Atlassian** menu ("Create team/project Jira issues (direct API)"), which prompts for the base URL, project key, optional team scope, the include-empty-projects option, and a dry-run toggle.
- `--dry-run` resolve fields and print the JSON payloads without creating issues.
- `--yes` skip the confirmation prompt.
- `--database <path>`, `--workspace-gid <gid>`, `--output-dir <path>`, `--log-level <LEVEL>` as elsewhere.

**Resilience (always on, no flags):**
- **Unassignable assignee fallback** — if Jira rejects an issue with a `400` because the (active) owner can't be assigned issues in the target project (missing "Assignable User" permission, or a customer/app account), the issue is retried once with the assignee removed so it is still created (unassigned). These show up in the run report as `created` with the message `assignee dropped (user not assignable)`.
- **Rate-limit (429) handling** — the shared Jira client retries 429s with `Retry-After`/jittered backoff. If a creation is still rate limited after that, it is requeued to the end of the run and retried up to a few more rounds (with an escalating delay) before being recorded as `error`. Errored items stay unmapped, so a later re-run picks them up automatically.

A run report CSV (`team_project_jira_create_<timestamp>.csv`) records each entry's `kind`, `asana_gid`, `summary`, resulting `jira_key`, `status` (`created`, `skipped-existing`, `skipped-limit`, `dry-run`, or `error`), and a `message` (e.g. the assignee-dropped note or the error detail).

### Update Personal-Project Jira URLs (Repair)
A one-time, in-place repair for personal-project Jira issues created before My Tasks links were available: those carry a dead synthetic `…/project/personal_<user_gid>` URL in their `Project (Asana)` field. After `get_users` captures each owner's My Tasks GID, this script rebuilds the corrected `https://app.asana.com/0/<user_task_list_gid>/list` value and PUTs it onto the existing issues (`PUT /rest/api/3/issue/{key}`), preserving comments, attachments, and history — no delete/recreate.
```bash
python3 -m scripts.atlassian.update_personal_project_jira_urls \
  --jira-base-url https://example.atlassian.net --jira-project-key MIG --dry-run
```
It targets personal-project Task rows that already have an Asana→Jira mapping and resolves the `Project (Asana)` field via the Task `createmeta`. Rows that resolve to a real My Tasks link are updated; rows where no proper link can be generated have the field **cleared** (`null`) so the dead synthetic URL is removed rather than left in place. Unmapped personal projects are reported `skipped-unmapped`.

Required flags:
- `--jira-base-url <url>` and `--jira-project-key <key>`.

Optional flags:
- `--task-issue-type <name>` issue type the project issues use (default `Task`).
- `--field-map <path>` JSON overriding the column→Jira field map (default `Project URL`→`Project (Asana)`).
- `--workers <n>` concurrent update workers (default 4); `--limit <n>` caps updates this run.
- `--clear-first` run in two passes — clear the field on every in-scope issue, then set the new values — so no stale value survives even if the set pass misses an issue.
- `--dry-run` print the PUT payloads without updating; `--yes` skip the confirmation prompt.
- `--database <path>`, `--workspace-gid <gid>`, `--output-dir <path>`, `--log-level <LEVEL>` as elsewhere.

Writes a run report CSV (`personal_project_url_update_report.csv`) with `asana_gid`, `jira_key`, `project_url`, `status` (`updated`, `cleared`, `skipped-unmapped`, `skipped-limit`, `dry-run-<action>`, or `error`), and a `message`.

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
