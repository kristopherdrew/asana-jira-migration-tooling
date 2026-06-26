#!/usr/bin/env python3
"""Interactive driver that orchestrates the existing scripts without breaking their standalone entrypoints."""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import shlex
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

# Ensure project root is on sys.path so imports work when run directly.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import script mains (they stay runnable on their own; we just reuse them).
from scripts import (  # noqa: E402
    get_custom_fields,
    get_projects,
    get_portfolios,
    get_project_status_updates,
    get_task_extras,
    get_tasks,
    get_teams,
    get_users,
    get_personal_tasks,
    get_personal_tasks_delta,
    get_incremental_updates,
    merge_project_exports,
    export_users,
    export_team_report,
)
from scripts import (  # noqa: E402
    export_project_details_report,
    export_team_memberships,
    export_custom_field_usage_report,
)
from scripts.atlassian import (  # noqa: E402
    clear_jira_task_keys,
    add_service_user_to_teams,
    add_service_user_to_projects,
    create_team_project_jira_issues,
    export_project_to_jira_csv,
    export_team_project_jira_csv,
    import_jira_task_keys,
    sync_jira_attachments,
    update_atlassian_user_map,
    import_jira_project_map,
)
from src.lib.config import (  # noqa: E402
    Credentials,
    DEFAULT_ENV_PATH,
    load_credentials,
    resolve_credentials_path,
    write_credentials_file,
    write_project_env_pointer,
)
from src.lib.logging_utils import configure_logging, resolve_log_level  # noqa: E402


LOG_DIR = PROJECT_ROOT / "exports" / "interactive_runs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: str
    handler: Callable[[], None]


