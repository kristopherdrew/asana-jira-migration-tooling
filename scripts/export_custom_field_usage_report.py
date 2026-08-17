#!/usr/bin/env python3
"""Export per-project custom field usage from SQLite to CSV.

Emits one row per (project, custom field) for every custom field that is
actually populated on at least one task in the project, so the migration team
can decide which fields are worth importing into Jira.
"""

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
from src.lib.asana_urls import build_project_url, build_team_url, infer_workspace_gid
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import parse_gid_list, read_project_gids_file


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
        help="Optional filename for the export. Defaults to custom_field_usage_<timestamp>.csv.",
    )
    parser.add_argument(
        "--team-gid",
        help="Optional team GID to limit the report to projects assigned to one team.",
    )
    parser.add_argument("--project-gids", action="append", default=None, help="Comma-separated project GIDs to include (may be repeated).")
    parser.add_argument("--project-gids-file", help="UTF-8 file containing comma- or newline-separated project GIDs.")
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


def export_custom_field_usage(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    team_gid: Optional[str] = None,
    project_gids: Optional[list[str]] = None,
    workspace_gid: Optional[str] = None,
) -> Path:
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"custom_field_usage_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing custom field usage report to %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)
        resolved_workspace_gid = workspace_gid or infer_workspace_gid(connection)
        filters = []
        params = []
        if team_gid:
            filters.append("p.team_gid = ?")
            params.append(team_gid)
        if project_gids is not None:
            if project_gids:
                filters.append("p.gid IN (" + ", ".join("?" for _ in project_gids) + ")")
                params.extend(project_gids)
            else:
                filters.append("1 = 0")
        project_filter = "WHERE " + " AND ".join(filters) if filters else ""
        query = f"""
            WITH populated AS (
                SELECT tp.project_gid, tcf.custom_field_gid, tcf.task_gid
                FROM task_projects tp
                JOIN task_custom_fields tcf
                    ON tcf.task_gid = tp.task_gid
                WHERE COALESCE(tcf.enum_option_gid, '') != ''
                    OR COALESCE(tcf.text_value, '') != ''
                    OR tcf.number_value IS NOT NULL
                    OR COALESCE(tcf.date_value, '') != ''
                    OR COALESCE(tcf.display_value, '') != ''
                UNION
                SELECT tp.project_gid, tcfm.custom_field_gid, tcfm.task_gid
                FROM task_projects tp
                JOIN task_custom_field_multi_enum tcfm
                    ON tcfm.task_gid = tp.task_gid
            ),
            project_task_counts AS (
                SELECT project_gid, COUNT(DISTINCT task_gid) AS project_task_count
                FROM task_projects
                GROUP BY project_gid
            )
            SELECT
                p.gid AS project_gid,
                p.name AS project_name,
                p.team_gid,
                COALESCE(t.name, p.team_name) AS team_name,
                p.archived,
                cf.gid AS custom_field_gid,
                cf.name AS custom_field_name,
                cf.resource_subtype AS field_type,
                cf.is_global_to_workspace,
                pcfs.is_important,
                COUNT(DISTINCT pop.task_gid) AS tasks_with_values,
                ptc.project_task_count
            FROM populated pop
            JOIN projects p ON p.gid = pop.project_gid
            JOIN custom_fields cf ON cf.gid = pop.custom_field_gid
            LEFT JOIN teams t ON t.gid = p.team_gid
            LEFT JOIN project_custom_field_settings pcfs
                ON pcfs.project_gid = p.gid AND pcfs.custom_field_gid = cf.gid
            LEFT JOIN project_task_counts ptc ON ptc.project_gid = p.gid
            {project_filter}
            GROUP BY p.gid, cf.gid
            ORDER BY team_name COLLATE NOCASE, p.name COLLATE NOCASE,
                tasks_with_values DESC, cf.name COLLATE NOCASE
        """
        rows = connection.execute(query, params).fetchall()

    if not rows:
        logger.info(
            "No populated custom fields found in %s; generated an empty report.",
            database_path,
        )

    fieldnames = [
        "project_gid",
        "project_name",
        "project_url",
        "team_gid",
        "team_name",
        "archived",
        "custom_field_gid",
        "custom_field_name",
        "field_type",
        "is_global_to_workspace",
        "is_important",
        "tasks_with_values",
        "project_task_count",
        "usage_pct",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            tasks_with_values = row["tasks_with_values"]
            project_task_count = row["project_task_count"]
            if project_task_count:
                usage_pct = round(tasks_with_values / project_task_count * 100, 1)
            else:
                usage_pct = ""
            writer.writerow(
                {
                    "project_gid": row["project_gid"],
                    "project_name": row["project_name"],
                    "project_url": build_project_url(resolved_workspace_gid, row["project_gid"]),
                    "team_gid": row["team_gid"] or "",
                    "team_name": row["team_name"] or "",
                    "archived": _bool_to_text(row["archived"]),
                    "custom_field_gid": row["custom_field_gid"],
                    "custom_field_name": row["custom_field_name"] or "",
                    "field_type": row["field_type"] or "",
                    "is_global_to_workspace": _bool_to_text(row["is_global_to_workspace"]),
                    "is_important": _bool_to_text(row["is_important"]),
                    "tasks_with_values": tasks_with_values,
                    "project_task_count": "" if project_task_count is None else project_task_count,
                    "usage_pct": usage_pct,
                }
            )

    logger.info("Exported %s custom field usage row(s) to %s.", len(rows), output_path)
    return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    raw_project_gids = list(args.project_gids or [])
    if args.project_gids_file:
        raw_project_gids.append(read_project_gids_file(args.project_gids_file))
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    output_path = export_custom_field_usage(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        team_gid=args.team_gid,
        project_gids=parse_gid_list(",".join(raw_project_gids)) if raw_project_gids else None,
        workspace_gid=args.workspace_gid or load_credentials(DEFAULT_CONFIG_ROOT).workspace_gid,
    )
    logger.info("Custom field usage report completed: %s", output_path)


if __name__ == "__main__":
    main()
