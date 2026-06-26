#!/usr/bin/env python3
"""CLI script to fetch Asana portfolios (and their items, memberships, custom field
settings, and status updates) and persist them to SQLite.

Asana cannot list portfolios workspace-wide: ``GET /portfolios`` requires an ``owner``.
This script therefore iterates over every user in the ``users`` table (or a single
``--owner-gid``) to discover the portfolios they own, then captures each portfolio's
sub-resources. Items whose ``resource_type`` is ``portfolio`` (nested portfolios) are
recursed into and fetched even when no iterated user owns them, with a ``seen`` set
guarding against cycles and duplicate work.
"""

from __future__ import annotations

import argparse
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterable, List, Optional, Set

from src.lib.config import load_credentials
from src.lib.database import Database, synced_at_timestamp
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.tasks.project_extras import merge_project_updates
from src.tasks.portfolios import (
    fetch_portfolio,
    fetch_portfolio_custom_field_settings,
    fetch_portfolio_items,
    fetch_portfolio_memberships,
    fetch_portfolio_status_updates,
    fetch_user_portfolios,
)


DEFAULT_CONFIG_ROOT = "."
DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_WORKERS = 5
PORTFOLIO_PAGE_SIZE = 100
PROGRESS_INTERVAL = 10
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace-gid",
        help="Override the workspace GID from the resolved credentials.",
    )
    parser.add_argument(
        "--owner-gid",
        help="Limit discovery to a single portfolio owner (defaults to every user in the database).",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=PORTFOLIO_PAGE_SIZE,
        help=f"Page size for API pagination (default: {PORTFOLIO_PAGE_SIZE}).",
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


def _owner_gids(connection, owner_gid: Optional[str]) -> List[str]:
    if owner_gid:
        return [owner_gid]
    rows = connection.execute("SELECT gid FROM users ORDER BY gid").fetchall()
    return [row["gid"] for row in rows if row["gid"]]


def _discover_owned_portfolios(
    client: AsanaHttpClient,
    workspace_gid: str,
    owner_gids: List[str],
    page_size: int,
    workers: int,
) -> Dict[str, Dict]:
    """Return a gid -> portfolio-record map for every portfolio owned by the given users."""

    def worker(owner_gid: str) -> List[Dict]:
        return list(
            fetch_user_portfolios(
                client,
                workspace_gid=workspace_gid,
                owner_gid=owner_gid,
                page_size=page_size,
            )
        )

    records: Dict[str, Dict] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {executor.submit(worker, gid): gid for gid in owner_gids}
        for future in as_completed(future_map):
            owner_gid = future_map[future]
            try:
                portfolios = future.result()
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to list portfolios for owner %s: %s", owner_gid, exc)
                continue
            for portfolio in portfolios:
                gid = portfolio.get("gid")
                if gid and gid not in records:
                    records[gid] = portfolio
    return records


def _fetch_subresources(client: AsanaHttpClient, portfolio_gid: str) -> Dict[str, List[Dict]]:
    return {
        "items": fetch_portfolio_items(client, portfolio_gid),
        "custom_field_settings": fetch_portfolio_custom_field_settings(client, portfolio_gid),
        "memberships": fetch_portfolio_memberships(client, portfolio_gid),
        "status_updates": fetch_portfolio_status_updates(client, portfolio_gid),
    }


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

    totals = {
        "portfolios": 0,
        "items": 0,
        "memberships": 0,
        "custom_field_settings": 0,
        "status_updates": 0,
        "nested": 0,
    }

    with database.connect() as connection:
        database.initialize(connection)

        owner_gids = _owner_gids(connection, args.owner_gid)
        if not owner_gids:
            logger.warning("No users found to use as portfolio owners. Run get_users first.")
            return

        logger.info(
            "Discovering portfolios for %s owner(s) in workspace %s...",
            len(owner_gids),
            workspace_gid,
        )
        records = _discover_owned_portfolios(
            client, workspace_gid, owner_gids, args.page_size, args.workers
        )

        seen: Set[str] = set(records)
        queue: List[str] = list(records)
        logger.info("Discovered %s owned portfolio(s). Capturing details...", len(queue))

        while queue:
            batch = queue
            queue = []
            nested_to_fetch: List[str] = []

            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
                future_map = {
                    executor.submit(_fetch_subresources, client, gid): gid for gid in batch
                }
                for future in as_completed(future_map):
                    portfolio_gid = future_map[future]
                    try:
                        sub = future.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Failed to fetch details for portfolio %s: %s", portfolio_gid, exc)
                        continue

                    record = dict(records.get(portfolio_gid) or {"gid": portfolio_gid})
                    record["custom_field_settings"] = sub["custom_field_settings"]
                    database.upsert_portfolio(connection, record, sync_time)
                    database.replace_portfolio_items(connection, portfolio_gid, sub["items"], sync_time)
                    database.replace_portfolio_memberships(
                        connection, portfolio_gid, sub["memberships"], sync_time
                    )
                    normalized_updates = merge_project_updates(sub["status_updates"], [])
                    database.replace_portfolio_updates(
                        connection, portfolio_gid, normalized_updates, sync_time
                    )

                    totals["portfolios"] += 1
                    totals["items"] += len(sub["items"])
                    totals["memberships"] += len(sub["memberships"])
                    totals["custom_field_settings"] += len(sub["custom_field_settings"])
                    totals["status_updates"] += len(normalized_updates)

                    for item in sub["items"]:
                        if item.get("resource_type") != "portfolio":
                            continue
                        child_gid = item.get("gid")
                        if child_gid and child_gid not in seen:
                            seen.add(child_gid)
                            nested_to_fetch.append(child_gid)

                    if totals["portfolios"] == 1 or totals["portfolios"] % PROGRESS_INTERVAL == 0:
                        logger.info("Captured %s portfolio(s)...", totals["portfolios"])

            connection.commit()

            # Fetch the records for any newly discovered nested portfolios, then enqueue them.
            for child_gid in nested_to_fetch:
                try:
                    records[child_gid] = fetch_portfolio(client, child_gid)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Failed to fetch nested portfolio %s: %s", child_gid, exc)
                    records[child_gid] = {"gid": child_gid}
                queue.append(child_gid)
                totals["nested"] += 1

        connection.commit()

    logger.info(
        "Synced %s portfolios into %s (%s nested) — %s items, %s memberships, "
        "%s custom field settings, %s status updates.",
        totals["portfolios"],
        DEFAULT_DB_PATH,
        totals["nested"],
        totals["items"],
        totals["memberships"],
        totals["custom_field_settings"],
        totals["status_updates"],
    )


if __name__ == "__main__":
    main()
