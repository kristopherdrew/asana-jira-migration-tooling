---
name: run-asana-jira-migration
description: Use to run, launch, build, smoke-test, or drive this Asana→Jira migration toolkit — a stdlib-only Python CLI suite (python3 -m scripts.<name>) plus a menu-driven interactive orchestrator. The offline smoke driver verifies the test suite, script entrypoints, SQLite schema init, and the interactive menu without any network call or credentials. Covers how to run individual scripts, run a subset of tests, introspect the DB, and (for live use) point at a credentials file.
---

# Run: Asana→Jira migration toolkit

A **stdlib-only Python CLI tool** — no GUI, no server. Business logic lives in
`src/`, entrypoints in `scripts/` (run as `python3 -m scripts.<name>`), and a
menu-driven wrapper in `scripts/interactive_cli.py`. Real work talks to the
Asana/Jira APIs and a local `data/asana.sqlite`, so the agent-facing way to
*drive* it without credentials is the **offline smoke driver**, which exercises
the four layers a change can land on (src logic, script argparse, the SQLite
schema/read path, the interactive menu).

All paths below are relative to the repo root (the unit root). The driver lives
at `.claude/skills/run-asana-jira-migration/smoke.sh`.

## Prerequisites
- **Python 3.11+** (verified here on 3.14.5). macOS or Linux.
- **No third-party packages** — the project is standard-library only. Do **not**
  `pip install` anything; a new dependency must be documented per `AGENTS.md`.
  The `.venv` exists only to pin the interpreter.
- Live API runs additionally need a credentials env file (see *Live run*). The
  smoke path needs none.

## Build / setup
Nothing to compile. Create the venv only if missing (the driver does this for you):
```bash
python3 -m venv .venv
source .venv/bin/activate
```

## Run (agent path) — offline smoke driver
This is the primary path. It runs the full suite, checks a script entrypoint,
builds a throwaway SQLite DB and introspects it, and launches + quits the
interactive menu. No network, never writes to `data/asana.sqlite`. Exit 0 = green.
```bash
.claude/skills/run-asana-jira-migration/smoke.sh
```
Verified output (this machine):
```
PASS: Ran 308 tests in 5.002s
PASS: python3 -m scripts.get_teams --help
PASS: describe_db introspected a fresh DB (32 tables)
PASS: menu rendered and quit on 'q'
ALL CHECKS PASSED
```

Launch the interactive orchestrator yourself (shows the 5-category menu; `q` quits):
```bash
source .venv/bin/activate
printf 'q\n' | python3 -m scripts.interactive_cli --log-level WARNING
```

## Direct invocation (most PRs need only this)
Run the suite, or a subset by name. This repo uses a custom `load_tests` hook in
`tests/test_suite.py`, so use **plain `discover tests`** and narrow with `-k` —
see Gotchas for what does *not* work.
```bash
source .venv/bin/activate
python3 -m unittest discover tests                 # full suite (308 tests)
python3 -m unittest discover tests -k empty_project # subset by substring
```
Resolve/inspect any script entrypoint (argparse, exits 0, no side effects):
```bash
python3 -m scripts.get_teams --help
python3 -m scripts.describe_db --help
```
Read-only DB introspection. `describe_db` never creates a DB — point it at the
real snapshot, or build a fresh one to test the schema path:
```bash
python3 -m scripts.describe_db --database data/asana.sqlite          # existing snapshot
# fresh, reproducible, no data:
TMPDB="$(mktemp -d)/fresh.sqlite"
python3 -c "from src.lib.database import Database; d=Database(db_path='$TMPDB'); c=d.connect(); d.initialize(c); c.close()"
python3 -m scripts.describe_db --database "$TMPDB"
```

## Live run (human path — makes network calls)
Only for real migration work; needs Asana/Jira credentials. Point this checkout
at a client env file, then run the orchestrator:
```bash
cat > ~/.asana_jira_env_client_a <<'EOF'
export ASANA_TOKEN="..."
export ASANA_WORKSPACE_GID="..."
export SKIP_SSL_VERIFICATION="false"
export ATLASSIAN_USERNAME="you@example.com"
export ATLASSIAN_TOKEN="..."
EOF
chmod 600 ~/.asana_jira_env_client_a
echo "~/.asana_jira_env_client_a" > .asana_jira_env_path   # this file is git-ignored
python3 -m scripts.interactive_cli --log-level INFO
```
Credential resolution order: process env → file named by `.asana_jira_env_path`
→ `~/.asana_jira_env`. (Not exercised by the smoke driver — it stays offline.)

## Gotchas (battle scars)
- **Tests don't discover the normal way.** Files are named `*.test.py` and are
  collected by a `load_tests` hook in `tests/test_suite.py`. `python3 -m unittest
  discover tests -p "*.test.py"` finds **0 tests** ("NO TESTS RAN"), and
  `python3 -m unittest tests.unit.scripts.foo.test` fails with
  `ModuleNotFoundError`. Always use `python3 -m unittest discover tests` and
  narrow with `-k`.
- **`describe_db` is strictly read-only** — it will not create the DB file. On a
  clean machine, build one first (snippet above) or it errors on a missing file.
- **No `timeout(1)` on macOS.** The driver guards the interactive launch with a
  background-watchdog subshell + `disown`/`set +m` to suppress job-control noise.
- **interactive_cli with no creds drops into a setup prompt** (asks for a token)
  instead of quitting on `q`. The driver detects this and prints `SKIP` rather
  than hanging; provide a creds file to exercise the launch path.
- **Invoke scripts as `python3 -m scripts.<name>`**, not `python3 scripts/<name>.py`
  — the module path keeps `src/` on `sys.path` (otherwise set `PYTHONPATH`).
- **`CLAUDE.md` is a symlink to `AGENTS.md`** — edits to `CLAUDE.md` are refused;
  edit `AGENTS.md`.
- **`data/asana.sqlite` is large (~300MB) and sensitive** (emails, comments). The
  smoke driver uses a temp DB and never touches it.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `NO TESTS RAN` / `ModuleNotFoundError: ...._test` | You used `discover -p "*.test.py"` or a dotted module path. Use `python3 -m unittest discover tests [-k filter]`. |
| `describe_db` errors / empty on a path | The DB file doesn't exist — `describe_db` won't create it. Build a fresh one or use `--database data/asana.sqlite`. |
| interactive_cli hangs asking for a token | No credentials resolved — set up `.asana_jira_env_path` (see *Live run*), or accept the driver's `SKIP`. |
| `python3 scripts/x.py` → import errors | Run as `python3 -m scripts.x`, or export `PYTHONPATH=$PWD`. |
