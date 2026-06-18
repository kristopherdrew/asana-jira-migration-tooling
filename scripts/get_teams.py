#!/usr/bin/env python3
"""CLI script to fetch Asana teams and persist them to SQLite."""

from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable, List, Optional

from src.lib.config import load_credentials
from src.lib.database import Database, synced_at_timestamp
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.team_picker import search_team
from src.tasks.teams import fetch_team_memberships, fetch_workspace_teams


DEFAULT_CONFIG_ROOT = "."
DEFAULT_DB_PATH = "data/asana.sqlite"
TEAM_PAGE_SIZE = 100
MEMBER_PAGE_SIZE = 50
DEFAULT_WORKERS = 5
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
        "--update-team-members",
        action="store_true",
        help=(
            "Interactively search for a single team already in the database and "
            "refresh only its membership association. Skips the full team sync."
        ),
    )
    parser.add_argument(
        "--workspace-gid",
        help="Override the workspace GID from the resolved credentials.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of concurrent workers for membership fetches (default: {DEFAULT_WORKERS}).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def sync_team_memberships(
    *,
    client: AsanaHttpClient,
    database: Database,
    connection,
    team_gids: List[str],
    workers: int,
    sync_time,
) -> int:
    """Fetch memberships for each team concurrently and upsert them.

    Network fetches run on a thread pool while SQLite writes stay on the calling
    thread. Returns the total number of membership rows captured.
    """
    membership_count = 0
    if not team_gids:
        return membership_count

    processed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_to_gid = {
            executor.submit(
                fetch_team_memberships,
                client=client,
                team_gid=team_gid,
                page_size=MEMBER_PAGE_SIZE,
            ): team_gid
            for team_gid in team_gids
        }
        for future in as_completed(future_to_gid):
            team_gid = future_to_gid[future]
            try:
                memberships = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed fetching memberships for team %s: %s", team_gid, exc)
                continue
            database.upsert_team_members(connection, team_gid, memberships, sync_time)
            membership_count += len(memberships)
            processed += 1
            if processed == 1 or processed % PROGRESS_INTERVAL == 0:
                logger.info(
                    "Captured memberships for %s/%s team(s) (%s row(s) so far).",
                    processed,
                    len(team_gids),
                    membership_count,
                )
    return membership_count


def update_single_team_members(
    *,
    client: AsanaHttpClient,
    database: Database,
    workers: int,
) -> None:
    """Search for one team in the database and refresh only its memberships."""
    sync_time = synced_at_timestamp()
    with database.connect() as connection:
        database.initialize(connection)
        selection = search_team(connection)
        if selection is None:
            logger.info("No team selected; nothing to update.")
            return
        team_gid, team_name = selection
        logger.info("Refreshing memberships for team '%s' (%s)...", team_name, team_gid)
        membership_count = sync_team_memberships(
            client=client,
            database=database,
            connection=connection,
            team_gids=[team_gid],
            workers=workers,
            sync_time=sync_time,
        )
        connection.commit()
    logger.info(
        "Updated %s membership row(s) for team '%s' (%s).",
        membership_count,
        team_name,
        team_gid,
    )


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

    if args.update_team_members:
        update_single_team_members(client=client, database=database, workers=args.workers)
        return

    sync_time = synced_at_timestamp()
    team_count = 0
    member_team_gids: List[str] = []

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
            if args.include_members and team.get("gid"):
                member_team_gids.append(team.get("gid"))

        membership_count = 0
        if args.include_members:
            logger.info("Fetching memberships for %s team(s)...", len(member_team_gids))
            membership_count = sync_team_memberships(
                client=client,
                database=database,
                connection=connection,
                team_gids=member_team_gids,
                workers=args.workers,
                sync_time=sync_time,
            )

        connection.commit()

    logger.info("Synced %s teams into %s.", team_count, DEFAULT_DB_PATH)
    if args.include_members:
        logger.info("Captured %s team membership row(s).", membership_count)


if __name__ == "__main__":
    main()
