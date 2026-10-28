# Atlassian / Jira Integration

Scripts that map Asana users to Atlassian accounts, generate Jira import CSVs, create Jira issues directly via the REST API, and sync attachments. Run each through the package module path (`python3 -m scripts.<name>`) as described in the README's Usage Conventions.

Jira-bound scripts read `ATLASSIAN_USERNAME` and `ATLASSIAN_TOKEN` from the resolved credentials. Jira comment timestamps in the Jira CSV export use Jira's required comment format; all other report date columns export as `YYYY-MM-DD`.

## Named Jira sites

Each client profile can define any number of Jira destinations with `JIRA_SITES`, `JIRA_DEFAULT_SITE`, and `JIRA_SITE_<KEY>_NAME` / `JIRA_SITE_<KEY>_URL`. Use `--jira-site <key>` for standalone commands. The interactive CLI has separate actions to select a site for the current session and to persist the default for future launches. Jira mapping, decision, project-association, and attachment-upload records carry `jira_site_key`, so identical Jira issue keys on different sites remain distinct. The legacy `--prod` flag continues to select `production_jira` when `--jira-site` is omitted.

← Back to [README](../README.md)

When an Atlassian CLI menu action accepts an input file, its picker lists matching files from the repository root regardless of the directory used to launch the CLI. Project-GID lists accept `.txt`, `.csv`, or `.list`; Jira key imports, Atlassian user maps, and Jira project associations list `.csv` files. A relative or absolute path can still be entered manually.

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
Fetch Jira issues using the v3 `POST /rest/api/3/search/jql` endpoint (nextPageToken pagination), pull the external ID field (e.g., an Asana task GID stored in `customfield_<id>`), and upsert the Jira→Asana mapping into SQLite. The query is always forced to `ORDER BY created ASC, issuekey ASC` to keep pagination deterministic. The Jira base URL comes from the credentials file (`JIRA_SANDBOX_BASE_URL` by default, `JIRA_BASE_URL` with `--prod`). Requires `ATLASSIAN_USERNAME` and `ATLASSIAN_TOKEN` in the resolved credentials.
```bash
python3 -m scripts.search_jira_external_ids \\
  --external-id-field customfield_12345
```
By default, the search scope comes from the distinct Jira space keys in the selected
site's `jira_project_mappings`. For repeated or same-day uploads, narrow that scope with
`--project-gids` or `--project-gids-file`; the command resolves those Asana project GIDs
through `jira_project_mappings` and searches only their associated Jira space keys.
External IDs are unique per Jira site: the first Jira issue found for a task is retained
and later occurrences are skipped, unless `--overwrite` is passed (e.g. after a task was
removed from a project and re-added, which gives it a new Jira key) — in that case the
latest Jira search result replaces the stored mapping in both directions (task_gid→key and
key→task_gid).

Optional flags:
- `--prod` to target the production Jira site (`JIRA_BASE_URL`); omit to use the sandbox (`JIRA_SANDBOX_BASE_URL`).
- `--external-id-field <name-or-key>` explicitly identifies the external/task ID field. Without it, the command uses the External Issue ID field saved for the selected site by the Jira CSV importer configuration; if none is saved (or it no longer exists), it discovers `External issue ID` from Jira automatically. A `customfield_<number>` ID is retained even when Jira omits it from its field catalog; the subsequent Jira search validates it.
- `--jira-site <key>` selects the named site and records recovered task mappings under that same site.
- `--overwrite` replaces an existing stored mapping with the latest Jira search result instead of skipping it (or raising on a conflicting Jira key). Use after a task was removed and re-added to a project and picked up a new Jira key.
- `--project-gids <gid1,gid2>` limits the Jira search to the Jira space keys associated with those Asana project GIDs in `jira_project_mappings`.
- `--project-gids-file <path>` limits the Jira search from a newline/comma-delimited file, or a CSV containing a `project_gid` column.
- Jira searches always request 100 issues per page, Jira's API limit; next-page-token pagination retrieves larger result sets.
- `--database <path>` SQLite file to update (default `data/asana.sqlite`).
- `--dry-run` to print mappings without writing to SQLite.
- `--log-level <LEVEL>` to adjust verbosity (default `INFO`).

### Pull Asana Migration Decisions From Jira
Read the **"Asana Migration Decision"** custom field back from the project-level Jira issues (the Task issues created by `create_team_project_jira_issues`) and store each value in the `project_jira_decisions` table, keyed by Asana project GID. The field id is resolved by name at runtime via `GET /rest/api/3/field` (no hard-coded `customfield_<id>`), issues are paged with `POST /rest/api/3/search/jql` (deterministic `ORDER BY created ASC, issuekey ASC`), and each `jira_key` is mapped to its project through `team_project_jira_mappings` (`kind='project'`). The decision then appears as the `jira_migration_decision` column in the project details report (and the combined `.xlsx` Projects tab). Requires `ATLASSIAN_USERNAME` and `ATLASSIAN_TOKEN` in the resolved credentials.

The Jira base URL comes from the credentials file — the **sandbox** (`JIRA_SANDBOX_BASE_URL`) by default, or production (`JIRA_BASE_URL`) with `--prod`. `--jql` is optional too: when omitted, the search is derived from the project-level Jira keys already stored in `team_project_jira_mappings`. The distinct project keys are pulled out of those issue keys (e.g. `MIG-12` → `MIG`) and combined into `project IN (<derived keys>) AND issuetype = "Task" AND "<field-name>" is not EMPTY`. So a fresh sandbox pull usually needs no flags at all.
```bash
# sandbox, JQL derived from stored keys (no flags needed)
python3 -m scripts.atlassian.get_jira_migration_decisions --dry-run
# write the values (sandbox)
python3 -m scripts.atlassian.get_jira_migration_decisions
# target production, scope explicitly
python3 -m scripts.atlassian.get_jira_migration_decisions \\
  --prod --jql 'project = MIG AND issuetype = "Task"'
```
Optional flags:
- `--prod` to target the production Jira site (`JIRA_BASE_URL`); omit to use the sandbox (`JIRA_SANDBOX_BASE_URL`).
- `--jql <expression>` to pick the issue set explicitly (order is forced to `created ASC`). Omit to derive the default from stored project keys.
- `--field-name <name>` display name of the decision field (default `Asana Migration Decision`; resolved case-insensitively and used in the derived JQL).
- Jira searches always request 100 issues per page, Jira's API limit; next-page-token pagination retrieves larger result sets.
- `--include-empty` to store mapped issues even when the decision field is blank (default skips blanks).
- `--database <path>` SQLite file to update (default `data/asana.sqlite`).
- `--dry-run` to print decisions without writing to SQLite.
- `--log-level <LEVEL>` to adjust verbosity (default `INFO`).