def prompt(text: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{text}{suffix}: ").strip()
    if value == "" and default is not None:
        return default
    return value


def prompt_required(text: str, default: Optional[str] = None) -> str:
    while True:
        value = prompt(text, default=default)
        if value:
            return value
        print("A value is required.")


def prompt_bool(text: str, default: bool = False) -> bool:
    default_str = "Y/n" if default else "y/N"
    value = input(f"{text} ({default_str}): ").strip().lower()
    if value == "":
        return default
    return value.startswith("y")


def _open_sqlite(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"SQLite database not found at {db_path}. Run the sync scripts first.")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _fuzzy_projects(term: str, conn: sqlite3.Connection, limit: int = 20, offset: int = 0):
    like = f"%{term.lower()}%"
    rows = conn.execute(
        """
        SELECT p.gid, p.name, t.name AS team_name
        FROM projects p
        LEFT JOIN teams t ON t.gid = p.team_gid
        WHERE lower(p.name) LIKE ?
        ORDER BY instr(lower(p.name), ?), length(p.name), p.name
        LIMIT ? OFFSET ?
        """,
        (like, term.lower(), limit, offset),
    ).fetchall()
    return rows


def _fuzzy_teams(term: str, conn: sqlite3.Connection, limit: int = 20, offset: int = 0):
    like = f"%{term.lower()}%"
    rows = conn.execute(
        """
        SELECT gid, name
        FROM teams
        WHERE lower(name) LIKE ?
        ORDER BY instr(lower(name), ?), length(name), name
        LIMIT ? OFFSET ?
        """,
        (like, term.lower(), limit, offset),
    ).fetchall()
    return rows


def _fuzzy_users(term: str, conn: sqlite3.Connection, limit: int = 20, offset: int = 0):
    like = f"%{term.lower()}%"
    rows = conn.execute(
        """
        SELECT gid, name, email
        FROM users
        WHERE lower(COALESCE(name, '')) LIKE ?
           OR lower(COALESCE(email, '')) LIKE ?
           OR lower(gid) LIKE ?
        ORDER BY instr(lower(COALESCE(name, email, gid)), ?), length(COALESCE(name, email, gid)), COALESCE(name, email, gid)
        LIMIT ? OFFSET ?
        """,
        (like, like, like, term.lower(), limit, offset),
    ).fetchall()
    return rows


def _prompt_lookup_gid(
    context: str,
    fallback_prompt: str,
    lookup_label: str,
    lookup_fn: Callable[[str, sqlite3.Connection, int, int], Sequence[sqlite3.Row]],
    row_label_fn: Callable[[sqlite3.Row], str],
) -> str:
    db_path = PROJECT_ROOT / "data" / "asana.sqlite"
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _open_sqlite(db_path)
    except SystemExit:
        conn = None

    team_gid = ""
    try:
        if conn:
            term = prompt(f"Search {lookup_label} for {context} (blank to type a GID)", default="")
            offset = 0
            while term:
                results = lookup_fn(term, conn, limit=20, offset=offset)
                if not results:
                    print("No matches. Try another term.")
                    term = prompt(f"Search {lookup_label} for {context} (blank to type a GID)", default="")
                    offset = 0
                    continue
                print("\nMatches:")
                for idx, row in enumerate(results, start=1):
                    print(f"{idx}. {row_label_fn(row)}")
                nav = prompt("Pick a number, 'n' for next page, or Enter to type a gid", default="")
                if nav.isdigit():
                    pick_idx = int(nav)
                    if 1 <= pick_idx <= len(results):
                        team_gid = results[pick_idx - 1]["gid"]
                        break
                elif nav.lower() == "n":
                    offset += 20
                    continue
                else:
                    break
    finally:
        if conn:
            conn.close()

    if not team_gid:
        team_gid = prompt(fallback_prompt, default="")
    return team_gid


def _prompt_project_gid_from_lookup(context: str, fallback_prompt: str) -> str:
    return _prompt_lookup_gid(
        context,
        fallback_prompt,
        "project name",
        _fuzzy_projects,
        lambda row: f"{row['name']}{(' [' + row['team_name'] + ']') if row['team_name'] else ''} (gid {row['gid']})",
    )


def _prompt_team_gid_from_lookup(context: str, fallback_prompt: str) -> str:
    return _prompt_lookup_gid(
        context,
        fallback_prompt,
        "team name",
        _fuzzy_teams,
        lambda row: f"{row['name']} (gid {row['gid']})",
    )


def _prompt_user_gid_from_lookup(context: str, fallback_prompt: str) -> str:
    return _prompt_lookup_gid(
        context,
        fallback_prompt,
        "user name",
        _fuzzy_users,
        lambda row: f"{row['name'] or row['gid']}{(' <' + row['email'] + '>') if row['email'] else ''} (gid {row['gid']})",
    )


def confirm(text: str, default: bool = True) -> bool:
    return prompt_bool(text, default=default)


def prompt_credentials_env_path(default: Optional[Path] = None) -> Path:
    default_path = str(default or DEFAULT_ENV_PATH)
    raw_path = prompt("Credentials env file path", default=default_path)
    return Path(raw_path).expanduser()


def prompt_credentials_values() -> Credentials:
    token = prompt_required("ASANA_TOKEN")
    workspace_gid = prompt_required("ASANA_WORKSPACE_GID")
    skip_ssl_verification = prompt_bool("Skip SSL verification?", default=False)
    atlassian_username = prompt("ATLASSIAN_USERNAME (optional)", default="")
    atlassian_token = prompt("ATLASSIAN_TOKEN (optional)", default="")
    return Credentials(
        token=token,
        workspace_gid=workspace_gid,
        skip_ssl_verification=skip_ssl_verification,
        atlassian_username=atlassian_username or None,
        atlassian_token=atlassian_token or None,
    )


def bootstrap_credentials(reference_path: Path = PROJECT_ROOT) -> Path:
    env_path = prompt_credentials_env_path(resolve_credentials_path(reference_path))
    if env_path.exists() and not prompt_bool(f"{env_path} already exists. Overwrite it?", default=False):
        raise SystemExit("Credentials file was not updated.")
    credentials = prompt_credentials_values()
    write_credentials_file(env_path, credentials)
    write_project_env_pointer(reference_path, env_path)
    print(f"Credentials saved to {env_path} and referenced from the project pointer.")
    return env_path


def change_credentials_file_reference(reference_path: Path = PROJECT_ROOT) -> Path:
    current_path = resolve_credentials_path(reference_path)
    env_path = prompt_credentials_env_path(current_path)
    if not env_path.exists():
        print(f"No file exists at {env_path}. You can create it now.")
        if not prompt_bool("Create this credentials file now?", default=True):
            raise SystemExit("Credentials file was not changed.")
        credentials = prompt_credentials_values()
        write_credentials_file(env_path, credentials)
    write_project_env_pointer(reference_path, env_path)
    print(f"Project pointer now references {env_path}.")
    return env_path


def ensure_interactive_credentials(reference_path: Path = PROJECT_ROOT) -> None:
    try:
        load_credentials(reference_path)
    except FileNotFoundError:
        print(
            "\nNo referenced credentials file was found for this project. "
            "Let's create one before continuing."
        )
        bootstrap_credentials(reference_path)


def build_credentials_menu() -> Dict[str, MenuItem]:
    return {
        "1": MenuItem("1", "Create or update credentials file", lambda: bootstrap_credentials(PROJECT_ROOT)),
        "2": MenuItem("2", "Change referenced credentials file", lambda: change_credentials_file_reference(PROJECT_ROOT)),
        "b": MenuItem("b", "Back", lambda: None),
    }


def log_run(step: str, argv: Sequence[str], status: str, error: Optional[str] = None) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "step": step,
        "argv": list(argv),
        "status": status,
    }
    if error:
        record["error"] = error
    log_path = LOG_DIR / f"interactive_{datetime.now(timezone.utc).strftime('%Y%m%d')}.jsonl"
    with log_path.open("a", encoding="utf-8") as fp:
        fp.write(f"{record}\n")


