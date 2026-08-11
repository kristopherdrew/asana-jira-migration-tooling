---
name: add-cli-script
description: Use when adding a new CLI script to this Asana→Jira migration repo (a new get_/export_/sync/diagnostic command, or wiring an existing script into the interactive CLI). Walks the full repo convention so nothing is missed — business logic in src/, entrypoint in scripts/, docs reference + index entry, mirrored test, interactive_cli registration, table_metadata entry, and the standard flags.
---

# Add a new CLI script

Follow this end-to-end checklist whenever a new command is added. The goal is that a
new script lands fully integrated — runnable standalone, registered in the interactive
CLI, documented, tested, and (if it touches the schema) reflected in the data dictionary.

## 1. Place the code
- **Business logic** goes in a feature module under `src/` (e.g. `src/tasks/`, or a new
  `src/<feature>/`). Shared helpers belong in `src/lib/` — reuse the existing ones:
  `config.load_credentials`, `database.Database`, `http_client`, `jira_client`,
  `logging_utils`, `project_scope`, `table_metadata`. Do **not** reimplement these.
- **The entrypoint** goes in `scripts/<name>.py` with shebang `#!/usr/bin/env python3` and a
  single public `main(argv)` entrypoint. The script parses argv and calls into `src/`; it
  must not hold business logic.

## 2. Standard flags & settings (non-negotiable)
- Seed `--log-level` like the sibling scripts.
- Always resolve and honor `skip_ssl_verification` from the shared credential loader
  (`src/lib/config.py`). Every script must respect it.
- If the script performs **concurrent API fetches**, expose a `--workers` override
  (pattern: `get_tasks.py`, `get_task_extras.py`, `get_project_status_updates.py`).
  Single-request / export / diagnostic scripts do **not** add `--workers`.

## 3. Register in the interactive CLI (`scripts/interactive_cli.py`)
The CLI is a thin orchestrator — never reimplement logic there. Steps:
1. Import the script's `main` in the `from scripts import (...)` block.
2. Write an `args_<thing>(log_level)` builder that assembles the `argv` list and owns the
   interactive prompts (reuse `prompt`/`confirm` and `build_common_args` for `--log-level`).
3. For project/team/user pickers, reuse the fuzzy lookups —
   `_prompt_project_gid_from_lookup`, `_prompt_team_gid_from_lookup`,
   `_prompt_user_gid_from_lookup` — never write a new picker.
4. Add a `MenuItem(key, label, handler)` to the right `build_*_menu` (Initialization,
   Tasks, Atlassian, Reports, or Credentials). The handler does
   `run_script(<module>.main, args_<thing>(log_level))`.

## 4. Schema changes → keep the data dictionary in lockstep
If the script creates or alters a table/view:
- Add/update its `TableMeta` entry in `src/lib/table_metadata.py` (the single source of
  truth — a drift-guard test fails otherwise).
- Regenerate the doc: `python3 -m scripts.describe_db --markdown > docs/data-model.md`.
- If hierarchy depth is affected, rebuild it:
  `python3 -m scripts.backfill_task_hierarchy_depth`.
Consider delegating this whole step to the **schema-keeper** subagent.

## 5. Tests (mirror the layout)
- Add a test mirroring `src/`/`scripts/` two levels deep under `tests/unit/...` (and an
  integration spec under `tests/integration/...` when there's external I/O to exercise).
- Name the file `<subject>.test.py` — these are loaded by `tests/test_suite.py`'s custom
  loader, not by the default unittest pattern.
- Cover pagination and async/concurrency edge cases. Target ≥90% statement coverage.
- Consider delegating to the **test-author** subagent.

## 6. Document
- Add full purpose/inputs/outputs/flags to the matching reference:
  `docs/sync-commands.md`, `docs/reports.md`, or `docs/jira-integration.md`.
- Add the one-line index entry to the **CLI Script Catalog** in `AGENTS.md`
  (remember: edit `AGENTS.md`, not the `AGENTS.md` symlink).

## 7. Verify
- Run the full suite: `python3 -m unittest discover tests`.
- Sanity-check the entrypoint, e.g. `python3 -m scripts.<name> --help`.

## Done criteria
Standalone-runnable • registered in the interactive CLI • flags correct • schema doc in
sync (if applicable) • tested ≥90% • documented in `docs/` + `AGENTS.md` index • suite green.