Issues whose `jira_key` has no project-level mapping are skipped and counted (run `create_team_project_jira_issues` first if they should map). If `--jql` is omitted and there are no project-level mappings yet, the run exits with guidance to create the issues first or pass `--jql`. One decision is stored per project (latest write wins); `UNIQUE(jira_key)` guards against a Jira issue mapping to two projects.

### Reverse Lookup: Jira To Asana Task Mapping
Discover Jira issues that have external IDs (Asana task GIDs) already set in a custom field, and map them back to the local SQLite database. Useful for backfilling mappings when collaborators have migrated data to Jira separately or using different tooling. Searches Jira via custom JQL, extracts external IDs, looks up tasks and their projects locally, and upserts `task_jira_mappings` and `jira_project_mappings`. Automatically handles conflicts: mappings that would overwrite existing assignments are logged to a conflict report for review rather than failing the run. Orphan tasks (Jira issues with external IDs not in the local database) are also reported. Requires `ATLASSIAN_USERNAME` and `ATLASSIAN_TOKEN` in the resolved credentials.

```bash
python3 -m scripts.atlassian.reverse_lookup_jira_to_asana \\
  --jira-site production_jira \\
  --jql 'project IN (PROJ1, PROJ2) AND "External issue ID" is not EMPTY'
```

The script writes three CSV files to `exports/` with an ISO timestamp:
- `reverse_lookup_summary_<timestamp>.csv` — overall counts (issues processed, tasks mapped, projects mapped, orphans, conflicts)
- `reverse_lookup_conflicts_<timestamp>.csv` (if any) — rows where an upsert would have overwritten an existing mapping. Each conflict includes the conflict type (`task_jira_mappings` or `jira_project_mappings`), the jira_key, task_gid or project_gid, existing value, and attempted value
- `reverse_lookup_orphans_<timestamp>.csv` (if any) — Jira issues with external IDs that have no corresponding task in the local database (reason: task not found, or task has no projects)

Required flags:
- `--jira-site <key>` — which Jira site to search (e.g., `production_jira`, `sandbox_jira`).
- `--jql <query>` — JQL query to find issues (e.g., `project IN (PROJ) AND "External issue ID" is not EMPTY`).

Optional flags:
- `--external-id-field <field-name-or-id>` — custom field name or `customfield_<id>` for the external ID (resolved automatically from prior CSV importer settings if not specified).
- `--database <path>` — SQLite file to update (default `data/asana.sqlite`).
- `--dry-run` — print findings and report names without upserting to SQLite.
- `--log-level <LEVEL>` — adjust logging verbosity (default `INFO`).

Project mapping: When a task is found, the script looks up all Asana projects the task belongs to and attempts to map each project to the Jira project extracted from the issue key. Project details (name, key) are fetched from Jira's REST API. If a project is already mapped to a different Jira project key, the conflict is logged and the existing mapping is retained.

