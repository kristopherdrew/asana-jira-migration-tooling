#!/usr/bin/env python3
"""CLI script to fetch Asana users and persist them to SQLite."""

from __future__ import annotations

import argparse
import logging
from typing import Iterable, Optional

from src.lib.config import load_credentials
from src.lib.database import Database, synced_at_timestamp
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.tasks.users import fetch_workspace_users


DEFAULT_CONFIG_ROOT = "."
DEFAULT_DB_PATH = "data/asana.sqlite"
USER_PAGE_SIZE = 100
PROGRESS_INTERVAL = 10
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-gid",
        help="Override the workspace GID from the resolved credentials.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=USER_PAGE_SIZE,
        help=f"Page size for API pagination (default: {USER_PAGE_SIZE}).",
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

    client = AsanaHttpClient(
        token=credentials.token,
        skip_ssl_verification=credentials.skip_ssl_verification,
    )
    database = Database(db_path=DEFAULT_DB_PATH)
    sync_time = synced_at_timestamp()

    user_count = 0
    workspace_links = 0

    logger.info("Starting user sync for workspace %s", workspace_gid)

    with database.connect() as connection:
        database.initialize(connection)

        for user in fetch_workspace_users(
            client=client,
            workspace_gid=workspace_gid,
            page_size=args.page_size,
        ):
            database.upsert_user(connection, user, sync_time)
            user_count += 1
            workspace_links += len(user.get("workspaces") or [])

            if user_count == 1 or user_count % PROGRESS_INTERVAL == 0:
                logger.info("Processed %s user(s)...", user_count)

        connection.commit()

    logger.info("Synced %s users into %s.", user_count, DEFAULT_DB_PATH)
    logger.info("Captured %s user-workspace links.", workspace_links)


if __name__ == "__main__":
    main()
