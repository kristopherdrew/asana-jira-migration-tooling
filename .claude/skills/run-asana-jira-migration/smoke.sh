#!/usr/bin/env bash
# Offline smoke driver for the Asana→Jira migration toolkit.
#
# This is a CLI-tool / library unit: there is no GUI or long-running server to
# click. "Driving" it means exercising the four surfaces a change can land on
# WITHOUT touching the network or live credentials:
#   1. src/ business logic        -> the unittest suite (temp SQLite DBs, no API)
#   2. script argparse entrypoints -> `--help` exits 0
#   3. the SQLite schema + read path -> build a fresh DB and introspect it
#   4. the interactive orchestrator -> launches and quits on `q`
#
# It never makes an Asana/Jira API call and never writes to data/asana.sqlite.
#
# Usage:  .claude/skills/run-asana-jira-migration/smoke.sh
# Exit:   0 = all checks passed, non-zero = something is broken.

set -u
set +m  # quiet job-control "Terminated" notices from the launch watchdog

# Repo root = three levels up from this script (.claude/skills/run-.../smoke.sh).
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
cd "$ROOT"

fail=0
step() { printf '\n=== %s ===\n' "$1"; }
ok()   { printf 'PASS: %s\n' "$1"; }
bad()  { printf 'FAIL: %s\n' "$1"; fail=1; }

# --- venv (stdlib-only project; venv just pins the interpreter) ----------------
if [ ! -x ".venv/bin/python" ]; then
  step "creating .venv (was missing)"
  python3 -m venv .venv || { echo "could not create venv"; exit 2; }
fi
PY=".venv/bin/python"
step "interpreter"
"$PY" --version

# --- 1. unit + integration suite ---------------------------------------------
step "unit/integration test suite"
if "$PY" -m unittest discover tests >/tmp/ajm_tests.log 2>&1; then
  ok "$(grep -E '^Ran ' /tmp/ajm_tests.log | tail -1)"
else
  tail -20 /tmp/ajm_tests.log
  bad "test suite (see /tmp/ajm_tests.log)"
fi

# --- 2. an argparse entrypoint resolves and exits 0 --------------------------
step "script entrypoint --help"
if "$PY" -m scripts.get_teams --help >/dev/null 2>&1; then
  ok "python3 -m scripts.get_teams --help"
else
  bad "scripts.get_teams --help"
fi

# --- 3. schema init + read-only introspection on a FRESH db -------------------
step "fresh-DB schema init + describe_db"
TMPDB="$(mktemp -d)/fresh.sqlite"
if "$PY" -c "from src.lib.database import Database; d=Database(db_path='$TMPDB'); c=d.connect(); d.initialize(c); c.close()" 2>/tmp/ajm_db.log; then
  if "$PY" -m scripts.describe_db --database "$TMPDB" >/tmp/ajm_desc.log 2>&1 \
     && grep -q "TABLE  teams" /tmp/ajm_desc.log; then
    ok "describe_db introspected a fresh DB ($(grep -c '^TABLE ' /tmp/ajm_desc.log) tables)"
  else
    tail -20 /tmp/ajm_desc.log
    bad "describe_db on fresh DB"
  fi
else
  cat /tmp/ajm_db.log
  bad "Database.initialize (schema creation)"
fi

# --- 4. interactive orchestrator launches and quits ---------------------------
# Needs a resolvable creds file (env / .asana_jira_env_path); otherwise it drops
# into an interactive setup prompt and this check is skipped, not failed.
step "interactive_cli launch + quit"
( printf 'q\n' | "$PY" -m scripts.interactive_cli --log-level WARNING ) >/tmp/ajm_cli.log 2>&1 &
pid=$!
( sleep 25; kill "$pid" 2>/dev/null ) &
watchdog=$!
disown "$watchdog" 2>/dev/null || true   # suppress job-control "Terminated" notice
wait "$pid" 2>/dev/null
kill "$watchdog" 2>/dev/null
if grep -q "Interactive CLI" /tmp/ajm_cli.log; then
  ok "menu rendered and quit on 'q'"
elif grep -qi "credential\|env file\|setup" /tmp/ajm_cli.log; then
  echo "SKIP: no credentials resolved on this checkout (setup flow); launch path not exercised"
else
  tail -20 /tmp/ajm_cli.log
  bad "interactive_cli launch"
fi

step "result"
if [ "$fail" -eq 0 ]; then echo "ALL CHECKS PASSED"; else echo "SMOKE FAILED"; fi
exit "$fail"