### Export Project Tasks To Jira CSV
Transforms normalized SQLite data for a project into a Jira-compatible CSV (one row per task/subtask). Includes parent IDs, assignee name/email, collaborators (Asana followers other than the task's creator or assignee), due date, completion status/timestamp, task description, user comments, and populated custom fields (multi-select values appear in sequential columns with the same name). Each collaborator is emitted as a repeated adjacent triplet—`Collaborators`, `collaborator_email`, and `collaborator_atlassian_id`—so their display name, email, and mapped Jira account ID remain aligned. Every row includes an `Asana Project Link`, and real task/subtask rows include an `Asana Task Link`; synthetic Epic rows leave the task link blank. Column layout keeps `modified_section` adjacent to the raw `Section` column(s), and places `notes` immediately before the comment columns. The companion user-association CSV includes a `collaborator_count` alongside creator, assignee, completer, and commenter counts. If a task’s assignee is not an active Atlassian user, the export substitutes the active user who created the most tasks in that project for the assignee-facing columns.
```bash
python3 -m scripts.export_project_to_jira_csv --project-gid <gid>
# multiple projects
python3 -m scripts.export_project_to_jira_csv --project-gids <gid1,gid2>
# all projects for a team
python3 -m scripts.export_project_to_jira_csv --team-gid <team_gid> [--archived-scope active|archived|all]
# exact task list
python3 -m scripts.export_project_to_jira_csv --task-gids <task_gid1,task_gid2>
python3 -m scripts.export_project_to_jira_csv --task-gids-file <path>
```
Required flags:
- One scope selector: `--project-gid`, `--project-gids`, `--project-gids-file`, `--team-gid` (optionally with `--archived-scope`), `--task-gids`, or `--task-gids-file`.
- Task-GID scope accepts comma-separated values inline or comma/newline-separated values from a file. Duplicate GIDs are removed while preserving input order. Each task is exported once under its primary project; shared tasks without a primary membership use the lowest project GID. Unknown and projectless GIDs are logged and skipped.

Optional flags:
- `--database <path>` to point at a different SQLite file.
- `--output-dir <path>` to change where the CSV is written (defaults to `exports/`).
- `--filename <name>` to override the auto-generated `<ProjectName>-<YYYYMMDD-HHMM>.csv` filename.
- `--stats-only` to print project statistics without writing a CSV.
- `--export-id <value>` to record every exported task under a shared identifier and skip any task already exported with the same ID.
- `--clear-export-ids` to wipe all recorded export/task mappings from SQLite and exit.
- `--clear-export-id <value>` to delete recorded mappings for a specific export ID and exit.
- When an interactive export includes projects without Jira project associations, the exporter asks for one Jira project key, looks up its name on the selected Jira site, and assigns that verified pair to every currently unmapped project in the selected scope. Existing associations are preserved. For non-interactive runs, use `--map-unmapped-to-jira-project-key <key>` together with `--map-unmapped-to-jira-project-name <name>` to do the same explicitly.
- `--fallback-atlassian-id <account_id>` to substitute an Atlassian account ID anywhere a mapping is missing.
- `--incremental-refresh [prompt|yes|no]` to control per-project refresh prompts.
- `--custom-fields <name_or_gid,...>` to restrict the export to the specified custom fields.
- `--no-custom-fields` to omit all custom fields from the export.
- `--no-custom-field-prompt` to skip the interactive selector.
- `--prod` to build the “External issue ID” JQL search links against the production Jira site (`JIRA_BASE_URL`); omit to use the sandbox (`JIRA_SANDBOX_BASE_URL`).
- `--add-epics` to inject a synthetic project-level epic row, place it in the `Open` section, and re-parent top-level tasks to it for Jira imports that expect an Epic ancestor. Synthetic Epics are automatically split into evenly balanced numbered parts when they would contain more than 1,000 descendant issues by default; each real parent/subtask hierarchy stays together.

A project with zero tasks is not treated as an error: the exporter logs a warning and writes a synthetic-epic-only CSV row for it (as if `--add-epics` had been passed for that project), so task-less projects are never silently dropped from a migration wave. If the project has rows in `project_updates` (status updates/comments captured for projects with no tasks — see [Sync Project Status Updates](sync-commands.md#sync-project-status-updates)), those are added as `Comment` columns on the synthetic epic. A `--task-gids`/`--task-gids-file` selection that matches none of a project's real tasks is still treated as an error.
- `--add-section-epics` to inject one synthetic epic per section and re-parent top-level tasks to the first section they belong to. The same balanced descendant cap applies to section Epics.
- `--synthetic-epic-limit <n>` to choose the maximum descendant issues under one synthetic Epic before it is split into numbered part Epics (default `1000`; valid range `1`-`7999`).
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

When you provide `--export-id`, every exported task GID is persisted to the `exported_tasks` table along with the project it came from. Subsequent runs that reuse the same `--export-id` omit any task already logged. Tasks are also deduplicated within every individual multi-project export, including runs without an export ID. Use `--clear-export-ids` before a new migration wave if you want a clean slate.

When `--export-id` is present and you're writing to the default `exports/` directory, each run (even single-project exports) now uses a dedicated `exports/export-XX-<export-id>` folder so the task CSV, field counts, link modifications, and skipped-task reports stay grouped together.

### Merge Project Export CSVs
Consolidates multiple per-project Jira export CSVs (from [Export Project Tasks To Jira CSV](#export-project-tasks-to-jira-csv)) into one file — useful when a migration wave was exported project-by-project and needs a single CSV for the Jira import wizard. Builds a union of column headers across all input files (repeating a label positionally if one file has more occurrences of it than another, e.g. multiple `Labels` columns) and writes one combined task CSV. By default it also merges each file's companion `*_field_counts.csv` and `*_link_modifications.csv`/`.txt` sidecars (when present) into their own consolidated outputs. If the combined header includes `hierarchy_depth`, rows are sorted parents-before-children (by depth, then `current_project`, then parsed `created_at`).
```bash
python3 -m scripts.merge_project_exports --inputs "exports/*-*.csv"
# multiple explicit patterns/paths, with a source-tracking column
python3 -m scripts.merge_project_exports --inputs exports/ProjectA-20260101-1200.csv exports/ProjectB-20260101-1201.csv --source-column source_project --strip-suffix "-20260101-1200"
```
Required flags:
- `--inputs <pattern...>` one or more glob patterns or literal file paths for the per-project task CSVs (companion `_field_counts.csv`/`_link_modifications.csv`/`.txt` files matched by pattern are excluded from the task-CSV merge and handled separately).

Optional flags:
- `--output <path>` for the combined task CSV (default `exports/_Consolidated-<timestamp>_tasks.csv`).
- `--source-column <name>` to add a column recording each row's originating file stem (with export/timestamp suffixes stripped); errors if the name collides with an existing column.
- `--strip-suffix <suffix>` to additionally strip a shared suffix (e.g. a common timestamp) from the derived source value.
- `--no-merge-field-counts` / `--no-merge-link-mods` to skip merging the respective companion files (merged by default when present).
- `--field-counts-output <path>` / `--link-mods-output <path>` to override the companion output paths (default `<output_stem>_field_counts.csv` / `<output_stem>_link_modifications.csv`).
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

### Manage Export IDs
Reviews and clears the persisted `exported_tasks` history that powers `--export-id` skipping. It does not add task GIDs manually; new IDs are created when an export runs with `--export-id`.
```bash
python3 -m scripts.manage_export_ids --list
python3 -m scripts.manage_export_ids --show <export_id>
python3 -m scripts.manage_export_ids --clear-export-id <export_id>
python3 -m scripts.manage_export_ids --clear-all
```
Flags:
- `--database <path>` to point at a different SQLite file.
- `--list` to show export IDs with task/project counts and first/last use timestamps.
- `--show <export_id>` to include a per-project task-count breakdown for one export ID.
- `--clear-export-id <export_id>` to delete one ID's recorded task history.
- `--clear-all` to delete every recorded export-ID/task mapping.
- `--log-level <LEVEL>` to adjust logging verbosity (default `INFO`).

The Interactive CLI exposes the same review and cleanup flow under **Migrate Projects -> Manage export IDs** for the active profile database.

When launched from an interactive terminal and `--custom-fields` isn’t provided, the script pauses after listing detected fields (with per-field value counts) so you can export all fields or enter a comma-separated subset. It also prints a single comma-separated line you can copy, prune, and paste back into the prompt. The JQL link host comes from the credentials file (`JIRA_SANDBOX_BASE_URL` by default, `JIRA_BASE_URL` with `--prod`).

Each user-authored comment appears in its own `Comment` column (the header repeats for every additional comment). Comment cells follow Jira’s expected format `dd/MMM/yy h:mm AM/PM;<atlassian_account_id>; <comment text>` and preserve multiline content via CSV quoting. Comment author IDs are resolved through the `user_atlassian_accounts` table using the task creator’s Asana GID; refresh it via `scripts.update_atlassian_user_map` whenever Atlassian account assignments change.

Jira project metadata columns (`jira_project_name`, `jira_project_key`) come from `scripts.atlassian.import_jira_project_map`; populate that table before exporting to carry the Jira identifiers alongside each task row. In an interactive terminal, either task CSV exporter can look up a Jira project name from a provided key and assign that pair to all unmapped projects in the selected scope; use the association import when projects need different destinations.
`jira_project_type` is emitted as `software` for every exported row so Jira imports have the project type context inline.

Every export also writes a `<filename>_link_modifications.csv` companion that lists every Asana URL the script augmented (plus full field contents when relevant). Each entry includes `task_gid`, field, original, modified, and optional full content.

Jira issue-import CSVs are hard-capped at **8,000 issue rows** (real tasks plus synthetic epics). A project above the cap is balanced into numbered `_part_001`, `_part_002`, … files while keeping every real parent/subtask tree intact. An oversized synthetic epic is replaced by uniquely identified `— Part N` epics so every child remains in the same import as its parent. Multi-project consolidated output is also emitted as bounded numbered parts, packing complete per-project parts without splitting them again. A `_tasks_manifest.csv` records every project and consolidated file with its issue count. If one real task subtree alone exceeds 8,000 issues, the export stops because that hierarchy cannot be safely divided across Jira imports.

### Generate Jira CSV Importer Configuration
Generates a reusable Jira importer configuration directly from the task export's canonical columns. No existing CSV is required:

```bash
python3 -m scripts.atlassian.generate_jira_csv_import_config
```

By default, the file is stored beside the active profile as `profiles/<profile-name>_<yyyy-mm-dd>_jira_config.txt`, using the profile's `CLIENT_NAME` (filename-safe) and generation date. A same-profile rerun on the same date refreshes that day's file; prior dated files remain available. Pass `--output <path>` to override that location or `--csv-name <name>` to change the placeholder CSV filename recorded inside the configuration. `--configure` forces a fresh review of every saved mapping; otherwise saved mappings are loaded and missing mappings are requested. `--jira-site <key>` selects a named Jira site, `--prod` selects the legacy production site, and `--log-level <LEVEL>` controls verbosity. The command is also available under Interactive CLI **Prepare Jira** ("Generate Jira CSV importer configuration") and does not ask for an exported CSV.

System mappings are generated locally and require no Jira discovery; this includes `Tags` → Jira Labels, `Resolution` → Jira Resolution, and every `collaborator_atlassian_id` column → Jira Watcher. Configuration setup queries only the selected Jira site's field catalog for five custom destinations: **Asana Section**, **Asana Project Link**, **Asana Task Link**, **External Issue ID**, and **Start Date** (`start_on`). Each picker first checks for an exact case-insensitive name match. When no exact match exists, it shows fuzzy matches and allows another fuzzy name search before the user confirms a specific field. A known numeric ID or `customfield_<id>` can also be entered directly and must exist in Jira's returned catalog. Each mapping can be skipped, and an existing saved mapping can be cleared. The `Resolution` column already carries the exact Jira value (`Done` when the Asana task is completed, blank otherwise), so no manual value mapping is needed for it. `modified_section` is mapped to Jira Status without predefined status values; perform that value mapping in Jira's importer UI. No resolutions API query is made.

The review screen shows the current mappings, allows each one to be changed, and can preview the generated JSON before saving. Confirmed field IDs/names/types are persisted as non-secret `JIRA_SITE_<SITE>_CSV_IMPORT_*` settings in the active profile, so Jira sites and clients remain isolated (legacy `CSV_ARCHIVE_*` settings are still read). Skip leaves the saved setting intact; Clear removes it. When either the archival or regular task exporter runs, it copies the active profile's saved configuration into that export bundle's `jira_configurations/` folder without prompting or querying Jira.

### Export Team Project Jira CSV
Builds a single Jira import CSV for the team-to-project hierarchy. The file is ordered with all Epic rows first, then all Project rows. Epic rows represent Asana Teams with at least one associated Project and include `Issue Type`, `Task ID`, `Summary`, `Owner`, `Owner Email`, `Owner Atlassian ID`, `Owner Atlassian Status`, three `Labels` columns, `Parent Id` (blank), `Project Status` (blank), `Project Count (Asana)` and `Task Count (Asana)` (the team's total project count and total actual-task count), and a multiline `Description`. The `Owner` column carries the Asana display name. Three columns share the `Labels` header so Atlassian merges them into multiple label values: the first holds the owner source (e.g. `team_admin`, `project_owner`, `task_creator`), the second holds the `last_active_$yyyy` label derived from `last_task_updated_at` (blank on Epic rows), and the third holds the project type (`personal_project` or `team_project`, blank on Epic rows). Project rows use `Issue Type = Task`, point `Parent Id` at the owning Epic’s `Task ID`, and set `Project Status` from the project archive flag. The `Project URL` is the project's Asana deep link, except for personal task trackers (synthetic `personal_<user_gid>` projects with no real Asana project): those use the owner's **My Tasks** list link (`https://app.asana.com/0/<user_task_list_gid>/list`, sourced from `users.user_task_list_gid` captured by `get_users`), or are left blank when the My Tasks GID is unknown (the dead synthetic URL is never emitted). The `Owner`/`Owner Email`/`Owner Atlassian ID`/`Owner Atlassian Status` columns and the owner-source `Labels` value carry the project's **effective owner** (the project owner when their Atlassian account is active, otherwise the project's top active task creator, so the source is `project_owner` or `task_creator`; all blank when neither applies — the raw project owner is still listed in the description). Project rows populate only `Task Count (Asana)` (the project's actual task count) and leave `Project Count (Asana)` blank. Project rows also carry a `Last Working Day` column holding the project's most recent task-update date (`MAX(tasks.modified_at)`, formatted `YYYY-MM-DD`; blank on Epic rows and empty projects), which the direct-API creator maps to the Jira "Last working day" date field. Each project `Description` is organized into three bullet-listed sections under bold headings (rich ADF in the direct-API path, plain text in the CSV): **Primary Details** (`actual_task_count`, `owner_name`, `owner_email`, `owner_atlassian_status`, `project_created_at`, `project_completed`, `project_completed_at`), **Secondary Details** (`num_incomplete_tasks`, `num_completed_tasks`, `last_task_created_at`, `last_task_resolved_at`, `last_task_updated_at`), and **Implied Details** — the effective owner (`effective_owner_source`/`_name`/`_email`/`_atlassian_status` — the project owner when their Atlassian account is active, otherwise the project's top active task creator) and the most-created-tasks creator (`top_task_creator_name`/`_email`/`_atlassian_status`/`_task_count`). Field names render bold; the project's own date/flag fields are prefixed `project_` to distinguish them from task-derived dates. Each Epic `Description` uses the same bold-keyed, bulleted, rich-ADF style across two sections: **Primary Details** (`first_admin_email`, `additional_admin_emails`, `project_count_total`, `project_count_active`, `project_count_archived`, `actual_task_count_total`) and **Implied Details** (`effective_admin`/`_email`/`_source`, the team's majority project owner `majority_project_owner`/`_email`/`_atlassian_status`/`_project_count`, and the most-created-tasks creator `task_creator`/`_email`/`_atlassian_status`/`_task_count`). The team-level owner fields follow the same Atlassian-aware selection order as the Team Report. Epic descriptions report owners by display name and omit the per-user Atlassian id/email and `first_admin_gid` fields. Projects without a team association are attached to a synthetic `No Team` Epic that has no owner or description but still carries `Project Count (Asana)`/`Task Count (Asana)` aggregated over the team-less projects it parents. The project type is exported as a `Labels` value (`personal_project` for personal task trackers, `team_project` otherwise; blank on Epic rows) rather than a standalone column.
```bash
python3 -m scripts.atlassian.export_team_project_jira_csv
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
A direct alternative to the CSV import above: instead of generating a CSV for the Jira import wizard, this creates the same Team→Epic and Project→Task hierarchy by POSTing to the Jira Cloud REST API. It reuses the exact row-building logic of `export_team_project_jira_csv`, resolves issue types and fields through `createmeta`, creates Epics first, and links each Project Task to its team's Epic. The default configuration resolves the setup fields by name: `Team URL`→`Asana Team Link`, `Project URL`→`Asana Project Link`, `Project Status`→`Asana Current Project Status`, `Task Count (Asana)`→`Asana Task Count`, and `Last Working Day`→`Asana Last Working Day`. `--field-map` can override these defaults. A mapped field must be on the relevant Jira create screen or its value is warned-and-skipped. Created issues are recorded in `team_project_jira_mappings`, so re-runs skip existing mappings. Requires `ATLASSIAN_USERNAME`/`ATLASSIAN_TOKEN` in the resolved credentials.
```bash
# Preview the payloads without creating anything (sandbox)
python3 -m scripts.create_team_project_jira_issues --jira-project-key MIG --dry-run
# Create issues in production (prompts for confirmation; use --yes to skip)
python3 -m scripts.create_team_project_jira_issues --prod --jira-project-key MIG
# Create issues for a single team only (interactively search for the team by name)
python3 -m scripts.create_team_project_jira_issues --prod --jira-project-key MIG --pick-team
```
Required flags:
- `--jira-project-key <KEY>` the target Jira project where Epics/Tasks are created.

Environment selection:
- `--prod` targets the production Jira site (`JIRA_BASE_URL`); omit to use the sandbox (`JIRA_SANDBOX_BASE_URL`).

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

Also available under Interactive CLI **Prepare Jira** ("Preview or create Team/Project Jira hierarchy (API)"), which prompts for the project key, optional team scope, the include-empty-projects option, and a dry-run toggle. The active CLI Sandbox/Production setting supplies the Jira environment.
- `--dry-run` resolve fields and print the JSON payloads without creating issues.
- `--yes` skip the confirmation prompt.
- `--database <path>`, `--workspace-gid <gid>`, `--output-dir <path>`, `--log-level <LEVEL>` as elsewhere.

**Resilience (always on, no flags):**
- **Unassignable assignee fallback** — if Jira rejects an issue with a `400` because the (active) owner can't be assigned issues in the target project (missing "Assignable User" permission, or a customer/app account), the issue is retried once with the assignee removed so it is still created (unassigned). These show up in the run report as `created` with the message `assignee dropped (user not assignable)`.
- **Rate-limit (429) handling** — the shared Jira client retries 429s with `Retry-After`/jittered backoff. If a creation is still rate limited after that, it is requeued to the end of the run and retried up to a few more rounds (with an escalating delay) before being recorded as `error`. Errored items stay unmapped, so a later re-run picks them up automatically.

A run report CSV (`team_project_jira_create_<timestamp>.csv`) records each entry's `kind`, `asana_gid`, `summary`, resulting `jira_key`, `status` (`created`, `skipped-existing`, `skipped-limit`, `dry-run`, or `error`), and a `message` (e.g. the assignee-dropped note or the error detail).

### Update Personal-Project Jira URLs (Repair)
A one-time, in-place repair for personal-project Jira issues created before My Tasks links were available: those carry a dead synthetic `…/project/personal_<user_gid>` URL in their configured project-link field. The default mapping now targets `Asana Project Link`. After `get_users` captures each owner's My Tasks GID, this script rebuilds the corrected `https://app.asana.com/0/<user_task_list_gid>/list` value and PUTs it onto the existing issues, preserving comments, attachments, and history.
```bash
python3 -m scripts.atlassian.update_personal_project_jira_urls \
  --prod --jira-project-key MIG --dry-run
```
It targets personal-project Task rows that already have an Asana→Jira mapping and resolves `Asana Project Link` via the Task `createmeta`. Rows that resolve to a real My Tasks link are updated; rows where no proper link can be generated have the field **cleared** (`null`) so the dead synthetic URL is removed rather than left in place. Unmapped personal projects are reported `skipped-unmapped`.

Required flags:
- `--jira-project-key <key>` (and `--prod` to target production; omit for the sandbox `JIRA_SANDBOX_BASE_URL`).

Optional flags:
- `--task-issue-type <name>` issue type the project issues use (default `Task`).
- `--field-map <path>` JSON overriding the column→Jira field map (default `Project URL`→`Asana Project Link`).
- `--workers <n>` concurrent update workers (default 4); `--limit <n>` caps updates this run.
- `--clear-first` run in two passes — clear the field on every in-scope issue, then set the new values — so no stale value survives even if the set pass misses an issue.
- `--dry-run` print the PUT payloads without updating; `--yes` skip the confirmation prompt.
- `--database <path>`, `--workspace-gid <gid>`, `--output-dir <path>`, `--log-level <LEVEL>` as elsewhere.

Writes a run report CSV (`personal_project_url_update_report.csv`) with `asana_gid`, `jira_key`, `project_url`, `status` (`updated`, `cleared`, `skipped-unmapped`, `skipped-limit`, `dry-run-<action>`, or `error`), and a `message`.

### Set Project "Last Working Day" Field
New issues created by [Create Team/Project Jira Issues](#create-teamproject-jira-issues-direct-api) already get this date at creation (the `Last Working Day`→`customfield_11475` field-map entry). This script is the way to **update** it on issues that already exist — a re-run after more Asana data has synced, or a backfill of issues created before the field was folded in.

Pushes each Asana project's most recent task-update date onto the migrated Jira **Task** issue that represents it. The value is `MAX(tasks.modified_at)` per project — the same figure the [project last-updated report](reports.md) computes — written as a `YYYY-MM-DD` date string to the "Last working day" date custom field (`customfield_11475` by default) via an in-place `PUT /rest/api/3/issue/{key}` (comments, attachments, and history are preserved). The same PUT can also write the project's actual task count onto a "Task Count (Asana)" numeric custom field when `--task-count-field-id` is supplied (no default — omit the flag to skip that field entirely). Only `team_project_jira_mappings` rows with `kind='project'` are targeted (Team→Epic issues are left alone). Projects with no tasks (no last-updated date) are skipped (`skipped-no-date`, field left untouched); unmapped projects are `skipped-unmapped`.
```bash
# Safe preview first (no writes):
python3 -m scripts.atlassian.set_project_last_updated_jira_field --dry-run
# Then a small live sandbox batch, then promote to --prod:
python3 -m scripts.atlassian.set_project_last_updated_jira_field --limit 2 --yes
# Also write the Task Count (Asana) numeric field in the same PUT:
python3 -m scripts.atlassian.set_project_last_updated_jira_field --task-count-field-id customfield_11480 --prod
```
The field is written as a plain date string, which is what a Jira date custom field expects on edit; the field must be present on the issue's edit screen or that issue's PUT is reported as an `error` (the run continues). Defaults to the sandbox site; pass `--prod` for production.

Required flags:
- (none; defaults to `customfield_11475`, the sandbox site, and `data/asana.sqlite`).

Optional flags:
- `--prod` to target production (`JIRA_BASE_URL`); omit for the sandbox `JIRA_SANDBOX_BASE_URL`.
- `--field-id <id>` to write a different Jira date custom field (default `customfield_11475`).
- `--task-count-field-id <id>` to also write the project's actual task count onto a Jira numeric custom field in the same PUT (no default — omit to skip updating a task-count field).
- `--team-gid <gid>` to scope the update to one team's projects.
- `--workers <n>` concurrent update workers (default 4); `--limit <n>` caps updates this run.
- `--dry-run` print the PUT payloads without updating; `--yes` skip the confirmation prompt.
- `--database <path>`, `--output-dir <path>`, `--log-level <LEVEL>` as elsewhere.

Writes a run report CSV (`project_last_updated_jira_update_report.csv`) with `project_gid`, `project_name`, `jira_key`, `last_updated`, `task_count`, `status` (`updated`, `skipped-no-date`, `skipped-unmapped`, `skipped-limit`, `dry-run-updated`, or `error`), and a `message`.

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

Task-centric user columns pair human-readable names and emails with Atlassian IDs (for example, `assignee`, `assignee_email`, and `assignee_atlassian_id`, with the same three columns for `creator`). Redundant Atlassian-email columns are omitted. The export also surfaces scheduling and scope metadata (`current_project`, aggregated `projects`, date-only `start_on`, `created_at`, `due_date`, `actual_time_minutes`); `start_at` is omitted. Repeated `Tags` columns preserve task tags. Every export also writes a companion `<filename>_field_counts.csv` listing each detected custom field, how many tasks populated it, and whether it was included in the primary CSV.

### Download Durable Asana Attachment Archives

Downloads only active, Asana-hosted task attachments into a portable, resumable local
archive. This phase does not require Jira mappings, so it can be completed alongside the
CSV extraction even when attachment uploads will be deferred for months.

```bash
python3 -m scripts.download_asana_attachments --all --archive-id wave-2026-08
python3 -m scripts.download_asana_attachments --project-gids <gid1,gid2> --archive-id wave-2026-08
python3 -m scripts.download_asana_attachments --verify-manifest attachment_archives/wave-2026-08/manifest.json
```

Scope must be supplied with exactly one of `--all`, `--project-gid`, `--project-gids`,
or `--project-gids-file`. `--task-gid` can further narrow that scope. `--archive-root`
defaults to the git-ignored `attachment_archives/` directory, `--archive-id` names the
immutable migration wave, and `--workers` controls concurrent Asana downloads (default 4).

Each archive contains the file payloads plus `manifest.json`, `manifest.csv`, and
`checksums.sha256`. Files are first written as `.part`, validated against Asana's recorded
size, hashed with SHA-256, and atomically renamed. Re-running the same archive ID resumes
valid files and retries incomplete ones. The database records wave state in
`attachment_archives` and per-file state in `attachment_archive_files`; paths inside the
manifest are relative so the archive can be moved or restored on another machine.

The archive directory contains potentially sensitive customer data and must be copied to
approved encrypted storage for long-term retention. A local project copy is not a backup.
Run `--verify-manifest` after every copy and immediately before a delayed Jira upload.

### Upload a Local Attachment Archive to Jira

Verifies every ready archived file, resolves each Asana Task GID through the database's
current `task_jira_mappings`, and uploads the file to every mapped Jira work item. It
also creates Jira remote web links for active non-Asana URL/app attachments captured in
`task_attachments` (for example Google Drive and external links) using the current
SQLite snapshot. Jira keys do not need to exist when the archive is created. Partial
archive entries from attachments that could not be downloaded are warned about and
skipped; corrupt or missing ready files still cancel the upload so they can be
investigated before Jira receives bad payloads.

```bash
python3 -m scripts.upload_jira_attachments \
  --manifest attachment_archives/wave-2026-08/manifest.json \
  --dry-run

python3 -m scripts.upload_jira_attachments \
  --manifest attachment_archives/wave-2026-08/manifest.json \
  --prod

# Upload only attachments whose Asana task belongs to selected projects
python3 -m scripts.upload_jira_attachments \
  --manifest attachment_archives/wave-2026-08/manifest.json \
  --project-gids <gid1,gid2>
```

`--database` selects the current mapping database; `--prod` targets production Jira,
otherwise sandbox is used. Omit project scope to upload all attachments in the archive, or
use exactly one of `--project-gid`, `--project-gids`, or `--project-gids-file` to upload
only files whose task is associated with the selected project(s). `--workers` and
`--throttle-seconds` control Jira request rate. Web-link creation is enabled by default;
pass `--no-weblinks` to upload only archived files.
Every run writes `reports/jira_upload_<timestamp>.csv` inside the archive. Per-destination
results are persisted in `attachment_jira_uploads`, including the Jira attachment ID and
returned size when available. Missing mappings are reported without blocking other files,
and Jira files with the generated `filename__attachment_gid.ext` name are skipped. When
web links are enabled, a companion `reports/jira_weblinks_<timestamp>.csv` is written and
outcomes are persisted in `attachment_jira_weblinks`. Re-runs use a stable Jira
`globalId` (`system=asana&attachment_gid=<attachment_gid>`) so Jira updates existing
remote links instead of duplicating them. `--dry-run` reports both intended file uploads
and intended web-link creates/updates without calling Jira.

In the Interactive CLI, the upload action lists every existing archive manifest beneath
the default `attachment_archives/` directory for numbered selection, then offers all
archive attachments, selected project GIDs, or a project-GID file. A manifest path or
archive directory can still be entered manually for an archive stored elsewhere.

### Deprecated: Direct Sync Jira Attachments

`scripts.deprecated.sync_jira_attachments` downloads and uploads in one run. Prefer
the durable archive workflow for all new migrations because it preserves a verified,
resumable copy before Jira upload.

Downloads every attachment referenced by the tasks in a project and uploads those files to the Jira issues linked via `task_jira_mappings`, keeping filenames consistent with the `!filename!` markers embedded in notes/comments.
```bash
python3 -m scripts.deprecated.sync_jira_attachments --project-gid <gid>
# or, against production
python3 -m scripts.deprecated.sync_jira_attachments --project-gids <gid1,gid2,...> --prod
# or load newline-/comma-separated project GIDs from a file
python3 -m scripts.deprecated.sync_jira_attachments --project-gids-file <path>
```
Required flags:
- One scope selector: `--project-gid <gid>`, `--project-gids <gid1,gid2,...>`, or `--project-gids-file <path>`.

Optional flags:
- `--prod` to target the production Jira site (`JIRA_BASE_URL`); omit to use the sandbox (`JIRA_SANDBOX_BASE_URL`).
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
- The Jira base URL is read from the credentials file (`JIRA_SANDBOX_BASE_URL` by default, `JIRA_BASE_URL` with `--prod`); the chosen environment's URL must be set.
- Attachments are downloaded under `exports/tmp_attachments/` (one temp folder per run). Each file is fetched once per Asana attachment id, cached locally, and uploaded per Jira issue key. If a Jira issue already has a file with the same name, the script skips re-uploading and records `skipped-existing` in the report.

### Publish Markdown to Confluence
Publishes local Markdown documents to Confluence Cloud as pages, converting each body to Confluence **storage format locally** rather than routing the content through a language model. This is the fast path: an agent that "copies" docs into Confluence actually re-generates every page body as model output (slow and expensive — minutes per page); this script does the markdown→storage translation deterministically in `src/lib/markdown_to_confluence.py` (~3 ms/page) so the only per-page cost is one REST call.

```bash
python3 -m scripts.atlassian.publish_markdown_to_confluence \
  --base-url https://example.atlassian.net \
  --space-key DOCS \
  --source-dir docs/presentation/playbook \
  --title-prefix "Vibe Coding — " \
  --exclude README.md \
  --parent-title "Vibe Coding, Safely"
```

Pages are **upserted by title**: a page whose title already exists in the target space is updated in place (a new version is created), otherwise it is created — so re-running is idempotent. Each page title defaults to the file's first `# ` heading (falling back to the filename), and that H1 is stripped from the body so it does not duplicate Confluence's own page title (pass `--keep-h1` to retain it). Files are processed in sorted filename order so numbered docs (`01-…`, `02-…`) land in order.

The converter covers the Markdown the repo's docs use: ATX headings, fenced code blocks (with language → Confluence `code` macro), pipe tables, blockquotes, ordered/unordered/nested lists, GitHub task lists (`- [ ]`/`- [x]` → Confluence task lists), horizontal rules, and inline bold/italic/inline-code/links. Intra-page anchor links (`#heading`) are emitted as written; Confluence generates its own heading anchors, so those may not resolve.

Inputs:
- `--base-url <url>` — the Atlassian **site** URL (e.g. `https://example.atlassian.net`); the client appends `/wiki` itself. Required.
- `--space-key <key>` — Confluence space key the pages live in. Required.
- `--source-dir <dir>` — directory of Markdown files (combined with `--glob`, default `*.md`), and/or positional `files …` for explicit paths.
- `--parent-id <id>` / `--parent-title <title>` — nest the published pages under a parent (title is resolved to an id in the space).
- `--title-prefix <str>` — prepended to every derived title; `--keep-h1` — keep the leading H1 in the body; `--exclude <name>` — skip a file by name (repeatable).
- `--workers <n>` (default 4), `--limit <n>`, `--output-dir <dir>` (default `exports/`), `--dry-run`, `--yes`, `--log-level <LEVEL>`.

Outputs `exports/confluence_publish_report.csv` (columns `source_file`, `title`, `page_id`, `status` [`created`/`updated`/`dry-run`/`error`], `message`). A `--dry-run` is a pure offline preview — it converts every file and writes the report **without credentials or any network call**, so it is the safest way to verify titles and conversion before publishing.

Requirements:
- `ATLASSIAN_USERNAME` and `ATLASSIAN_TOKEN` in the resolved credentials (same as the Jira scripts) — required only for a real publish, not for `--dry-run`.
- The shared Confluence REST client lives in `src/lib/confluence_client.py` (mirrors `src/lib/jira_client.py`: stdlib-only, Basic auth, 429/5xx retries with jittered backoff, honors `skip_ssl_verification`).

### Unassigned Jira Assignee Report (.xlsx)
A one-time, **read-only** diagnostic. Queries the live Jira instance for the migrated issues (the Team→Epic / Project→Task hierarchy created via `create_team_project_jira_issues`) that are **currently unassigned**, then shows which of them would now receive an assignee under the revised owner logic (owner → top active task creator → **active project member**), and who. "Currently unassigned" is taken from Jira itself — the authoritative state, since the create-time assignee `400` drop and any manual edits mean the local DB can't be trusted for it — while the *proposed* assignee is recomputed locally from `build_team_project_rows` (no writes to Jira, Asana, or the database).

```bash
python3 -m scripts.report_unassigned_jira_assignees --prod
```

It reads `team_project_jira_mappings` to learn which Jira issues we created (and their Asana project/team), derives the Jira project key(s) from those issue keys, and runs a single JQL query (`project IN (…) AND assignee IS EMPTY AND issuetype IN ("Task","Epic")`, paged via `nextPageToken`). Each unassigned issue is joined back to its Asana source and its revised effective owner. Unassigned issues that are **not** in our mappings (e.g. manually-created ones) are skipped and counted.

The workbook (`exports/unassigned_jira_assignees_<timestamp>.xlsx`) has three tabs:
- **Overview** — generation time, source DB, Jira base URL, the JQL scope, and headline counts (unassigned+mapped, would-get-assignee, still-unassigned, unmapped-skipped).
- **Unassigned Issues** — one row per currently-unassigned mapped issue: `jira_key`, `issue_type`, `summary`, `asana_kind`/`asana_gid`/`asana_name`, `would_get_assignee`, and the revised `revised_owner_source`/`name`/`email`/`atlassian_id`/`atlassian_status`. Recovered rows (would get an assignee) sort first.
- **Summary** — counts by `issue_type` × `revised_owner_source` (project_owner / task_creator / project_member / none), making the `project_member` recovery bucket explicit.

Optional flags:
- `--prod` to target production Jira (`JIRA_BASE_URL`); omit for sandbox (`JIRA_SANDBOX_BASE_URL`).
- `--database <path>` (defaults to the active profile's DB), `--output-dir <dir>` (default `exports/`), `--filename <name>`, `--log-level <LEVEL>`.

Requires `ATLASSIAN_USERNAME` and `ATLASSIAN_TOKEN` in the resolved credentials (same as the other Jira scripts).

### Foreign-Migration Coverage Report (.xlsx)
A **read-only** diagnostic for Asana projects that were migrated into Jira *before* our involvement, by someone else's tooling — so none of our mapping tables (`task_jira_mappings`, `team_project_jira_mappings`, `project_jira_decisions`) were populated for them. It reconciles the local Asana snapshot against the live Jira projects to quantify how much actually made it across (working hypothesis: subtasks were dropped). Writes nothing to Jira, Asana, or the database.

Because no reliable Asana reference survives on the Jira side, matching is **by name**: the normalized Asana task name is matched to the normalized Jira issue summary (`normalize_name` — trim, collapse internal whitespace, casefold), multiset-matched per name so duplicates pair by count. Two built-in groups ship as defaults — `POPS` (a flat union of 7 Asana projects) and `PROJ24` (`Project 24`) — identified by project **GID** (all-digit) so they resolve regardless of name drift; overridable via `--group`. Each identifier in a group may be a GID or a project name.

```bash
python3 -m scripts.atlassian.report_migration_coverage --prod
```

For each group it resolves the source Asana projects (a GID is looked up directly; a name is matched trimmed; an identifier matching 0 or, for a name, >1 projects is logged as a warning), pulls their tasks via `task_projects` (union deduped by task GID, `hierarchy_depth` → top-level vs subtask, plus `completed`), then pages every issue in the Jira project (`project = "<KEY>" ORDER BY key ASC`, fields `issuetype`/`summary`/`status`/`parent`, via `nextPageToken`). Reconciliation yields matched / missing-in-Jira / extra-in-Jira sets.

The workbook (`exports/migration_coverage_<timestamp>.xlsx`) has seven tabs:
- **Overview** — generation time, source DB, Jira base URL, match strategy, and per-group headline counts (Asana tasks, Jira issues, matched, missing, extra, match rate %).
- **Asana Baseline** — one row per source project (total / top-level / subtask / completed / open) plus a per-group deduped-union subtotal.
- **Jira Inventory** — per group, live issue counts by issue type × subtask flag, with a group total.
- **Reconciliation Summary** — per group: Asana total, Jira total, matched, missing (split top-level/subtask and completed/open), extra, match rate %.
- **Per-Project Coverage** — one row per source project scored *independently* against the whole group's Jira pool (tasks / top-level / subtask / completed / open / matched / missing / rate). This is the trustworthy per-project view: the group-level `matched` on the other tabs is deflated by flat-pool multiset collisions (generic `Action:`/`Milestone:` names competing for the same issue), whereas here each project is matched on its own. Because the pool is shared, a Jira issue can satisfy a name in more than one project, so these are per-project estimates and do not sum to the group total.
- **Missing in Jira** — one row per unmatched Asana task (group, source project, name, top-level/subtask, completed, `created_at`, `modified_at`, parent task [resolved parent name, else parent gid], assignee email, creator email, gid) — the actionable coverage gap. Assignee/creator emails are resolved from the `users` table (blank when unassigned or the user isn't in the local snapshot).
- **Extra in Jira** — one row per unmatched Jira issue (group, key, summary, issue type, subtask flag, status).

Optional flags:
- `--prod` to target production Jira (`JIRA_BASE_URL`); omit for sandbox (`JIRA_SANDBOX_BASE_URL`).
- `--group "KEY=Name A||Name B"` (repeatable) to override the built-in groups for a future wave.
- `--database <path>` (defaults to the active profile's DB), `--output-dir <dir>` (default `exports/`), `--filename <name>`, `--log-level <LEVEL>`.

Requires `ATLASSIAN_USERNAME` and `ATLASSIAN_TOKEN` in the resolved credentials (same as the other Jira scripts). Since matching is name-based, spot-check a few Missing-in-Jira rows directly in Jira to rule out false negatives from renamed tasks.
### Create Standard Jira Import Fields

`python3 -m scripts.atlassian.create_jira_import_fields`

Creates one explicitly selected field set; the two sets are never created together.

- `--field-set import` creates the task-import mapping fields: **Asana Section** (short text), **Asana Project Link** (URL), and **Asana Task Link** (URL).
- `--field-set tracking` creates the Team/Project tracking fields: **Asana Project Link** and **Asana Team Link** (URL), **Asana Task Count** (number), **Asana Last Working Day** (date), **Asana Current Project Status** (single select: Active, Archived), and **Asana Migration Decision** (single select: Migrate - Active, Migrate - Production Read Only, Migrate - Archive, Migrate - Abandon).

The command is safe to re-run: it checks the complete paginated Jira custom-field administration catalog, retains every exact-name field with the expected Jira type, and creates only missing fields/options. Existing same-name duplicates are reported and skipped without creating another field; the command stops when a name exists only with the wrong type. The Prepare Jira interface exposes the two sets as separate actions. Jira administrator permission is required.

Flags:

- `--dry-run` lists existing and missing fields without creating anything.
- `--field-set import|tracking` selects exactly one field set (default: `import`).
- `--jira-site <key>` targets a named Jira site from the active profile; `--prod` remains the legacy production selector.
- `--yes` skips the standalone confirmation prompt.
- `--log-level <level>` controls logging verbosity.
