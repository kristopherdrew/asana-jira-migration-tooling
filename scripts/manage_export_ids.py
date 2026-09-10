#!/usr/bin/env python3
"""Review and clear persisted Jira export-ID history."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.export_id_history import (  # noqa: E402
    clear_all_export_ids,
    clear_export_id,
    get_export_id_detail,
    list_export_ids,
)
from src.lib.database import Database, resolve_default_db_path  # noqa: E402
from src.lib.logging_utils import configure_logging, resolve_log_level  # noqa: E402


logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to the SQLite database (default: active profile database).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="List recorded export IDs.")
    action.add_argument("--show", metavar="EXPORT_ID", help="Show details for one export ID.")
    action.add_argument(
        "--clear-export-id",
        metavar="EXPORT_ID",
        help="Delete recorded task history for one export ID.",
    )
    action.add_argument(
        "--clear-all",
        action="store_true",
        help="Delete all recorded export-ID task history.",
    )
    return parser.parse_args(argv)


def _print_summaries(database: Database) -> None:
    summaries = list_export_ids(database)
    if not summaries:
        print("No recorded export IDs found.")
        return
    print("Export ID | Tasks | Projects | First used | Last used")
    for summary in summaries:
        print(
            f"{summary.export_id} | {summary.task_count:,} | {summary.project_count:,} | "
            f"{summary.first_used or 'unknown'} | {summary.last_used or 'unknown'}"
        )


def _print_detail(database: Database, export_id: str) -> None:
    detail = get_export_id_detail(database, export_id)
    if detail is None:
        raise SystemExit(f"Export ID not found: {export_id}")
    summary = detail.summary
    print(f"Export ID: {summary.export_id}")
    print(f"Tasks: {summary.task_count:,}")
    print(f"Projects: {summary.project_count:,}")
    print(f"First used: {summary.first_used or 'unknown'}")
    print(f"Last used: {summary.last_used or 'unknown'}")
    if not detail.projects:
        return
    print("\nProjects:")
    for project in detail.projects:
        label = project.project_gid
        if project.project_name:
            label = f"{project.project_name} ({project.project_gid})"
        print(
            f"- {label}: {project.task_count:,} tasks, "
            f"last used {project.last_used or 'unknown'}"
        )


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    database = Database(db_path=args.database)
    if args.list:
        _print_summaries(database)
        return
    if args.show:
        _print_detail(database, args.show)
        return
    if args.clear_export_id:
        cleared = clear_export_id(database, args.clear_export_id)
        logger.info(
            "Cleared %s recorded export_id/task mapping(s) for export ID %s.",
            cleared,
            args.clear_export_id,
        )
        print(f"Cleared {cleared:,} recorded task mapping(s) for export ID '{args.clear_export_id}'.")
        return
    if args.clear_all:
        cleared = clear_all_export_ids(database)
        logger.info("Cleared %s recorded export_id/task mapping(s).", cleared)
        print(f"Cleared {cleared:,} recorded export-ID task mapping(s).")


if __name__ == "__main__":  # pragma: no cover
    main()
