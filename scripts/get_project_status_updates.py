#!/usr/bin/env python3
"""CLI script to fetch project-level status updates and comments into SQLite.

Captures the narrative content that lives on an Asana project itself — status
updates and project comments — including for projects that have no tasks. Status
updates and their auto-generated comment mirrors are folded into a single
``project_updates`` table tagged by ``kind`` (see
:func:`src.tasks.project_extras.merge_project_updates`).
"""

from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional, Set

from src.lib.config import load_credentials
from src.lib.database import Database, synced_at_timestamp, resolve_default_db_path
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import determine_projects, validate_scope_args
from src.lib.team_picker import choose_team
from src.lib.user_backfill import UserBackfill
from src.tasks.project_extras import (
    fetch_project_comments,
    fetch_project_status_updates,
    merge_project_updates,
)


DEFAULT_CONFIG_ROOT = "."
DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_WORKERS = 5
PROGRESS_INTERVAL = 25
COMMIT_INTERVAL = 25
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-gid",
        help="Override the workspace GID from the resolved credentials (for logging only).",
    )
    parser.add_argument(
        "--project-gid",
        help="Sync a single project by GID.",
    )
    parser.add_argument(
        "--project-gids",
        help="Comma-separated list of project GIDs to sync.",
    )
    parser.add_argument(
        "--team",
        action="store_true",
        help="Interactively select a team to sync.",
    )
    parser.add_argument(
        "--team-gids",
        help="Comma-separated list of team GIDs to sync sequentially.",
    )
    parser.add_argument(
        "--all-teams",
        action="store_true",
        help="Sync every team present in the database (combine with --exclude-team-gids).",
    )
    parser.add_argument(
        "--exclude-team-gids",
        help="Comma-separated team GIDs to skip when using --all-teams.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of concurrent workers (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def collect_user_gids(updates: Iterable[Dict]) -> Set[str]:
    user_gids: Set[str] = set()
    for update in updates:
        gid = update.get("created_by_gid")
        if gid:
            user_gids.add(gid)
    return user_gids


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    validate_scope_args(
        args.project_gid,
        args.team,
        args.team_gids,
        args.all_teams,
        args.exclude_team_gids,
        args.project_gids,
    )
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    credentials = load_credentials(DEFAULT_CONFIG_ROOT)
    workspace_gid = args.workspace_gid or credentials.workspace_gid

    client = AsanaHttpClient(
        token=credentials.token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )
    database = Database(db_path=resolve_default_db_path(DEFAULT_DB_PATH))
    sync_time = synced_at_timestamp()

    with database.connect() as connection:
        database.initialize(connection)
        user_backfill = UserBackfill(client, database)
        user_backfill.prime(connection)

        projects = determine_projects(
            connection=connection,
            project_gid=args.project_gid,
            use_team_picker=args.team,
            team_gids=args.team_gids,
            all_teams=args.all_teams,
            exclude_team_gids=args.exclude_team_gids,
            team_selector=choose_team,
            project_gids=args.project_gids,
        )

        if not projects:
            logger.warning("No projects selected for status-update sync.")
            return

        logger.info(
            "Preparing to sync status updates for %s project(s) in workspace %s.",
            len(projects),
            workspace_gid,
        )

        def worker(project: Dict[str, Optional[str]]) -> tuple[Dict[str, Optional[str]], List[Dict]]:
            project_gid = project["project_gid"]
            status_updates = fetch_project_status_updates(client, project_gid)
            comments = fetch_project_comments(client, project_gid)
            return project, merge_project_updates(status_updates, comments)

        processed = 0
        committed_since = 0
        totals = {"status_update": 0, "comment": 0}
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            future_map = {executor.submit(worker, project): project for project in projects}
            for future in as_completed(future_map):
                project = future_map[future]
                project_gid = project["project_gid"]
                project_name = project.get("project_name")
                try:
                    _, updates = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Failed to fetch status updates for project '%s' (%s): %s",
                        project_name,
                        project_gid,
                        exc,
                    )
                    continue

                user_backfill.ensure_users(connection, collect_user_gids(updates), sync_time)
                database.replace_project_updates(connection, project_gid, updates, sync_time)

                for update in updates:
                    kind = update.get("kind")
                    if kind in totals:
                        totals[kind] += 1

                processed += 1
                committed_since += 1
                if committed_since >= COMMIT_INTERVAL:
                    connection.commit()
                    committed_since = 0

                if processed == 1 or processed % PROGRESS_INTERVAL == 0:
                    logger.info("Processed status updates for %s project(s)...", processed)

        if committed_since:
            connection.commit()
        connection.commit()

    logger.info(
        "Project status-update sync completed. Projects: %s, status updates: %s, comments: %s.",
        processed,
        totals["status_update"],
        totals["comment"],
    )


if __name__ == "__main__":
    main()
