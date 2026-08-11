#!/usr/bin/env python3
"""Interactive driver that orchestrates the existing scripts without breaking their standalone entrypoints."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import shlex
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set

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
    export_portfolio_report,
    export_field_section_review_report,
    export_project_members_report,
    export_project_last_updated_report,
    export_all_reports_xlsx,
)
from scripts.atlassian import (  # noqa: E402
    clear_jira_task_keys,
    add_service_user_to_teams,
    add_service_user_to_projects,
    create_team_project_jira_issues,
    export_archival_project_jira_csv,
    export_project_to_jira_csv,
    export_team_project_jira_csv,
    get_jira_migration_decisions,
    import_jira_task_keys,
    search_jira_external_ids,
    sync_jira_attachments,
    update_atlassian_user_map,
    import_jira_project_map,
    set_project_last_updated_jira_field,
)
from src.lib.config import (  # noqa: E402
    Credentials,
    file_has_plaintext_tokens,
    has_active_marker,
    list_project_profiles,
    load_credentials,
    migrate_home_profiles_into_project,
    migrate_tokens_to_keychain,
    profile_slug,
    project_profile_path,
    read_active_slug,
    read_profile_name,
    read_profile_jira_urls,
    resolve_credentials_path,
    slugify,
    write_active_slug,
    write_credentials_file,
)
from src.lib.database import ASANA_JIRA_DB_PATH_ENV  # noqa: E402
from src.lib import keychain  # noqa: E402
from src.lib.credential_check import check_credentials  # noqa: E402
from src.lib.logging_utils import configure_logging, resolve_log_level  # noqa: E402


LOG_DIR = PROJECT_ROOT / "exports" / "interactive_runs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: str
    handler: Callable[[], None]


@dataclass
class CliSession:
    """Mutable per-run state: which profile is active and the Jira environment.

    The profile is also persisted to the project pointer file (the source of
    truth invoked scripts read); this object drives the on-screen header, the
    Sandbox/Production toggle, and the per-profile database path exported to
    in-process scripts via ``ASANA_JIRA_DB_PATH``.
    """

    reference_path: Path
    env_path: Path
    client_name: Optional[str] = None
    use_prod: bool = False
    jira_base_url: Optional[str] = None
    jira_sandbox_base_url: Optional[str] = None

    @property
    def db_path(self) -> Path:
        return db_path_for_env(self.env_path)

    @property
    def profile_label(self) -> str:
        return self.client_name or profile_slug(self.env_path) or "default"

    @property
    def environment_label(self) -> str:
        return "PRODUCTION" if self.use_prod else "SANDBOX"

    @property
    def environment_url(self) -> Optional[str]:
        return self.jira_base_url if self.use_prod else self.jira_sandbox_base_url


# Set once in main(); read by the args builders and DB helpers below.
_session: Optional[CliSession] = None


def db_path_for_env(env_path: Path) -> Path:
    """Per-profile SQLite path: the default profile keeps data/asana.sqlite."""
    slug = profile_slug(env_path)
    name = "asana.sqlite" if not slug else f"asana_{slug}.sqlite"
    return PROJECT_ROOT / "data" / name


def _apply_session_env() -> None:
    """Point in-process scripts at the active profile's database."""
    if _session is not None:
        os.environ[ASANA_JIRA_DB_PATH_ENV] = str(_session.db_path)


def _set_session(reference_path: Path, env_path: Path, *, use_prod: Optional[bool] = None) -> CliSession:
    """(Re)build the module session from an env file and refresh the DB env var."""
    global _session
    prod = use_prod if use_prod is not None else (_session.use_prod if _session else False)
    base_url, sandbox_url = read_profile_jira_urls(env_path)
    _session = CliSession(
        reference_path=reference_path,
        env_path=env_path,
        client_name=read_profile_name(env_path),
        use_prod=prod,
        jira_base_url=base_url,
        jira_sandbox_base_url=sandbox_url,
    )
    _apply_session_env()
    return _session


def offer_legacy_db_adoption() -> None:
    """Offer to reuse a pre-profiles ``data/asana.sqlite`` for the active profile.

    When a named profile has no database yet but the legacy default database exists,
    adopting (renaming) it avoids a full re-sync after upgrading to per-profile DBs.
    The default profile already uses ``data/asana.sqlite``, so it is never offered.
    """
    if _session is None:
        return
    legacy = PROJECT_ROOT / "data" / "asana.sqlite"
    target = _session.db_path
    if target == legacy or target.exists() or not legacy.exists():
        return
    size_mb = legacy.stat().st_size / (1024 * 1024)
    print(
        f"\nNo database exists yet for profile '{_session.profile_label}' "
        f"({target.name}),\nbut a legacy {legacy} ({size_mb:.0f} MB) exists from before "
        "per-profile databases."
    )
    if not prompt_bool(
        "Adopt it for this profile (rename it — no re-sync needed)?", default=True
    ):
        return
    legacy.rename(target)
    print(f"Adopted {legacy.name} -> {target.name}; this profile's data is ready.")


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


def active_db_path() -> Path:
    """The active profile's SQLite path (falls back to the default profile)."""
    if _session is not None:
        return _session.db_path
    return PROJECT_ROOT / "data" / "asana.sqlite"


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
    db_path = active_db_path()
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


