#!/usr/bin/env python3
"""Export Asana projects as Jira Epics with project status updates as comments."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.database import Database, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level
from scripts.atlassian.export_team_project_jira_csv import build_team_project_rows
from scripts.atlassian.export_project_to_jira_csv import fetch_project_update_comment_rows

DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Asana projects as Jira Epics with status updates as comments."
    )
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(DEFAULT_DB_PATH),
        help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for CSV export (default: {DEFAULT_OUTPUT_DIR}).",
    )
    parser.add_argument(
        "--filename",
        help="Custom CSV filename (default: auto-generated with timestamp).",
    )
    parser.add_argument(
        "--workspace-gid",
        help="Override the workspace GID from the resolved credentials (for logging only).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO).",
    )
    return parser.parse_args(argv)


def export_team_project_epic_jira_csv(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    workspace_gid: Optional[str] = None,
) -> tuple[Path, int, int]:
    """Export Asana projects as Epics with comments, ready for Jira import.

    Returns: (output_path, epic_count, comment_count)
    """
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"epic_import_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing Jira epic import export to %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)
        epic_rows, _ = build_team_project_rows(
            connection, workspace_gid, include_empty_projects=True
        )

        # Fetch comments (project status updates) for each epic
        epic_comments = {}
        for epic_row in epic_rows:
            project_gid = epic_row.get("_asana_gid")
            if project_gid:
                comments = fetch_project_update_comment_rows(connection, project_gid)
                if comments:
                    epic_comments[project_gid] = comments

    # Prepare output rows: epics + comments
    output_rows = []
    comment_count = 0

    for epic_row in epic_rows:
        output_rows.append(epic_row)
        project_gid = epic_row.get("_asana_gid")
        if project_gid and project_gid in epic_comments:
            output_rows.extend(epic_comments[project_gid])
            comment_count += len(epic_comments[project_gid])

    # Determine CSV columns from first epic row
    if epic_rows:
        csv_columns = [k for k in epic_rows[0].keys() if not k.startswith("_")]
    else:
        csv_columns = []

    logger.debug("CSV columns: %s", csv_columns)

    # Write CSV
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_columns, restval="")
        writer.writeheader()
        for row in output_rows:
            filtered_row = {k: row.get(k, "") for k in csv_columns}
            writer.writerow(filtered_row)

    logger.info(
        "Exported %s epic row(s) and %s comment row(s) to %s.",
        len(epic_rows),
        comment_count,
        output_path,
    )
    return output_path, len(epic_rows), comment_count


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    output_path, epic_count, comment_count = export_team_project_epic_jira_csv(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        workspace_gid=args.workspace_gid,
    )
    logger.info("Epic export completed: %s", output_path)


if __name__ == "__main__":
    main()
