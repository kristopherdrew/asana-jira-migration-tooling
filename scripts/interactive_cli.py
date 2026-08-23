#!/usr/bin/env python3
"""Interactive driver that orchestrates the existing scripts without breaking their standalone entrypoints."""

from __future__ import annotations

import argparse
import builtins
import json
import logging
import os
import sqlite3
import sys
import shlex
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

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
    download_asana_attachments,
    upload_jira_attachments,
)
from scripts import (  # noqa: E402
    export_project_details_report,
    export_team_memberships,
    export_custom_field_usage_report,
    export_portfolio_report,
    export_field_section_review_report,
    export_project_members_report,
    export_project_last_updated_report,
    export_jira_migration_validation,
    export_all_reports_xlsx,
)
from scripts.atlassian import (  # noqa: E402
    clear_jira_task_keys,
    add_service_user_to_teams,
    add_service_user_to_projects,
    create_jira_import_fields,
    create_team_project_jira_issues,
    export_archival_project_jira_csv,
    export_project_to_jira_csv,
    export_team_project_jira_csv,
    generate_jira_csv_import_config,
    get_jira_migration_decisions,
    search_jira_external_ids,
    update_atlassian_user_map,
    import_jira_project_map,
    set_project_last_updated_jira_field,
)
from scripts.deprecated import sync_jira_attachments  # noqa: E402
from src.lib.config import (  # noqa: E402
    Credentials,
    JiraSite,
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
    read_profile_jira_sites,
    resolve_credentials_path,
    slugify,
    write_active_slug,
    write_credentials_file,
    write_profile_default_jira_site,
    write_profile_jira_sites,
)
from src.lib.database import ASANA_JIRA_DB_PATH_ENV, ASANA_JIRA_SITE_KEY_ENV  # noqa: E402
from src.lib import keychain  # noqa: E402
from src.lib.credential_check import check_credentials  # noqa: E402
from src.lib.http_client import AsanaHttpClient  # noqa: E402
from src.lib.logging_utils import configure_logging, resolve_log_level  # noqa: E402
from src.lib.jira_csv_import_config import clear_archive_import_settings, latest_profile_import_config_path, load_archive_import_settings, save_archive_import_settings, stale_profile_import_config_paths  # noqa: E402
from src.lib.project_scope import parse_gid_list, read_project_gids_file  # noqa: E402


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
    jira_sites: Tuple[JiraSite, ...] = ()
    jira_site_key: Optional[str] = None
    default_jira_site_key: Optional[str] = None

    @property
    def db_path(self) -> Path:
        return db_path_for_env(self.env_path)

    @property
    def profile_label(self) -> str:
        return self.client_name or profile_slug(self.env_path) or "default"

    @property
    def environment_label(self) -> str:
        if self.active_jira_site:
            return self.active_jira_site.name
        return "PRODUCTION" if self.use_prod else "SANDBOX"

    @property
    def environment_url(self) -> Optional[str]:
        if self.active_jira_site:
            return self.active_jira_site.base_url
        return self.jira_base_url if self.use_prod else self.jira_sandbox_base_url

    @property
    def active_jira_site(self) -> Optional[JiraSite]:
        return next((site for site in self.jira_sites if site.key == self.jira_site_key), None)


# Set once in main(); read by the args builders and DB helpers below.
_session: Optional[CliSession] = None


class CliExit(Exception):
    """Signal a user-requested graceful exit from any interactive prompt."""


class CliBack(Exception):
    """Signal a user-requested return to the previous interactive menu."""


class CliCommandError(Exception):
    """Report an invoked command failure without ending the interactive session."""


_menu_depth = 0


def _with_navigation_hint(text: str) -> str:
    """Make the available global navigation actions visible on every prompt."""
    lowered = text.lower()
    actions = []
    if _menu_depth > 0 and "b to go back" not in lowered and "'b' to go back" not in lowered:
        actions.append("b to go back")
    if "q to quit" not in lowered and "'q' to quit" not in lowered:
        actions.append("q to quit")
    if not actions:
        return text
    hint = ", ".join(actions)
    if text.endswith(": "):
        return f"{text[:-2]} ({hint}): "
    return f"{text} ({hint})"


def input(text: str) -> str:
    """Read a prompt, treating navigation keys and interrupts consistently."""
    try:
        value = builtins.input(_with_navigation_hint(text))
    except (EOFError, KeyboardInterrupt) as exc:
        raise CliExit from exc
    navigation = value.strip().lower()
    if navigation == "q":
        raise CliExit
    if navigation == "b" and _menu_depth > 0:
        raise CliBack
    return value


def db_path_for_env(env_path: Path) -> Path:
    """Per-profile SQLite path: the default profile keeps data/asana.sqlite."""
    slug = profile_slug(env_path)
    name = "asana.sqlite" if not slug else f"asana_{slug}.sqlite"
    return PROJECT_ROOT / "data" / name


def _apply_session_env() -> None:
    """Point in-process scripts at the active profile's database."""
    if _session is not None:
        os.environ[ASANA_JIRA_DB_PATH_ENV] = str(_session.db_path)
        if _session.jira_site_key:
            os.environ[ASANA_JIRA_SITE_KEY_ENV] = _session.jira_site_key
        else:
            os.environ.pop(ASANA_JIRA_SITE_KEY_ENV, None)


