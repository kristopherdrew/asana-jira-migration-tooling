#!/usr/bin/env python3
"""Export project import status: which Asana projects have been exported to which Jira environments with task coverage."""

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

from src.lib.config import load_credentials
from src.lib.database import Database, resolve_default_db_path
from src.lib.date_formatting import format_report_date
from src.lib.logging_utils import configure_logging, resolve_log_level


DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_CONFIG_ROOT = "."
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
        help="Optional filename for the export. Defaults to project_import_status_<timestamp>.csv.",
    )
    parser.add_argument(
        "--team-gid",
        help="Optional team GID to limit the export to projects assigned to one team.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def export_project_import_status(
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
        filename = f"project_import_status_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing project import status export to %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)

        filters = []
        params = []
        if team_gid:
            filters.append("p.team_gid = ?")
            params.append(team_gid)

        team_filter = "WHERE " + " AND ".join(filters) if filters else ""

        query = f"""
            WITH jira_exports AS (
                -- Explicit project mappings from jira_project_mappings
                SELECT DISTINCT
                    jpm.project_gid,
                    jpm.jira_site_key,
                    jpm.jira_project_key,
                    jpm.jira_project_name
                FROM jira_project_mappings jpm

                UNION

                -- Infer from task-level imports (extract project key from issue key)
                SELECT DISTINCT
                    tp.project_gid,
                    tjm.jira_site_key,
                    SUBSTR(tjm.jira_key, 1, INSTR(tjm.jira_key, '-') - 1) AS jira_project_key,
                    NULL AS jira_project_name
                FROM task_jira_mappings tjm
                JOIN tasks t ON t.gid = tjm.task_gid
                JOIN task_projects tp ON tp.task_gid = t.gid
                WHERE INSTR(tjm.jira_key, '-') > 0
            ),
            project_task_summary AS (
                SELECT
                    p.gid AS project_gid,
                    COUNT(DISTINCT tp.task_gid) AS total_tasks,
                    SUM(CASE WHEN t.is_deleted = 0 THEN 1 ELSE 0 END) AS active_tasks,
                    SUM(CASE WHEN t.is_deleted = 0 AND t.completed = 1 THEN 1 ELSE 0 END) AS completed_tasks,
                    SUM(CASE WHEN t.is_deleted = 0 AND t.completed = 0 THEN 1 ELSE 0 END) AS incomplete_tasks
                FROM projects p
                LEFT JOIN task_projects tp ON tp.project_gid = p.gid
                LEFT JOIN tasks t ON t.gid = tp.task_gid
                GROUP BY p.gid
            ),
            project_environment_imports AS (
                SELECT
                    je.project_gid,
                    je.jira_site_key,
                    je.jira_project_key,
                    MAX(je.jira_project_name) AS jira_project_name,
                    COUNT(DISTINCT tjm.task_gid) AS imported_tasks
                FROM jira_exports je
                LEFT JOIN task_jira_mappings tjm ON tjm.task_gid IN (
                    SELECT t.gid
                    FROM task_projects tp
                    JOIN tasks t ON t.gid = tp.task_gid
                    WHERE tp.project_gid = je.project_gid
                )
                AND tjm.jira_site_key = je.jira_site_key
                AND SUBSTR(tjm.jira_key, 1, INSTR(tjm.jira_key, '-') - 1) = je.jira_project_key
                GROUP BY je.project_gid, je.jira_site_key, je.jira_project_key
            )
            SELECT
                p.gid AS asana_project_gid,
                p.name AS asana_project_name,
                p.team_gid,
                COALESCE(t.name, p.team_name) AS team_name,
                pe.jira_site_key AS jira_environment,
                pe.jira_project_key,
                COALESCE(pe.jira_project_name, '') AS jira_project_name,
                COALESCE(pts.total_tasks, 0) AS total_tasks,
                COALESCE(pts.active_tasks, 0) AS active_tasks,
                COALESCE(pts.completed_tasks, 0) AS completed_tasks,
                COALESCE(pts.incomplete_tasks, 0) AS incomplete_tasks,
                COALESCE(pe.imported_tasks, 0) AS imported_tasks,
                COALESCE(pts.total_tasks, 0) - COALESCE(pe.imported_tasks, 0) AS unimported_tasks,
                CASE
                    WHEN COALESCE(pts.total_tasks, 0) = 0 THEN 0
                    ELSE ROUND(100.0 * COALESCE(pe.imported_tasks, 0) / COALESCE(pts.total_tasks, 0), 2)
                END AS import_coverage_percent,
                p.archived,
                p.synced_at
            FROM projects p
            LEFT JOIN teams t ON t.gid = p.team_gid
            LEFT JOIN project_task_summary pts ON pts.project_gid = p.gid
            INNER JOIN project_environment_imports pe ON pe.project_gid = p.gid
            {team_filter}
            ORDER BY p.team_gid, p.name COLLATE NOCASE, jira_environment
        """
        rows = connection.execute(query, params).fetchall()

    if not rows:
        logger.info("No projects found in %s; generated an empty export.", database_path)

    fieldnames = [
        "asana_project_gid",
        "asana_project_name",
        "team_gid",
        "team_name",
        "jira_environment",
        "jira_project_key",
        "jira_project_name",
        "total_tasks",
        "active_tasks",
        "completed_tasks",
        "incomplete_tasks",
        "imported_tasks",
        "unimported_tasks",
        "import_coverage_percent",
        "archived",
        "synced_at",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "asana_project_gid": row["asana_project_gid"],
                    "asana_project_name": row["asana_project_name"],
                    "team_gid": row["team_gid"],
                    "team_name": row["team_name"],
                    "jira_environment": row["jira_environment"],
                    "jira_project_key": row["jira_project_key"],
                    "jira_project_name": row["jira_project_name"],
                    "total_tasks": row["total_tasks"],
                    "active_tasks": row["active_tasks"],
                    "completed_tasks": row["completed_tasks"],
                    "incomplete_tasks": row["incomplete_tasks"],
                    "imported_tasks": row["imported_tasks"],
                    "unimported_tasks": row["unimported_tasks"],
                    "import_coverage_percent": row["import_coverage_percent"],
                    "archived": "true" if row["archived"] else "false",
                    "synced_at": format_report_date(row["synced_at"]),
                }
            )

    logger.info("Exported %s project-environment(s) to %s.", len(rows), output_path)
    return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    output_path = export_project_import_status(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        team_gid=args.team_gid,
    )
    logger.info("Project import status export completed: %s", output_path)


if __name__ == "__main__":
    main()
