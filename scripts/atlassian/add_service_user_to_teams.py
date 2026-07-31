#!/usr/bin/env python3
"""Add a service user to every team stored in SQLite."""

from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import load_credentials
from src.lib.database import Database, DEFAULT_DB_PATH, resolve_default_db_path
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.tasks.users import resolve_service_user_gid


DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_CONFIG_ROOT = "."
USER_SEARCH_PAGE_SIZE = 20
PROGRESS_INTERVAL = 10
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to the SQLite database (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to store failure exports (default: exports/).",
    )
    parser.add_argument(
        "--user-gid",
        help=(
            "Service user GID to add to every team. If omitted, defaults to the token's "
            "own account (via GET /users/me)."
        ),
    )
    parser.add_argument(
        "--search-users",
        action="store_true",
        help="Search the current users in SQLite and pick the service user GID interactively.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


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


def _user_search_rows(connection: sqlite3.Connection, term: str, limit: int, offset: int) -> Sequence[sqlite3.Row]:
    like = f"%{term.lower()}%"
    return connection.execute(
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


def _has_users(connection: sqlite3.Connection) -> bool:
    row = connection.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    return row is not None


def prompt_for_service_user_gid(connection: sqlite3.Connection, search_users: bool = False) -> str:
    if search_users and _has_users(connection):
        return _prompt_user_gid_from_search(connection)
    if not search_users and _has_users(connection) and prompt_bool(
        "Search current users from the DB before typing the service user GID?",
        default=True,
    ):
        return _prompt_user_gid_from_search(connection)
    return prompt_required("Service user GID")


def _prompt_user_gid_from_search(connection: sqlite3.Connection) -> str:
    term = prompt("Search current users by name, email, or GID", default="")
    offset = 0
    while term:
        results = _user_search_rows(connection, term, limit=USER_SEARCH_PAGE_SIZE, offset=offset)
        if not results:
            print("No matches. Try another term.")
            term = prompt("Search current users by name, email, or GID", default="")
            offset = 0
            continue

        print("\nMatches:")
        for idx, row in enumerate(results, start=1):
            label = row["name"] or row["gid"]
            if row["email"]:
                label = f"{label} <{row['email']}>"
            print(f"{idx}. {label} (gid {row['gid']})")

        nav = prompt("Pick a number, 'n' for next page, or Enter to type a GID", default="")
        if nav.isdigit():
            pick_idx = int(nav)
            if 1 <= pick_idx <= len(results):
                return results[pick_idx - 1]["gid"]
        elif nav.lower() == "n":
            offset += USER_SEARCH_PAGE_SIZE
            continue
        else:
            break

    return prompt_required("Service user GID")


def build_add_user_payload(user_gid: str) -> dict:
    return {
        "data": {
            "resource_type": "team_membership",
            "user": {"gid": user_gid},
            "is_guest": False,
            "is_limited_access": False,
            "is_admin": True,
        }
    }


def add_service_user_to_teams(
    connection: sqlite3.Connection,
    client: AsanaHttpClient,
    user_gid: str,
) -> Tuple[int, List[Tuple[str, str]]]:
    rows = connection.execute(
        """
        SELECT gid, name
        FROM teams
        ORDER BY name COLLATE NOCASE, gid
        """
    ).fetchall()
    if not rows:
        raise SystemExit("No teams found in the database. Run get_teams.py first.")

    payload = build_add_user_payload(user_gid)
    success_count = 0
    failures: List[Tuple[str, str]] = []
    for index, row in enumerate(rows, start=1):
        team_gid = row["gid"]
        team_name = row["name"] or team_gid
        try:
            client.post_json(f"/teams/{team_gid}/addUser", payload)
            success_count += 1
            if index == 1 or index % PROGRESS_INTERVAL == 0:
                logger.info("Processed %s team(s)...", index)
        except Exception as exc:  # noqa: BLE001
            error_text = str(exc)
            failures.append((team_gid, error_text))
            logger.warning("Failed to add service user %s to team %s (%s): %s", user_gid, team_name, team_gid, error_text)
    return success_count, failures


def write_failure_report(output_dir: Path, failed_teams: Sequence[Tuple[str, str]]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = output_dir / f"service_user_team_add_failures_{timestamp}.csv"
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["team_gid", "error"])
        writer.writeheader()
        for team_gid, error in failed_teams:
            writer.writerow({"team_gid": team_gid, "error": error})
    return output_path


def _resolve_service_user_gid(
    client: AsanaHttpClient,
    database: Database,
    connection: sqlite3.Connection,
    args: argparse.Namespace,
) -> str:
    """Pick the service user GID: explicit flag > interactive search > token account."""
    if args.user_gid:
        return args.user_gid
    if args.search_users:
        return prompt_for_service_user_gid(connection, search_users=True)
    try:
        user_gid = resolve_service_user_gid(
            client, database, connection, datetime.now(timezone.utc)
        )
        logger.info("Using token account (gid %s) as the service user.", user_gid)
        return user_gid
    except Exception as exc:  # noqa: BLE001 - fall back to the interactive prompt on any failure
        logger.warning(
            "Could not resolve the token account via /users/me (%s); prompting instead.", exc
        )
        return prompt_for_service_user_gid(connection, search_users=False)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    credentials = load_credentials(DEFAULT_CONFIG_ROOT)
    client = AsanaHttpClient(
        token=credentials.token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )
    database = Database(db_path=args.database)

    with database.connect() as connection:
        database.initialize(connection)
        user_gid = _resolve_service_user_gid(client, database, connection, args)
        success_count, failures = add_service_user_to_teams(connection, client, user_gid)

    logger.info("Added service user %s to %s team(s).", user_gid, success_count)
    if failures:
        report_path = write_failure_report(Path(args.output_dir), failures)
        raise SystemExit(f"{len(failures)} team(s) failed to update. See {report_path}.")


if __name__ == "__main__":
    main()
