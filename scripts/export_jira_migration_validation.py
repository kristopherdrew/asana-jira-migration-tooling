#!/usr/bin/env python3
"""Validate Jira-key coverage and optionally export tasks missing from Jira."""

from __future__ import annotations

import argparse
import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from src.lib.database import Database, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import parse_gid_list, read_project_gids_file
from src.lib.task_scope import parse_task_gids
from src.tasks.jira_migration_validation import fetch_task_validation


logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=resolve_default_db_path(), help="SQLite database path.")
    parser.add_argument("--jira-site", help="Named Jira site key; inferred when only one association site exists.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--team-gid", help="Asana team GID; validates tasks in its associated projects.")
    scope.add_argument("--project-gids", help="Comma-separated Asana project GIDs for one migration wave.")
    scope.add_argument("--project-gids-file", help="Newline- or comma-separated project GID file for one wave.")
    scope.add_argument("--task-gids", help="Comma-separated Asana task GIDs for an exact validation set.")
    scope.add_argument("--task-gids-file", help="Newline- or comma-separated task GID file.")
    parser.add_argument("--export-missing", action="store_true", help="Write a CSV containing every missing task.")
    parser.add_argument("--export-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output-dir", default="exports", help="CSV output directory (default: exports/).")
    parser.add_argument("--filename", help="Missing-task CSV filename.")
    parser.add_argument("--log-level", default="INFO", help="Logging verbosity (default: INFO).")
    return parser.parse_args(argv)


def export_missing_tasks(rows, output_dir: Path, filename: Optional[str] = None) -> tuple[Path, Path]:
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not filename:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"jira_missing_tasks_{stamp}.csv"
    path = output_dir / filename
    missing_rows = [row for row in rows if not row["jira_key"]]
    fields = ["task_gid", "task_name", "completed", "project_gids", "project_names"]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in missing_rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    gids_path = path.with_name(f"{path.stem}_task_gids.txt")
    gids_path.write_text("\n".join(str(row["task_gid"]) for row in missing_rows) + ("\n" if missing_rows else ""), encoding="utf-8")
    return path, gids_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    configure_logging(resolve_log_level(args.log_level))
    database = Database(args.database)
    raw_project_gids = args.project_gids
    if args.project_gids_file:
        raw_project_gids = read_project_gids_file(args.project_gids_file)
    project_gids = parse_gid_list(raw_project_gids)
    task_gids = None
    if args.task_gids or args.task_gids_file:
        task_gids = parse_task_gids(args.task_gids, args.task_gids_file)
    try:
        with database.connect() as connection:
            database.initialize(connection)
            site, rows, summary = fetch_task_validation(
                connection,
                args.jira_site,
                project_gids or None,
                args.team_gid,
                task_gids,
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if not args.export_only:
        print(f"Jira site: {site}")
        if project_gids:
            scope_label = f"wave ({len(project_gids)} projects)"
        elif args.team_gid:
            scope_label = f"team {args.team_gid}"
        elif task_gids:
            scope_label = f"exact task list ({len(task_gids)} tasks)"
        else:
            scope_label = "all associated projects"
        print(f"Validation scope: {scope_label}")
        print(f"Associated Asana tasks: {summary.total:,}")
        print(f"Valid Jira keys found: {summary.found:,}")
        print(f"Missing Jira keys: {summary.missing:,}")
        print(f"Migration coverage: {summary.coverage_percent:.2f}%")
    if args.export_missing:
        path, gids_path = export_missing_tasks(rows, Path(args.output_dir), args.filename)
        logger.info("Exported %s missing task(s) to %s.", summary.missing, path)
        logger.info("Focused reimport GID list written to %s.", gids_path)


if __name__ == "__main__":
    main()