def _set_session(reference_path: Path, env_path: Path, *, use_prod: Optional[bool] = None) -> CliSession:
    """(Re)build the module session from an env file and refresh the DB env var."""
    global _session
    prod = use_prod if use_prod is not None else (_session.use_prod if _session else False)
    base_url, sandbox_url = read_profile_jira_urls(env_path)
    sites, default_site = read_profile_jira_sites(env_path)
    selected = None
    if use_prod is not None:
        selected = "production_jira" if use_prod else "sandbox_jira"
    elif _session and _session.env_path == env_path and any(site.key == _session.jira_site_key for site in sites):
        selected = _session.jira_site_key
    else:
        selected = default_site or (sites[0].key if sites else None)
    _session = CliSession(
        reference_path=reference_path,
        env_path=env_path,
        client_name=read_profile_name(env_path),
        use_prod=prod,
        jira_base_url=base_url,
        jira_sandbox_base_url=sandbox_url,
        jira_sites=sites,
        jira_site_key=selected,
        default_jira_site_key=default_site,
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
    """Select a matching file from ``inputs/`` or accept a typed path."""
    search_dir = PROJECT_ROOT / "inputs"
    search_dir.mkdir(parents=True, exist_ok=True)
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


def _prompt_task_gids_file(context: str) -> str:
    return _prompt_list_file(context, "Task GID")


def _prompt_jira_keys_file(context: str) -> str:
    return _prompt_list_file(context, "Jira key")


def _prompt_csv_file(context: str, item_label: str) -> str:
    return _prompt_list_file(context, item_label, {".csv"})


def _prompt_attachment_archive_manifest() -> str:
    """Select an existing attachment archive manifest or enter its path."""
    archive_root = PROJECT_ROOT / "attachment_archives"
    candidates = (
        sorted(archive_root.rglob("manifest.json"), key=lambda path: str(path).lower())
        if archive_root.is_dir()
        else []
    )
    print(f"\nAttachment archives in {archive_root}:")
    if candidates:
        for index, path in enumerate(candidates, start=1):
            print(f"[{index}] {path.parent.relative_to(archive_root)}")
    else:
        print("  No archive manifests found.")

    selection = prompt(
        "Pick an archive number, or enter an archive/manifest path",
        default="",
    )
    if selection.isdigit() and 1 <= int(selection) <= len(candidates):
        manifest = candidates[int(selection) - 1]
    else:
        manifest = Path(selection).expanduser()
        if not manifest.is_absolute():
            manifest = PROJECT_ROOT / manifest
        if manifest.is_dir():
            manifest = manifest / "manifest.json"
    if not selection or not manifest.is_file():
        raise SystemExit(f"Attachment archive manifest not found: {selection or '(blank)'}")
    return str(manifest.resolve())


def confirm(text: str, default: bool = True) -> bool:
    return prompt_bool(text, default=default)


def append_jira_env_flag(args: List[str]) -> None:
    """Append the active named Jira site (with legacy ``--prod`` fallback)."""
    if _session is not None and _session.jira_site_key:
        args.extend(["--jira-site", _session.jira_site_key])
    elif _session is not None and _session.use_prod:
        args.append("--prod")


def prompt_credentials_values() -> Credentials:
    token = prompt_required("ASANA_TOKEN")
    workspace_gid = prompt_required("ASANA_WORKSPACE_GID")
    skip_ssl_verification = prompt_bool("Skip SSL verification?", default=False)
    atlassian_username = prompt("ATLASSIAN_USERNAME (optional)", default="")
    atlassian_token = prompt("ATLASSIAN_TOKEN (optional)", default="")
    jira_sites: List[JiraSite] = []
    if prompt_bool("Configure a Jira site now?", default=bool(atlassian_username or atlassian_token)):
        while True:
            site_name = prompt_required("Jira site label")
            site_key = slugify(prompt("Stable site key", default=slugify(site_name)))
            site_url = prompt_required("Jira site URL").rstrip("/")
            jira_sites.append(JiraSite(site_key, site_name, site_url))
            if not prompt_bool("Add another Jira site?", default=False):
                break
    return Credentials(
        token=token,
        workspace_gid=workspace_gid,
        skip_ssl_verification=skip_ssl_verification,
        atlassian_username=atlassian_username or None,
        atlassian_token=atlassian_token or None,
        jira_sites=tuple(jira_sites),
        jira_default_site=jira_sites[0].key if jira_sites else None,
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
    values["jira_sites"] = current.jira_sites
    values["jira_default_site"] = current.jira_default_site
    secret_backend = "Keychain" if keychain.is_available() else "env file"
    profile_label = current.client_name or env_path.name
    dirty = False
    while True:
        print(f"\nShared credentials for profile '{profile_label}' in {env_path} (tokens stored in: {secret_backend}):")
        for index, (field, env_name, kind) in enumerate(CREDENTIAL_FIELDS, start=1):
            display = _display_credential_value(kind, values[field])
            if kind == "secret" and values[field]:
                display = f"******** (set, {secret_backend})"
            print(f"  [{index}] {env_name} = {display}")
        choice = input("Select a number to update, 's' to save, or 'b' to go back: ").strip().lower()
        if choice in {"b", "c"}:
            print("No changes saved.")
            return env_path
        if choice == "s":
            if not dirty:
                print("No changes to save.")
                return env_path
            candidate = Credentials(**values)
            if not report_credential_checks(candidate):
                print("Not saved. Fix the flagged values and try again, or 'b' to go back.")
                continue
            write_credentials_file(env_path, candidate)
            print(f"Updated credentials saved to {env_path}.")
            return env_path
        if choice.isdigit() and 1 <= int(choice) <= len(CREDENTIAL_FIELDS):
            field, env_name, kind = CREDENTIAL_FIELDS[int(choice) - 1]
            _edit_credential_field(field, env_name, kind, values)
            dirty = True
        else:
            print("Invalid choice. Enter a number, 's', 'b', or 'q'.")


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
        "for one Asana workspace, shared Atlassian credentials, and named Jira sites.\n"
        "\n"
        "To get started you'll need:\n"
        "  REQUIRED\n"
        "   - ASANA_TOKEN          your Asana personal access token\n"
        "   - ASANA_WORKSPACE_GID  the workspace to migrate\n"
        "\n"
        "  OPTIONAL (only for the Jira migration steps)\n"
        "   - ATLASSIAN_USERNAME / ATLASSIAN_TOKEN  Atlassian API credentials\n"
        "   - One or more labeled Jira site URLs\n"
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
        ("Jira site", env_value),
        ("Database", _session.db_path.name),
    ]
    lines = [f"{label}: {value}" for label, value in rows]
    width = max(len(line) for line in lines)
    border_color = "red" if _session.jira_site_key == "production_jira" else "green"

    top = _colorize("┌" + "─" * (width + 2) + "┐", border_color)
    bottom = _colorize("└" + "─" * (width + 2) + "┘", border_color)
    side = _colorize("│", border_color)

    print()
    print(top)
    for line in lines:
        print(f"{side} {line.ljust(width)} {side}")
    print(bottom)


def select_jira_site(*, make_default: bool = False) -> None:
    if _session is None:
        return
    if not _session.jira_sites:
        print("No Jira sites are configured for this profile.")
        return
    print("\nAvailable Jira sites:")
    for index, site in enumerate(_session.jira_sites, start=1):
        markers = []
        if site.key == _session.jira_site_key:
            markers.append("active")
        if site.key == _session.default_jira_site_key:
            markers.append("default")
        suffix = f" ({', '.join(markers)})" if markers else ""
        print(f"  [{index}] {site.name} — {site.base_url}{suffix}")
    choice = prompt("Pick a number (Enter to go back)", default="")
    if not choice:
        return
    if not (choice.isdigit() and 1 <= int(choice) <= len(_session.jira_sites)):
        print("Invalid selection.")
        return
    site = _session.jira_sites[int(choice) - 1]
    _session.jira_site_key = site.key
    os.environ[ASANA_JIRA_SITE_KEY_ENV] = site.key
    _session.use_prod = site.key == "production_jira"
    if make_default:
        write_profile_default_jira_site(_session.env_path, site.key)
        _session.default_jira_site_key = site.key
        print(f"{site.name} is now active and will be selected on future launches.")
    else:
        print(f"{site.name} is active for this session.")


def add_jira_site() -> None:
    """Add a user-labeled Jira site to the active client profile."""
    if _session is None:
        return
    name = prompt_required("Jira site label")
    suggested = slugify(name)
    key = slugify(prompt("Stable site key", default=suggested))
    if any(site.key == key for site in _session.jira_sites):
        print(f"A Jira site with key '{key}' already exists.")
        return
    url = prompt_required("Jira site URL").rstrip("/")
    sites = (*_session.jira_sites, JiraSite(key, name, url))
    default = _session.default_jira_site_key or key
    write_profile_jira_sites(_session.env_path, sites, default)
    _session.jira_sites = sites
    _session.default_jira_site_key = default
    if not _session.jira_site_key:
        _session.jira_site_key = key
    print(f"Added {name} ({url}).")


def edit_jira_site() -> None:
    """Edit a named Jira site's display label and URL without changing its key."""
    if _session is None:
        return
    if not _session.jira_sites:
        print("No Jira sites are configured for this profile.")
        return

    print("\nEdit a Jira site:")
    for index, site in enumerate(_session.jira_sites, start=1):
        print(f"  [{index}] {site.name} — {site.base_url} (key: {site.key})")
    choice = prompt("Pick a number (Enter to go back)", default="")
    if not choice:
        return
    if not (choice.isdigit() and 1 <= int(choice) <= len(_session.jira_sites)):
        print("Invalid selection.")
        return

    site = _session.jira_sites[int(choice) - 1]
    print(f"Site key '{site.key}' is retained so saved Jira mappings keep working.")
    name = prompt_required("Jira site label", default=site.name)
    url = prompt_required("Jira site URL", default=site.base_url).rstrip("/")
    replacement = JiraSite(site.key, name, url)
    sites = tuple(replacement if candidate.key == site.key else candidate for candidate in _session.jira_sites)
    write_profile_jira_sites(_session.env_path, sites, _session.default_jira_site_key)
    _session.jira_sites = sites
    print(f"Updated {name} ({url}).")


def remove_jira_site() -> None:
    """Remove a named Jira site from the active client profile."""
    if _session is None:
        return
    if not _session.jira_sites:
        print("No Jira sites are configured for this profile.")
        return

    print("\nRemove a Jira site:")
    for index, site in enumerate(_session.jira_sites, start=1):
        print(f"  [{index}] {site.name} — {site.base_url}")
    choice = prompt("Pick a number (Enter to go back)", default="")
    if not choice:
        return
    if not (choice.isdigit() and 1 <= int(choice) <= len(_session.jira_sites)):
        print("Invalid selection.")
        return

    site = _session.jira_sites[int(choice) - 1]
    if not confirm(f"Remove Jira site '{site.name}' ({site.base_url})?", default=False):
        print("Jira site was not removed.")
        return

    sites = tuple(candidate for candidate in _session.jira_sites if candidate.key != site.key)
    default = _session.default_jira_site_key
    if default == site.key:
        default = sites[0].key if sites else None
    active = _session.jira_site_key
    if active == site.key:
        active = default or (sites[0].key if sites else None)

    write_profile_jira_sites(_session.env_path, sites, default)
    _session.jira_sites = sites
    _session.default_jira_site_key = default
    _session.jira_site_key = active
    if active:
        os.environ[ASANA_JIRA_SITE_KEY_ENV] = active
    else:
        os.environ.pop(ASANA_JIRA_SITE_KEY_ENV, None)
    print(f"Removed {site.name}.")


def toggle_environment() -> None:
    """Compatibility alias for older callers; opens the named-site selector."""
    select_jira_site()


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
    choice = prompt("Pick a number (Enter to go back)", default="")
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
        "1": MenuItem("1", "Select active Jira site", select_jira_site),
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
    export_mains = {
        export_all_reports_xlsx.main,
        export_custom_field_usage_report.main,
        export_field_section_review_report.main,
        export_jira_migration_validation.main,
        export_portfolio_report.main,
        export_project_details_report.main,
        export_project_last_updated_report.main,
        export_project_members_report.main,
        export_team_memberships.main,
        export_team_project_jira_csv.main,
        export_team_report.main,
        export_users.main,
    }
    if main_func in export_mains:
        jira_csv = main_func is export_team_project_jira_csv.main
        show_export_readiness(argv, main_func.__module__.rsplit(".", 1)[-1], jira_csv)
    original_input = builtins.input

    def session_input(text: str = "") -> str:
        value = original_input(_with_navigation_hint(text))
        navigation = value.strip().lower()
        if navigation == "q":
            raise CliExit
        if navigation == "b" and _menu_depth > 0:
            raise CliBack
        return value

    builtins.input = session_input
    try:
        return main_func(argv)
    except SystemExit as exc:
        if exc.code not in (None, 0):
            detail = str(exc) or f"Command exited with status {exc.code}."
            raise CliCommandError(detail) from exc
        return None
    except (EOFError, KeyboardInterrupt) as exc:
        raise CliExit from exc
    finally:
        builtins.input = original_input


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
    run_task_sync_for_args(args, log_level)


def run_task_sync_for_args(args: List[str], log_level: str) -> None:
    """Run a task sync and offer the standard service-account recovery path."""
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


def _known_project_gids() -> Set[str]:
    """Return project GIDs in the active snapshot before a metadata refresh."""
    db_path = active_db_path()
    if not db_path.exists():
        return set()
    with _open_sqlite(db_path) as connection:
        return {
            row["gid"]
            for row in connection.execute("SELECT gid FROM projects")
            if row["gid"]
        }


def _new_projects_after_refresh(known_gids: Set[str]) -> List[sqlite3.Row]:
    """List projects discovered by the immediately preceding project refresh."""
    with _open_sqlite(active_db_path()) as connection:
        rows = connection.execute(
            """
            SELECT gid, name, team_name, num_tasks, num_incomplete_tasks
            FROM projects
            ORDER BY lower(COALESCE(team_name, '')), lower(name), gid
            """
        ).fetchall()
    return [row for row in rows if row["gid"] not in known_gids]


def _select_discovered_project_gids(projects: Sequence[sqlite3.Row]) -> List[str]:
    """Print discovered projects and return the selected project GIDs."""
    print("\nNew active projects discovered:")
    for index, project in enumerate(projects, start=1):
        team = project["team_name"] or "No team"
        task_count = project["num_tasks"]
        count_label = "unknown task count" if task_count is None else f"{task_count} task(s)"
        print(f"[{index}] {project['name']} — {team} ({count_label}) [{project['gid']}]")

    selection = prompt(
        "Project numbers to sync (comma-separated; blank = all; 0 = cancel)",
        default="",
    )
    if selection == "0":
        return []
    if not selection:
        return [project["gid"] for project in projects]

    selected: List[str] = []
    try:
        numbers = [int(value.strip()) for value in selection.split(",") if value.strip()]
    except ValueError:
        print("Selection must contain project numbers separated by commas.")
        return []
    for number in numbers:
        if number < 1 or number > len(projects):
            print(f"Project number {number} is out of range.")
            return []
        gid = projects[number - 1]["gid"]
        if gid not in selected:
            selected.append(gid)
    return selected


def run_new_project_discovery_flow(log_level: str) -> None:
    """Discover newly visible active projects and sync selected project data."""
    print("\nNew-project discovery: refresh active projects, then sync selected projects.")
    known_gids = _known_project_gids()
    project_args = build_common_args(log_level) + ["--archived-scope", "active"]
    run_script(get_projects.main, project_args)

    projects = _new_projects_after_refresh(known_gids)
    if not projects:
        print("No new active projects were discovered.")
        return
    project_gids = _select_discovered_project_gids(projects)
    if not project_gids:
        print("No projects selected; nothing to sync.")
        return

    scope = ",".join(project_gids)
    if confirm("Add the service account to the selected projects first?", default=True):
        run_script(
            add_service_user_to_projects.main,
            build_common_args(log_level) + ["--project-gids", scope],
        )
    if confirm("Sync tasks for the selected projects now?", default=True):
        workers = prompt("Workers (default 5)", default="5")
        task_args = build_common_args(log_level) + ["--project-gids", scope]
        if workers:
            task_args.extend(["--workers", workers])
        run_task_sync_for_args(task_args, log_level)
    if confirm("Sync task comments and attachment metadata now?", default=True):
        run_script(
            get_task_extras.main,
            build_common_args(log_level) + ["--project-gids", scope],
        )
    if confirm("Sync project status updates now?", default=False):
        run_script(
            get_project_status_updates.main,
            build_common_args(log_level) + ["--project-gids", scope],
        )


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
    print("[4] Specific task GIDs")
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
    elif choice == "3":
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
    elif choice == "4":
        if confirm("Load task GIDs from a file?", default=False):
            args.extend(
                ["--task-gids-file", _prompt_task_gids_file("Jira CSV export")]
            )
        else:
            gids = prompt("Task GIDs (comma-separated)", default="")
            if not gids:
                raise SystemExit("At least one task GID is required.")
            args.extend(["--task-gids", gids])
        # The exporter already consolidates task-scoped projects and organizes
        # their outputs. The wrapper's legacy merge would run afterward against
        # files that have already moved into subdirectories.
        allow_merge = False
    else:
        raise SystemExit(f"Invalid export scope choice: {choice}")

    show_export_readiness(args, "Jira task CSV", jira_csv=True)
    if not confirm("Continue with this export scope?", default=True):
        raise CliBack

    if not confirm("Prompt to select custom fields during export?", default=True):
        args.append("--no-custom-field-prompt")

    if choice in ("2", "3", "4"):
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
    export_id = prompt_export_id()
    if export_id:
        args.extend(["--export-id", export_id])

    # Custom-field handling.
    print("\nCustom-field handling:")
    print("[1] Keep as individual Jira CSV columns  (default)")
    print("[2] Fold all populated fields into Description")
    cf_choice = input("Choice (default 1): ").strip() or "1"
    if cf_choice == "2":
        args.extend(["--custom-field-mode", "description"])

    # Hierarchy mode selection.
    above_epic_name: Optional[str] = None
    if _session and _session.jira_site_key:
        try:
            site_settings = load_archive_import_settings(_session.env_path, _session.jira_site_key)
            above_epic_name = site_settings.above_epic_issue_type_name
        except Exception:
            above_epic_name = None

    print("\nIssue hierarchy mode:")
    print("[1] No synthetic epics (flat tasks only)")
    print("[2] Project → Epic  (one Epic per project)")
    if above_epic_name:
        print(f"[3] Project → {above_epic_name} / Section → Epic  (two-level hierarchy)")
    else:
        print("[3] Project → <above-Epic> / Section → Epic  (not configured — see Jira Preparation → [8])")
    hierarchy_choice = input("Choice (default 2): ").strip() or "2"
    if hierarchy_choice == "2":
        args.append("--add-epics")
    elif hierarchy_choice == "3":
        if above_epic_name:
            args.extend(["--above-epic-issue-type", above_epic_name])
        else:
            print("  Above-Epic issue type not configured. Falling back to Project → Epic.")
            args.append("--add-epics")

    return args, allow_merge


def _export_id_summaries() -> List[Tuple[str, int, str]]:
    """Return persisted export IDs for the active client, newest activity first."""
    db_path = active_db_path()
    if not db_path.exists():
        return []
    connection = sqlite3.connect(str(db_path))
    try:
        rows = connection.execute(
            """
            SELECT export_id, COUNT(*) AS task_count, MAX(exported_at) AS last_used
            FROM exported_tasks
            WHERE export_id <> ''
            GROUP BY export_id
            ORDER BY last_used DESC, export_id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        connection.close()
    return [(row[0], row[1], row[2] or "") for row in rows]


def prompt_export_id() -> Optional[str]:
    """Choose, create, or remove the persisted task-history key for one export."""
    while True:
        summaries = _export_id_summaries()
        print("\nExport ID controls cross-run skipping; current-run task deduplication is always on.")
        print("[1] Start a new export ID")
        print("[2] Continue an existing export ID")
        print("[3] Remove an export ID and clear its recorded export history")
        print("[4] Export without an ID")
        choice = prompt("Choice", default="4")
        if choice == "4":
            return None
        if choice == "1":
            export_id = prompt_required("New export ID")
            if any(existing_id == export_id for existing_id, _count, _last_used in summaries):
                print(f"Export ID '{export_id}' already exists; choose Continue instead.")
                continue
            return export_id
        if choice in ("2", "3"):
            if not summaries:
                print("No recorded export IDs exist for this profile.")
                continue
            print("\nRecorded export IDs:")
            for index, (export_id, task_count, last_used) in enumerate(summaries, start=1):
                print(f"[{index}] {export_id} — {task_count:,} tasks, last used {last_used or 'unknown'}")
            selected = prompt("Select an export ID number", default="")
            if not selected.isdigit() or not 1 <= int(selected) <= len(summaries):
                print("Select one of the listed export IDs.")
                continue
            export_id = summaries[int(selected) - 1][0]
            if choice == "2":
                return export_id
            if not confirm(
                f"Remove export ID '{export_id}' and clear its recorded task history?",
                default=False,
            ):
                continue
            if prompt("Type the export ID to confirm", default="") != export_id:
                print("Export ID did not match; nothing was removed.")
                continue
            connection = sqlite3.connect(str(active_db_path()))
            try:
                cleared = connection.execute(
                    "DELETE FROM exported_tasks WHERE export_id = ?", (export_id,)
                ).rowcount
                connection.commit()
            finally:
                connection.close()
            print(f"Cleared {cleared:,} recorded task mapping(s) for export ID '{export_id}'.")
            continue
        print("Invalid choice.")


def args_export_archival_project(log_level: str) -> List[str]:
    args = build_common_args(log_level)

    print("\nSelect export scope:")
    print("[1] Single project")
    print("[2] Multiple projects (comma-separated GIDs)")
    print("[3] All projects in a team")
    print("[4] Specific task GIDs")
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
    elif choice == "3":
        team_gid = _prompt_team_gid_from_lookup(
            "archival export", "Team GID whose projects to export"
        )
        if not team_gid:
            raise SystemExit("Team GID is required.")
        args.extend(["--team-gid", team_gid])
        scope = prompt("Archived scope (active|archived|all) [all]", default="all")
        args.extend(["--archived-scope", scope])
    elif choice == "4":
        if confirm("Load task GIDs from a file?", default=False):
            args.extend(
                ["--task-gids-file", _prompt_task_gids_file("archival Jira CSV export")]
            )
        else:
            gids = prompt("Task GIDs (comma-separated)", default="")
            if not gids:
                raise SystemExit("At least one task GID is required.")
            args.extend(["--task-gids", gids])
    else:
        raise SystemExit(f"Invalid export scope choice: {choice}")

    show_export_readiness(args, "archival Jira task CSV", jira_csv=True)
    if not confirm("Continue with this export scope?", default=True):
        raise CliBack

    append_jira_env_flag(args)
    fallback_atl = prompt("Fallback Atlassian account ID for unmapped users (optional)", default="")
    if fallback_atl:
        args.extend(["--fallback-atlassian-id", fallback_atl])
    export_id = prompt_export_id()
    if export_id:
        args.extend(["--export-id", export_id])

    # Hierarchy mode selection (mirrors regular export).
    above_epic_name_arch: Optional[str] = None
    if _session and _session.jira_site_key:
        try:
            site_settings_arch = load_archive_import_settings(_session.env_path, _session.jira_site_key)
            above_epic_name_arch = site_settings_arch.above_epic_issue_type_name
        except Exception:
            above_epic_name_arch = None

    print("\nIssue hierarchy mode:")
    print("[1] Project → Epic  (one Epic per project, default)")
    if above_epic_name_arch:
        print(f"[2] Project → {above_epic_name_arch} / Section → Epic  (two-level hierarchy)")
    else:
        print("[2] Project → <above-Epic> / Section → Epic  (not configured — see Jira Preparation → [8])")
    hier_choice = input("Choice (default 1): ").strip() or "1"
    if hier_choice == "2":
        if above_epic_name_arch:
            args.extend(["--above-epic-issue-type", above_epic_name_arch])
        else:
            print("  Above-Epic issue type not configured. Using Project → Epic.")

    return args


def args_generate_jira_csv_import_config(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    append_jira_env_flag(args)
    if confirm("Review every saved Jira CSV mapping?", default=False):
        args.append("--configure")
    return args


def rename_jira_import_config() -> None:
    """Rename the active profile's saved Jira CSV importer configuration file."""
    env_path = resolve_credentials_path()
    site_key = _session.jira_site_key if _session else None
    current = latest_profile_import_config_path(env_path, site_key=site_key)
    if current is None:
        print("No saved Jira CSV importer configuration found for this profile.")
        return
    print(f"Current file: {current.name}")
    raw = prompt("New filename (press Enter to cancel)").strip()
    if not raw:
        print("Rename cancelled.")
        return
    new_name = raw if raw.endswith(".txt") else f"{raw}.txt"
    new_path = current.with_name(new_name)
    current.rename(new_path)
    print(f"Renamed to {new_path.name}")


def purge_jira_import_config() -> None:
    """Delete stale config files and clear saved env keys for the active Jira site."""
    env_path = resolve_credentials_path()
    site_key = _session.jira_site_key if _session else None
    if not site_key:
        print("No active Jira site selected — select a site first.")
        return

    stale_files = stale_profile_import_config_paths(env_path, site_key=site_key)
    current = latest_profile_import_config_path(env_path, site_key=site_key)

    print(f"\nActive site: {site_key}")
    if current:
        print(f"  Keeping: {current.name}")
    if stale_files:
        print(f"  Stale files to delete ({len(stale_files)}):")
        for f in stale_files:
            print(f"    {f.name}")
    else:
        print("  No stale config files found.")
    print("  Env keys: all saved CSV import mappings for this site will be cleared.")

    if not confirm("Proceed with cleanup?", default=False):
        print("Cleanup cancelled.")
        return

    for f in stale_files:
        f.unlink()
        print(f"Deleted {f.name}")
    clear_archive_import_settings(env_path, site_key)
    print("Cleared saved import settings from env file.")


def run_configure_above_epic_hierarchy() -> None:
    """Interactively configure the issue type that sits above Epic for the active Jira site."""
    if _session is None or not _session.jira_site_key:
        print("No active Jira site selected. Select a site first via Client & Environment.")
        return
    from src.lib.config import load_credentials, resolve_jira_base_url
    from src.lib.jira_client import JiraClient
    try:
        credentials = load_credentials()
        jira_base_url = resolve_jira_base_url(credentials, site_key=_session.jira_site_key)
        if not jira_base_url:
            print("Could not resolve Jira base URL for the active site.")
            return
        client = JiraClient(
            base_url=jira_base_url.rstrip("/"),
            username=credentials.atlassian_username,
            token=credentials.atlassian_token,
            skip_ssl_verification=credentials.skip_ssl_verification,
        )
    except Exception as exc:
        print(f"Could not connect to Jira: {exc}")
        return

    env_path = _session.env_path
    site_key = _session.jira_site_key
    current_settings = load_archive_import_settings(env_path, site_key)
    updated = generate_jira_csv_import_config._configure_above_epic_issue_type(client, current_settings)
    if updated is current_settings:
        return
    save_archive_import_settings(env_path, site_key, updated)
    print(
        f"Saved: issue type above Epic = '{updated.above_epic_issue_type_name}' "
        f"(id: {updated.above_epic_issue_type_id})"
    )


def run_export_project_to_jira(log_level: str) -> None:
    args, allow_merge = args_export_project_to_jira(log_level)
    if allow_merge:
        export_id = None
        for idx, val in enumerate(args):
            if val == "--export-id" and idx + 1 < len(args):
                export_id = args[idx + 1]
                break
        export_dir = next_export_dir(PROJECT_ROOT / "exports", export_id=export_id)
        print(f"\nExporting multi-project output into {export_dir}")
        args.extend(["--output-dir", str(export_dir)])
    display_args = list(args)
    run_script(export_project_to_jira_csv.main, args)
    print_rerun_command("scripts.export_project_to_jira_csv", display_args)


def run_export_archival_project(log_level: str) -> None:
    """Run the archival Jira CSV flow with its scoped advisory summary."""
    args = args_export_archival_project(log_level)
    run_script(export_archival_project_jira_csv.main, args)
    print_rerun_command("scripts.atlassian.export_archival_project_jira_csv", args)


def run_export_tasks_to_jira(log_level: str) -> None:
    """Export project tasks to a Jira CSV using the unified exporter."""
    run_export_project_to_jira(log_level)


def run_sync_attachments(log_level: str) -> None:
    args = args_sync_attachments(log_level)
    run_script(sync_jira_attachments.main, args)
    print_rerun_command("scripts.deprecated.sync_jira_attachments", args)


def build_reports_menu(log_level: str) -> Dict[str, MenuItem]:
    def append_project_report_scope(args: List[str], context: str) -> None:
        """Prompt for a project-oriented report scope."""
        print(f"\nSelect {context} scope:")
        print("[1] Team lookup")
        print("[2] Paste project GIDs")
        print("[3] Project GID file")
        print("[4] Whole workspace")
        choice = input("Choice (default 1): ").strip() or "1"
        if choice == "1":
            team_gid = _prompt_team_gid_from_lookup(
                context, f"Team GID to scope {context} (blank = all teams)"
            )
            if team_gid:
                args.extend(["--team-gid", team_gid])
        elif choice == "2":
            args.extend(["--project-gids", prompt_required("Project GIDs (comma-separated)")])
        elif choice == "3":
            args.extend(["--project-gids-file", _prompt_project_gids_file(context)])
        elif choice != "4":
            raise SystemExit("Invalid report scope.")

    def run_project_details_report() -> None:
        args = args_report_defaults(log_level)
        append_project_report_scope(args, "project details")
        run_script(export_project_details_report.main, args)

    def run_custom_field_usage_report() -> None:
        args = args_report_defaults(log_level)
        append_project_report_scope(args, "custom field usage")
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
        append_project_report_scope(args, "project members")
        run_script(export_project_members_report.main, args)

    def run_field_section_review_report() -> None:
        args = args_report_defaults(log_level)
        print("\nScope the field & section review:")
        print("[1] Team-centric (one team's projects)")
        print("[2] Owner-centric (one project owner's projects)")
        print("[3] Whole workspace")
        print("[4] By Jira key(s) (projects behind migrated Jira Task keys)")
        print("[5] Explicit project GID list")
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
        elif choice == "5":
            print("\nProvide project GIDs:")
            print("[1] Paste project GIDs (comma-separated)")
            print("[2] Project GID file")
            project_scope = input("Choice (default 1): ").strip() or "1"
            if project_scope == "1":
                args.extend(["--project-gids", prompt_required("Project GIDs (comma-separated)")])
            elif project_scope == "2":
                args.extend(["--project-gids-file", _prompt_project_gids_file("field & section review")])
            else:
                raise SystemExit("Invalid project GID scope.")
        run_script(export_field_section_review_report.main, args)

    def run_project_last_updated_report() -> None:
        args = args_report_defaults(log_level)
        append_project_report_scope(args, "project last updated")
        run_script(export_project_last_updated_report.main, args)

    def run_jira_migration_validation() -> None:
        args = args_report_defaults(log_level)
        append_jira_env_flag(args)
        print("\nSelect Jira migration validation scope:")
        print("[1] Team lookup")
        print("[2] Migration wave project GID file")
        print("[3] Paste project GIDs")
        print("[4] Task GID file")
        print("[5] Paste task GIDs")
        print("[6] All associated projects")
        scope = input("Choice (default 1): ").strip() or "1"
        if scope == "1":
            team_gid = _prompt_team_gid_from_lookup(
                "Jira migration validation", "Team GID to validate"
            )
            if not team_gid:
                raise SystemExit("Team GID is required.")
            args.extend(["--team-gid", team_gid])
        elif scope == "2":
            args.extend(
                ["--project-gids-file", _prompt_project_gids_file("Jira migration validation")]
            )
        elif scope == "3":
            project_gids = prompt_required("Project GIDs (comma-separated)")
            args.extend(["--project-gids", project_gids])
        elif scope == "4":
            args.extend(["--task-gids-file", _prompt_task_gids_file("Jira migration validation")])
        elif scope == "5":
            task_gids = prompt_required("Task GIDs (comma-separated)")
            args.extend(["--task-gids", task_gids])
        elif scope != "6":
            raise SystemExit("Invalid validation scope.")
        run_script(export_jira_migration_validation.main, args)
        if confirm("Export every missing task for focused reimport?", default=True):
            run_script(
                export_jira_migration_validation.main,
                args + ["--export-missing", "--export-only"],
            )

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
        "c": MenuItem(
            "c",
            "Validate Jira task migration coverage",
            run_jira_migration_validation,
        ),
        "b": MenuItem("b", "Back", lambda: None),
    }


def args_search_jira_external_ids(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    append_jira_env_flag(args)
    if confirm("Dry run (fetch and print mappings, write nothing)?", default=True):
        args.append("--dry-run")
    return args


def args_import_jira_project_map(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    append_jira_env_flag(args)
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
    append_jira_env_flag(args)
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
        "Raw Atlassian user export CSV",
    )
    args.extend(["--atlassian-export", csv_path])
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


def args_download_attachment_archive(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    print("\nProvide Asana-hosted attachment archive scope:")
    print("[1] All active Asana-hosted attachments")
    print("[2] Project GID(s)")
    print("[3] Project GIDs file")
    choice = input("Choice (default 1): ").strip() or "1"
    if choice == "1":
        args.append("--all")
    elif choice == "2":
        gids = prompt("Project GID(s), comma-separated", default="")
        if not gids:
            raise SystemExit("At least one project GID is required.")
        args.extend(["--project-gids" if "," in gids else "--project-gid", gids])
    elif choice == "3":
        args.extend(["--project-gids-file", _prompt_project_gids_file("attachment archive")])
    else:
        raise SystemExit("Invalid attachment archive scope.")
    archive_root = prompt("Archive root directory", default="attachment_archives")
    archive_id = prompt("Archive/wave ID (blank = timestamped default)", default="")
    args.extend(["--archive-root", archive_root])
    if archive_id:
        args.extend(["--archive-id", archive_id])
    return args


def args_upload_attachment_archive(log_level: str) -> List[str]:
    args = build_common_args(log_level)
    manifest = _prompt_attachment_archive_manifest()
    args.extend(["--manifest", manifest])
    print("\nChoose attachment upload scope:")
    print("[1] All attachments in this archive")
    print("[2] Project GID(s)")
    print("[3] Project GIDs file")
    choice = input("Choice (default 1): ").strip() or "1"
    if choice == "2":
        gids = prompt("Project GID(s), comma-separated", default="")
        if not gids:
            raise SystemExit("At least one project GID is required.")
        args.extend(["--project-gids" if "," in gids else "--project-gid", gids])
    elif choice == "3":
        args.extend(["--project-gids-file", _prompt_project_gids_file("attachment upload")])
    elif choice != "1":
        raise SystemExit("Invalid attachment upload scope.")
    append_jira_env_flag(args)
    if confirm("Dry run (resolve destinations without Jira uploads)?", default=True):
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


def args_create_jira_import_fields(log_level: str, field_set: str = "import") -> List[str]:
    args = build_common_args(log_level)
    args.extend(["--field-set", field_set])
    append_jira_env_flag(args)
    if confirm("Dry run (inspect fields, create nothing)?", default=True):
        args.append("--dry-run")
    else:
        args.append("--yes")
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
    global _menu_depth
    _menu_depth += 1
    try:
        while True:
            print_menu(title, menu)
            try:
                choice = input("Select an option: ").strip()
                if choice not in menu:
                    print("Invalid choice. Please try again.")
                    continue
                if choice == "b":
                    return
                item = menu[choice]
                item.handler()
            except CliBack:
                return
            except CliExit:
                raise
            except CliCommandError as exc:
                print(f"\nCommand could not complete: {exc}")
            except Exception as exc:  # pragma: no cover - defensive
                logging.exception("Step failed: %s", exc)
            else:
                logging.info("Step '%s' completed.", item.label)
    finally:
        _menu_depth -= 1


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


def _missing_service_account_team_gids() -> tuple[str, List[str]]:
    """Return the active token's GID and locally synced teams missing that user."""
    credentials = load_credentials(PROJECT_ROOT)
    payload = AsanaHttpClient(
        token=credentials.token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    ).get("/users/me")
    user_gid = str(((payload or {}).get("data") or {}).get("gid") or "").strip()
    if not user_gid:
        raise ValueError("Asana did not return the active service-account GID.")
    with _open_sqlite(active_db_path()) as connection:
        rows = connection.execute(
            "SELECT t.gid FROM teams t LEFT JOIN team_members tm "
            "ON tm.team_gid = t.gid AND tm.user_gid = ? "
            "WHERE tm.user_gid IS NULL ORDER BY t.gid",
            (user_gid,),
        ).fetchall()
    return user_gid, [row["gid"] for row in rows]


def run_add_service_account_to_missing_teams(log_level: str) -> None:
    """Add the token account to missing teams, then refresh only their projects."""
    try:
        user_gid, team_gids = _missing_service_account_team_gids()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not determine missing service-account teams: {exc}")
        return
    if not team_gids:
        print("The active service account is already a member of every synced team.")
        return
    print(f"Adding the service account to {len(team_gids)} missing team(s)...")
    run_script(
        add_service_user_to_teams.main,
        build_common_args(log_level) + ["--user-gid", user_gid],
    )
    run_script(
        get_projects.main,
        build_common_args(log_level) + ["--team-gids", ",".join(team_gids)],
    )


def run_guided_asana_refresh(log_level: str) -> None:
    """Run the Asana refresh layers in dependency order, with manual scope prompts."""
    print("\nGuided Asana refresh: metadata -> tasks -> task-derived data")
    run_initial_metadata(log_level)
    run_task_sync_flow(log_level)
    if confirm("Sync task extras (comments and attachment metadata) now?", default=True):
        run_script(get_task_extras.main, args_get_task_extras(log_level))
    if confirm("Sync project status updates now?", default=True):
        run_script(
            get_project_status_updates.main,
            args_get_project_status_updates(log_level),
        )
    if confirm("Sync portfolios now?", default=False):
        run_script(get_portfolios.main, args_portfolios(log_level))
    if confirm("Sync personal tasks now?", default=False):
        run_script(
            get_personal_tasks.main,
            args_personal_tasks(log_level, delta=False),
        )


def run_guided_migration_review(log_level: str) -> None:
    """Refresh decisions when requested, then generate the primary review workbook."""
    print("\nGuided migration review")
    if confirm("Pull the latest 'Asana Migration Decision' values from Jira?", default=False):
        run_script(
            get_jira_migration_decisions.main,
            args_get_jira_migration_decisions(log_level),
        )
    build_reports_menu(log_level)["2"].handler()
    if confirm("Also export the project last-updated report?", default=True):
        build_reports_menu(log_level)["a"].handler()


def run_guided_project_migration(log_level: str) -> None:
    """Guide one migration wave while keeping CSV and direct-API paths distinct."""
    print("\nGuided project migration")
    print("[1] CSV import path (export -> Jira import -> bring keys back)")
    print("[2] Direct API path (creates Team/Project hierarchy and stores mappings)")
    choice = prompt("Migration path", default="1")
    if choice == "1":
        if confirm("Refresh the Atlassian user map before exporting?", default=True):
            run_script(
                update_atlassian_user_map.main,
                args_update_user_map(log_level),
            )
        if confirm("Import or update Jira project associations?", default=False):
            run_script(
                import_jira_project_map.main,
                args_import_jira_project_map(log_level),
            )
        run_export_tasks_to_jira(log_level)
        print("\nImport the generated CSV through Jira's external-import wizard.")
        if confirm("Is the Jira import complete and ready for Jira-key recovery?", default=False):
            run_script(
                search_jira_external_ids.main,
                args_search_jira_external_ids(log_level),
            )
    elif choice == "2":
        if confirm("Refresh the Atlassian user map before creating issues?", default=True):
            run_script(
                update_atlassian_user_map.main,
                args_update_user_map(log_level),
            )
        run_script(
            create_team_project_jira_issues.main,
            args_create_team_project_jira_issues(log_level),
        )
    else:
        print("Invalid migration path; no migration commands were run.")
        return

    if confirm("Upload a previously downloaded attachment archive now?", default=False):
        run_script(
            upload_jira_attachments.main,
            args_upload_attachment_archive(log_level),
        )


def _readiness_table_summary(connection: sqlite3.Connection, table: str) -> tuple[int, str]:
    """Return row count and latest sync for a table, tolerating older databases."""
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name = ?",
        (table,),
    ).fetchone()
    if not exists:
        return 0, ""
    count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    columns = {
        row[1] for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }
    latest = ""
    if "synced_at" in columns:
        latest = connection.execute(
            f'SELECT COALESCE(MAX(synced_at), "") FROM "{table}"'
        ).fetchone()[0]
    return count, latest


def _readiness_date(value: str) -> str:
    """Render an ISO-like sync value as the operator-facing calendar date."""
    return value[:10] if value else ""


def show_service_account_team_access(
    connection: sqlite3.Connection,
    team_gids: Optional[Sequence[str]] = None,
) -> None:
    """Report active-token membership in all or selected locally synced teams."""
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('teams', 'team_members')"
        )
    }
    if tables != {"teams", "team_members"}:
        print("[!] Service-account team access: team memberships have not been synced.")
        return

    if team_gids:
        placeholders = ",".join("?" for _ in team_gids)
        teams = connection.execute(
            f"SELECT gid FROM teams WHERE gid IN ({placeholders})", team_gids
        ).fetchall()
        scope = "selected"
    else:
        teams = connection.execute("SELECT gid FROM teams").fetchall()
        scope = "all"
    resolved_team_gids = [row[0] for row in teams]
    if not resolved_team_gids:
        print(f"[!] Service-account team access: no {scope} teams are available to check.")
        return

    try:
        credentials = load_credentials(PROJECT_ROOT)
        payload = AsanaHttpClient(
            token=credentials.token,
            skip_ssl_verification=credentials.skip_ssl_verification,
        ).get("/users/me")
        service_user = (payload or {}).get("data") or {}
        service_user_gid = str(service_user.get("gid") or "").strip()
        if not service_user_gid:
            raise ValueError("Asana did not return a user GID")
    except Exception as exc:  # noqa: BLE001 - readiness must remain non-blocking
        print(f"[!] Service-account team access: could not identify the active token account ({exc}).")
        return

    placeholders = ",".join("?" for _ in resolved_team_gids)
    member_count = connection.execute(
        "SELECT COUNT(DISTINCT team_gid) FROM team_members "
        f"WHERE user_gid = ? AND team_gid IN ({placeholders}) "
        "AND COALESCE(is_active, 1) = 1",
        [service_user_gid, *resolved_team_gids],
    ).fetchone()[0]
    latest = connection.execute(
        f"SELECT COALESCE(MAX(synced_at), '') FROM team_members "
        f"WHERE team_gid IN ({placeholders})",
        resolved_team_gids,
    ).fetchone()[0]
    marker = "x" if member_count == len(resolved_team_gids) else "!"
    display_name = service_user.get("name") or service_user.get("email") or service_user_gid
    latest_suffix = f", rosters latest {_readiness_date(latest)}" if latest else ""
    scope_label = "selected teams" if team_gids else "teams (all teams)"
    print(
        f"[{marker}] Service-account team access ({display_name}): {member_count:,} of "
        f"{len(resolved_team_gids):,} {scope_label}{latest_suffix}"
    )
    if member_count != len(resolved_team_gids):
        missing_scope = "selected " if team_gids else ""
        print(
            f"[!] Active service account is absent from "
            f"{len(resolved_team_gids) - member_count:,} {missing_scope}team(s); "
            "refresh team memberships or add it before syncing their projects."
        )


