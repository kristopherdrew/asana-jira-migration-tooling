#!/usr/bin/env python3
"""CLI script to fetch Asana teams and persist them to SQLite."""

from __future__ import annotations

import argparse
import logging
from typing import Iterable, Optional

from src.lib.config import load_credentials
from src.lib.database import Database, synced_at_timestamp
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.tasks.teams import fetch_team_memberships, fetch_workspace_teams


DEFAULT_CONFIG_ROOT = "."
DEFAULT_DB_PATH = "data/asana.sqlite"
TEAM_PAGE_SIZE = 100
MEMBER_PAGE_SIZE = 50
PROGRESS_INTERVAL = 10
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--include-members",
        action="store_true",
        help="Fetch and persist team memberships alongside teams.",
    )
    parser.add_argument(
        "--workspace-gid",
        help="Override the workspace GID from the resolved credentials.",
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

    team_count = 0
    membership_count = 0

    logger.info("Starting team sync for workspace %s", workspace_gid)

    with database.connect() as connection:
        database.initialize(connection)

        for team in fetch_workspace_teams(
            client=client,
            workspace_gid=workspace_gid,
            page_size=TEAM_PAGE_SIZE,
        ):
            database.upsert_team(connection, team, sync_time)
            team_count += 1
            if team_count == 1 or team_count % PROGRESS_INTERVAL == 0:
                logger.info("Processed %s team(s)...", team_count)

            if args.include_members:
                memberships = fetch_team_memberships(
                    client=client,
                    team_gid=team.get("gid"),
                    page_size=MEMBER_PAGE_SIZE,
                )
                database.upsert_team_members(connection, team.get("gid"), memberships, sync_time)
                membership_count += len(memberships)
                if (team_count == 1 or team_count % PROGRESS_INTERVAL == 0) and membership_count:
                    logger.info(
                        "Captured %s membership row(s) so far for workspace %s.",
                        membership_count,
                        workspace_gid,
                    )

        connection.commit()

    logger.info("Synced %s teams into %s.", team_count, DEFAULT_DB_PATH)
    if args.include_members:
        logger.info("Captured %s team membership row(s).", membership_count)


if __name__ == "__main__":
    main()