def _prompt_list_file(
    context: str,
    item_label: str,
    suffixes: Optional[Set[str]] = None,
) -> str:
    """Select a matching file from the repository root or accept a typed path."""
    search_dir = PROJECT_ROOT
    allowed_suffixes = suffixes or {".txt", ".csv", ".list"}
    candidates = sorted(
        (
            path
            for path in search_dir.iterdir()
            if path.is_file() and path.suffix.lower() in allowed_suffixes
        ),
        key=lambda path: path.name.lower(),
    )
    print(f"\n{item_label} files in {search_dir}:")
    if candidates:
        for index, path in enumerate(candidates, start=1):
            print(f"[{index}] {path.name}")
    else:
        print("  No .txt, .csv, or .list files found.")

    selection = prompt(
        f"Pick a file number for {context}, or enter a path",
        default="",
    )
    if selection.isdigit() and 1 <= int(selection) <= len(candidates):
        path = candidates[int(selection) - 1]
    else:
        path = Path(selection).expanduser()
        if not path.is_absolute():
            path = search_dir / path
    if not selection or not path.is_file():
        raise SystemExit(f"{item_label} file not found: {selection or '(blank)'}")
    return str(path.resolve())


def _prompt_project_gids_file(context: str) -> str:
    return _prompt_list_file(context, "Project GID")


def _prompt_jira_keys_file(context: str) -> str:
    return _prompt_list_file(context, "Jira key")


def _prompt_csv_file(context: str, item_label: str) -> str:
    return _prompt_list_file(context, item_label, {".csv"})


def confirm(text: str, default: bool = True) -> bool:
    return prompt_bool(text, default=default)


def append_jira_env_flag(args: List[str]) -> None:
    """Append --prod when the session targets Production; default is the sandbox URL.

    The Sandbox/Production choice is a session-level toggle (see the "Profile &
    Environment" menu and the header), so Jira commands no longer prompt per-run.
    The base URL itself comes from the credentials file (JIRA_BASE_URL /
    JIRA_SANDBOX_BASE_URL).
    """
    if _session is not None and _session.use_prod:
        args.append("--prod")


def prompt_credentials_values() -> Credentials:
    token = prompt_required("ASANA_TOKEN")
    workspace_gid = prompt_required("ASANA_WORKSPACE_GID")
    skip_ssl_verification = prompt_bool("Skip SSL verification?", default=False)
    atlassian_username = prompt("ATLASSIAN_USERNAME (optional)", default="")
    atlassian_token = prompt("ATLASSIAN_TOKEN (optional)", default="")
    jira_base_url = prompt("JIRA_BASE_URL — production Jira site (optional)", default="")
    jira_sandbox_base_url = prompt("JIRA_SANDBOX_BASE_URL — sandbox Jira site (optional)", default="")
    return Credentials(
        token=token,
        workspace_gid=workspace_gid,
        skip_ssl_verification=skip_ssl_verification,
        atlassian_username=atlassian_username or None,
        atlassian_token=atlassian_token or None,
        jira_base_url=jira_base_url or None,
        jira_sandbox_base_url=jira_sandbox_base_url or None,
    )


def report_credential_checks(credentials: Credentials) -> bool:
    """Validate the tokens live and print per-service results.

    Returns True when it is safe to save — either every check passed, or the user
    explicitly chose to save despite failures (e.g. they are offline)."""
    print("\nValidating credentials...")
    results = check_credentials(credentials)
    any_failed = False
    for result in results:
        status = "OK  " if result.ok else "FAIL"
        print(f"  [{status}] {result.service}: {result.detail}")
        any_failed = any_failed or not result.ok
    if not any_failed:
        return True
    return prompt_bool("One or more checks failed. Save anyway?", default=False)


def prompt_validated_credentials() -> Credentials:
    """Prompt for all credential values, re-prompting until they validate (or save-anyway)."""
    credentials = prompt_credentials_values()
    while not report_credential_checks(credentials):
        if not prompt_bool("Re-enter credentials and try again?", default=True):
            raise SystemExit("Credentials file was not written.")
        credentials = prompt_credentials_values()
    return credentials


# Credentials dataclass field -> (env var name, kind) for the view/edit flow.
# kind: "required" text, "optional" text, "bool", or "secret" (masked on display).
CREDENTIAL_FIELDS = [
    ("token", "ASANA_TOKEN", "secret"),
    ("workspace_gid", "ASANA_WORKSPACE_GID", "required"),
    ("skip_ssl_verification", "SKIP_SSL_VERIFICATION", "bool"),
    ("atlassian_username", "ATLASSIAN_USERNAME", "optional"),
    ("atlassian_token", "ATLASSIAN_TOKEN", "secret"),
    ("jira_base_url", "JIRA_BASE_URL", "optional"),
    ("jira_sandbox_base_url", "JIRA_SANDBOX_BASE_URL", "optional"),
]


def _display_credential_value(kind: str, value) -> str:
    if kind == "bool":
        return "true" if value else "false"
    if not value:
        return "(not set)"
    if kind == "secret":
        return "******** (set)"
    return str(value)


