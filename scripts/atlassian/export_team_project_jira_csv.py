#!/usr/bin/env python3
"""Export Asana teams and projects into a single Jira import CSV."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.asana_urls import build_project_url, build_team_url, infer_workspace_gid
from src.lib.database import Database
from src.lib.date_formatting import format_report_date, parse_iso_datetime
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.team_analysis import build_team_analysis


DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=DEFAULT_DB_PATH,
        help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to store the CSV export (default: {DEFAULT_OUTPUT_DIR}/).",
    )
    parser.add_argument(
        "--filename",
        help="Optional filename for the export. Defaults to team_project_jira_import_<timestamp>.csv.",
    )
    parser.add_argument(
        "--workspace-gid",
        help="Optional Asana workspace GID used to build project URLs.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def _bool_to_text(value: Optional[int]) -> str:
    if value is None:
        return ""
    return "true" if bool(value) else "false"


def _count_to_text(value: Optional[int]) -> str:
    if value is None:
        return ""
    return f"{value:,}"


def _clean_text(value: Optional[str]) -> str:
    return (value or "").strip()


def _description_text(lines: list[tuple[str, str]]) -> str:
    return "\n".join(f"{field}: {value}" for field, value in lines)


def _label_for_last_active(last_task_updated_at: Optional[str]) -> str:
    parsed = parse_iso_datetime(last_task_updated_at)
    if not parsed:
        return ""
    return f"last_active_{parsed.year}"


def _format_team_description(row: dict, analysis) -> str:
    return _description_text(
        [
            ("team_url", build_team_url(row["team_gid"])),
            ("project_count_total", _count_to_text(row["project_count_total"])),
            ("project_count_active", _count_to_text(row["project_count_active"])),
            ("project_count_archived", _count_to_text(row["project_count_archived"])),
            ("project_task_count_total", _count_to_text(row["project_task_count_total"])),
            ("actual_task_count_total", _count_to_text(row["actual_task_count_total"])),
            ("first_admin_email", analysis.first_admin_email if analysis else ""),
            ("additional_admin_emails", analysis.additional_admin_emails if analysis else ""),
            ("effective_admin", analysis.effective_admin_name if analysis else ""),
            ("effective_admin_email", analysis.effective_admin_email if analysis else ""),
            ("effective_admin_atlassian_status", analysis.effective_admin_atlassian_status if analysis else ""),
            ("effective_admin_source", analysis.effective_admin_source if analysis else ""),
            ("project_owner", analysis.project_owner_name if analysis else ""),
            ("project_owner_email", analysis.project_owner_email if analysis else ""),
            ("project_owner_atlassian_status", analysis.project_owner_atlassian_status if analysis else ""),
            ("project_owner_project_count", _count_to_text(analysis.project_owner_project_count if analysis else 0)),
            ("task_creator", analysis.task_creator_name if analysis else ""),
            ("task_creator_email", analysis.task_creator_email if analysis else ""),
            ("task_creator_atlassian_status", analysis.task_creator_atlassian_status if analysis else ""),
            ("task_creator_task_count", _count_to_text(analysis.task_creator_task_count if analysis else 0)),
        ]
    )


def _format_project_description(row: dict, project_url: str) -> str:
    return _description_text(
        [
            ("project_url", project_url),
            ("team_gid", row["team_gid"] or ""),
            ("team_name", row["team_name"] or ""),
            ("team_url", build_team_url(row["team_gid"])),
            ("privacy_setting", row["privacy_setting"] or ""),
            ("created_at", format_report_date(row["created_at"])),
            ("completed", _bool_to_text(row["completed"])),
            ("completed_at", format_report_date(row["completed_at"])),
            ("num_tasks", _count_to_text(row["num_tasks"])),
            ("num_incomplete_tasks", _count_to_text(row["num_incomplete_tasks"])),
            ("num_completed_tasks", _count_to_text(row["num_completed_tasks"])),
            ("last_task_created_at", format_report_date(row["last_task_created_at"])),
            ("last_task_resolved_at", format_report_date(row["last_task_resolved_at"])),
            ("last_task_updated_at", format_report_date(row["last_task_updated_at"])),
            ("actual_task_count", _count_to_text(row["actual_task_count"])),
        ]
    )


def _row_owner_from_project(row: dict) -> str:
    return _clean_text(row["owner_display_name"]) or _clean_text(row["owner_name"]) or _clean_text(row["owner_gid"])


def _row_owner_email_from_project(row: dict) -> str:
    return _clean_text(row["owner_atlassian_email"]) or _clean_text(row["owner_email"])


def _row_owner_atlassian_id_from_project(row: dict) -> str:
    return _clean_text(row["owner_atlassian_id"])


def _row_owner_atlassian_status_from_project(row: dict) -> str:
    return _clean_text(row["owner_atlassian_status"])


def export_team_project_jira_csv(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    workspace_gid: Optional[str] = None,
) -> Path:
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"team_project_jira_import_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing Jira team/project import export to %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)
        resolved_workspace_gid = workspace_gid or infer_workspace_gid(connection)
        project_rows = connection.execute(
            """
            WITH actual_task_counts AS (
                SELECT
                    tp.project_gid,
                    COUNT(DISTINCT t.gid) AS actual_task_count,
                    MAX(t.created_at) AS last_task_created_at,
                    MAX(t.completed_at) AS last_task_resolved_at,
                    MAX(t.modified_at) AS last_task_updated_at
                FROM task_projects tp
                JOIN tasks t
                    ON t.gid = tp.task_gid
                GROUP BY tp.project_gid
            )
            SELECT
                p.gid,
                p.name,
                p.archived,
                p.owner_gid,
                p.owner_name,
                p.team_gid,
                COALESCE(t.name, p.team_name, p.team_gid, '') AS team_name,
                p.privacy_setting,
                p.created_at,
                p.completed,
                p.completed_at,
                p.is_personal_project,
                p.num_tasks,
                p.num_incomplete_tasks,
                p.num_completed_tasks,
                atc.actual_task_count,
                atc.last_task_created_at,
                atc.last_task_resolved_at,
                atc.last_task_updated_at,
                u.email AS owner_email,
                u.name AS owner_display_name,
                ua.atlassian_account_id AS owner_atlassian_id,
                ua.atlassian_email AS owner_atlassian_email,
                ua.atlassian_status AS owner_atlassian_status
            FROM projects p
            LEFT JOIN teams t
                ON t.gid = p.team_gid
            LEFT JOIN actual_task_counts atc
                ON atc.project_gid = p.gid
            LEFT JOIN users u
                ON u.gid = p.owner_gid
            LEFT JOIN user_atlassian_accounts ua
                ON ua.user_gid = p.owner_gid
            ORDER BY COALESCE(t.name, p.team_name, p.team_gid, '') COLLATE NOCASE,
                p.name COLLATE NOCASE,
                p.gid
            """
        ).fetchall()

        project_rows = [dict(row) for row in project_rows]
        team_analysis = build_team_analysis(connection)

        team_groups: dict[str, list[dict]] = defaultdict(list)
        no_team_projects: list[dict] = []
        for row in project_rows:
            team_gid = _clean_text(row["team_gid"])
            if team_gid:
                team_groups[team_gid].append(row)
            else:
                if row["actual_task_count"] is not None:
                    no_team_projects.append(row)

        team_aggregates: dict[str, dict[str, object]] = {}
        for team_gid, rows in team_groups.items():
            project_count_total = len(rows)
            project_count_active = sum(1 for row in rows if not bool(row["archived"]))
            project_count_archived = sum(1 for row in rows if bool(row["archived"]))
            project_task_count_total = sum(int(row["num_tasks"] or 0) for row in rows)
            actual_task_count_values = [int(row["actual_task_count"]) for row in rows if row["actual_task_count"] is not None]
            actual_task_count_total = sum(actual_task_count_values) if actual_task_count_values else None
            team_name = next((row["team_name"] for row in rows if _clean_text(row["team_name"])), team_gid)
            team_aggregates[team_gid] = {
                "team_gid": team_gid,
                "team_name": team_name,
                "project_count_total": project_count_total,
                "project_count_active": project_count_active,
                "project_count_archived": project_count_archived,
                "project_task_count_total": project_task_count_total,
                "actual_task_count_total": actual_task_count_total,
                "rows": rows,
            }

        epic_rows: list[dict[str, str]] = []
        epic_task_ids: dict[str, str] = {}
        task_id_counter = 1

        sorted_team_ids = sorted(
            team_aggregates.keys(),
            key=lambda gid: (
                (team_aggregates[gid]["team_name"] or "").casefold(),
                gid,
            ),
        )
        for team_gid in sorted_team_ids:
            aggregate = team_aggregates[team_gid]
            analysis = team_analysis.get(team_gid)
            owner = analysis.effective_admin_name if analysis else ""

            epic_task_id = str(task_id_counter)
            task_id_counter += 1
            epic_task_ids[team_gid] = epic_task_id
            epic_rows.append(
                {
                    "Issue Type": "Epic",
                    "Task ID": epic_task_id,
                    "Parent Id": "",
                    "Summary": aggregate["team_name"] or "",
                    "Owner": owner,
                    "Owner Email": analysis.effective_admin_email if analysis else "",
                    "Owner Atlassian ID": analysis.effective_admin_atlassian_id if analysis else "",
                    "Owner Atlassian Status": analysis.effective_admin_atlassian_status if analysis else "",
                    "Owner Source": analysis.effective_admin_source if analysis else "",
                    "is_personal_project": "",
                    "Project Status": "",
                    "Labels": "",
                    "Description": _format_team_description(
                        {
                            "team_gid": team_gid,
                            "project_count_total": aggregate["project_count_total"],
                            "project_count_active": aggregate["project_count_active"],
                            "project_count_archived": aggregate["project_count_archived"],
                            "project_task_count_total": aggregate["project_task_count_total"],
                            "actual_task_count_total": aggregate["actual_task_count_total"],
                        },
                        analysis,
                    ),
                }
            )

        no_team_task_id: Optional[str] = None
        if no_team_projects:
            no_team_task_id = str(task_id_counter)
            task_id_counter += 1
            epic_rows.append(
                {
                    "Issue Type": "Epic",
                    "Task ID": no_team_task_id,
                    "Parent Id": "",
                    "Summary": "No Team",
                    "Owner": "",
                    "Owner Email": "",
                    "Owner Atlassian ID": "",
                    "Owner Atlassian Status": "",
                    "Owner Source": "",
                    "is_personal_project": "",
                    "Project Status": "",
                    "Labels": "",
                    "Description": "",
                }
            )

        task_rows: list[dict[str, str]] = []
        project_row_sort_key = {
            team_gid: index
            for index, team_gid in enumerate(sorted_team_ids)
        }
        no_team_order = len(project_row_sort_key)

        def _project_parent_sort_value(row: dict) -> int:
            team_gid = _clean_text(row["team_gid"])
            if not team_gid:
                return no_team_order
            return project_row_sort_key.get(team_gid, no_team_order)

        for row in sorted(
            [project_row for project_row in project_rows if project_row["actual_task_count"] is not None],
            key=lambda row: (
                _project_parent_sort_value(row),
                (row["name"] or "").casefold(),
                row["gid"],
            ),
        ):
            team_gid = _clean_text(row["team_gid"])
            parent_id = epic_task_ids.get(team_gid, no_team_task_id or "")
            task_id = str(task_id_counter)
            task_id_counter += 1
            task_rows.append(
                {
                    "Issue Type": "Task",
                    "Task ID": task_id,
                    "Parent Id": parent_id or "",
                    "Summary": row["name"] or "",
                    "Owner": _row_owner_from_project(row),
                    "Owner Email": _row_owner_email_from_project(row),
                    "Owner Atlassian ID": _row_owner_atlassian_id_from_project(row),
                    "Owner Atlassian Status": _row_owner_atlassian_status_from_project(row),
                    "Owner Source": "project_owner",
                    "is_personal_project": _bool_to_text(row["is_personal_project"]),
                    "Project Status": "Archived" if bool(row["archived"]) else "Active",
                    "Labels": _label_for_last_active(row["last_task_updated_at"]),
                    "Description": _format_project_description(
                        row,
                        build_project_url(resolved_workspace_gid, row["gid"]),
                    ),
                }
            )

        # Each entry pairs the CSV header with the internal row key. The owner
        # source is emitted as a second "Labels" column because Atlassian merges
        # repeated columns of the same name into multiple label values.
        columns = [
            ("Issue Type", "Issue Type"),
            ("Task ID", "Task ID"),
            ("Parent Id", "Parent Id"),
            ("Summary", "Summary"),
            ("Owner", "Owner"),
            ("Owner Email", "Owner Email"),
            ("Owner Atlassian ID", "Owner Atlassian ID"),
            ("Owner Atlassian Status", "Owner Atlassian Status"),
            ("Labels", "Owner Source"),
            ("is_personal_project", "is_personal_project"),
            ("Project Status", "Project Status"),
            ("Labels", "Labels"),
            ("Description", "Description"),
        ]

        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([header for header, _ in columns])
            for row in epic_rows + task_rows:
                writer.writerow([row.get(key, "") for _, key in columns])

        logger.info("Exported %s epic row(s) and %s task row(s) to %s.", len(epic_rows), len(task_rows), output_path)
        return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    output_path = export_team_project_jira_csv(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        workspace_gid=args.workspace_gid,
    )
    logger.info("Jira team/project import export completed: %s", output_path)


if __name__ == "__main__":
    main()
