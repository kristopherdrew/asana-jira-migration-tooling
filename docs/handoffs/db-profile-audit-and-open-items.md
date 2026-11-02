# Handoff: DB / profile / Jira-site audit and open items

Written 2026-11-02. Everything below was done or discovered in one session. The shell tool was unavailable for most of it; once it recovered, `python3 -m unittest tests.test_suite` passed (952 tests), including a run with `ASANA_JIRA_DB_PATH` set to a probe path that was never created. The one failure found (a `get_tasks_cli` fixture using the fake GID `proj_1`) was fixed. The changes were then committed locally (not pushed). Sections 3 and 4 below are partly historical: the suite verification in section 4 is done, and the "first actions" check on unverified audit claims remains open.

## 1. Why this work happened

Scripts, Interactive CLI entries and tests repeatedly failed to use the active profile's database or the selected Jira site. Two incidents prompted a full audit:

- **Test fixture rows leaked into `data/asana_client_roblox.sqlite`**: `proj_active`, `proj_archived`, `proj_1` (written 2026-06-27 15:14). Cause: tests patch a script's `DEFAULT_DB_PATH`, but `resolve_default_db_path()` (`src/lib/database.py:29`) gives `ASANA_JIRA_DB_PATH` priority, and the interactive CLI exports that variable for the active profile. A task sync from `inputs/No Tasks.csv` then crashed with Asana 400 on `proj_active`.
- **Menu [14] (project import status) mixed sites.** Cause: `run_project_import_status_report` called `append_jira_env_flag` (adds the session's active site) before `_append_jira_site_flags` (the user's pick). "all" produced only the active site; a specific pick also included the active site.

## 2. Changes made (uncommitted, unrun)

| Area | File | Change |
|---|---|---|
| Test guard | `tests/__init__.py` | New `scrub_profile_env()` removes `ASANA_JIRA_DB_PATH`, `ASANA_JIRA_ENV_FILE`, `ASANA_JIRA_SITE_KEY` at import; `PROFILE_ENV_VARS` constant. |
| Test guard | `tests/test_suite.py` | `load_tests` calls `scrub_profile_env()`. |
| Test guard | `tests/unit/lib/test_env_guard.test.py` (new) | Tests the scrub function. |
| Test pins | `tests/integration/scripts/get_projects_cli.test.py`, `get_tasks_cli.test.py`, `get_project_status_updates_cli.test.py`, `tests/unit/scripts/get_portfolios.test.py` | `ASANA_JIRA_DB_PATH` pinned to the temp DB inside the patch block. |
| Sync safety | `scripts/get_tasks.py` | `is_syncable_project_gid()`; the loop skips non-numeric GIDs (including `personal_*`) with a warning instead of calling the API. |
| Sync safety | `tests/unit/scripts/get_tasks.test.py` | `SyncableProjectGidTest`. |
| CLI site bug | `scripts/interactive_cli.py` | Removed the `append_jira_env_flag(args)` call before `_append_jira_site_flags(args)` in the import status and ATJM coverage handlers. Both scripts treat no `--jira-site` as all sites. |
| CLI database | `scripts/interactive_cli.py` | `script_accepts_database_flag()` + `with_active_database()`; `run_script` now appends `--database <active_db_path()>` when the target module defines `"--database"` and argv lacks it. Added `import inspect`. |
| Clear map | `scripts/interactive_cli.py`, `src/lib/database.py`, `scripts/atlassian/export_project_to_jira_csv.py` | New `args_clear_jira_project_map`; `clear_jira_project_mappings(connection, jira_site_key=None)` honours the explicit site (falls back to `ASANA_JIRA_SITE_KEY`); script passes `args.jira_site`. |
| Deprecated scripts | `scripts/deprecated/get_personal_tasks_delta.py`, `backfill_task_hierarchy_depth.py` | Default DB is `resolve_default_db_path(DEFAULT_DB_PATH)`; the delta script gained `--database`. |

Data fixes already applied to the Roblox DB and inputs: the three fixture rows deleted from `projects` (backup: `/tmp/roblox_backup_20261102102955.sqlite`, may be gone if `/tmp` was cleared), and those three lines removed from `inputs/No Tasks.csv` (289 lines remain).

## 3. Remaining work on this audit

**Status update (later in the same session; still unrun):** items 1, 2 and 4 below were written afterwards.
- Item 1 done: all ten `get_*` scripts now have `--database` (default `None`, then `args.database or resolve_default_db_path(DEFAULT_DB_PATH)`); `get_custom_fields`, `get_projects`, `get_teams`, `get_users` and `get_portfolios` now log the resolved path.
- Item 2 done: `tests/unit/scripts/interactive_cli_profile_flags.test.py` (menu `j`/`k` site selection with session site = archive; `with_active_database` behaviour) and `tests/unit/scripts/script_profile_conventions.test.py` (static scan of `scripts/**/*.py`: no hardcoded DB path, `--database` default not a literal, DB-opening scripts expose `--database`, `site_key=` passed by keyword). These regexes were written without being run, so expect to adjust them (for example a script that legitimately opens a DB without `--database`, or a false positive on the positional-site pattern).
- Item 4 partly done: the AGENTS.md CLI note is updated; `docs/sync-commands.md` has not been checked for flag lists.
- Items 3 and 5 are still open (and nothing is committed).