def _edit_credential_field(field: str, env_name: str, kind: str, values: Dict[str, object]) -> None:
    if kind == "bool":
        values[field] = prompt_bool(f"{env_name}?", default=bool(values[field]))
        return
    if kind == "secret":
        new = prompt(f"New {env_name} (blank = keep current)", default="")
        if new:
            values[field] = new
        return
    if kind == "required":
        new = prompt(env_name, default=str(values[field] or ""))
        if new:
            values[field] = new
        else:
            print(f"{env_name} is required; keeping the current value.")
        return
    # optional text: blank keeps current, '-' clears it
    new = prompt(f"{env_name} (blank = keep, '-' = clear)", default="")
    if new == "-":
        values[field] = None
    elif new:
        values[field] = new


def edit_existing_credentials(env_path: Path, current: Credentials) -> Path:
    """Show current credentials (tokens masked) and let the user update individual keys."""
    values: Dict[str, object] = {field: getattr(current, field) for field, _, _ in CREDENTIAL_FIELDS}
    # client_name is not edited here (it names the profile/file); preserve it on save.
    values["client_name"] = current.client_name
    secret_backend = "Keychain" if keychain.is_available() else "env file"
    profile_label = current.client_name or env_path.name
    dirty = False
    while True:
        print(f"\nCredentials for profile '{profile_label}' in {env_path} (tokens stored in: {secret_backend}):")
        for index, (field, env_name, kind) in enumerate(CREDENTIAL_FIELDS, start=1):
            display = _display_credential_value(kind, values[field])
            if kind == "secret" and values[field]:
                display = f"******** (set, {secret_backend})"
            print(f"  [{index}] {env_name} = {display}")
        choice = input("Select a number to update, 's' to save, or 'q' to cancel: ").strip().lower()
        if choice == "q":
            print("No changes saved.")
            return env_path
        if choice == "s":
            if not dirty:
                print("No changes to save.")
                return env_path
            candidate = Credentials(**values)
            if not report_credential_checks(candidate):
                print("Not saved. Fix the flagged values and try again, or 'q' to cancel.")
                continue
            write_credentials_file(env_path, candidate)
            print(f"Updated credentials saved to {env_path}.")
            return env_path
        if choice.isdigit() and 1 <= int(choice) <= len(CREDENTIAL_FIELDS):
            field, env_name, kind = CREDENTIAL_FIELDS[int(choice) - 1]
            _edit_credential_field(field, env_name, kind, values)
            dirty = True
        else:
            print("Invalid choice. Enter a number, 's', or 'q'.")


def manage_credentials(reference_path: Path = PROJECT_ROOT) -> Path:
    """Create credentials when none are found; otherwise view/update the existing file."""
    env_path = resolve_credentials_path(reference_path)
    if not env_path.exists():
        print(f"\nNo credentials file found at {env_path}. Let's walk through creating one.")
        return bootstrap_credentials(reference_path)
    offer_token_migration(env_path)
    try:
        current = load_credentials(reference_path)
    except (KeyError, ValueError) as exc:
        print(f"\nCredentials at {env_path} are incomplete or invalid ({exc}).")
        if prompt_bool("Recreate the file from scratch?", default=True):
            return bootstrap_credentials(reference_path)
        raise SystemExit("Credentials were not changed.")
    return edit_existing_credentials(env_path, current)


def offer_token_migration(env_path: Path) -> bool:
    """Prompt to move plaintext tokens from the env file into the macOS Keychain.

    Returns True when a migration was performed. No-op when the Keychain is
    unavailable or the file holds no tokens."""
    if not keychain.is_available() or not file_has_plaintext_tokens(env_path):
        return False
    print(f"\n{env_path} still stores API token(s) in plaintext.")
    if not prompt_bool("Move them into the macOS Keychain now? (recommended)", default=True):
        return False
    migrated = migrate_tokens_to_keychain(env_path)
    if migrated:
        print(f"Moved {', '.join(migrated)} into the Keychain and removed them from {env_path}.")
    return bool(migrated)


def print_welcome() -> None:
    """First-run dialogue explaining what's required before the toolkit can run."""
    print(
        "\n"
        "============================================================\n"
        " Welcome to the Asana -> Jira Migration Toolkit\n"
        "============================================================\n"
        "\n"
        "No client profile is set up yet. A profile holds the credentials\n"
        "for one Asana workspace (and, optionally, its Atlassian/Jira site).\n"
        "\n"
        "To get started you'll need:\n"
        "  REQUIRED\n"
        "   - ASANA_TOKEN          your Asana personal access token\n"
        "   - ASANA_WORKSPACE_GID  the workspace to migrate\n"
        "\n"
        "  OPTIONAL (only for the Jira migration steps)\n"
        "   - ATLASSIAN_USERNAME / ATLASSIAN_TOKEN  Atlassian API credentials\n"
        "   - JIRA_BASE_URL / JIRA_SANDBOX_BASE_URL  your Jira sites\n"
        "\n"
        "On macOS the two tokens are stored securely in the Keychain; the\n"
        "rest is saved to a per-profile file in the project (profiles/<name>.env).\n"
        "Each profile also gets its own database (data/asana_<name>.sqlite).\n"
        "\n"
        "Let's create your first profile.\n"
    )