def show_migration_readiness() -> None:
    """Display advisory workspace-level readiness from the active SQLite snapshot."""
    db_path = active_db_path()
    print(f"\n=== Migration Readiness ===\nDatabase: {db_path}")
    if not db_path.exists():
        print("[ ] Database has not been created for this client.")
        return

    checks = [
        ("Asana projects", "projects"),
        ("Asana tasks", "tasks"),
        ("Atlassian user mappings", "user_atlassian_accounts"),
        ("Team/project Jira mappings", "team_project_jira_mappings"),
        ("Project migration decisions", "project_jira_decisions"),
        ("Task Jira mappings", "task_jira_mappings"),
        ("Attachment archives", "attachment_archives"),
        ("Jira attachment uploads", "attachment_jira_uploads"),
    ]
    connection = sqlite3.connect(str(db_path))
    try:
        for label, table in checks:
            count, latest = _readiness_table_summary(connection, table)
            marker = "x" if count else " "
            suffix = f", latest {_readiness_date(latest)}" if latest else ""
            print(f"[{marker}] {label}: {count:,}{suffix}")
        show_service_account_team_access(connection)
    finally:
        connection.close()
    site_key = _session.jira_site_key if _session else None
    config_path = latest_profile_import_config_path(resolve_credentials_path(), site_key=site_key)
    if config_path:
        print(f"[x] Jira CSV configuration: {config_path.name}")
    else:
        print("[!] Jira CSV configuration: no saved profile (system defaults remain available)")
    print("\nStatus is advisory; select a report or operation for project-level scope.")


