# Asana Data Export Toolkit

Python-only automation scripts for exporting entities from the Asana API into a local SQLite database. All tooling relies on the standard library so it can run anywhere without third-party dependencies.

## Requirements
- Python 3.11 or later (use `python3` on macOS/Linux).
- Asana personal access token with read access to the target workspace.
- Credentials live in project-local, git-ignored profile files under `profiles/`. The easiest way to create one is the interactive CLI (it prompts for everything and validates the tokens). Supported keys are `CLIENT_NAME` (display name for the profile), `ASANA_TOKEN`, `ASANA_WORKSPACE_GID`, `SKIP_SSL_VERIFICATION`, `ATLASSIAN_USERNAME`, `ATLASSIAN_TOKEN`, `JIRA_BASE_URL` (production Jira site), and `JIRA_SANDBOX_BASE_URL` (sandbox Jira site). Jira scripts read the base URL from these two keys and default to the **sandbox**; pass `--prod` to target production.
- **Profiles:** each profile file is the config for one client, stored at `profiles/<slug>.env` (`profiles/default.env` for the default profile), with the human-readable `CLIENT_NAME` recorded inside it. The active profile is named by a one-line `.asana_jira_active` marker at the project root. The CLI header shows the active profile and whether the session is targeting **Sandbox** or **Production**, and the **Profile & Environment** menu lets you switch profiles or toggle the environment (the toggle drives every Jira command for the session — no per-command prompt).
- **Per-profile database:** each profile syncs into its own SQLite file — `data/asana_<slug>.sqlite` for named profiles, and `data/asana.sqlite` for the default profile — so switching clients never mixes data. The interactive CLI points every command it runs at the active profile's database via the `ASANA_JIRA_DB_PATH` environment variable; running a script standalone honors `ASANA_JIRA_DB_PATH` too (or pass `--database <path>` on the scripts that accept it). After switching profiles, re-sync if that profile's database is empty.
- **Token storage:** on macOS the two secret tokens (`ASANA_TOKEN`, `ATLASSIAN_TOKEN`) are stored in the **Keychain** (service `asana-jira-migration`, account `"<slug>:<VAR>"`, with `default` standing in for the default profile) and kept out of the plaintext profile file, which retains only the non-secret config. Read precedence is process env var → Keychain → profile file. On non-macOS systems (or without the `security` binary) the tokens transparently fall back to the profile file. The interactive **Credentials** menu offers a one-time "Migrate tokens from env file to macOS Keychain" step (also prompted automatically when it sees plaintext tokens), and lets you re-enter them at any time.
- **Resolution order** for the active config file is: `ASANA_JIRA_ENV_FILE` env override → the profile named by `.asana_jira_active` → `profiles/default.env` → the legacy `.asana_jira_env_path` pointer → `~/.asana_jira_env`. Keychain entries are namespaced by profile slug, so each client's tokens stay separate.
- **Migration from the old home-directory layout:** earlier versions kept these files in your home directory (`~/.asana_jira_env*`) selected by a `.asana_jira_env_path` pointer. On first launch the interactive CLI automatically copies any such files into `profiles/`, re-keys their Keychain entries, and leaves the originals in place (remove them once you've confirmed everything works). Standalone scripts keep reading the home files until that one-time migration runs.

### Initial Setup
The simplest path is to launch the interactive CLI and let it create your first profile:
```bash
python3 -m scripts.interactive_cli
```
It prompts for a client name, the Asana token + workspace GID, the SSL flag, and the optional Atlassian/Jira values, validates the tokens, then writes `profiles/<slug>.env` and the `.asana_jira_active` marker for you (storing the tokens in the Keychain on macOS).

To create a profile by hand instead, write the file directly (tokens included here only when you are not using the macOS Keychain):
```bash
cat > profiles/client_a.env <<'EOF'
export CLIENT_NAME="Client A"
export ASANA_TOKEN="your_asana_token"
export ASANA_WORKSPACE_GID="your_workspace_gid"
export SKIP_SSL_VERIFICATION="false"
export ATLASSIAN_USERNAME="you@example.com"
export ATLASSIAN_TOKEN="your_jira_api_token"
export JIRA_BASE_URL="https://your-prod.atlassian.net"
export JIRA_SANDBOX_BASE_URL="https://your-sandbox.atlassian.net"
EOF
chmod 600 profiles/client_a.env
echo "client_a" > .asana_jira_active
```

Leave `SKIP_SSL_VERIFICATION` set to `false` for normal use. Only set it to `true` as a temporary local troubleshooting workaround for a broken trust store or certificate validation issue, then return it to `false` once the environment is fixed. Rotate tokens immediately if they are ever committed or shared.

## Project Layout
- `scripts/` — CLI entrypoints for each export task (kept runnable on their own even when wrapped by higher-level orchestration).
- `scripts/atlassian/` — Jira/Atlassian integration scripts; thin wrappers stay in `scripts/` so existing `python3 -m scripts.<name>` calls keep working.
- `scripts/deprecated/` — Parking spot for retired scripts so history remains discoverable without cluttering the primary CLI surface.
- `src/lib/` — Shared helpers (configuration loader, HTTP client, SQLite helpers).
- `src/tasks/` — Domain-specific logic per Asana entity.
- `tests/` — Unit tests mirroring the `src/` layout.
- `scripts/interactive_cli.py` — Menu-driven wrapper that chains the existing scripts without changing their standalone behavior.

## Usage Conventions
Run every CLI through the package module path so Python keeps `src/` on `sys.path`:
```bash
python3 -m scripts.get_teams --help
```

All entrypoints accept `--log-level` (`DEBUG`, `INFO`, `WARNING`, `ERROR`). If you prefer `python3 scripts/<name>.py`, export the project root on `PYTHONPATH` first; otherwise stick with `python3 -m scripts.<name>`. Each script remains runnable independently even when wrapped by consolidated flows.

## Interactive Orchestrator
Run the menu-driven wrapper to combine the existing entrypoints without losing standalone support:
```bash
python3 -m scripts.interactive_cli --log-level INFO
```
Menu highlights:
- Category 1 (Initialization): teams + memberships → users → add the service user to every Asana team → projects → custom fields.
- Category 2 (Tasks): tasks, task extras, personal tasks (full), incremental updates.
- Category 3 (Atlassian): export/import Jira keys, clear mappings, update Atlassian user map, sync attachments.
- Category 4 (Reports): project details, team summary, users, team memberships, custom field usage.
- Category 5 (Credentials): view/update the active profile's config key-by-key, or migrate plaintext tokens into the macOS Keychain.
- Category 6 (Profile & Environment): switch the active client profile, create a new profile, or toggle the Jira target between Sandbox and Production. The active profile and environment are shown in the header above the menu, and the Sandbox/Production choice applies to every Jira command for the session.

On first launch the CLI relocates any legacy `~/.asana_jira_env*` files into `profiles/` (re-keying their Keychain entries; originals left in place). If it still cannot resolve a profile, it shows a welcome dialogue explaining what's required, asks for a **client name** (used to name the profile file `profiles/<slug>.env` and stored as `CLIENT_NAME`), then walks through the Asana token, workspace GID, SSL verification flag, optional Atlassian credentials, and the optional production/sandbox Jira base URLs before writing the profile file and `.asana_jira_active` marker for you. The **Credentials** menu also lets you view/update an existing file key-by-key (the two tokens are shown masked). Before anything is written, the entered tokens are validated live — Asana via `GET /users/me` and Atlassian via `GET /rest/api/3/myself` (against the production Jira URL when set, otherwise the sandbox). A failed check reports the error so you can correct the username/token; you can re-enter, or choose to save anyway (e.g. when offline).

When you run the Initialization flow, the projects step prompts for archived scope so you can choose active, archived, or all projects during the first data pull. The flow now adds the service user immediately after the user sync so any team-scoped metadata later in the run can see that membership. If any team update fails, the script writes `exports/service_user_team_add_failures_<timestamp>.csv` with the team GIDs and error messages.

## Documentation
Per-script command reference lives under [`docs/`](docs/), split by concern:
- [`docs/sync-commands.md`](docs/sync-commands.md) — Core sync commands that pull Asana entities (teams, projects, custom fields, users, tasks, task extras, personal tasks, incremental updates) into the local SQLite snapshot.
- [`docs/reports.md`](docs/reports.md) — CSV reports for auditing and scoping the migration (users, project details, custom field usage, team report, team memberships).
- [`docs/jira-integration.md`](docs/jira-integration.md) — Atlassian/Jira integration: user mapping, external-ID search, Jira CSV/issue creation, key import, project associations, and attachment sync.

## Sensitive Local Data
This toolkit intentionally writes migration artifacts to local disk. Treat the following as potentially sensitive:
- `data/asana.sqlite` — normalized snapshot of tasks, comments, attachments metadata, users, emails, and related entities.
- `exports/` — Jira CSVs, user exports, audit reports, link-modification reports, and attachment sync reports.
- `exports/tmp_attachments/` — temporary attachment downloads created during Jira attachment syncs.

Attachment temp folders are deleted automatically on normal completion unless `--keep-downloads` is used. Interrupted or failed runs can still leave residual temp files behind, so review `exports/tmp_attachments/` during cleanup. SQLite snapshots and export files persist until you remove or archive them according to the client’s retention policy.

Suggested post-migration cleanup:
```bash
rm -rf exports
rm -f data/asana.sqlite
rm -rf profiles
rm -f .asana_jira_active
```

If access is no longer needed, also remove or rotate the profile files under `profiles/` (and any legacy `~/.asana_jira_env*` files left in your home directory), and clear the matching Keychain entries.

## Testing
Run the full unit test suite:
```bash
python3 -m unittest discover tests
```
Target ≥90% coverage and add regression tests for every bug fix.

Generate a statement-coverage report when preparing releases:
```bash
python3 -m trace --count --file trace.out -m unittest discover tests
```
Inspect `trace.out` to verify critical modules stay within the ≥90% target. Integration smoke tests live under `tests/integration/`; they exercise CLI entrypoints end-to-end with in-memory SQLite databases and mocked HTTP clients.

## Virtual Environment
Create and activate a project-local virtual environment to keep dependencies isolated and avoid conflicts with system/global Python packages:
```bash
python3 -m venv .venv
source .venv/bin/activate
```
This keeps any future dependency pins scoped to the project, prevents global packages from shadowing modules, and lets you remove the environment cleanly by deleting `.venv`.

## Operations Runbook
- **Credentials**: keep client profiles in the project's git-ignored `profiles/` directory, select the active one with the `.asana_jira_active` marker (the **Profile & Environment** menu manages both), and rotate personal access tokens immediately if they are ever committed or shared.
- **Logging**: every script accepts `--log-level` (`DEBUG`, `INFO`, `WARNING`, `ERROR`). Use `DEBUG` to trace API pagination or rate-limit retries; default `INFO` is suitable for unattended runs.
- **Sync recovery**: rerun the relevant script with the same scope. Upserts are idempotent, and incremental sync summaries confirm whether rows were analyzed, updated, or re-created.
- **SQLite maintenance**: new indexes on task linkage tables keep large syncs responsive. Vacuum occasionally (`sqlite3 data/asana.sqlite 'VACUUM;'`) after pruning old data.
- **Exports**: Jira CSVs accumulate under `exports/` (git-ignored). Rotate or archive large batches to avoid local storage bloat.

## Rate Limit Awareness
The shared HTTP client surfaces the most recent Asana rate limit headers via `AsanaHttpClient.last_rate_limit`. Inspect this metadata after requests to monitor remaining capacity and defer heavy jobs when approaching the limit.

## Open Question
- `tasks.hierarchy_depth` now records nested subtask depth, but we still need to confirm whether downstream exports should flatten deeper chains or preserve the full hierarchy as-is.
