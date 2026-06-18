#!/usr/bin/env python3
"""Export project-level details and task count snapshots from SQLite to CSV."""

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
from src.lib.database import Database
from src.lib.date_formatting import format_report_date
from src.lib.asana_urls import build_project_url, build_team_url, infer_workspace_gid
from src.lib.logging_utils import configure_logging, resolve_log_level


DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_CONFIG_ROOT = "."
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=DEFAULT_DB_PATH,
        help="Path to the SQLite database (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to store the CSV export (default: exports/).",
    )
    parser.add_argument(
        "--filename",
        help="Optional filename for the export. Defaults to project_details_<timestamp>.csv.",
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
    parser.add_argument(
        "--workspace-gid",
        help="Optional Asana workspace GID used to build project URLs.",
    )
    return parser.parse_args(argv)


def _bool_to_text(value: Optional[int]) -> str:
    if value is None:
        return ""
    return "true" if bool(value) else "false"


def export_project_details(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    team_gid: Optional[str] = None,
    workspace_gid: Optional[str] = None,
) -> Path:
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"project_details_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing project details export to %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)
        resolved_workspace_gid = workspace_gid or infer_workspace_gid(connection)
        team_filter = "WHERE p.team_gid = ?" if team_gid else ""
        query = f"""
            WITH actual_task_counts AS (
                SELECT
                    tp.project_gid,
                    COUNT(DISTINCT t.gid) AS actual_task_count
                FROM task_projects tp
                JOIN tasks t
                    ON t.gid = tp.task_gid
                GROUP BY tp.project_gid
            ),
            task_activity AS (
                SELECT
                    tp.project_gid,
                    MAX(t.created_at) AS last_task_created_at,
                    MAX(t.completed_at) AS last_task_resolved_at,
                    MAX(t.modified_at) AS last_task_updated_at
                FROM task_projects tp
                JOIN tasks t
                    ON t.gid = tp.task_gid
                GROUP BY tp.project_gid
            ),
            top_active_task_creators AS (
                SELECT
                    project_gid,
                    creator_name,
                    creator_email,
                    creator_atlassian_status,
                    task_count
                FROM (
                    SELECT
                        tp.project_gid AS project_gid,
                        COALESCE(u.name, '') AS creator_name,
                        COALESCE(u.email, '') AS creator_email,
                        COALESCE(ua.atlassian_status, '') AS creator_atlassian_status,
                        COUNT(DISTINCT t.gid) AS task_count,
                        ROW_NUMBER() OVER (
                            PARTITION BY tp.project_gid
                            ORDER BY COUNT(DISTINCT t.gid) DESC,
                                COALESCE(ua.atlassian_email, u.email, u.name, t.created_by_gid) COLLATE NOCASE,
                                t.created_by_gid
                        ) AS rn
                    FROM task_projects tp
                    JOIN tasks t
                        ON t.gid = tp.task_gid
                    JOIN user_atlassian_accounts ua
                        ON ua.user_gid = t.created_by_gid
                    LEFT JOIN users u
                        ON u.gid = t.created_by_gid
                    WHERE TRIM(COALESCE(t.created_by_gid, '')) != ''
                        AND LOWER(TRIM(COALESCE(ua.atlassian_status, ''))) = 'active'
                    GROUP BY tp.project_gid, t.created_by_gid
                )
                WHERE rn = 1
            )
            SELECT
                p.gid,
                p.name,
                p.team_gid,
                COALESCE(t.name, p.team_name) AS team_name,
                p.archived,
                p.owner_name,
                uo.email AS owner_email,
                uoa.atlassian_status AS owner_atlassian_status,
                p.privacy_setting,
                p.created_at,
                p.modified_at,
                p.start_on,
                p.due_on,
                p.completed,
                p.completed_at,
                p.is_personal_project,
                p.num_tasks,
                p.num_incomplete_tasks,
                p.num_completed_tasks,
                p.task_counts_captured_at,
                ta.last_task_created_at,
                ta.last_task_resolved_at,
                ta.last_task_updated_at,
                atc.actual_task_count,
                CASE WHEN atc.actual_task_count IS NULL THEN 'false' ELSE 'true' END AS actual_task_data_present,
                tatc.creator_name AS top_task_creator_name,
                tatc.creator_email AS top_task_creator_email,
                tatc.creator_atlassian_status AS top_task_creator_atlassian_status,
                tatc.task_count AS top_task_creator_task_count,
                p.synced_at
            FROM projects p
            LEFT JOIN teams t ON t.gid = p.team_gid
            LEFT JOIN actual_task_counts atc ON atc.project_gid = p.gid
            LEFT JOIN task_activity ta ON ta.project_gid = p.gid
            LEFT JOIN top_active_task_creators tatc ON tatc.project_gid = p.gid
            LEFT JOIN users uo ON uo.gid = p.owner_gid
            LEFT JOIN user_atlassian_accounts uoa ON uoa.user_gid = p.owner_gid
            {team_filter}
            ORDER BY COALESCE(t.name, p.team_name) COLLATE NOCASE, p.name COLLATE NOCASE, p.gid
        """
        params = []
        if team_gid:
            params.append(team_gid)
        rows = connection.execute(query, params).fetchall()

    if not rows:
        logger.info("No projects found in %s; generated an empty export.", database_path)

    fieldnames = [
        "gid",
        "name",
        "project_url",
        "team_gid",
        "team_name",
        "team_url",
        "archived",
        "owner_name",
        "owner_email",
        "owner_atlassian_status",
        "privacy_setting",
        "created_at",
        "modified_at",
        "start_on",
        "due_on",
        "completed",
        "completed_at",
        "is_personal_project",
        "actual_task_data_present",
        "actual_task_count",
        "num_tasks",
        "num_incomplete_tasks",
        "num_completed_tasks",
        "last_task_created_at",
        "last_task_resolved_at",
        "last_task_updated_at",
        "top_task_creator_name",
        "top_task_creator_email",
        "top_task_creator_atlassian_status",
        "top_task_creator_task_count",
        "synced_at",
        "task_counts_captured_at",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            actual_task_count = row["actual_task_count"]
            writer.writerow(
                {
                    "gid": row["gid"],
                    "name": row["name"],
                    "project_url": build_project_url(resolved_workspace_gid, row["gid"]),
                    "team_gid": row["team_gid"],
                    "team_name": row["team_name"],
                    "team_url": build_team_url(row["team_gid"]),
                    "archived": _bool_to_text(row["archived"]),
                    "owner_name": row["owner_name"],
                    "owner_email": row["owner_email"] or "",
                    "owner_atlassian_status": row["owner_atlassian_status"] or "",
                    "privacy_setting": row["privacy_setting"],
                    "created_at": format_report_date(row["created_at"]),
                    "modified_at": format_report_date(row["modified_at"]),
                    "start_on": format_report_date(row["start_on"]),
                    "due_on": format_report_date(row["due_on"]),
                    "completed": _bool_to_text(row["completed"]),
                    "completed_at": format_report_date(row["completed_at"]),
                    "is_personal_project": _bool_to_text(row["is_personal_project"]),
                    "num_tasks": "" if row["num_tasks"] is None else row["num_tasks"],
                    "num_incomplete_tasks": "" if row["num_incomplete_tasks"] is None else row["num_incomplete_tasks"],
                    "num_completed_tasks": "" if row["num_completed_tasks"] is None else row["num_completed_tasks"],
                    "task_counts_captured_at": format_report_date(row["task_counts_captured_at"]),
                    "last_task_created_at": format_report_date(row["last_task_created_at"]),
                    "last_task_resolved_at": format_report_date(row["last_task_resolved_at"]),
                    "last_task_updated_at": format_report_date(row["last_task_updated_at"]),
                    "actual_task_count": "" if actual_task_count is None else actual_task_count,
                    "actual_task_data_present": _bool_to_text(actual_task_count is not None),
                    "top_task_creator_name": row["top_task_creator_name"] or "",
                    "top_task_creator_email": row["top_task_creator_email"] or "",
                    "top_task_creator_atlassian_status": row["top_task_creator_atlassian_status"] or "",
                    "top_task_creator_task_count": (
                        "" if row["top_task_creator_task_count"] is None else row["top_task_creator_task_count"]
                    ),
                    "synced_at": format_report_date(row["synced_at"]),
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

    output_path = export_project_details(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        team_gid=args.team_gid,
        workspace_gid=args.workspace_gid or load_credentials(DEFAULT_CONFIG_ROOT).workspace_gid,
    )
    logger.info("Project details export completed: %s", output_path)


if __name__ == "__main__":
    main()
