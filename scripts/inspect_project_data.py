#!/usr/bin/env python3
"""Inspect and catalog project-level data (fields, custom fields, status updates).

This read-only discovery script shows what project-level data is actually captured
in SQLite before building exporters. Outputs a multi-tab Excel workbook showing:
- Project Core Fields (all captured columns)
- Custom Fields (field names, types, and values)
- Status Updates (recent project updates)
- Member Summary (project member counts)
- Field Usage Summary (which fields are populated across projects)
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.database import Database, resolve_default_db_path
from src.lib.date_formatting import format_report_date
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.project_scope import parse_gid_list, read_project_gids_file
from src.lib.xlsx_writer import write_workbook


DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help=f"Path to the SQLite database (default: {DEFAULT_DB_PATH}).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory to store the Excel export (default: {DEFAULT_OUTPUT_DIR}/).",
    )
    parser.add_argument(
        "--filename",
        help="Optional filename for the export. Defaults to project_data_inspection_<timestamp>.xlsx.",
    )
    parser.add_argument(
        "--team-gid",
        help="Optional team GID to limit inspection to projects assigned to one team.",
    )
    parser.add_argument(
        "--project-gids",
        action="append",
        default=None,
        help="Comma-separated project GIDs to include (may be repeated).",
    )
    parser.add_argument(
        "--project-gids-file",
        help="UTF-8 file containing comma- or newline-separated project GIDs.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def _resolve_project_scope(
    connection, team_gid: Optional[str], project_gids: Optional[list], project_gids_file: Optional[str]
) -> list[str]:
    """Resolve which project GIDs to inspect based on scope flags."""
    if project_gids_file:
        return read_project_gids_file(project_gids_file)

    requested_gids = parse_gid_list(project_gids or [])
    if requested_gids:
        return requested_gids

    if team_gid:
        rows = connection.execute(
            "SELECT DISTINCT gid FROM projects WHERE team_gid = ?", (team_gid,)
        ).fetchall()
        return [row[0] for row in rows]

    # No scope: return all project GIDs
    rows = connection.execute("SELECT DISTINCT gid FROM projects").fetchall()
    return [row[0] for row in rows]


def build_project_core_fields_sheet(connection, project_gids: list[str]) -> tuple[str, list]:
    """Build a sheet showing all captured project core fields."""
    if not project_gids:
        return ("Project Core Fields", [["No projects found"]])

    placeholders = ",".join(["?"] * len(project_gids))
    rows = connection.execute(
        f"""
        SELECT
            gid,
            name,
            team_gid,
            team_name,
            owner_gid,
            owner_name,
            archived,
            completed,
            created_at,
            modified_at,
            start_on,
            due_on,
            completed_at,
            privacy_setting,
            resource_subtype,
            notes,
            is_personal_project,
            num_tasks,
            num_incomplete_tasks,
            num_completed_tasks,
            task_counts_captured_at
        FROM projects
        WHERE gid IN ({placeholders})
        ORDER BY team_name, name
        """,
        project_gids,
    ).fetchall()

    header = [
        "Project GID",
        "Project Name",
        "Team GID",
        "Team Name",
        "Owner GID",
        "Owner Name",
        "Archived",
        "Completed",
        "Created At",
        "Modified At",
        "Start On",
        "Due On",
        "Completed At",
        "Privacy Setting",
        "Resource Subtype",
        "Notes (excerpt)",
        "Is Personal",
        "Task Count (snapshot)",
        "Incomplete Tasks",
        "Completed Tasks",
        "Task Counts Captured At",
    ]

    data = []
    for row in rows:
        notes_excerpt = (row[15] or "")[:100] if row[15] else ""
        data.append([
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            "true" if row[6] else "false" if row[6] is not None else "",
            "true" if row[7] else "false" if row[7] is not None else "",
            format_report_date(row[8]),
            format_report_date(row[9]),
            format_report_date(row[10]),
            format_report_date(row[11]),
            format_report_date(row[12]),
            row[13],
            row[14],
            notes_excerpt,
            "true" if row[16] else "false" if row[16] is not None else "",
            row[17],
            row[18],
            row[19],
            format_report_date(row[20]),
        ])

    return ("Project Core Fields", [header] + data)


def build_custom_fields_sheet(connection, project_gids: list[str]) -> tuple[str, list]:
    """Build a sheet showing project-level custom fields."""
    if not project_gids:
        return ("Custom Fields", [["No projects found"]])

    placeholders = ",".join(["?"] * len(project_gids))
    rows = connection.execute(
        f"""
        SELECT
            pcf.project_gid,
            p.name AS project_name,
            pcf.custom_field_gid AS field_gid,
            pcf.name AS field_name,
            pcf.type AS field_type,
            COALESCE(pcf.enum_value_name, CAST(pcf.number_value AS TEXT), pcf.text_value, '') AS field_value,
            p.team_name
        FROM project_custom_fields pcf
        JOIN projects p ON p.gid = pcf.project_gid
        WHERE p.gid IN ({placeholders})
        ORDER BY p.team_name, p.name, pcf.name
        """,
        project_gids,
    ).fetchall()

    if not rows:
        return ("Custom Fields", [["No custom fields found on selected projects"]])

    header = [
        "Project GID",
        "Project Name",
        "Team Name",
        "Field GID",
        "Field Name",
        "Field Type",
        "Value",
    ]

    data = []
    for row in rows:
        data.append([
            row[0],
            row[1],
            row[6],
            row[2],
            row[3],
            row[4],
            row[5],
        ])

    return ("Custom Fields", [header] + data)


def build_status_updates_sheet(connection, project_gids: list[str]) -> tuple[str, list]:
    """Build a sheet showing project status updates."""
    if not project_gids:
        return ("Status Updates", [["No projects found"]])

    placeholders = ",".join(["?"] * len(project_gids))
    rows = connection.execute(
        f"""
        SELECT
            pu.project_gid,
            p.name AS project_name,
            p.team_name,
            pu.gid AS update_gid,
            pu.title,
            pu.created_at,
            pu.created_by_gid,
            pu.text,
            pu.kind
        FROM project_updates pu
        JOIN projects p ON p.gid = pu.project_gid
        WHERE p.gid IN ({placeholders})
        AND pu.is_deleted = 0
        ORDER BY p.team_name, p.name, pu.created_at DESC
        """,
        project_gids,
    ).fetchall()

    if not rows:
        return ("Status Updates", [["No status updates found on selected projects"]])

    header = [
        "Project GID",
        "Project Name",
        "Team Name",
        "Update GID",
        "Title",
        "Created At",
        "Created By GID",
        "Content (excerpt)",
        "Kind",
    ]

    data = []
    for row in rows:
        content_excerpt = (row[7] or "")[:100] if row[7] else ""
        data.append([
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            format_report_date(row[5]),
            row[6],
            content_excerpt,
            row[8],
        ])

    return ("Status Updates", [header] + data)


def build_members_sheet(connection, project_gids: list[str]) -> tuple[str, list]:
    """Build a sheet showing project member counts."""
    if not project_gids:
        return ("Members", [["No projects found"]])

    placeholders = ",".join(["?"] * len(project_gids))
    rows = connection.execute(
        f"""
        SELECT
            p.gid,
            p.name,
            p.team_name,
            COUNT(DISTINCT pm.user_gid) AS member_count
        FROM projects p
        LEFT JOIN project_members pm ON pm.project_gid = p.gid
        WHERE p.gid IN ({placeholders})
        GROUP BY p.gid, p.name, p.team_name
        ORDER BY p.team_name, p.name
        """,
        project_gids,
    ).fetchall()

    header = [
        "Project GID",
        "Project Name",
        "Team Name",
        "Member Count",
    ]

    data = []
    for row in rows:
        data.append([
            row[0],
            row[1],
            row[2],
            row[3],
        ])

    return ("Members", [header] + data)


def build_field_usage_summary_sheet(connection, project_gids: list[str]) -> tuple[str, list]:
    """Build a summary of which fields are populated across projects."""
    if not project_gids:
        return ("Field Usage Summary", [["No projects found"]])

    placeholders = ",".join(["?"] * len(project_gids))

    # Count projects with various fields populated
    summary_data = []

    # Core fields
    core_fields = [
        ("start_on", "SELECT COUNT(*) FROM projects WHERE start_on IS NOT NULL AND gid IN ({placeholders})"),
        ("due_on", "SELECT COUNT(*) FROM projects WHERE due_on IS NOT NULL AND gid IN ({placeholders})"),
        ("notes", "SELECT COUNT(*) FROM projects WHERE notes IS NOT NULL AND notes != '' AND gid IN ({placeholders})"),
        ("html_notes", "SELECT COUNT(*) FROM projects WHERE html_notes IS NOT NULL AND html_notes != '' AND gid IN ({placeholders})"),
        ("completed", "SELECT COUNT(*) FROM projects WHERE completed = 1 AND gid IN ({placeholders})"),
        ("privacy_setting", "SELECT COUNT(*) FROM projects WHERE privacy_setting IS NOT NULL AND gid IN ({placeholders})"),
        ("custom_fields", f"SELECT COUNT(DISTINCT project_gid) FROM project_custom_fields WHERE project_gid IN ({placeholders})"),
        ("status_updates", f"SELECT COUNT(DISTINCT project_gid) FROM project_updates WHERE is_deleted = 0 AND project_gid IN ({placeholders})"),
    ]

    total_projects = len(project_gids)

    for field_name, query in core_fields:
        count = connection.execute(query.format(placeholders=placeholders), project_gids).fetchone()[0]
        summary_data.append([
            field_name,
            count,
            total_projects,
            f"{count}/{total_projects}" if total_projects > 0 else "0/0",
        ])

    header = [
        "Field/Feature",
        "Projects with Data",
        "Total Projects",
        "Coverage",
    ]

    return ("Field Usage Summary", [header] + summary_data)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.log_level)

    db_path = Path(args.database)
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        return 1

    database = Database(str(db_path))

    with database.connect() as connection:
        # Resolve which projects to inspect
        project_gids = _resolve_project_scope(
            connection, args.team_gid, args.project_gids, args.project_gids_file
        )

        if not project_gids:
            logger.error("No projects found matching the specified scope.")
            return 1

        logger.info(f"Inspecting {len(project_gids)} project(s)")

        # Build sheets
        sheets = [
            build_project_core_fields_sheet(connection, project_gids),
            build_custom_fields_sheet(connection, project_gids),
            build_status_updates_sheet(connection, project_gids),
            build_members_sheet(connection, project_gids),
            build_field_usage_summary_sheet(connection, project_gids),
        ]

    # Write workbook
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = args.filename
    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"project_data_inspection_{timestamp}.xlsx"

    output_path = output_dir / filename
    write_workbook(output_path, sheets)

    logger.info(f"Inspection complete. Output: {output_path}")
    print(f"Inspection exported to: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