def run_script(main_func: Callable[[Optional[Iterable[str]]], None], argv: List[str]):
    return main_func(argv)


def _strip_task_scope_args(argv: Sequence[str]) -> List[str]:
    scope_flags_with_value = {
        "--project-gid",
        "--project-gids",
        "--team-gids",
        "--exclude-team-gids",
        "--all-teams-state-file",
    }
    scope_flags_without_value = {
        "--team",
        "--all-teams",
        "--resume-all-teams",
    }
    stripped: List[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in scope_flags_with_value:
            index += 2
            continue
        if arg in scope_flags_without_value:
            index += 1
            continue
        stripped.append(arg)
        index += 1
    return stripped


def _candidate_project_gids(task_sync_result) -> List[str]:
    if not task_sync_result:
        return []
    candidates = getattr(task_sync_result, "missing_membership_candidates", None) or []
    project_gids = [candidate.project_gid for candidate in candidates if getattr(candidate, "project_gid", None)]
    if project_gids:
        return project_gids
    state_path = getattr(task_sync_result, "missing_membership_state_path", None)
    if not state_path:
        return []
    state_file = Path(state_path)
    if not state_file.exists():
        return []
    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    gids = payload.get("missing_project_gids") or []
    return [gid for gid in gids if gid]


def run_task_sync_flow(log_level: str) -> None:
    args = args_get_tasks(log_level)
    result = run_script(get_tasks.main, args)
    missed_project_gids = _candidate_project_gids(result)
    if not missed_project_gids:
        return

    print(
        "\nThe task sync completed with projects that have expected task counts but no imported task rows:"
    )
    for gid in missed_project_gids:
        print(f"- {gid}")

    if not confirm("Add a Service Account to these projects now?", default=True):
        return

    service_user_gid = prompt_required("Service user GID")
    add_args = build_common_args(log_level)
    add_args.extend(["--user-gid", service_user_gid, "--project-gids", ",".join(missed_project_gids)])
    run_script(add_service_user_to_projects.main, add_args)

    if not confirm("Try fetching tasks from the missed projects now?", default=True):
        return

    retry_args = _strip_task_scope_args(args)
    retry_args.extend(["--project-gids", ",".join(missed_project_gids)])
    run_script(get_tasks.main, retry_args)


def print_rerun_command(module: str, argv: Sequence[str]) -> None:
    parts = ["python3", "-m", module]
    parts.extend(str(a) for a in argv)
    cmd = shlex.join(parts)
    print("\nRe-run directly without the interactive menu:\n  " + cmd + "\n")


def next_export_dir(root: Path, export_id: Optional[str] = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    index = 1
    while True:
        suffix = f"-{export_id}" if export_id else ""
        candidate = root / f"export-{index:02d}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def build_common_args(log_level: str) -> List[str]:
    args: List[str] = []
    if log_level:
        args.extend(["--log-level", log_level])
    return args


# ---------------------- Argument builders ---------------------- #
def args_initial_metadata(log_level: str) -> List[str]:
    return build_common_args(log_level)


def args_initial_projects(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    scope = prompt("Archived scope for projects (active|archived|all)", default="active")
    args.extend(["--archived-scope", scope])
    return args


def args_initial_teams(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    args.append("--include-members")
    return args


def args_report_defaults(log_level: str) -> List[str]:
    return build_common_args(log_level)


def args_get_tasks(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    print("\nSelect task sync scope:")
    print("[1] All teams")
    print("[2] Single project GID")
    print("[3] Team interactive picker")
    choice = input("Choice (default 3): ").strip() or "3"
    if choice == "1":
        args.append("--all-teams")
        if confirm("Resume from last all-team run if progress file exists?", default=True):
            args.append("--resume-all-teams")
        # Allow custom state file?
        # Use default path from get_tasks; advanced users can edit CLI args manually if needed.
    elif choice == "2":
        project_gid = _prompt_project_gid_from_lookup(
            "task sync",
            "Project GID to sync",
        )
        if not project_gid:
            raise SystemExit("Project GID is required for single-project sync.")
        args.extend(["--project-gid", project_gid])
    else:
        args.append("--team")

    print(
        "\nHeads up: task syncs can take a long time (hours on large workspaces). "
        "Once started, let it run to avoid partial snapshots."
    )
    default_workers = "5"
    recommended = "10"
    max_recommended = "15"
    workers = prompt(
        f"Workers (default {default_workers}, recommended {recommended}, max recommended {max_recommended})",
        default=default_workers,
    )
    if workers:
        args.extend(["--workers", workers])

    return args


def args_get_task_extras(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    print("\nSelect task extras scope:")
    print("[1] Single project GID")
    print("[2] Team interactive picker")
    print("[3] All teams")
    print("[4] Personal tasks (all users)")
    print("[5] Personal tasks (specific assignees)")
    choice = input("Choice (default 1): ").strip() or "1"
    if choice == "1":
        project_gid = _prompt_project_gid_from_lookup(
            "task extras",
            "Project GID for task extras",
        )
        if not project_gid:
            raise SystemExit("Project GID is required.")
        args.extend(["--project-gid", project_gid])
    elif choice == "2":
        args.append("--team")
    elif choice == "3":
        args.append("--all-teams")
    elif choice == "4":
        args.append("--personal")
    else:
        args.append("--personal")
        assignees = prompt("Comma-separated user GIDs for personal tasks", default="")
        if not assignees:
            raise SystemExit("At least one assignee GID is required for option 5.")
        args.extend(["--personal-assignees", assignees])
    return args


def args_get_project_status_updates(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    print("\nSelect project status-update scope:")
    print("[1] Single project GID")
    print("[2] Team interactive picker")
    print("[3] All teams")
    print("[4] Multiple project GIDs (comma-separated)")
    choice = input("Choice (default 1): ").strip() or "1"
    if choice == "1":
        project_gid = _prompt_project_gid_from_lookup(
            "project status updates",
            "Project GID for status updates",
        )
        if not project_gid:
            raise SystemExit("Project GID is required.")
        args.extend(["--project-gid", project_gid])
    elif choice == "2":
        args.append("--team")
    elif choice == "3":
        args.append("--all-teams")
        excluded = prompt("Exclude team GIDs (comma-separated, optional)", default="")
        if excluded:
            args.extend(["--exclude-team-gids", excluded])
    else:
        gids = prompt("Project GIDs (comma-separated)", default="")
        if not gids:
            raise SystemExit("At least one project GID is required for option 4.")
        args.extend(["--project-gids", gids])

    workers = prompt("Workers (default 5)", default="5")
    if workers:
        args.extend(["--workers", workers])
    return args


def args_portfolios(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    if confirm("Limit to a single portfolio owner? (default: all users)", default=False):
        owner_gid = _prompt_user_gid_from_lookup(
            "portfolio owner",
            "Owner gid",
        )
        if owner_gid:
            args.extend(["--owner-gid", owner_gid])
    workers = prompt("Workers (default 5)", default="5")
    if workers:
        args.extend(["--workers", workers])
    return args


def args_personal_tasks(log_level: str, delta: bool = False) -> List[str]:
    args = build_common_args(log_level)
    assignee = _prompt_user_gid_from_lookup(
        "personal tasks",
        "Assignee gid ('me' allowed, blank to use me)",
    )
    if assignee:
        args.extend(["--assignee", assignee])
    if confirm("Sync all users instead?", default=False):
        args.append("--all-users")
    if delta:
        since = prompt("Override --since ISO8601 cursor (blank to use stored cursor)", default="")
        if since:
            args.extend(["--since", since])
    return args


def args_incremental_updates(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    if confirm("Auto lookback from last task update (fetch all if none)?", default=False):
        args.append("--auto-from-last-update")
    else:
        days = prompt("Days back to scan (default 3)", default="3")
        args.extend(["--days-back", days])
    multi_scope = False
    if confirm("Target all teams?", default=True):
        args.append("--all-teams")
        multi_scope = True
        excluded = prompt("Exclude team GIDs (comma-separated, optional)", default="")
        if excluded:
            args.extend(["--exclude-team-gids", excluded])
    else:
        print("\nSelect incremental sync scope:")
        print("[1] Single project GID")
        print("[2] Team interactive picker")
        print("[3] Team GIDs (comma-separated)")
        choice = input("Choice (default 2): ").strip() or "2"
        if choice == "1":
            project_gid = _prompt_project_gid_from_lookup(
                "incremental sync",
                "Project GID to sync",
            )
            if not project_gid:
                raise SystemExit("Project GID is required for option 1.")
            args.extend(["--project-gid", project_gid])
        elif choice == "2":
            args.append("--team")
            multi_scope = True
        else:
            team_gids = prompt("Team GIDs (comma-separated)", default="")
            if not team_gids:
                raise SystemExit("At least one team GID is required for option 3.")
            args.extend(["--team-gids", team_gids])
            multi_scope = True

    if multi_scope:
        if confirm("Show per-project breakdown after the run?", default=False):
            args.extend(["--project-breakdown", "yes"])
        else:
            args.extend(["--project-breakdown", "no"])
    return args


def args_export_project_to_jira(log_level: str) -> tuple[List[str], bool]:
    args = build_common_args(log_level)
    db_path = PROJECT_ROOT / "data" / "asana.sqlite"
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _open_sqlite(db_path)
    except SystemExit:
        conn = None

    allow_merge = False

    print("\nSelect export scope:")
    print("[1] Single project")
    print("[2] Multiple projects (comma-separated GIDs)")
    print("[3] All projects in a team")
    choice = input("Choice (default 1): ").strip() or "1"
    if choice == "1":
        project_gid = ""
        if conn and confirm("Search for project by name?", default=True):
            term = prompt("Search text", default="")
            offset = 0
            while term:
                results = _fuzzy_projects(term, conn, limit=20, offset=offset)
                if not results:
                    print("No matches. Try another term.")
                    term = prompt("Search text", default="")
                    offset = 0
                    continue
                print("\nMatches:")
                for idx, row in enumerate(results, start=1):
                    team_label = f" [{row['team_name']}]" if row["team_name"] else ""
                    print(f"{idx}. {row['name']}{team_label} (gid {row['gid']})")
                nav = prompt("Pick a number, 'n' for next page, or Enter to type a gid", default="")
                if nav.isdigit():
                    pick_idx = int(nav)
                    if 1 <= pick_idx <= len(results):
                        project_gid = results[pick_idx - 1]["gid"]
                        break
                elif nav.lower() == "n":
                    offset += 20
                    continue
                else:
                    break
            if not project_gid:
                project_gid = prompt("Project GID to export", default="")
        else:
            project_gid = prompt("Project GID to export", default="")
        if not project_gid:
            raise SystemExit("Project GID is required.")
        args.extend(["--project-gid", project_gid])
    elif choice == "2":
        if confirm("Load project GIDs from a file?", default=False):
            path = prompt("Path to file with project GIDs (newline or comma separated)", default="")
            if not path:
                raise SystemExit("Project GID file is required.")
            args.extend(["--project-gids-file", path])
        else:
            gids = prompt("Project GIDs (comma-separated)", default="")
            if not gids:
                raise SystemExit("At least one project GID is required.")
            args.extend(["--project-gids", gids])
        allow_merge = True
    else:
        team_gid = ""
        if conn and confirm("Search for team by name?", default=True):
            term = prompt("Search text", default="")
            offset = 0
            while term:
                results = _fuzzy_teams(term, conn, limit=20, offset=offset)
                if not results:
                    print("No matches. Try another term.")
                    term = prompt("Search text", default="")
                    offset = 0
                    continue
                print("\nMatches:")
                for idx, row in enumerate(results, start=1):
                    print(f"{idx}. {row['name']} (gid {row['gid']})")
                nav = prompt("Pick a number, 'n' for next page, or Enter to type a gid", default="")
                if nav.isdigit():
                    pick_idx = int(nav)
                    if 1 <= pick_idx <= len(results):
                        team_gid = results[pick_idx - 1]["gid"]
                        break
                elif nav.lower() == "n":
                    offset += 20
                    continue
                else:
                    break
        if not team_gid:
            team_gid = prompt("Team GID whose projects to export", default="")
        if not team_gid:
            raise SystemExit("Team GID is required.")
        args.extend(["--team-gid", team_gid])
        scope = prompt("Archived scope (active|archived|all) [active]", default="active")
        args.extend(["--archived-scope", scope])
        allow_merge = True

    if not confirm("Prompt to select custom fields during export?", default=True):
        args.append("--no-custom-field-prompt")

    if choice in ("2", "3"):
        incremental_yes = confirm("Run incremental refresh for every selected project?", default=False)
        args.extend(["--incremental-refresh", "yes" if incremental_yes else "no"])
        if incremental_yes:
            breakdown = (
                "yes"
                if confirm("Show per-project breakdown after each incremental refresh?", default=False)
                else "no"
            )
            args.extend(["--incremental-project-breakdown", breakdown])

    if confirm("Stats only (no CSV)?", default=False):
        args.append("--stats-only")
    jira_base = prompt("Jira base URL (blank to use default)", default="")
    if jira_base:
        args.extend(["--jira-base-url", jira_base])
    fallback_atl = prompt("Fallback Atlassian account ID for unmapped users (optional)", default="")
    if fallback_atl:
        args.extend(["--fallback-atlassian-id", fallback_atl])
    export_id = prompt("Export ID to record/skip previously exported tasks (optional)", default="")
    if export_id:
        args.extend(["--export-id", export_id])
    return args, allow_merge


def run_export_project_to_jira(log_level: str) -> None:
    args, allow_merge = args_export_project_to_jira(log_level)
    export_dir: Optional[Path] = None
    if allow_merge:
        export_id = None
        # args already contains flags; find export-id if present
        for idx, val in enumerate(args):
            if val == "--export-id" and idx + 1 < len(args):
                export_id = args[idx + 1]
                break
        export_dir = next_export_dir(PROJECT_ROOT / "exports", export_id=export_id)
        print(f"\nExporting multi-project output into {export_dir}")
        args.extend(["--output-dir", str(export_dir)])
    display_args = list(args)  # capture final argv used for the export command
    run_script(export_project_to_jira_csv.main, args)
    if allow_merge:
        merge_root = export_dir or (PROJECT_ROOT / "exports")
        inputs = str(merge_root / "*_tasks.csv")
        output = merge_root / f"_Consolidated-{datetime.now().strftime('%Y%m%d-%H%M')}_tasks.csv"
        source_col = "project"
        strip_suffix = ""
        merge_args = build_common_args(log_level)
        merge_args.extend(["--inputs", inputs, "--output", str(output), "--source-column", source_col])
        run_script(merge_project_exports.main, merge_args)
    print_rerun_command("scripts.export_project_to_jira_csv", display_args)


def run_sync_attachments(log_level: str) -> None:
    args = args_sync_attachments(log_level)
    run_script(sync_jira_attachments.main, args)
    print_rerun_command("scripts.atlassian.sync_jira_attachments", args)


def build_reports_menu(log_level: str) -> Dict[str, MenuItem]:
    def run_project_details_report() -> None:
        args = args_report_defaults(log_level)
        team_gid = _prompt_team_gid_from_lookup(
            "project details",
            "Team GID to filter project details (blank = all teams)",
        )
        if team_gid:
            args.extend(["--team-gid", team_gid])
        run_script(export_project_details_report.main, args)

    def run_custom_field_usage_report() -> None:
        args = args_report_defaults(log_level)
        team_gid = _prompt_team_gid_from_lookup(
            "custom field usage",
            "Team GID to filter custom field usage (blank = all teams)",
        )
        if team_gid:
            args.extend(["--team-gid", team_gid])
        run_script(export_custom_field_usage_report.main, args)

    def run_team_memberships_report() -> None:
        args = args_report_defaults(log_level)
        team_gid = _prompt_team_gid_from_lookup(
            "team memberships",
            "Team GID to filter memberships (blank = all teams)",
        )
        if team_gid:
            args.extend(["--team-gid", team_gid])
        run_script(export_team_memberships.main, args)

    def run_team_report() -> None:
        args = args_report_defaults(log_level)
        team_gid = _prompt_team_gid_from_lookup(
            "team report",
            "Team GID to filter team report (blank = all teams)",
        )
        if team_gid:
            args.extend(["--team-gid", team_gid])
        run_script(export_team_report.main, args)

    def run_team_project_jira_export() -> None:
        args = args_report_defaults(log_level)
        if confirm("Include projects with 0 tasks?", default=False):
            args.append("--include-empty-projects")
        run_script(export_team_project_jira_csv.main, args)

    return {
        "1": MenuItem(
            "1",
            "Export project details report",
            run_project_details_report,
        ),
        "2": MenuItem(
            "2",
            "Export users report",
            lambda: run_script(export_users.main, args_report_defaults(log_level)),
        ),
        "3": MenuItem(
            "3",
            "Export team memberships report",
            run_team_memberships_report,
        ),
        "4": MenuItem(
            "4",
            "Export team report",
            run_team_report,
        ),
        "5": MenuItem(
            "5",
            "Export custom field usage report",
            run_custom_field_usage_report,
        ),
        "6": MenuItem(
            "6",
            "Export team/project Jira CSV",
            run_team_project_jira_export,
        ),
        "b": MenuItem("b", "Back", lambda: None),
    }


def args_import_jira_keys(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    csv_path = prompt("Path to Jira export CSV (with Issue key + External issue ID)", default="")
    if not csv_path:
        raise SystemExit("CSV path is required.")
    args.extend(["--csv", csv_path])
    task_column = prompt("Task ID column name", default="External issue ID")
    if task_column:
        args.extend(["--task-column", task_column])
    jira_column = prompt("Jira key column name", default="Issue key")
    if jira_column:
        args.extend(["--jira-column", jira_column])
    return args


def args_import_jira_project_map(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    csv_path = prompt("Path to Jira project association CSV (project_gid,project_name,project_key)", default="")
    if not csv_path:
        raise SystemExit("CSV path is required.")
    args.extend(["--csv", csv_path])
    return args


def run_merge_project_exports(log_level: str) -> None:
    default_glob = str(PROJECT_ROOT / "exports" / "*.csv")
    inputs = prompt("Glob for CSVs to merge", default=default_glob)
    if not inputs:
        raise SystemExit("At least one input glob/path is required.")
    default_output = PROJECT_ROOT / "exports" / f"Consolidated-{datetime.now().strftime('%Y%m%d-%H%M')}.csv"
    output = prompt("Output path for merged CSV", default=str(default_output))
    source_col = prompt("Optional source column name (e.g., project)", default="")
    strip_suffix = prompt("Optional suffix to strip from filename stem", default="")
    args = build_common_args(log_level)
    args.extend(["--inputs", inputs, "--output", output])
    if source_col:
        args.extend(["--source-column", source_col])
    if strip_suffix:
        args.extend(["--strip-suffix", strip_suffix])
    run_script(merge_project_exports.main, args)


def args_clear_jira_keys(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    gids = prompt("Comma-separated task GIDs to clear (blank = clear all)", default="")
    if gids:
        args.extend(["--task-gids", gids])
    else:
        project_gid = _prompt_project_gid_from_lookup(
            "clearing Jira mappings",
            "Project GID to clear mappings for (blank = all projects)",
        )
        if project_gid:
            args.extend(["--project-gid", project_gid])
    return args


def args_clear_export_ids(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    args.append("--clear-export-ids")
    return args


def args_update_user_map(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    csv_path = prompt(
        "CSV with gid,atlassianAccountId[,atlassianEmail,atlassianStatus] "
        "(template: Sample Data/atlassian_user_map_template.csv)",
        default="",
    )
    if not csv_path:
        raise SystemExit("CSV path is required.")
    args.extend(["--csv", csv_path])
    return args


def args_sync_attachments(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    project_gids = prompt("Project GID(s) to sync attachments for (comma-separated allowed)", default="")
    if not project_gids:
        raise SystemExit("At least one project GID is required.")
    jira_base = prompt("Jira base URL", default="")
    if not jira_base:
        raise SystemExit("Jira base URL is required.")
    if "," in project_gids:
        args.extend(["--project-gids", project_gids])
    else:
        args.extend(["--project-gid", project_gids])
    args.extend(["--jira-base-url", jira_base])
    if confirm("Dry run (no uploads)?", default=True):
        args.append("--dry-run")
    return args


def args_create_team_project_jira_issues(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    jira_base = prompt("Jira base URL (e.g. https://example.atlassian.net)", default="")
    if not jira_base:
        raise SystemExit("Jira base URL is required.")
    jira_project_key = prompt("Target Jira project key (e.g. MIG)", default="")
    if not jira_project_key:
        raise SystemExit("Jira project key is required.")
    args.extend(["--jira-base-url", jira_base, "--jira-project-key", jira_project_key])
    team_gid = _prompt_team_gid_from_lookup(
        "team/project Jira creation",
        "Team GID to restrict creation to (blank = all teams)",
    )
    if team_gid:
        args.extend(["--team-gid", team_gid])
    if confirm("Include projects with 0 tasks?", default=False):
        args.append("--include-empty-projects")
    if confirm("Dry run (resolve fields and print payloads, create nothing)?", default=True):
        args.append("--dry-run")
    return args


def run_group(title: str, menu: Dict[str, MenuItem]) -> None:
    while True:
        print_menu(title, menu)
        choice = input("Select an option: ").strip()
        if choice not in menu:
            print("Invalid choice. Please try again.")
            continue
        if choice == "b":
            return
        item = menu[choice]
        try:
            item.handler()
        except Exception as exc:  # pragma: no cover - defensive
            logging.exception("Step failed: %s", exc)
        else:
            logging.info("Step '%s' completed.", item.label)


# ---------------------- Menu handlers ---------------------- #
def run_initial_metadata(log_level: str) -> None:
    steps = [
        ("get_teams", get_teams.main),
        ("get_users", get_users.main),
        ("add_service_user_to_teams", add_service_user_to_teams.main),
        ("get_projects", get_projects.main),
        ("get_custom_fields", get_custom_fields.main),
    ]
    for label, func in steps:
        if label == "get_teams":
            argv = args_initial_teams(log_level)
        elif label == "get_projects":
            argv = args_initial_projects(log_level)
        else:
            argv = args_initial_metadata(log_level)
        try:
            logging.info("Running %s ...", label)
            run_script(func, argv)
            log_run(label, argv, "ok")
        except SystemExit as exc:
            log_run(label, argv, f"exit({exc.code})", error=str(exc))
            raise
        except Exception as exc:  # pragma: no cover - defensive
            log_run(label, argv, "error", error=str(exc))
            raise


def build_init_menu(log_level: str) -> Dict[str, MenuItem]:
    return {
        "1": MenuItem("1", "Run initial metadata (teams + memberships, users, service user Asana team access, projects, custom fields)", lambda: run_initial_metadata(log_level)),
        "b": MenuItem("b", "Back", lambda: None),
    }


def build_tasks_menu(log_level: str) -> Dict[str, MenuItem]:
    return {
        "1": MenuItem("1", "Sync tasks", lambda: run_task_sync_flow(log_level)),
        "2": MenuItem("2", "Sync task extras (comments/attachments)", lambda: run_script(get_task_extras.main, args_get_task_extras(log_level))),
        "3": MenuItem("3", "Sync project status updates (project-level updates/comments)", lambda: run_script(get_project_status_updates.main, args_get_project_status_updates(log_level))),
        "4": MenuItem("4", "Sync portfolios (all users, incl. items/memberships/updates)", lambda: run_script(get_portfolios.main, args_portfolios(log_level))),
        "5": MenuItem("5", "Sync personal tasks (full)", lambda: run_script(get_personal_tasks.main, args_personal_tasks(log_level, delta=False))),
        "6": MenuItem("6", "Incremental updates (recently modified tasks incl. personal)", lambda: run_script(get_incremental_updates.main, args_incremental_updates(log_level))),
        "b": MenuItem("b", "Back", lambda: None),
    }


def build_atlassian_menu(log_level: str) -> Dict[str, MenuItem]:
    return {
        "1": MenuItem("1", "Export project to Jira CSV", lambda: run_export_project_to_jira(log_level)),
        "2": MenuItem("2", "Import Jira task keys", lambda: run_script(import_jira_task_keys.main, args_import_jira_keys(log_level))),
        "3": MenuItem("3", "Clear Jira task keys", lambda: run_script(clear_jira_task_keys.main, args_clear_jira_keys(log_level))),
        "4": MenuItem(
            "4",
            "Update Atlassian user map (template: Sample Data/atlassian_user_map_template.csv)",
            lambda: run_script(update_atlassian_user_map.main, args_update_user_map(log_level)),
        ),
        "5": MenuItem("5", "Import Jira project associations", lambda: run_script(import_jira_project_map.main, args_import_jira_project_map(log_level))),
        "6": MenuItem("6", "Clear export IDs", lambda: run_script(export_project_to_jira_csv.main, args_clear_export_ids(log_level))),
        "7": MenuItem("7", "Clear Jira project associations", lambda: run_script(export_project_to_jira_csv.main, ["--clear-jira-project-map"])),
        "8": MenuItem("8", "Sync Jira attachments", lambda: run_sync_attachments(log_level)),
        "9": MenuItem(
            "9",
            "Create team/project Jira issues (direct API)",
            lambda: run_script(create_team_project_jira_issues.main, args_create_team_project_jira_issues(log_level)),
        ),
        "b": MenuItem("b", "Back", lambda: None),
    }


def print_menu(title: str, menu: Dict[str, MenuItem]) -> None:
    print(f"\n=== {title} ===")
    for key in sorted(menu, key=lambda k: (k == "q", float("inf") if k == "q" else float("inf") if k == "b" else int(k) if k.isdigit() else k)):
        item = menu[key]
        print(f"[{item.key}] {item.label}")


def parse_cli_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-level", default="INFO", help="Log level for the interactive driver and invoked scripts.")
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_cli_args(argv)
    log_level = resolve_log_level(args.log_level)
    configure_logging(log_level)
    ensure_interactive_credentials(PROJECT_ROOT)
    top_menu = {
        "1": MenuItem("1", "Initialization (metadata only)", lambda: run_group("Initialization", build_init_menu(args.log_level))),
        "2": MenuItem("2", "Tasks (full/extras/incremental)", lambda: run_group("Tasks", build_tasks_menu(args.log_level))),
        "3": MenuItem("3", "Atlassian (exports, mappings, attachments)", lambda: run_group("Atlassian", build_atlassian_menu(args.log_level))),
        "4": MenuItem("4", "Reports (projects, users, memberships, team summary, Jira CSV)", lambda: run_group("Reports", build_reports_menu(args.log_level))),
        "5": MenuItem("5", "Credentials (create or change env file)", lambda: run_group("Credentials", build_credentials_menu())),
        "q": MenuItem("q", "Quit", lambda: sys.exit(0)),
    }

    while True:
        print_menu("Asana Toolkit Interactive CLI", top_menu)
        choice = input("Select a category: ").strip()
        if choice not in top_menu:
            print("Invalid choice. Please try again.")
            continue
        item = top_menu[choice]
        try:
            item.handler()
        except Exception as exc:  # pragma: no cover - defensive
            logging.exception("Step failed: %s", exc)
        else:
            logging.info("Completed: %s", item.label)


if __name__ == "__main__":
    main()
