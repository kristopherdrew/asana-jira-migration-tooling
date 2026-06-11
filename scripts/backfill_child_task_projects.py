#!/usr/bin/env python3
"""Backfill child task project memberships from their parents."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from src.lib.database import Database, DEFAULT_DB_PATH
from src.lib.logging_utils import configure_logging, resolve_log_level


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=DEFAULT_DB_PATH,
        help="Path to the SQLite snapshot (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (DEBUG, INFO, WARNING, ERROR).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    log_level = resolve_log_level(args.log_level)
    configure_logging(log_level)

    database = Database(db_path=args.database)
    synced_at = datetime.now(timezone.utc)
    with database.connect() as connection:
        database.initialize(connection)
        backfilled = database.backfill_child_task_projects(connection, synced_at)
        connection.commit()

    logger.info(
        "Backfilled parent project memberships for %s child task(s).",
        backfilled,
    )


if __name__ == "__main__":
    main()