def _arg_value(args: Sequence[str], flag: str) -> Optional[str]:
    """Return an option value from an argv list, if supplied."""
    try:
        return args[args.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def _scope_values(args: Sequence[str], flag: str) -> List[str]:
    """Read comma-separated values or a newline-delimited scope file."""
    value = _arg_value(args, flag)
    if value:
        return [item.strip() for item in value.split(",") if item.strip()]
    file_name = _arg_value(args, f"{flag}-file")
    if not file_name:
        return []
    try:
        if flag == "--project-gids":
            return parse_gid_list(read_project_gids_file(file_name))
        contents = Path(file_name).read_text().replace(",", "\n")
        return [line.strip() for line in contents.splitlines() if line.strip()]
    except (OSError, SystemExit):
        return []


def show_export_readiness(args: Sequence[str], label: str, jira_csv: bool = False) -> None:
    """Show non-blocking data coverage for the target selected by an export flow."""
    db_path = active_db_path()
    print(f"\n=== Export Readiness — {label} ===")
    if not db_path.exists():
        print("[ ] Database has not been created for this client; export may not have data.")
        return
    project_gids = _scope_values(args, "--project-gids")
    project_gid = _arg_value(args, "--project-gid")
    if project_gid:
        project_gids.append(project_gid)
    requested_project_count = len(project_gids)
    task_gids = _scope_values(args, "--task-gids")
    team_gid = _arg_value(args, "--team-gid")
    connection = sqlite3.connect(str(db_path))
    try:
        if task_gids:
            placeholders = ",".join("?" for _ in task_gids)
            selected_task_sql = f"SELECT gid FROM tasks WHERE gid IN ({placeholders})"
            selected_task_parameters = task_gids
            selected_project_sql = (
                "SELECT DISTINCT project_gid AS project_gid FROM task_projects "
                f"WHERE task_gid IN ({placeholders})"
            )
            project_count = connection.execute(
                "SELECT COUNT(DISTINCT project_gid) FROM task_projects "
                f"WHERE task_gid IN ({placeholders})",
                task_gids,
            ).fetchone()[0]
        else:
            where, parameters = "", []
            if project_gids:
                placeholders = ",".join("?" for _ in project_gids)
                where, parameters = f"WHERE p.gid IN ({placeholders})", project_gids
            elif team_gid:
                where, parameters = "WHERE p.team_gid = ?", [team_gid]
            selected_task_sql = (
                "SELECT DISTINCT tp.task_gid FROM task_projects tp "
                f"JOIN projects p ON p.gid = tp.project_gid {where}"
            )
            selected_task_parameters = parameters
            selected_project_sql = f"SELECT p.gid AS project_gid FROM projects p {where}"
            project_count = connection.execute(
                f"SELECT COUNT(*) FROM projects p {where}", parameters
            ).fetchone()[0]
        task_count = connection.execute(
            f"SELECT COUNT(*) FROM ({selected_task_sql})", selected_task_parameters
        ).fetchone()[0]
        marker = "x" if project_count and task_count else "!"
        if requested_project_count:
            print(
                f"[{marker}] Selected data: {project_count:,} of "
                f"{requested_project_count:,} requested projects / {task_count:,} tasks"
            )
            if project_count != requested_project_count:
                print(
                    f"[!] {requested_project_count - project_count:,} requested project GID(s) "
                    "were not found in this database."
                )
        else:
            print(f"[{marker}] Selected data: {project_count:,} projects / {task_count:,} tasks")
        if not task_count:
            print("[!] No selected tasks were found; verify the scope or run a task sync.")
        selected_team_gids = [
            row[0]
            for row in connection.execute(
                f"SELECT DISTINCT team_gid FROM projects WHERE gid IN ({selected_project_sql}) "
                "AND COALESCE(team_gid, '') <> ''",
                selected_task_parameters,
            )
        ]
        if jira_csv and selected_team_gids:
            show_service_account_team_access(connection, selected_team_gids)
        task_latest = connection.execute(
            f"SELECT COALESCE(MAX(synced_at), '') FROM tasks "
            f"WHERE gid IN ({selected_task_sql})",
            selected_task_parameters,
        ).fetchone()[0]
        project_latest = connection.execute(
            f"SELECT COALESCE(MAX(synced_at), '') FROM projects "
            f"WHERE gid IN ({selected_project_sql})",
            selected_task_parameters,
        ).fetchone()[0]
        print(
            "[x] Core data sync: "
            f"projects {_readiness_date(project_latest) or 'not recorded'} / "
            f"tasks {_readiness_date(task_latest) or 'not recorded'}"
        )

        extras_parameters = selected_task_parameters
        comment_count, comment_latest = connection.execute(
            "SELECT COUNT(*), COALESCE(MAX(synced_at), '') FROM task_comments "
            f"WHERE task_gid IN ({selected_task_sql})",
            extras_parameters,
        ).fetchone()
        attachment_count, attachment_latest = connection.execute(
            "SELECT COUNT(*), COALESCE(MAX(synced_at), '') FROM task_attachments "
            f"WHERE task_gid IN ({selected_task_sql})",
            extras_parameters,
        ).fetchone()
        extras_marker = "x" if comment_count or attachment_count else "!"
        print(
            f"[{extras_marker}] Task extras: {comment_count:,} comments "
            f"(latest {_readiness_date(comment_latest) or 'not recorded'}); "
            f"{attachment_count:,} attachments "
            f"(latest {_readiness_date(attachment_latest) or 'not recorded'})"
        )
        if not comment_count and not attachment_count:
            print("[!] No task-extras metadata found for this scope; run the task-extras sync.")

        referenced_user_sql = (
            "SELECT assignee_gid AS user_gid FROM tasks "
            f"WHERE gid IN ({selected_task_sql}) AND assignee_gid <> '' "
            "UNION SELECT created_by_gid FROM tasks "
            f"WHERE gid IN ({selected_task_sql}) AND created_by_gid <> '' "
            "UNION SELECT created_by_gid FROM task_comments "
            f"WHERE task_gid IN ({selected_task_sql}) AND created_by_gid <> ''"
        )
        referenced_parameters = selected_task_parameters * 3
        referenced_count, mapped_count = connection.execute(
            "SELECT COUNT(*), SUM(CASE WHEN uaa.atlassian_account_id <> '' "
            "THEN 1 ELSE 0 END) FROM ("
            f"{referenced_user_sql}"
            ") referenced LEFT JOIN user_atlassian_accounts uaa "
            "ON uaa.user_gid = referenced.user_gid",
            referenced_parameters,
        ).fetchone()
        mapped_count = mapped_count or 0
        mapping_marker = "x" if mapped_count == referenced_count else "!"
        print(
            f"[{mapping_marker}] Atlassian user mapping: {mapped_count:,} of "
            f"{referenced_count:,} referenced users mapped"
        )
        if mapped_count != referenced_count:
            print(
                f"[!] {referenced_count - mapped_count:,} referenced users lack an Atlassian account ID."
            )
        if jira_csv:
            jira_site_key = _arg_value(args, "--jira-site")
            if not jira_site_key and _session is not None:
                jira_site_key = _session.jira_site_key
            mapping_table_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'jira_project_mappings'"
            ).fetchone()
            mapped_project_count = 0
            if jira_site_key and mapping_table_exists:
                mapped_project_count = connection.execute(
                    "SELECT COUNT(DISTINCT jpm.project_gid) "
                    f"FROM ({selected_project_sql}) selected "
                    "JOIN jira_project_mappings jpm "
                    "ON jpm.project_gid = selected.project_gid AND jpm.jira_site_key = ?",
                    [*selected_task_parameters, jira_site_key],
                ).fetchone()[0]
            if jira_site_key:
                mapping_marker = "x" if mapped_project_count == project_count else "!"
                unmapped = project_count - mapped_project_count
                suffix = f"; {unmapped:,} unmapped" if unmapped else ""
                print(
                    f"[{mapping_marker}] Jira project associations ({jira_site_key}): "
                    f"{mapped_project_count:,} of {project_count:,} selected projects mapped{suffix}"
                )
            else:
                print("[!] Jira project associations: no active Jira site selected.")
            config_path = latest_profile_import_config_path(resolve_credentials_path(), site_key=jira_site_key)
            if config_path:
                print(f"[x] Jira CSV configuration: {config_path.name}")
            else:
                print("[!] Jira CSV configuration: no saved profile; system defaults will be used.")
    finally:
        connection.close()
    print("Status is advisory; warnings do not prevent export.\n")


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
        "1": MenuItem("1", "Export project tasks to Jira CSV", lambda: run_export_tasks_to_jira(log_level)),
        "2": MenuItem("2", "Export team/project Jira CSV", run_team_project_jira_export),
        "3": MenuItem("3", "Associate Jira Task Keys with Asana Task Keys", lambda: run_script(search_jira_external_ids.main, args_search_jira_external_ids(log_level))),
        "4": MenuItem("4", "Clear Jira task keys", lambda: run_script(clear_jira_task_keys.main, args_clear_jira_keys(log_level))),
        "5": MenuItem(
            "5",
            "Update Atlassian user map from Atlassian export",
            lambda: run_script(update_atlassian_user_map.main, args_update_user_map(log_level)),
        ),
        "6": MenuItem("6", "Import Jira project associations", lambda: run_script(import_jira_project_map.main, args_import_jira_project_map(log_level))),
        "7": MenuItem("7", "Clear export IDs", lambda: run_script(export_project_to_jira_csv.main, args_clear_export_ids(log_level))),
        "8": MenuItem("8", "Clear Jira project associations", lambda: run_script(export_project_to_jira_csv.main, build_common_args(log_level) + ["--clear-jira-project-map"])),
        "9": MenuItem("9", "Deprecated: Sync Jira attachments immediately", lambda: run_sync_attachments(log_level)),
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
        "14": MenuItem(
            "14",
            "Recover Jira keys from project associations",
            lambda: run_script(
                search_jira_external_ids.main,
                args_search_jira_external_ids(log_level),
            ),
        ),
        "15": MenuItem(
            "15",
            "Download durable Asana attachment archive",
            lambda: run_script(
                download_asana_attachments.main,
                args_download_attachment_archive(log_level),
            ),
        ),
        "16": MenuItem(
            "16",
            "Upload local attachment archive to Jira",
            lambda: run_script(
                upload_jira_attachments.main,
                args_upload_attachment_archive(log_level),
            ),
        ),
        "17": MenuItem(
            "17",
            "Generate Jira CSV importer configuration",
            lambda: run_script(
                generate_jira_csv_import_config.main,
                args_generate_jira_csv_import_config(log_level),
            ),
        ),
        "b": MenuItem("b", "Back", lambda: None),
    }