1. Add `--database` to the ten `get_*` sync scripts (`get_custom_fields`, `get_projects`, `get_teams`, `get_users`, `get_portfolios`, `get_tasks`, `get_task_extras`, `get_incremental_updates`, `get_project_status_updates`, `get_personal_tasks`). Plan: default `None`, then `args.database or resolve_default_db_path(DEFAULT_DB_PATH)` inside `main` (keeps tests that patch `DEFAULT_DB_PATH` working). Log the resolved path instead of the literal `DEFAULT_DB_PATH` (`get_custom_fields.py:116`, `get_projects.py:130`, `get_teams.py:199`, `get_users.py:124`, `get_portfolios.py:237`).
2. Tests: (a) menu [14] and ATJM handlers with session site = archive and choice = production produce only `--jira-site production_jira`, and choice = all produces no `--jira-site`; (b) `with_active_database` injects `--database` for a script that defines it, skips scripts that do not, and does not duplicate an existing flag; (c) static drift test scanning `scripts/**/*.py` for literal `data/asana*.sqlite`, scripts that open a DB without `--database`, and positional `resolve_jira_base_url(creds, x)` calls.
3. Minor script items: `get_jira_migration_decisions.py:166` bare `Database()`; `capture_project_epic_jira_keys.py` has `--jira-site` but no `--prod`; `export_all_reports_xlsx.py` does not forward `--jira-site`; stale "default: data/asana.sqlite" help text (e.g. `export_team_report.py:35`, `clear_jira_task_keys.py:28`); unused imports in `backfill_project_mappings_from_tasks.py`.
4. Docs: update the AGENTS.md CLI note (it says builders add `--database` explicitly; it is now injected centrally); note new flags in `docs/sync-commands.md`.
5. Commit in small Conventional Commits, propose messages first, do not push.

## 4. First actions in the new session

1. `python3 -m unittest tests.test_suite` and fix failures. Likely trouble spots: the `inspect` import and `run_script` change in `interactive_cli.py`, and CLI tests asserting exact argv lists.
2. `ASANA_JIRA_DB_PATH=/tmp/copy.sqlite python3 -m unittest tests.test_suite` against a copy of `data/asana_client_roblox.sqlite`; confirm its table row counts are unchanged afterwards.
3. Confirm the audit's unverified claims: whether `export_recurring_tasks_report` and `export_atjm_coverage_report` read the DB at all; whether `capture_project_epic_jira_keys` and `export_team_project_epic_jira_csv` accept `--jira-site`; which `interactive_cli.test.py` classes reach the macOS Keychain.

## 5. Other open items (not part of the audit)

- **Unpushed commits:** `f0d65d9`, `a4a1235`, `2301fd5` (project import status rebuild). Push to GitHub only (`github` remote); Bitbucket is paused until the licensing issue is resolved. The user has said to push only when asked.
- **Confirm the menu [14] fix:** restart `interactive_cli`, run [14] with `production_jira` only, and check no archive columns or tabs appear. Earlier mixed output may have come from a stale process, but the double-site-flag bug above is a real cause.
- **Unsynced project GIDs:** 14 task-referenced project GIDs with no `projects` row; see `docs/handoffs/project-import-status-unsynced-projects.md`. Next step there is a read-only `GET /projects/{gid}` check per GID, then a targeted fetch or membership fix, sync-time detection, and a decision on "(not synced)" report rows.
- **Re-run the sync** from the CLI with `inputs/No Tasks.csv` once the guard and GID skip are verified. Personal projects (`personal_*`) are skipped by `get_tasks` and need `get_personal_tasks`.
- **Possible cleanup:** dead CLI code (`run_merge_project_exports`, `args_clear_export_ids`, the `get_personal_tasks_delta` import) and the `--prod` fallback in `append_jira_env_flag` for reports that lack `--prod`.

## 6. Reference

- Approved plan: `/Users/kperez/.claude/plans/structured-gathering-wozniak.md`.
- Audit results (scripts / CLI / tests) are summarized in this session's transcript; the key conclusions are encoded in sections 1-3 above. Every script already passes `site_key=` by name and respects `skip_ssl_verification`.
- Suite command: `python3 -m unittest tests.test_suite` (`.test.py` files are not found by plain `discover`). Single file: importlib load with `PYTHONPATH=$PWD`.
