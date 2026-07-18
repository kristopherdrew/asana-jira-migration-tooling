#!/usr/bin/env python3
"""Export a per-project "last updated" report (latest task modification date) to CSV."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.database import Database, resolve_default_db_path
from src.lib.date_formatting import format_report_date
from src.lib.logging_utils import configure_logging, resolve_log_level


DEFAULT_OUTPUT_DIR = "exports"
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to the SQLite database (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to store the CSV export (default: exports/).",
    )
    parser.add_argument(
        "--filename",
        help="Optional filename for the export. Defaults to project_last_updated_<timestamp>.csv.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    parser.add_argument(
        "--team-gid",
        help="Optional team GID to scope the report to a single team's projects.",
    )
    return parser.parse_args(argv)


def fetch_project_last_updated(
    connection, team_gid: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Return one dict per project with its latest task-update timestamp.

    Each dict holds ``project_gid``, ``project_name``, ``task_count``,
    ``latest_task_update`` (the raw ISO ``MAX(tasks.modified_at)`` across the
    project's tasks, or ``None`` when the project has no tasks), and the
    ``latest_updated_task_gid``/``latest_updated_task_name`` of the single most
    recently modified task. Projects without tasks are included (LEFT JOIN) with a
    zero ``task_count`` and empty task columns. Rows are sorted most-recently-active
    first. Shared by the CSV report and the Jira field-update script so the
    "MAX(modified_at) per project" logic lives in one place.
    """
    params: List[object] = []
    where_clause = ""
    if team_gid:
        where_clause = "WHERE p.team_gid = ?"
        params.append(team_gid)

    projects = connection.execute(
        f"""
        SELECT p.gid AS project_gid,
               p.name AS project_name,
               COUNT(tp.task_gid) AS task_count,
               MAX(t.modified_at) AS latest_task_update
        FROM projects p
        LEFT JOIN task_projects tp ON tp.project_gid = p.gid
        LEFT JOIN tasks t ON t.gid = tp.task_gid
        {where_clause}
        GROUP BY p.gid, p.name
        ORDER BY latest_task_update DESC, p.name COLLATE NOCASE, p.gid
        """,
        params,
    ).fetchall()

    rows: List[Dict[str, Any]] = []
    for row in projects:
        latest_task_gid = ""
        latest_task_name = ""
        if row["latest_task_update"]:
            task_row = connection.execute(
                """
                SELECT t.gid AS task_gid, t.name AS task_name
                FROM task_projects tp
                JOIN tasks t ON t.gid = tp.task_gid
                WHERE tp.project_gid = ?
                ORDER BY t.modified_at DESC, t.gid
                LIMIT 1
                """,
                (row["project_gid"],),
            ).fetchone()
            if task_row is not None:
                latest_task_gid = task_row["task_gid"] or ""
                latest_task_name = task_row["task_name"] or ""
        rows.append(
            {
                "project_gid": row["project_gid"],
                "project_name": row["project_name"] or "",
                "task_count": row["task_count"],
                "latest_task_update": row["latest_task_update"],
                "latest_updated_task_gid": latest_task_gid,
                "latest_updated_task_name": latest_task_name,
            }
        )
    return rows


def export_project_last_updated_report(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    team_gid: Optional[str] = None,
) -> Path:
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"project_last_updated_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing project last-updated report to %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)
        rows = fetch_project_last_updated(connection, team_gid)

    if not rows:
        logger.info("No projects found in %s; generated an empty export.", database_path)

    fieldnames = [
        "project_gid",
        "project_name",
        "task_count",
        "latest_task_update",
        "latest_updated_task_gid",
        "latest_updated_task_name",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    "project_gid": row["project_gid"],
                    "project_name": row["project_name"],
                    "task_count": row["task_count"],
                    "latest_task_update": format_report_date(row["latest_task_update"]),
                    "latest_updated_task_gid": row["latest_updated_task_gid"],
                    "latest_updated_task_name": row["latest_updated_task_name"],
                }
            )

    logger.info("Exported %s project(s) to %s.", len(rows), output_path)
    return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    output_path = export_project_last_updated_report(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        team_gid=args.team_gid,
    )
    logger.info("Project last-updated report completed: %s", output_path)


if __name__ == "__main__":
    main()