def build_client_environment_menu(
    reference_path: Path = PROJECT_ROOT,
) -> Dict[str, MenuItem]:
    return {
        "1": MenuItem("1", "Switch active client profile", lambda: switch_profile(reference_path)),
        "2": MenuItem("2", "Create a new client profile", lambda: bootstrap_credentials(reference_path)),
        "3": MenuItem("3", "Select active Jira site", select_jira_site),
        "4": MenuItem("4", "Set default Jira site", lambda: select_jira_site(make_default=True)),
        "5": MenuItem("5", "Add Jira site", add_jira_site),
        "6": MenuItem("6", "Edit Jira site", edit_jira_site),
        "7": MenuItem("7", "Remove Jira site", remove_jira_site),
        "8": MenuItem("8", "View or update shared credentials", lambda: manage_credentials(reference_path)),
        "b": MenuItem("b", "Back", lambda: None),
    }


def build_discover_refresh_menu(log_level: str) -> Dict[str, MenuItem]:
    return {
        "1": MenuItem("1", "Guided full Asana refresh", lambda: run_guided_asana_refresh(log_level)),
        "2": MenuItem("2", "Refresh workspace metadata", lambda: run_initial_metadata(log_level)),
        "3": MenuItem("3", "Sync project tasks", lambda: run_task_sync_flow(log_level)),
        "4": MenuItem("4", "Sync task comments and attachment metadata", lambda: run_script(get_task_extras.main, args_get_task_extras(log_level))),
        "5": MenuItem("5", "Sync project status updates", lambda: run_script(get_project_status_updates.main, args_get_project_status_updates(log_level))),
        "6": MenuItem("6", "Sync portfolios", lambda: run_script(get_portfolios.main, args_portfolios(log_level))),
        "7": MenuItem("7", "Sync personal tasks", lambda: run_script(get_personal_tasks.main, args_personal_tasks(log_level, delta=False))),
        "8": MenuItem("8", "Run incremental Asana refresh", lambda: run_script(get_incremental_updates.main, args_incremental_updates(log_level))),
        "9": MenuItem("9", "Discover and sync newly found projects", lambda: run_new_project_discovery_flow(log_level)),
        "10": MenuItem("10", "Add service account to missing teams and sync their project metadata", lambda: run_add_service_account_to_missing_teams(log_level)),
        "b": MenuItem("b", "Back", lambda: None),
    }


