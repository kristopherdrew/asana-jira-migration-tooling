# Repository Guidelines

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
- Each script should expose a `--workers` override and respect the resolved `skip_ssl_verification` setting from the shared credential loader.
- Document every new CLI script (purpose, inputs, outputs) in the README and below.
- Rebuild `tasks.hierarchy_depth` with `python3 -m scripts.backfill_task_hierarchy_depth` after schema changes; top-level tasks sit at depth 0 and each subtask increments by 1.

## CLI Script Catalog
- `scripts/export_users.py` — exports all users and their workspace memberships from SQLite into a CSV (`exports/users_<timestamp>.csv` by default). Fields: user gid, name, email, resource type, active flag, Atlassian account id, Atlassian status, task activity counts (`tasks_created`, `tasks_completed`, `tasks_commented`), `last_active_date`, user URL, last sync timestamp, semicolon-separated workspace labels. Atlassian id/status are resolved from the `user_atlassian_accounts` table keyed by Asana user GID (blank when unmapped). Activity counts derive from synced task data: `tasks_created`/`tasks_completed` count tasks where the user is `created_by`/`completed_by`, and `tasks_commented` counts the distinct tasks with at least one non-deleted comment authored by the user (distinct tasks, not comment volume). `last_active_date` (`YYYY-MM-DD`) is the most recent of the user's last created task, last completed task, or last non-deleted comment. Counts default to `0` and `last_active_date` is blank until `tasks`/`task_comments` are populated.
- `scripts/update_atlassian_user_map.py` — ingests a CSV (`gid`, `atlassianAccountId`, optional `atlassianEmail`, `atlassianStatus`) and upserts entries into `user_atlassian_accounts` so Jira exports can resolve comment authors. The checked-in template lives at `Sample Data/atlassian_user_map_template.csv`. Alternatively, pass `--atlassian-export` with a raw Atlassian user export CSV (columns `User id`, `email`, `User status`; other columns ignored) to skip manual mapping — rows are matched to Asana users by email (case-insensitive) and the account id/status are stored per match. Pass `--clear` to delete all existing mappings first (use alone to wipe the table and start from scratch, or with an input file to fully replace the mappings); supply at least one of `--csv`, `--atlassian-export`, or `--clear`.
- `scripts/search_jira_external_ids.py` — calls Jira Cloud’s modern `POST /rest/api/3/search/jql` endpoint with a supplied JQL filter (order forced to `created` ASC), paginates via `nextPageToken`, harvests an external ID field (e.g., Asana task GID), and upserts Jira→Asana mappings into SQLite. Caps `maxResults` to the API limit per request.
- `scripts/export_project_to_jira_csv.py` — exports one row per task for a project (or prints stats with `--stats-only`), adding repeated `Comment` columns populated via the `user_atlassian_accounts` table keyed by Asana user GID (`dd/MMM/yy h:mm AM/PM;<account_id>; <text>` format), pairing user columns with their Atlassian IDs and emails (e.g., `assignee`, `assignee_email`, `assignee_atlassian_id`, `assignee_atlassian_email`), surfacing project scope/schedule fields (including task `created_at`), prompting interactively to keep all custom fields or enter a comma-separated list (override with `--custom-fields` or `--no-custom-field-prompt`) and printing a ready-to-copy comma-separated line of field names, defaulting CSV filenames to `<ProjectName>-<YYYYMMDD-HHMM>.csv`, emitting a companion `<filename>_field_counts.csv` summary that lists each custom field, its value count, and whether it was included in the export, and requiring a Jira base URL either via `--jira-base-url` or an interactive prompt.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation and descriptive snake_case module/function names.
- Prefer dataclasses and type hints (PEP 484 / `typing`) to keep data structures explicit.
- Keep each module focused on a single responsibility; expose a single public entrypoint per file and treat helpers as internal.
- Configuration defaults belong in `config/` and mirrored entries must be documented in the README and env-file setup guidance.

## Testing Guidelines
- Write unit tests under `tests/unit` and integration specs under `tests/integration`, naming files `<subject>.test.py`.
- Keep custom fixtures and mocks alongside tests (e.g., `tests/unit/__mocks__/`).
- Target ≥90% statement coverage; include asynchronous and pagination edge cases in every suite.
- Use `python3 -m unittest --failfast` during development for faster feedback and add regression tests for every bug fix.

## Commit & Pull Request Guidelines
- Follow Conventional Commits (e.g., `feat(tasks): add recurring scheduling`). Scope names should align with the `src/` folder touched.
- Rebase onto `master` before opening a PR and link the relevant Asana task.
- Describe changes plainly, enumerate tests executed (`python3 -m unittest ...`), and attach logs or exports when relevant.
- Wait for CI checks to pass and secure at least one reviewer approval before merging.
- When a prompt-driven change is completed, ask whether the user wants the changes committed.
- If you ask, include a suggested Conventional Commit message.
- Do not create a commit unless the user explicitly approves it.
- Keep new script shebangs aligned with the interpreter standard (`#!/usr/bin/env python3`).
