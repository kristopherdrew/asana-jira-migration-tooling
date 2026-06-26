# Repository Guidelines

> **Editing note:** `CLAUDE.md` is a symlink to this file (`AGENTS.md`). Edit `AGENTS.md` — writes to `CLAUDE.md` are refused as symlink writes.

## Project Structure & Module Organization
- Runtime Python packages live under `src/`, grouped by feature (`src/tasks`, `src/projects`, etc.).
- Shared utilities (config loader, HTTP client, concurrency helpers) live in `src/lib/` and are imported by feature modules.
- CLI entrypoints and orchestration scripts sit in `scripts/`, keeping business logic inside `src/`.
- Mirror the `src/` layout inside `tests/` two levels deep so every module has a neighboring test.
- Ignore build artifacts (`__pycache__/`, `*.pyc`, local SQLite files) and local environment files via `.gitignore`.

## Build, Test, and Development Commands
- Standardize on CPython 3.11+ using a project-local virtual environment (`python3 -m venv .venv`).
- Use `python3` consistently in commands, scripts, and README examples unless you are referring to a language name or a literal code snippet.
- On macOS, activate the virtual environment with `source .venv/bin/activate`.
- Do not assume `python` exists or maps to the intended interpreter.
- Only the Python standard library is allowed; document any exception in advance.
- Run `python3 -m unittest discover tests` for the full test suite; use `python3 -m trace --count --file trace.out -m unittest discover tests` to check coverage when needed.
- Run the full test suite before committing non-trivial changes.
- Scripts that perform concurrent API fetches should expose a `--workers` override (see `get_tasks.py`, `get_task_extras.py`, `get_project_status_updates.py`); single-request and export/diagnostic scripts (e.g. `get_projects.py`, `export_users.py`, `inspect_project_status_data.py`) do not need one. Every script must respect the resolved `skip_ssl_verification` setting from the shared credential loader.
- Document every new CLI script (purpose, inputs, outputs) in the matching `docs/` reference file (`docs/sync-commands.md`, `docs/reports.md`, or `docs/jira-integration.md`) and add a one-line index entry below.
- Rebuild `tasks.hierarchy_depth` with `python3 -m scripts.backfill_task_hierarchy_depth` after schema changes; top-level tasks sit at depth 0 and each subtask increments by 1.

## CLI Script Catalog
This is a quick index; full purpose/inputs/outputs/flags for every script live in the `docs/` references.
- **Core Asana sync** scripts (`get_teams`, `get_users`, `get_projects`, `get_portfolios`, `get_custom_fields`, `get_tasks`, `get_task_extras`, `get_personal_tasks`, `get_personal_tasks_delta`, `get_incremental_updates`, `get_project_status_updates`) → [docs/sync-commands.md](docs/sync-commands.md).
- **CSV reports** → [docs/reports.md](docs/reports.md).
- **Jira / Atlassian** scripts → [docs/jira-integration.md](docs/jira-integration.md).

Index of the export / Atlassian / diagnostic scripts (see the linked doc for full detail):
- `scripts/export_users.py` — exports the `users` table + workspace memberships to CSV, with per-user task activity counts, Atlassian id/status, and `last_active_date`. → [reports](docs/reports.md)
- `scripts/export_custom_field_usage_report.py` — one row per (project, custom field) actually populated on a task, to scope which fields to migrate. → [reports](docs/reports.md)
- `scripts/update_atlassian_user_map.py` — populates `user_atlassian_accounts` (Asana GID → Atlassian id/status) from a mapping CSV or a raw Atlassian user export (matched by email). → [jira-integration](docs/jira-integration.md)
- `scripts/search_jira_external_ids.py` — harvests an external-ID field from Jira (`POST /rest/api/3/search/jql`) and upserts Jira→Asana mappings into SQLite. → [jira-integration](docs/jira-integration.md)
- `scripts/export_project_to_jira_csv.py` — per-task Jira import CSV for a project (comments, custom fields, paired user/Atlassian columns) plus a `_field_counts.csv` companion; `--stats-only` prints stats. → [jira-integration](docs/jira-integration.md)
- `scripts/export_team_project_jira_csv.py` — single Jira import CSV for the Team→Epic / Project→Task hierarchy (effective-owner resolution, rich descriptions, `No Team` epic). → [jira-integration](docs/jira-integration.md)
- `scripts/create_team_project_jira_issues.py` — direct-API alternative to the above CSV: creates Epics/Tasks via the Jira REST API (createmeta field resolution, persisted mappings, always-on assignee fallback + 429 requeue). → [jira-integration](docs/jira-integration.md)
- `scripts/atlassian/update_personal_project_jira_urls.py` — one-time in-place repair of the `Project (Asana)` field on personal-project Jira issues so dead synthetic URLs become working My Tasks links. → [jira-integration](docs/jira-integration.md)
- `scripts/get_project_status_updates.py` — captures project-level status updates + comments into the unified `project_updates` table (kind-tagged, mirror dedup); also in the Interactive CLI Tasks menu. → [sync-commands](docs/sync-commands.md)
- `scripts/get_portfolios.py` — iterates every user as a portfolio owner (`GET /portfolios` needs an owner), capturing each portfolio plus its items/memberships/custom-field-settings/status-updates and fully recursing into nested portfolios (dedup + cycle guard); `--workers`, `--owner-gid`. Also in the Interactive CLI Tasks menu. → [sync-commands](docs/sync-commands.md)
- `scripts/inspect_project_status_data.py` — read-only diagnostic probe of a project's status-update / story endpoints; prints a summary and dumps raw JSON to `exports/`. → [sync-commands](docs/sync-commands.md)
- `scripts/describe_db.py` — read-only DB introspection: per-table context/provenance (from `src/lib/table_metadata.py`) + live structure (columns, FKs, indexes, row counts, latest `synced_at`); `--table <name>` adds sample rows; `--markdown` regenerates the data dictionary. → [data-model](docs/data-model.md), [db-queries](docs/db-queries.md)

