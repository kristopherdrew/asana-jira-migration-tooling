#!/usr/bin/env python3
"""CLI script to fetch Asana custom fields and persist them to SQLite."""

from __future__ import annotations

import argparse
import logging
from typing import Iterable, Optional

from src.lib.config import load_credentials
from src.lib.database import Database, synced_at_timestamp, resolve_default_db_path
from src.lib.http_client import AsanaHttpClient
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.tasks.custom_fields import fetch_custom_field, fetch_workspace_custom_fields


DEFAULT_CONFIG_ROOT = "."
DEFAULT_DB_PATH = "data/asana.sqlite"
CUSTOM_FIELD_PAGE_SIZE = 100
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
        default=CUSTOM_FIELD_PAGE_SIZE,
        help=f"Page size for API pagination (default: {CUSTOM_FIELD_PAGE_SIZE}).",
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
    database = Database(db_path=resolve_default_db_path(DEFAULT_DB_PATH))
    sync_time = synced_at_timestamp()

    field_count = 0
    enum_option_count = 0
    local_field_count = 0

    logger.info("Starting custom field sync for workspace %s", workspace_gid)

    with database.connect() as connection:
        database.initialize(connection)

        for custom_field in fetch_workspace_custom_fields(
            client=client,
            workspace_gid=workspace_gid,
            page_size=args.page_size,
        ):
            database.upsert_custom_field(connection, custom_field, sync_time)
            field_count += 1
            enum_option_count += len(custom_field.get("enum_options") or [])

            if field_count == 1 or field_count % PROGRESS_INTERVAL == 0:
                logger.info("Processed %s custom field(s)...", field_count)

        connection.commit()

        cursor = connection.execute(
            """
            SELECT DISTINCT custom_field_gid
            FROM project_custom_field_settings
            WHERE custom_field_gid IS NOT NULL
              AND COALESCE(is_global_to_workspace, 0) = 0
            """
        )
        local_gids = {row["custom_field_gid"] for row in cursor}
        existing_field_ids = {
            row["gid"]
            for row in connection.execute("SELECT gid FROM custom_fields WHERE gid IS NOT NULL")
        }

        pending_local_gids = sorted(gid for gid in local_gids if gid not in existing_field_ids)

        if pending_local_gids:
            logger.info(
                "Discovered %s project-local custom field(s) to backfill...",
                len(pending_local_gids),
            )

        for index, local_gid in enumerate(pending_local_gids, start=1):
            custom_field = fetch_custom_field(client, local_gid)
            database.upsert_custom_field(connection, custom_field, sync_time)
            enum_option_count += len(custom_field.get("enum_options") or [])
            local_field_count += 1
            connection.commit()
            if index == 1 or index % PROGRESS_INTERVAL == 0:
                logger.info("Backfilled %s project-local custom field(s)...", index)

    logger.info("Synced %s custom fields into %s.", field_count, DEFAULT_DB_PATH)
    if local_field_count:
        logger.info("Backfilled %s project-local custom field(s).", local_field_count)
    logger.info("Captured %s enum option rows.", enum_option_count)


if __name__ == "__main__":
    main()