def build_assess_plan_menu(log_level: str) -> Dict[str, MenuItem]:
    reports = build_reports_menu(log_level)
    def report_item(key: str, report_key: str) -> MenuItem:
        item = reports[report_key]
        return MenuItem(key, item.label, item.handler)

    return {
        "1": MenuItem("1", "Guided migration readiness review", lambda: run_guided_migration_review(log_level)),
        "2": report_item("2", "1"),
        "3": report_item("3", "2"),
        "4": report_item("4", "3"),
        "5": report_item("5", "a"),
        "6": report_item("6", "9"),
        "7": report_item("7", "4"),
        "8": report_item("8", "5"),
        "9": report_item("9", "6"),
        "10": report_item("10", "7"),
        "11": report_item("11", "8"),
        "12": MenuItem("12", "Pull migration decisions from Jira", lambda: run_script(get_jira_migration_decisions.main, args_get_jira_migration_decisions(log_level))),
        "b": MenuItem("b", "Back", lambda: None),
    }


def build_prepare_jira_menu(log_level: str) -> Dict[str, MenuItem]:
    return {
        "1": MenuItem("1", "Create Jira fields for task-import mappings", lambda: run_script(create_jira_import_fields.main, args_create_jira_import_fields(log_level, "import"))),
        "2": MenuItem("2", "Create Jira fields for Team/Project tracking", lambda: run_script(create_jira_import_fields.main, args_create_jira_import_fields(log_level, "tracking"))),
        "3": MenuItem("3", "Update Atlassian user mappings", lambda: run_script(update_atlassian_user_map.main, args_update_user_map(log_level))),
        "4": MenuItem("4", "Import Jira project associations", lambda: run_script(import_jira_project_map.main, args_import_jira_project_map(log_level))),
        "5": MenuItem("5", "Preview or create Team/Project Jira hierarchy (API)", lambda: run_script(create_team_project_jira_issues.main, args_create_team_project_jira_issues(log_level))),
        "6": MenuItem("6", "Set project 'Last working day' in Jira", lambda: run_script(set_project_last_updated_jira_field.main, args_set_project_last_updated_jira_field(log_level))),
        "7": MenuItem("7", "Generate Jira CSV importer configuration", lambda: run_script(generate_jira_csv_import_config.main, args_generate_jira_csv_import_config(log_level))),
        "8": MenuItem("8", "Rename Jira CSV importer configuration", rename_jira_import_config),
        "9": MenuItem("9", "Clean up stale Jira CSV importer configuration", purge_jira_import_config),
        "10": MenuItem("10", "Configure issue type above Epic (hierarchy)", run_configure_above_epic_hierarchy),
        "b": MenuItem("b", "Back", lambda: None),
    }