The SQLite schema is documented in [docs/data-model.md](docs/data-model.md) (per-table purpose, source API/CSV, writer method, write semantics, columns) with investigative SQL in [docs/db-queries.md](docs/db-queries.md). The data dictionary is generated — `src/lib/table_metadata.py` is the single source of truth for table context, and a drift-guard test asserts every created table/view has an entry. Regenerate the doc after schema changes: `python3 -m scripts.describe_db --markdown > docs/data-model.md`.

## Interactive CLI
- `scripts/interactive_cli.py` is a thin orchestrator over the standalone script `main()`s — it never reimplements business logic, it builds an `argv` list and calls the script's `main(argv)` via `run_script`. Every script stays independently runnable; the CLI is just a guided front end.
- Structure: `main()` shows a top menu of 5 categories, each backed by a `build_<category>_menu(log_level)` that returns `Dict[str, MenuItem]` (`MenuItem(key, label, handler)`). The categories are: **Initialization** (metadata-only fixed pipeline: teams, users, service-user team access, projects, custom fields), **Tasks** (`build_tasks_menu` — sync tasks, task extras, project status updates, personal tasks, incremental), **Atlassian** (`build_atlassian_menu` — Jira exports, key/map import-clear, attachments, direct-API team/project issue creation), **Reports** (`build_reports_menu`), and **Credentials**.
- Per-item pattern: a handler does `run_script(<module>.main, args_<thing>(log_level))`. The `args_*` builders assemble the `argv` list and own all interactive prompts; `build_common_args` seeds `--log-level`.
- Scope selection reuses fuzzy SQLite lookups over `data/asana.sqlite`: `_prompt_project_gid_from_lookup`, `_prompt_team_gid_from_lookup`, `_prompt_user_gid_from_lookup` (built on `_fuzzy_projects`/`_fuzzy_teams`/`_fuzzy_users`). Prefer these for any new project/team/user picker instead of writing a new prompt.
- To add a script to the CLI: (1) import its `main` in the `from scripts import (...)` block, (2) write an `args_<thing>(log_level)` builder (reuse the lookup helpers + `prompt`/`confirm`), (3) add a `MenuItem` to the relevant `build_*_menu`. No need to re-investigate the file each time — follow this pattern.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation and descriptive snake_case module/function names.
- Prefer dataclasses and type hints (PEP 484 / `typing`) to keep data structures explicit.
- Keep each module focused on a single responsibility; expose a single public entrypoint per file and treat helpers as internal.
- Configuration and credentials are loaded from the resolved env file via `src/lib/config.py` (`load_credentials`), not a `config/` directory; document any new setting in the README and env-file setup guidance.

## Testing Guidelines
- Write unit tests under `tests/unit` and integration specs under `tests/integration`, naming files `<subject>.test.py`.
- Keep custom fixtures and mocks alongside tests (e.g., `tests/unit/__mocks__/`).
- Target ≥90% statement coverage; include asynchronous and pagination edge cases in every suite.
- Use `python3 -m unittest --failfast` during development for faster feedback and add regression tests for every bug fix.

## Commit & Pull Request Guidelines
- Follow Conventional Commits (e.g., `feat(tasks): add recurring scheduling`). Scope names should align with the `src/` folder touched.
- Rebase onto `main` before opening a PR and link the relevant Asana task.
- Describe changes plainly, enumerate tests executed (`python3 -m unittest ...`), and attach logs or exports when relevant.
- Wait for CI checks to pass and secure at least one reviewer approval before merging.
- When a prompt-driven change is completed, ask whether the user wants the changes committed.
- If you ask, include a suggested Conventional Commit message.
- Do not create a commit unless the user explicitly approves it.
- When the user requests or approves a commit, also push it to the remote in the same step (commit **and** push together) unless they say otherwise.
- Keep new script shebangs aligned with the interpreter standard (`#!/usr/bin/env python3`).
