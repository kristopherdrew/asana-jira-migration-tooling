# Asana Project Operations Guide

Practical, end-to-end steps for running this repository as a project workflow: set up prerequisites, ingest Asana metadata via the interactive CLI, keep tasks fresh (including personal work), and export Jira-ready artifacts with attachment support. The interactive CLI covers nearly every flow; call individual scripts only for edge cases or when you need a repeatable, parameterized run that would be cumbersome to click through each time.

## Requirements
- Python 3.11+ with a project-local virtual environment (`python3 -m venv .venv && source .venv/bin/activate`).
- Standard library only; no third-party installs expected.
- Store credentials in a home-directory env file using `ASANA_TOKEN`, `ASANA_WORKSPACE_GID`, `SKIP_SSL_VERIFICATION`, `ATLASSIAN_USERNAME`, and `ATLASSIAN_TOKEN`.
- If you keep separate client credentials under your home directory, add a git-ignored `.asana_jira_env_path` file in the project root containing the desired env file path for that copy of the project. The scripts will follow that pointer automatically. Explicit process environment variables still take precedence.
- Writable SQLite snapshot at `data/asana.sqlite` (created automatically by the sync scripts).

### Recommended Credential Setup
```bash
cat > ~/.asana_jira_env_client_a <<'EOF'
export ASANA_TOKEN="your_asana_token"
export ASANA_WORKSPACE_GID="your_workspace_gid"
export SKIP_SSL_VERIFICATION="false"
export ATLASSIAN_USERNAME="you@example.com"
export ATLASSIAN_TOKEN="your_jira_api_token"
EOF
chmod 600 ~/.asana_jira_env_client_a
echo "~/.asana_jira_env_client_a" > .asana_jira_env_path
```

## Initiate Workspace Data (Interactive CLI)
Use the menu-driven orchestrator to pull the base entities your runs depend on—teams, projects, and custom fields—without losing access to the individual scripts. Prefer this path; drop to direct scripts only for special handling or tightly scripted automation with fixed arguments.
```bash
python3 -m scripts.interactive_cli --log-level INFO
```
- Choose **Initialization** to run, in order: `get_teams`, `get_projects`, `get_custom_fields`, and `get_users`.
- Data lands in `data/asana.sqlite`; reruns are idempotent and safe after config changes.
- If local certificate validation is broken, you can temporarily set `SKIP_SSL_VERIFICATION="true"` in the selected env file as a local troubleshooting workaround, then revert it to `false` once the environment is fixed.

## Sync Tasks
- **Projects:** pull full task hierarchies (plus backfilled users/fields) for selected scope.
  ```bash
  python3 -m scripts.get_tasks --team     # interactive team picker
  python3 -m scripts.get_tasks --project-gid <gid>
  python3 -m scripts.get_tasks --all-teams [--exclude-team-gids <gid1,gid2>]
  ```
- **Personal projects:** capture “My Tasks” items that are not in any project; stored under synthetic `personal_<user_gid>` projects.
  ```bash
  python3 -m scripts.get_personal_tasks --assignee me
  python3 -m scripts.get_personal_tasks_delta --assignee me   # incremental cursor-based refresh
  ```
- **Task extras (comments & attachments metadata):**
  ```bash
  python3 -m scripts.get_task_extras --team-gids <gid1,gid2>
  # or pair with personal runs
  python3 -m scripts.get_task_extras --personal --personal-assignees <gid1,gid2>
  ```
- **Incremental updates:** replay recent changes (tasks + extras) by modified timestamp.
  ```bash
  python3 -m scripts.get_incremental_updates --days-back 3 --all-teams
  ```

## Pre-Export Mappings
- User ↔ Atlassian IDs: `python3 -m scripts.update_atlassian_user_map --csv <map.csv>` (needed for assignee/creator/comment author resolution).
- Jira project name/key map: `python3 -m scripts.atlassian.import_jira_project_map --csv <map.csv>` (adds Jira columns to exports).

## Export to Jira
Keep SQLite current (run project/task syncs first) before exporting.
```bash
python3 -m scripts.export_project_to_jira_csv --project-gid <gid>
# team scope example:
python3 -m scripts.export_project_to_jira_csv --team-gid <team_gid> --archived-scope active
```
- Prompts for Jira base URL unless provided (`--jira-base-url` or `--no-jira-url-prompt`).
- Prompts for custom fields unless skipped (`--custom-fields ...` or `--no-custom-field-prompt`).
- Artifacts (under `exports/` or a scoped `export-XX-<export-id>/` folder):
  - `<ProjectName>-<timestamp>.csv` — Jira-importable tasks/subtasks.
  - `<filename>_field_counts.csv` — field usage counts + inclusion flags.
  - `<filename>_link_modifications.csv` — Asana URLs rewritten for Jira context.
- Sync dependencies:
  - `user_atlassian_accounts` populated so assignee/creator/comment authors resolve to Atlassian IDs.
  - `jira_project_map` entries loaded so exports emit Jira project name/key columns.
  - Jira task keys back into SQLite (post-import) with `python3 -m scripts.import_jira_task_keys --csv <jira_export.csv>` to avoid duplicate exports and enable attachment uploads.

## Post-Export Activities
- Persist Jira issue keys produced by the import: `python3 -m scripts.import_jira_task_keys --csv <jira_export.csv>` (required before attachments sync).
- Optional verification rerun: `python3 -m scripts.export_project_to_jira_csv --stats-only --project-gid <gid>` to confirm mappings and field coverage.
- Sync attachments into Jira (uses the stored task→issue mappings):
  ```bash
  python3 -m scripts.sync_jira_attachments --project-gid <gid> --jira-base-url https://your-domain.atlassian.net
  ```
- If new Asana changes occur after export, rerun incremental updates (`get_incremental_updates`) and re-export with a fresh `--export-id` or clear prior mappings via `--clear-export-id`.

## Attachments to Jira
Upload stored Asana attachments to their mapped Jira issues once task keys exist.
```bash
python3 -m scripts.sync_jira_attachments --project-gid <gid> --jira-base-url https://your-domain.atlassian.net
```
- Generates per-project CSV audit reports under `exports/attachment-export-XX/`.
- Respects existing Jira files (skips duplicates) and caches downloads in `exports/tmp_attachments/` per run.
- Set `--dry-run` to preview operations without network calls; use `--keep-downloads` to retain temp files for debugging.

## Local Data Handling
- `data/asana.sqlite` and files under `exports/` may contain sensitive business data, user emails, comments, links, and attachment-related artifacts.
- Attachment temp folders under `exports/tmp_attachments/` are removed automatically on normal completion unless `--keep-downloads` is used.
- Interrupted or failed attachment-sync runs can leave residual temp files behind; check `exports/tmp_attachments/` during cleanup.
- After migration or review is complete, remove or archive `exports/`, `data/asana.sqlite`, and `.asana_jira_env_path` according to the client’s retention policy. Remove or rotate the selected home-directory env file if credentials are no longer needed.

## Runbook Tips
- Rerun any sync if in doubt; pipelines are idempotent and overwrite stale rows.
- Use `--workers` to tune concurrency when rate limits allow; drop to smaller values if seeing HTTP 429s.
- After schema changes, rebuild task hierarchy depths: `python3 -m scripts.backfill_task_hierarchy_depth`.