def build_migrate_projects_menu(log_level: str) -> Dict[str, MenuItem]:
    return {
        "1": MenuItem("1", "Guided project migration", lambda: run_guided_project_migration(log_level)),
        "2": MenuItem("2", "Export project tasks for Jira CSV import", lambda: run_export_tasks_to_jira(log_level)),
        "3": MenuItem("3", "Associate Jira Task Keys with Asana Task Keys", lambda: run_script(search_jira_external_ids.main, args_search_jira_external_ids(log_level))),
        "4": MenuItem("4", "Validate Jira task migration coverage", build_reports_menu(log_level)["c"].handler),
        "5": MenuItem("5", "Download durable Asana attachment archive", lambda: run_script(download_asana_attachments.main, args_download_attachment_archive(log_level))),
        "6": MenuItem("6", "Upload attachment archive to Jira", lambda: run_script(upload_jira_attachments.main, args_upload_attachment_archive(log_level))),
        "b": MenuItem("b", "Back", lambda: None),
    }


def build_maintain_validate_menu(log_level: str) -> Dict[str, MenuItem]:
    return {
        "1": MenuItem("1", "Show migration readiness summary", show_migration_readiness),
        "2": MenuItem("2", "Run incremental Asana refresh", lambda: run_script(get_incremental_updates.main, args_incremental_updates(log_level))),
        "3": MenuItem("3", "Pull migration decisions from Jira", lambda: run_script(get_jira_migration_decisions.main, args_get_jira_migration_decisions(log_level))),
        "4": MenuItem("4", "Associate Jira Task Keys with Asana Task Keys", lambda: run_script(search_jira_external_ids.main, args_search_jira_external_ids(log_level))),
        "5": MenuItem("5", "Update project 'Last working day' in Jira", lambda: run_script(set_project_last_updated_jira_field.main, args_set_project_last_updated_jira_field(log_level))),
        "6": MenuItem("6", "Export project last-updated report", build_reports_menu(log_level)["a"].handler),
        "b": MenuItem("b", "Back", lambda: None),
    }


