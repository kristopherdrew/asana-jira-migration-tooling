#!/usr/bin/env python3
"""Legacy helper to rebuild task hierarchy depths for existing SQLite snapshots."""

from __future__ import annotations

import argparse
import logging
from typing import Iterable, Optional

from src.lib.database import Database
from src.lib.logging_utils import configure_logging, resolve_log_level


DEFAULT_DB_PATH = "data/asana.sqlite"
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=DEFAULT_DB_PATH,
        help="Path to the SQLite database (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Only recalc tasks missing a stored hierarchy_depth.",
    )
    parser.add_argument(
        "--rebuild-flattened",
        action="store_true",
        help="Also rebuild task_hierarchy_flattened (used by Jira exports).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    configure_logging(resolve_log_level(args.log_level))
    database = Database(args.database)
    connection = database.connect()
    try:
        database.initialize(connection)
        stats = database.recalculate_all_task_depths(
            connection,
            incremental=args.incremental,
        )
        logger.info(
            "Recalculated hierarchy depths (incremental=%s): %s",
            args.incremental,
            stats,
        )
        if args.rebuild_flattened:
            flat_stats = database.rebuild_flattened_task_hierarchy(connection)
            logger.info("Rebuilt flattened hierarchy: %s", flat_stats)
    finally:
        connection.close()


if __name__ == "__main__":
    main()
