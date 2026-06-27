#!/usr/bin/env python3
"""Remove Jira issue key mappings from SQLite (optionally scoped to specific tasks)."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.database import Database, DEFAULT_DB_PATH, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level


logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to the SQLite database (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--task-gids",
        help="Optional comma-separated list of Asana task GIDs to clear. If omitted, clears every mapping.",
    )
    parser.add_argument(
        "--project-gid",
        help="Optional Asana project GID; clears mappings for tasks within this project (ignored if combined with --task-gids).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def clear_jira_mappings(
    database_path: str,
    task_gids: Optional[List[str]] = None,
    project_gid: Optional[str] = None,
) -> int:
    database = Database(db_path=database_path)
    with database.connect() as connection:
        database.initialize(connection)
        sql = "DELETE FROM task_jira_mappings"
        params: List[str] = []
        if task_gids:
            placeholders = ",".join("?" for _ in task_gids)
            sql += f" WHERE task_gid IN ({placeholders})"
            params.extend(task_gids)
        elif project_gid:
            sql += " WHERE task_gid IN (SELECT task_gid FROM task_projects WHERE project_gid = ?)"
            params.append(project_gid)
        cursor = connection.execute(sql, tuple(params))
        connection.commit()
        return cursor.rowcount if cursor else 0


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    tasks = (
        [value.strip() for value in args.task_gids.split(",") if value.strip()]
        if args.task_gids
        else None
    )

    count = clear_jira_mappings(args.database, tasks, args.project_gid)

    if tasks:
        logger.info("Cleared %s Jira mapping(s) for %s task(s).", count, len(tasks))
    elif args.project_gid:
        logger.info("Cleared %s Jira mapping(s) for project %s.", count, args.project_gid)
    else:
        logger.info("Cleared all Jira mappings (%s row(s)).", count)


if __name__ == "__main__":
    main()