def build_advanced_recovery_menu(log_level: str) -> Dict[str, MenuItem]:
    def run_recovery_action(label: str, action: Callable[[], None]) -> None:
        client = _session.profile_label if _session else "current client"
        environment = _session.environment_label if _session else "current environment"
        if confirm(
            f"{label} for {client} ({environment})? This changes saved migration state",
            default=False,
        ):
            action()

    def reset_direct_api_mappings() -> None:
        args = args_create_team_project_jira_issues(log_level)
        args.append("--reset")
        run_script(create_team_project_jira_issues.main, args)

    return {
        "1": MenuItem("1", "Clear Jira task-key mappings", lambda: run_recovery_action("Clear Jira task-key mappings", lambda: run_script(clear_jira_task_keys.main, args_clear_jira_keys(log_level)))),
        "2": MenuItem("2", "Clear Jira project associations", lambda: run_recovery_action("Clear Jira project associations", lambda: run_script(export_project_to_jira_csv.main, build_common_args(log_level) + ["--clear-jira-project-map"]))),
        "3": MenuItem("3", "Clear export-ID history", lambda: run_recovery_action("Clear export-ID history", lambda: run_script(export_project_to_jira_csv.main, args_clear_export_ids(log_level)))),
        "4": MenuItem("4", "Reset direct-API Team/Project mappings", lambda: run_recovery_action("Reset direct-API Team/Project mappings", reset_direct_api_mappings)),
        "5": MenuItem("5", "Migrate tokens from env file to macOS Keychain", lambda: run_token_migration(PROJECT_ROOT)),
        "6": MenuItem("6", "Legacy Initialization menu", lambda: run_group("Legacy Initialization", build_init_menu(log_level))),
        "7": MenuItem("7", "Legacy Tasks menu", lambda: run_group("Legacy Tasks", build_tasks_menu(log_level))),
        "8": MenuItem("8", "Legacy Atlassian menu", lambda: run_group("Legacy Atlassian", build_atlassian_menu(log_level))),
        "9": MenuItem("9", "Legacy Reports menu", lambda: run_group("Legacy Reports", build_reports_menu(log_level))),
        "b": MenuItem("b", "Back", lambda: None),
    }


def build_top_menu(log_level: str) -> Dict[str, MenuItem]:
    return {
        "1": MenuItem("1", "Client & Environment", lambda: run_group("Client & Environment", build_client_environment_menu(PROJECT_ROOT))),
        "2": MenuItem("2", "Discover & Refresh Asana", lambda: run_group("Discover & Refresh Asana", build_discover_refresh_menu(log_level))),
        "3": MenuItem("3", "Prepare Jira", lambda: run_group("Prepare Jira", build_prepare_jira_menu(log_level))),
        "4": MenuItem("4", "Assess & Plan Migration", lambda: run_group("Assess & Plan Migration", build_assess_plan_menu(log_level))),
        "5": MenuItem("5", "Migrate Projects", lambda: run_group("Migrate Projects", build_migrate_projects_menu(log_level))),
        "6": MenuItem("6", "Maintain & Validate", lambda: run_group("Maintain & Validate", build_maintain_validate_menu(log_level))),
        "7": MenuItem("7", "Advanced & Recovery", lambda: run_group("Advanced & Recovery", build_advanced_recovery_menu(log_level))),
        "q": MenuItem("q", "Quit", lambda: sys.exit(0)),
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
    if "b" in menu and "q" not in menu:
        print("[q] Quit")


def parse_cli_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-level", default="INFO", help="Log level for the interactive driver and invoked scripts.")
    return parser.parse_args(argv)


def _run_cli(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_cli_args(argv)
    log_level = resolve_log_level(args.log_level)
    configure_logging(log_level)
    run_startup_migration(PROJECT_ROOT)
    env_path = ensure_interactive_credentials(PROJECT_ROOT)
    if _session is None:
        # Existing profile (ensure didn't bootstrap, which sets the session itself).
        _set_session(PROJECT_ROOT, env_path)
        offer_legacy_db_adoption()
    top_menu = build_top_menu(args.log_level)
    show_migration_readiness()

    while True:
        print_session_header()
        print_menu("Asana -> Jira Migration Toolkit", top_menu)
        choice = input("Select a category: ").strip()
        if choice not in top_menu:
            print("Invalid choice. Please try again.")
            continue
        item = top_menu[choice]
        try:
            item.handler()
        except CliExit:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logging.exception("Step failed: %s", exc)
        else:
            logging.info("Completed: %s", item.label)


def main(argv: Optional[Iterable[str]] = None) -> None:
    """Run the CLI and turn exits from any prompt into a clean return."""
    try:
        _run_cli(argv)
    except CliExit:
        print("\nExiting Asana -> Jira Migration Toolkit.")


if __name__ == "__main__":
    main()
