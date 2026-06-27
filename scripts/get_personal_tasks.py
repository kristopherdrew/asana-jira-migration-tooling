#!/usr/bin/env python3
"""CLI script to sync personal (project-less) tasks into SQLite."""

from __future__ import annotations

import argparse
import logging
from typing import Iterable, Optional, Set

from src.lib.config import load_credentials
from src.lib.database import Database, synced_at_timestamp, resolve_default_db_path
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.user_backfill import UserBackfill
from src.tasks.personal_tasks import sync_personal_tasks
from scripts.personal_tasks_cli import resolve_assignees

DEFAULT_CONFIG_ROOT = "."
DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_PAGE_SIZE = 100
DEFAULT_WORKERS = 5
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-gid",
        help="Override the workspace GID from the resolved credentials.",
    )
    parser.add_argument(
        "--assignee",
        default=None,
        help="Assignee identifier (`me` or a user GID). Defaults to the API token owner.",
    )
    parser.add_argument(
        "--all-users",
        action="store_true",
        help="Sync personal tasks for every user stored in SQLite.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help=f"Page size for paginated fetches (default: {DEFAULT_PAGE_SIZE}).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of concurrent detail requests (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--modified-since",
        help="Optional ISO-8601 timestamp to limit tasks to those updated after this point.",
    )
    parser.add_argument(
        "--completed-since",
        help="Optional ISO-8601 timestamp for completed task filtering (mirrors Asana API semantics).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    credentials = load_credentials(DEFAULT_CONFIG_ROOT)
    workspace_gid = args.workspace_gid or credentials.workspace_gid
    if args.all_users and args.assignee:
        raise SystemExit("Do not combine --assignee with --all-users.")
    assignee = args.assignee or "me"

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
        known_custom_fields: Set[str] = {
            row["gid"] for row in connection.execute("SELECT gid FROM custom_fields WHERE gid IS NOT NULL")
        }

        targets = resolve_assignees(connection, assignee if not args.all_users else None, args.all_users)

        total_processed = 0
        total_skipped = 0
        overall_max: Optional[str] = None

        for target in targets:
            logger.info("Syncing personal tasks for %s...", target.label)
            result = sync_personal_tasks(
                connection=connection,
                database=database,
                client=client,
                user_backfill=user_backfill,
                known_custom_fields=known_custom_fields,
                workspace_gid=workspace_gid,
                assignee=target.selector,
                page_size=args.page_size,
                workers=args.workers,
                sync_time=sync_time,
                modified_since=args.modified_since,
                completed_since=args.completed_since,
            )
            connection.commit()
            total_processed += result.processed
            total_skipped += result.skipped
            if result.max_modified_at and (overall_max is None or result.max_modified_at > overall_max):
                overall_max = result.max_modified_at

    logger.info(
        "Personal task sync finished: processed=%s skipped=%s max_modified=%s",
        total_processed,
        total_skipped,
        overall_max,
    )


if __name__ == "__main__":
    main()