def bootstrap_credentials(reference_path: Path = PROJECT_ROOT) -> Path:
    """Create a new, client-named profile and make it the active one."""
    name = prompt_required("Client / profile name")
    slug = slugify(name)
    env_path = project_profile_path(slug, reference_path)
    if env_path.exists() and not prompt_bool(
        f"A profile already exists at {env_path}. Overwrite it?", default=False
    ):
        raise SystemExit("Credentials file was not updated.")
    credentials = replace(prompt_validated_credentials(), client_name=name)
    write_credentials_file(env_path, credentials)
    write_active_slug(slug, reference_path)
    _set_session(reference_path, env_path)
    print(
        f"\nProfile '{name}' saved to {env_path} and set as the active profile.\n"
        f"Its data will sync into {db_path_for_env(env_path)}."
    )
    offer_legacy_db_adoption()
    return env_path


def run_startup_migration(reference_path: Path = PROJECT_ROOT) -> None:
    """One-time relocation of home profiles (~/.asana_jira_env*) into profiles/.

    Copies each home profile into ``profiles/<slug>.env`` and re-keys its Keychain
    secrets (see ``migrate_home_profiles_into_project``). When no active profile is
    selected yet, adopts a sensible one so the toolkit comes up on the right profile.
    """
    migrated = migrate_home_profiles_into_project(reference_path)
    if migrated:
        print("\nMoved existing profiles into the project:")
        for _slug, home_path, project_path in migrated:
            print(f"  {home_path}  ->  {project_path}")
        print(
            "(The originals in your home directory were left untouched; you can "
            "remove them once you've confirmed everything works.)"
        )

    if has_active_marker(reference_path):
        return
    profiles = list_project_profiles(reference_path)
    if not profiles:
        return
    if len(profiles) == 1:
        slug = profile_slug(profiles[0][0])
        write_active_slug(slug, reference_path)
        return
    # Prefer the default profile when present; otherwise let the user choose.
    slugs = {profile_slug(path): path for path, _name in profiles}
    if "" in slugs:
        write_active_slug("", reference_path)
        return
    print("\nMultiple profiles found — pick the one to start with.")
    switch_profile(reference_path)


def ensure_interactive_credentials(reference_path: Path = PROJECT_ROOT) -> Path:
    """Resolve (or create) the active profile and return its env-file path.

    On a true first launch (no credentials file anywhere) we show the welcome
    dialogue and create a client-named profile. An incomplete/invalid existing
    file is left in place so the user can repair it from the Credentials menu.
    """
    try:
        load_credentials(reference_path)
    except FileNotFoundError:
        print_welcome()
        return bootstrap_credentials(reference_path)
    except (KeyError, ValueError) as exc:
        print(
            f"\nThe active credentials file is incomplete or invalid ({exc}). "
            "Use the Credentials menu to finish setting it up."
        )
    return resolve_credentials_path(reference_path)


def run_token_migration(reference_path: Path = PROJECT_ROOT) -> None:
    """Credentials-menu action: move plaintext tokens into the Keychain."""
    env_path = resolve_credentials_path(reference_path)
    if not keychain.is_available():
        print("The macOS Keychain is not available on this system; tokens stay in the env file.")
        return
    if not env_path.exists() or not file_has_plaintext_tokens(env_path):
        print(f"No plaintext tokens to migrate in {env_path}.")
        return
    offer_token_migration(env_path)


def build_credentials_menu() -> Dict[str, MenuItem]:
    return {
        "1": MenuItem("1", "View / update credentials (creates the file if none exists)", lambda: manage_credentials(PROJECT_ROOT)),
        "2": MenuItem("2", "Migrate tokens from env file to macOS Keychain", lambda: run_token_migration(PROJECT_ROOT)),
        "b": MenuItem("b", "Back", lambda: None),
    }


_ANSI_CODES = {
    "reset": "\033[0m",
    "bold": "\033[1m",
    "green": "\033[32m",
    "red": "\033[31m",
}


def _supports_color() -> bool:
    """Only emit ANSI codes when writing to an interactive terminal."""
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _colorize(text: str, *codes: str) -> str:
    if not _supports_color() or not codes:
        return text
    prefix = "".join(_ANSI_CODES[code] for code in codes)
    return f"{prefix}{text}{_ANSI_CODES['reset']}"


def print_session_header() -> None:
    """Show the active profile + Jira environment in a bordered box above the menu.

    The border is green for the sandbox and red for production, giving an
    at-a-glance cue about which Jira environment commands will target.
    """
    if _session is None:
        return
    env_value = _session.environment_label
    if _session.environment_url:
        env_value += f" ({_session.environment_url})"
    rows = [
        ("Profile", _session.profile_label),
        ("Environment", env_value),
        ("Database", _session.db_path.name),
    ]
    lines = [f"{label}: {value}" for label, value in rows]
    width = max(len(line) for line in lines)
    border_color = "red" if _session.use_prod else "green"

    top = _colorize("┌" + "─" * (width + 2) + "┐", border_color)
    bottom = _colorize("└" + "─" * (width + 2) + "┘", border_color)
    side = _colorize("│", border_color)

    print()
    print(top)
    for line in lines:
        print(f"{side} {line.ljust(width)} {side}")
    print(bottom)


def toggle_environment() -> None:
    if _session is None:
        return
    _session.use_prod = not _session.use_prod
    print(
        f"\nJira environment is now {_session.environment_label}. "
        "All Jira commands will target it until you change it again."
    )


def switch_profile(reference_path: Path = PROJECT_ROOT) -> None:
    """Pick another project profile (profiles/*.env) and make it active."""
    profiles = list_project_profiles(reference_path)
    active = _session.env_path if _session else resolve_credentials_path(reference_path)
    if not profiles:
        print(
            "\nNo profiles found in profiles/ yet. "
            "Use 'Create a new profile' to add one."
        )
        return
    print("\nAvailable profiles:")
    for index, (path, name) in enumerate(profiles, start=1):
        marker = " (active)" if path == active else ""
        label = name or path.name
        print(f"  [{index}] {label} — {path}{marker}")
    choice = prompt("Pick a number (Enter to cancel)", default="")
    if not choice:
        print("No profile selected.")
        return
    if not (choice.isdigit() and 1 <= int(choice) <= len(profiles)):
        print("Invalid selection.")
        return
    env_path = profiles[int(choice) - 1][0]

    write_active_slug(profile_slug(env_path), reference_path)
    session = _set_session(reference_path, env_path)
    print(f"\nActive profile is now '{session.profile_label}' ({env_path}).")
    offer_legacy_db_adoption()
    if not session.db_path.exists():
        print(
            f"Note: {session.db_path} does not exist yet — run a sync for this "
            "profile before using the Tasks/Reports/Atlassian menus."
        )


def build_profile_env_menu(reference_path: Path = PROJECT_ROOT) -> Dict[str, MenuItem]:
    return {
        "1": MenuItem("1", "Toggle Jira environment (Sandbox <-> Production)", toggle_environment),
        "2": MenuItem("2", "Switch active profile", lambda: switch_profile(reference_path)),
        "3": MenuItem("3", "Create a new profile", lambda: bootstrap_credentials(reference_path)),
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
        "--project-gids-file",
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

    add_args = build_common_args(log_level)
    add_args.extend(["--project-gids", ",".join(missed_project_gids)])
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
    # Prefix the per-profile database so a standalone re-run targets the same DB.
    if _session is not None and _session.db_path != PROJECT_ROOT / "data" / "asana.sqlite":
        cmd = f"{ASANA_JIRA_DB_PATH_ENV}={shlex.quote(str(_session.db_path))} {cmd}"
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
    print("[2] Team lookup (fuzzy search)")
    print("[3] Project GIDs (comma-separated)")
    print("[4] Project GIDs file")
    choice = input("Choice (default 2): ").strip() or "2"
    if choice == "1":
        args.append("--all-teams")
        if confirm("Resume from last all-team run if progress file exists?", default=True):
            args.append("--resume-all-teams")
        # Allow custom state file?
        # Use default path from get_tasks; advanced users can edit CLI args manually if needed.
    elif choice == "2":
        team_gid = _prompt_team_gid_from_lookup(
            "task sync",
            "Team GID to sync",
        )
        if not team_gid:
            raise SystemExit("Team GID is required for team sync.")
        args.extend(["--team-gids", team_gid])
    elif choice == "3":
        project_gids = prompt("Project GIDs (comma-separated)", default="")
        if not project_gids:
            raise SystemExit("At least one project GID is required.")
        args.extend(["--project-gids", project_gids])
    else:
        args.extend(["--project-gids-file", _prompt_project_gids_file("task sync")])

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
    print("[1] Team lookup (fuzzy search)")
    print("[2] Project GIDs (comma-separated)")
    print("[3] Project GIDs file")
    print("[4] All teams")
    print("[5] Personal tasks (all users)")
    print("[6] Personal tasks (specific assignees)")
    choice = input("Choice (default 1): ").strip() or "1"
    if choice == "1":
        team_gid = _prompt_team_gid_from_lookup(
            "task extras",
            "Team GID for task extras",
        )
        if not team_gid:
            raise SystemExit("Team GID is required.")
        args.extend(["--team-gids", team_gid])
    elif choice == "2":
        project_gids = prompt("Project GIDs (comma-separated)", default="")
        if not project_gids:
            raise SystemExit("At least one project GID is required.")
        args.extend(["--project-gids", project_gids])
    elif choice == "3":
        args.extend(["--project-gids-file", _prompt_project_gids_file("task extras")])
    elif choice == "4":
        args.append("--all-teams")
    elif choice == "5":
        args.append("--personal")
    else:
        args.append("--personal")
        assignees = prompt("Comma-separated user GIDs for personal tasks", default="")
        if not assignees:
            raise SystemExit("At least one assignee GID is required for option 6.")
        args.extend(["--personal-assignees", assignees])
    return args


def args_get_project_status_updates(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    print("\nSelect project status-update scope:")
    print("[1] Team lookup (fuzzy search)")
    print("[2] Project GIDs (comma-separated)")
    print("[3] Project GIDs file")
    print("[4] All teams")
    choice = input("Choice (default 1): ").strip() or "1"
    if choice == "1":
        team_gid = _prompt_team_gid_from_lookup(
            "project status updates",
            "Team GID for status updates",
        )
        if not team_gid:
            raise SystemExit("Team GID is required.")
        args.extend(["--team-gids", team_gid])
    elif choice == "2":
        gids = prompt("Project GIDs (comma-separated)", default="")
        if not gids:
            raise SystemExit("At least one project GID is required.")
        args.extend(["--project-gids", gids])
    elif choice == "3":
        args.extend(["--project-gids-file", _prompt_project_gids_file("project status updates")])
    elif choice == "4":
        args.append("--all-teams")
        excluded = prompt("Exclude team GIDs (comma-separated, optional)", default="")
        if excluded:
            args.extend(["--exclude-team-gids", excluded])
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
        print("[1] Team lookup (fuzzy search)")
        print("[2] Project GIDs (comma-separated)")
        print("[3] Project GIDs file")
        choice = input("Choice (default 1): ").strip() or "1"
        if choice == "1":
            team_gid = _prompt_team_gid_from_lookup(
                "incremental sync",
                "Team GID to sync",
            )
            if not team_gid:
                raise SystemExit("Team GID is required.")
            args.extend(["--team-gids", team_gid])
            multi_scope = True
        elif choice == "2":
            project_gids = prompt("Project GIDs (comma-separated)", default="")
            if not project_gids:
                raise SystemExit("At least one project GID is required.")
            args.extend(["--project-gids", project_gids])
            multi_scope = True
        else:
            args.extend(
                ["--project-gids-file", _prompt_project_gids_file("incremental sync")]
            )
            multi_scope = True

    if multi_scope:
        if confirm("Show per-project breakdown after the run?", default=False):
            args.extend(["--project-breakdown", "yes"])
        else:
            args.extend(["--project-breakdown", "no"])
    return args


def args_export_project_to_jira(log_level: str) -> tuple[List[str], bool]:
    args = build_common_args(log_level)
    db_path = active_db_path()
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
            args.extend(
                ["--project-gids-file", _prompt_project_gids_file("Jira CSV export")]
            )
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
    append_jira_env_flag(args)
    fallback_atl = prompt("Fallback Atlassian account ID for unmapped users (optional)", default="")
    if fallback_atl:
        args.extend(["--fallback-atlassian-id", fallback_atl])
    export_id = prompt("Export ID to record/skip previously exported tasks (optional)", default="")
    if export_id:
        args.extend(["--export-id", export_id])
    return args, allow_merge


def args_export_archival_project(log_level: str) -> List[str]:
    args = build_common_args(log_level)

    print("\nSelect export scope:")
    print("[1] Single project")
    print("[2] Multiple projects (comma-separated GIDs)")
    print("[3] All projects in a team")
    choice = input("Choice (default 1): ").strip() or "1"
    if choice == "1":
        project_gid = _prompt_project_gid_from_lookup(
            "archival export", "Project GID to export"
        )
        if not project_gid:
            raise SystemExit("Project GID is required.")
        args.extend(["--project-gid", project_gid])
    elif choice == "2":
        if confirm("Load project GIDs from a file?", default=False):
            args.extend(
                ["--project-gids-file", _prompt_project_gids_file("archival Jira CSV export")]
            )
        else:
            gids = prompt("Project GIDs (comma-separated)", default="")
            if not gids:
                raise SystemExit("At least one project GID is required.")
            args.extend(["--project-gids", gids])
    else:
        team_gid = _prompt_team_gid_from_lookup(
            "archival export", "Team GID whose projects to export"
        )
        if not team_gid:
            raise SystemExit("Team GID is required.")
        args.extend(["--team-gid", team_gid])
        scope = prompt("Archived scope (active|archived|all) [all]", default="all")
        args.extend(["--archived-scope", scope])

    append_jira_env_flag(args)
    fallback_atl = prompt("Fallback Atlassian account ID for unmapped users (optional)", default="")
    if fallback_atl:
        args.extend(["--fallback-atlassian-id", fallback_atl])
    return args


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

    def run_portfolio_report() -> None:
        args = args_report_defaults(log_level)
        if not confirm("Include the portfolio→items companion CSV?", default=True):
            args.append("--summary-only")
        run_script(export_portfolio_report.main, args)

    def run_project_members_report() -> None:
        args = args_report_defaults(log_level)
        team_gid = _prompt_team_gid_from_lookup(
            "project members",
            "Team GID to scope project members (blank = all teams)",
        )
        if team_gid:
            args.extend(["--team-gid", team_gid])
        run_script(export_project_members_report.main, args)

    def run_field_section_review_report() -> None:
        args = args_report_defaults(log_level)
        print("\nScope the field & section review:")
        print("[1] Team-centric (one team's projects)")
        print("[2] Owner-centric (one project owner's projects)")
        print("[3] Whole workspace")
        print("[4] By Jira key(s) (projects behind migrated Jira Task keys)")
        choice = input("Choice (default 1): ").strip() or "1"
        if choice == "1":
            team_gid = _prompt_team_gid_from_lookup(
                "field & section review",
                "Team GID to scope the review (blank = all teams)",
            )
            if team_gid:
                args.extend(["--team-gid", team_gid])
        elif choice == "2":
            owner_gid = _prompt_user_gid_from_lookup(
                "field & section review",
                "Project-owner user GID to scope the review (blank = all owners)",
            )
            if owner_gid:
                args.extend(["--owner-gid", owner_gid])
        elif choice == "4":
            print("\nProvide Jira keys:")
            print("[1] Paste Jira keys (comma/space separated)")
            print("[2] Jira keys file")
            jira_scope = input("Choice (default 1): ").strip() or "1"
            if jira_scope == "1":
                jira_keys = input(
                    "Jira Task keys (comma/space separated, e.g. ATJM-1, ATJM-2): "
                ).strip()
                if not jira_keys:
                    raise SystemExit("At least one Jira key is required.")
                args.extend(["--jira-keys", jira_keys])
            else:
                args.extend(
                    [
                        "--jira-keys-file",
                        _prompt_jira_keys_file("field & section review"),
                    ]
                )
        run_script(export_field_section_review_report.main, args)

    def run_project_last_updated_report() -> None:
        args = args_report_defaults(log_level)
        team_gid = _prompt_team_gid_from_lookup(
            "project last updated",
            "Team GID to scope the report (blank = all teams)",
        )
        if team_gid:
            args.extend(["--team-gid", team_gid])
        run_script(export_project_last_updated_report.main, args)

    return {
        "1": MenuItem(
            "1",
            "Export initial metadata report (.xlsx)",
            lambda: run_script(export_all_reports_xlsx.main, args_report_defaults(log_level)),
        ),
        "2": MenuItem(
            "2",
            "Export field & section migration review (.xlsx)",
            run_field_section_review_report,
        ),
        "3": MenuItem(
            "3",
            "Export project details report",
            run_project_details_report,
        ),
        "4": MenuItem(
            "4",
            "Export users report",
            lambda: run_script(export_users.main, args_report_defaults(log_level)),
        ),
        "5": MenuItem(
            "5",
            "Export team memberships report",
            run_team_memberships_report,
        ),
        "6": MenuItem(
            "6",
            "Export team report",
            run_team_report,
        ),
        "7": MenuItem(
            "7",
            "Export custom field usage report",
            run_custom_field_usage_report,
        ),
        "8": MenuItem(
            "8",
            "Export portfolio report (summary + items)",
            run_portfolio_report,
        ),
        "9": MenuItem(
            "9",
            "Export project members report (.xlsx)",
            run_project_members_report,
        ),
        "a": MenuItem(
            "a",
            "Export project last-updated report",
            run_project_last_updated_report,
        ),
        "b": MenuItem("b", "Back", lambda: None),
    }


def args_import_jira_keys(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    csv_path = _prompt_csv_file("Jira task-key import", "Jira export CSV")
    args.extend(["--csv", csv_path])
    task_column = prompt("Task ID column name", default="External issue ID")
    if task_column:
        args.extend(["--task-column", task_column])
    jira_column = prompt("Jira key column name", default="Issue key")
    if jira_column:
        args.extend(["--jira-column", jira_column])
    return args


def args_search_jira_external_ids(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    append_jira_env_flag(args)
    jql = prompt_required(
        'JQL (for example: project = ENG AND "External issue ID" is not EMPTY)',
    )
    args.extend(["--jql", jql])
    external_id_field = prompt_required(
        "External ID field key (for example: customfield_10001)",
    )
    args.extend(["--external-id-field", external_id_field])
    max_results = prompt("Results per Jira page", default="500")
    if max_results:
        args.extend(["--max-results", max_results])
    if confirm("Dry run (fetch and print mappings, write nothing)?", default=True):
        args.append("--dry-run")
    return args


def args_import_jira_project_map(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    csv_path = _prompt_csv_file("Jira project association import", "Project association CSV")
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
    csv_path = _prompt_csv_file(
        "Atlassian user-map update",
        "Atlassian user-map CSV",
    )
    args.extend(["--csv", csv_path])
    return args


def args_sync_attachments(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    print("\nProvide attachment-sync project scope:")
    print("[1] Project GID(s) (comma-separated)")
    print("[2] Project GIDs file")
    choice = input("Choice (default 1): ").strip() or "1"
    if choice == "2":
        args.extend(
            ["--project-gids-file", _prompt_project_gids_file("Jira attachment sync")]
        )
    else:
        project_gids = prompt(
            "Project GID(s) to sync attachments for (comma-separated allowed)",
            default="",
        )
        if not project_gids:
            raise SystemExit("At least one project GID is required.")
        if "," in project_gids:
            args.extend(["--project-gids", project_gids])
        else:
            args.extend(["--project-gid", project_gids])
    append_jira_env_flag(args)
    if confirm("Dry run (no uploads)?", default=True):
        args.append("--dry-run")
    return args


def args_create_team_project_jira_issues(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    jira_project_key = prompt("Target Jira project key (e.g. MIG)", default="")
    if not jira_project_key:
        raise SystemExit("Jira project key is required.")
    args.extend(["--jira-project-key", jira_project_key])
    append_jira_env_flag(args)
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


def args_set_project_last_updated_jira_field(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    append_jira_env_flag(args)
    team_gid = _prompt_team_gid_from_lookup(
        "project last-updated Jira field",
        "Team GID to scope the update (blank = all teams)",
    )
    if team_gid:
        args.extend(["--team-gid", team_gid])
    if confirm("Dry run (print payloads, update nothing)?", default=True):
        args.append("--dry-run")
    return args


def args_get_jira_migration_decisions(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    append_jira_env_flag(args)
    jql = prompt(
        "JQL (blank = derive from stored project keys: project IN (...) AND issuetype = Task AND field is not EMPTY)",
        default="",
    )
    if jql:
        args.extend(["--jql", jql])
    field_name = prompt("Decision field name", default="Asana Migration Decision")
    if field_name:
        args.extend(["--field-name", field_name])
    if confirm("Store mapped issues even when the decision is blank?", default=False):
        args.append("--include-empty")
    if confirm("Dry run (fetch and print decisions, write nothing)?", default=True):
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
    def run_team_project_jira_export() -> None:
        args = args_report_defaults(log_level)
        if confirm("Include projects with 0 tasks?", default=False):
            args.append("--include-empty-projects")
        run_script(export_team_project_jira_csv.main, args)

    return {
        "1": MenuItem("1", "Export project to Jira CSV", lambda: run_export_project_to_jira(log_level)),
        "2": MenuItem("2", "Export team/project Jira CSV", run_team_project_jira_export),
        "3": MenuItem("3", "Import Jira task keys", lambda: run_script(import_jira_task_keys.main, args_import_jira_keys(log_level))),
        "4": MenuItem("4", "Clear Jira task keys", lambda: run_script(clear_jira_task_keys.main, args_clear_jira_keys(log_level))),
        "5": MenuItem(
            "5",
            "Update Atlassian user map (template: Sample Data/atlassian_user_map_template.csv)",
            lambda: run_script(update_atlassian_user_map.main, args_update_user_map(log_level)),
        ),
        "6": MenuItem("6", "Import Jira project associations", lambda: run_script(import_jira_project_map.main, args_import_jira_project_map(log_level))),
        "7": MenuItem("7", "Clear export IDs", lambda: run_script(export_project_to_jira_csv.main, args_clear_export_ids(log_level))),
        "8": MenuItem("8", "Clear Jira project associations", lambda: run_script(export_project_to_jira_csv.main, build_common_args(log_level) + ["--clear-jira-project-map"])),
        "9": MenuItem("9", "Sync Jira attachments", lambda: run_sync_attachments(log_level)),
        "10": MenuItem(
            "10",
            "Create team/project Jira issues (direct API)",
            lambda: run_script(create_team_project_jira_issues.main, args_create_team_project_jira_issues(log_level)),
        ),
        "11": MenuItem(
            "11",
            "Pull 'Asana Migration Decision' from Jira (project-level)",
            lambda: run_script(get_jira_migration_decisions.main, args_get_jira_migration_decisions(log_level)),
        ),
        "12": MenuItem(
            "12",
            "Set project 'Last working day' field on Jira issues",
            lambda: run_script(set_project_last_updated_jira_field.main, args_set_project_last_updated_jira_field(log_level)),
        ),
        "13": MenuItem(
            "13",
            "Export archival project to Jira CSV (custom fields folded into description)",
            lambda: run_script(export_archival_project_jira_csv.main, args_export_archival_project(log_level)),
        ),
        "14": MenuItem(
            "14",
            "Search Jira for External IDs (JQL)",
            lambda: run_script(
                search_jira_external_ids.main,
                args_search_jira_external_ids(log_level),
            ),
        ),
        "b": MenuItem("b", "Back", lambda: None),
    }


def _menu_sort_key(k: str) -> tuple:
    """Order menu keys: numeric first (numerically), then other letters, then Back, then Quit.

    Every tuple element is compared only against the same type, so a mixed digit/letter
    menu (e.g. keys "1".."9", "a", "b", "q") never triggers a str-vs-int comparison.
    """
    if k == "q":
        return (3, 0, "")
    if k == "b":
        return (2, 0, "")
    if k.isdigit():
        return (0, int(k), "")
    return (1, 0, k)


def print_menu(title: str, menu: Dict[str, MenuItem]) -> None:
    print(f"\n=== {title} ===")
    for key in sorted(menu, key=_menu_sort_key):
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
    run_startup_migration(PROJECT_ROOT)
    env_path = ensure_interactive_credentials(PROJECT_ROOT)
    if _session is None:
        # Existing profile (ensure didn't bootstrap, which sets the session itself).
        _set_session(PROJECT_ROOT, env_path, use_prod=False)
        offer_legacy_db_adoption()
    top_menu = {
        "1": MenuItem("1", "Initialization (metadata only)", lambda: run_group("Initialization", build_init_menu(args.log_level))),
        "2": MenuItem("2", "Tasks (full/extras/incremental)", lambda: run_group("Tasks", build_tasks_menu(args.log_level))),
        "3": MenuItem("3", "Atlassian (exports, mappings, attachments)", lambda: run_group("Atlassian", build_atlassian_menu(args.log_level))),
        "4": MenuItem("4", "Reports (projects, users, memberships, team summary, portfolios)", lambda: run_group("Reports", build_reports_menu(args.log_level))),
        "5": MenuItem("5", "Credentials (create or change env file)", lambda: run_group("Credentials", build_credentials_menu())),
        "6": MenuItem("6", "Profile & Environment (switch profile, toggle Sandbox/Production)", lambda: run_group("Profile & Environment", build_profile_env_menu(PROJECT_ROOT))),
        "q": MenuItem("q", "Quit", lambda: sys.exit(0)),
    }

    while True:
        print_session_header()
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
