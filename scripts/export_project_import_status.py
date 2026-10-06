#!/usr/bin/env python3
"""Export project import status as multi-tab workbook: Overview, Migrated, Summary, Unmigrated."""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.database import Database, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.lib.xlsx_writer import write_workbook

DEFAULT_DB_PATH = "data/asana.sqlite"
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
        help="Directory to store the workbook (default: exports/).",
    )
    parser.add_argument(
        "--filename",
        help="Optional filename for the workbook. Defaults to project_import_status_<timestamp>.xlsx.",
    )
    parser.add_argument(
        "--jira-site",
        action="append",
        dest="jira_sites",
        help="Jira site key to include (can be repeated for multi-select). Default: all sites.",
    )
    parser.add_argument(
        "--project-gids",
        help="Comma-separated project GIDs to scope the report.",
    )
    parser.add_argument(
        "--project-gids-file",
        help="File containing comma- or newline-separated project GIDs.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def _load_project_gids(gids_arg: Optional[str], gids_file: Optional[str]) -> Optional[list[str]]:
    """Load project GIDs from argument and/or file. Returns None if not scoped."""
    gids = []
    if gids_arg:
        gids.extend([g.strip() for g in gids_arg.split(",") if g.strip()])
    if gids_file:
        with Path(gids_file).open("r", encoding="utf-8") as f:
            content = f.read()
            gids.extend([g.strip() for g in content.replace(",", "\n").split("\n") if g.strip()])
    return gids if gids else None


def _get_all_jira_sites(connection: sqlite3.Connection) -> list[str]:
    """Get all distinct jira_site_key values from mapping tables."""
    query = """
    SELECT DISTINCT jira_site_key FROM (
        SELECT DISTINCT jira_site_key FROM jira_project_mappings
        UNION
        SELECT DISTINCT jira_site_key FROM task_jira_mappings
    )
    ORDER BY jira_site_key
    """
    return [row[0] for row in connection.execute(query).fetchall()]


def _build_overview_tab(
    connection: sqlite3.Connection, jira_sites: list[str], project_gids: Optional[list[str]]
) -> list[list]:
    """Build Overview tab with workspace-wide and per-environment stats."""
    jira_filter = "" if not jira_sites else f"AND tjm.jira_site_key IN ({','.join('?' * len(jira_sites))})"
    gid_filter = "" if not project_gids else f"AND p.gid IN ({','.join('?' * len(project_gids))})"
    params_migrated = jira_sites + (project_gids or [])

    # Workspace stats - count total projects in scope
    workspace_query = "SELECT COUNT(DISTINCT gid) FROM projects"
    workspace_params = []
    if project_gids:
        workspace_query += f" WHERE gid IN ({','.join('?' * len(project_gids))})"
        workspace_params = project_gids
    total_projects = connection.execute(workspace_query, workspace_params).fetchone()[0]

    # Migrated count - projects with External IDs synced back (tasks in task_jira_mappings)
    migrated_query = f"""
    SELECT COUNT(DISTINCT tp.project_gid)
    FROM task_jira_mappings tjm
    JOIN tasks t ON t.gid = tjm.task_gid
    JOIN task_projects tp ON tp.task_gid = t.gid
    JOIN projects p ON p.gid = tp.project_gid
    WHERE 1=1 {jira_filter} {gid_filter}
    """
    migrated_projects = connection.execute(migrated_query, params_migrated).fetchone()[0]

    # Per-environment stats
    per_env_query = f"""
    SELECT
        tjm.jira_site_key,
        COUNT(DISTINCT tp.project_gid) as migrated_count
    FROM task_jira_mappings tjm
    JOIN tasks t ON t.gid = tjm.task_gid
    JOIN task_projects tp ON tp.task_gid = t.gid
    JOIN projects p ON p.gid = tp.project_gid
    WHERE 1=1 {jira_filter} {gid_filter}
    GROUP BY tjm.jira_site_key
    ORDER BY tjm.jira_site_key
    """
    per_env_rows = connection.execute(per_env_query, params_migrated).fetchall()

    # Build overview rows
    rows = [
        ["Project Import Status Report"],
        ["Generated (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")],
        ["Total Projects in Scope", str(total_projects)],
        ["Migrated to Selected Sites", str(migrated_projects)],
        ["Not Yet Migrated", str(total_projects - migrated_projects)],
        [
            "Overall Coverage %",
            f"{100.0 * migrated_projects / total_projects:.1f}%" if total_projects > 0 else "0%",
        ],
        [None, None],
        ["Environment", "Migrated Count"],
    ]
    for site_key, count in per_env_rows:
        rows.append([site_key, str(count)])

    return rows


def _build_migrated_tab(
    connection: sqlite3.Connection, jira_sites: list[str], project_gids: Optional[list[str]]
) -> list[list]:
    """Build Migrated tab with projects that have External IDs synced back, sorted by environment."""
    jira_filter = "" if not jira_sites else f"AND tjm.jira_site_key IN ({','.join('?' * len(jira_sites))})"
    gid_filter = "" if not project_gids else f"AND p.gid IN ({','.join('?' * len(project_gids))})"
    params = jira_sites + (project_gids or [])

    query = f"""
    SELECT DISTINCT
        tjm.jira_site_key,
        p.gid,
        p.name,
        COUNT(DISTINCT tp.task_gid) as total_tasks,
        SUM(CASE WHEN t.is_deleted = 0 THEN 1 ELSE 0 END) as active_tasks,
        COUNT(DISTINCT tjm.task_gid) as imported_tasks
    FROM task_jira_mappings tjm
    JOIN tasks t ON t.gid = tjm.task_gid
    JOIN task_projects tp ON tp.task_gid = t.gid
    JOIN projects p ON p.gid = tp.project_gid
    WHERE 1=1 {jira_filter} {gid_filter}
    GROUP BY tjm.jira_site_key, p.gid
    ORDER BY tjm.jira_site_key, p.name
    """

    rows = connection.execute(query, params).fetchall()
    if not rows:
        return [["(no data)"]]

    result = [
        ["Environment", "Asana Project GID", "Asana Project Name", "Total Tasks", "Active Tasks", "Imported Tasks", "Coverage %"]
    ]
    for env, proj_gid, proj_name, total, active, imported in rows:
        coverage = 100.0 * imported / total if total > 0 else 0
        result.append([env, proj_gid, proj_name, str(total), str(active), str(imported), f"{coverage:.1f}%"])

    return result


def _build_summary_tab(
    connection: sqlite3.Connection, jira_sites: list[str], project_gids: Optional[list[str]]
) -> list[list]:
    """Build Summary tab with unmigrated project counts pivoted by decision and team."""
    jira_filter = (
        "" if not jira_sites else f"AND tjm.jira_site_key IN ({','.join('?' * len(jira_sites))})"
    )
    gid_filter = "" if not project_gids else f"AND p.gid IN ({','.join('?' * len(project_gids))})"

    params = jira_sites + (project_gids or [])

    # Get all decisions and teams that appear in unmigrated projects
    query = f"""
    SELECT DISTINCT
        COALESCE(pjd.decision, '(no decision)') as decision,
        COALESCE(t.name, p.team_name, '(no team)') as team_name
    FROM projects p
    LEFT JOIN teams t ON t.gid = p.team_gid
    LEFT JOIN project_jira_decisions pjd ON pjd.project_gid = p.gid
    WHERE NOT EXISTS (
        SELECT 1
        FROM task_jira_mappings tjm
        JOIN tasks t2 ON t2.gid = tjm.task_gid
        JOIN task_projects tp ON tp.task_gid = t2.gid
        WHERE tp.project_gid = p.gid {jira_filter}
    )
    {gid_filter}
    ORDER BY team_name, decision
    """

    distinct_rows = connection.execute(query, params).fetchall()
    if not distinct_rows:
        return [["(no unmigrated projects)"]]

    # Get all unique decisions and teams
    decisions = sorted(set(row[0] for row in distinct_rows))
    teams = sorted(set(row[1] for row in distinct_rows))

    # Build pivot counts: (team, decision) -> count
    count_query = f"""
    SELECT
        COALESCE(t.name, p.team_name, '(no team)') as team_name,
        COALESCE(pjd.decision, '(no decision)') as decision,
        COUNT(DISTINCT p.gid) as count
    FROM projects p
    LEFT JOIN teams t ON t.gid = p.team_gid
    LEFT JOIN project_jira_decisions pjd ON pjd.project_gid = p.gid
    WHERE NOT EXISTS (
        SELECT 1
        FROM task_jira_mappings tjm
        JOIN tasks t2 ON t2.gid = tjm.task_gid
        JOIN task_projects tp ON tp.task_gid = t2.gid
        WHERE tp.project_gid = p.gid {jira_filter}
    )
    {gid_filter}
    GROUP BY team_name, decision
    """

    count_rows = connection.execute(count_query, params).fetchall()
    pivot_data = {(row[0], row[1]): row[2] for row in count_rows}

    # Build result with pivot table
    result = [["Team / Decision"] + decisions + ["Team Total"]]

    total_by_team = {}
    for team in teams:
        row = [team]
        team_total = 0
        for decision in decisions:
            count = pivot_data.get((team, decision), 0)
            row.append(count)
            team_total += count
        row.append(team_total)
        result.append(row)
        total_by_team[team] = team_total

    # Add total row
    result.append([None] * len(result[0]))  # Blank row for spacing
    total_row = ["Total by Decision"]
    grand_total = 0
    for decision in decisions:
        decision_total = sum(pivot_data.get((team, decision), 0) for team in teams)
        total_row.append(decision_total)
        grand_total += decision_total
    total_row.append(grand_total)
    result.append(total_row)

    return result


def _build_unmigrated_tab(
    connection: sqlite3.Connection, jira_sites: list[str], project_gids: Optional[list[str]]
) -> list[list]:
    """Build Unmigrated tab with projects without External IDs synced, including their ATJM ticket and decision."""
    jira_filter = (
        "" if not jira_sites else f"AND tjm.jira_site_key IN ({','.join('?' * len(jira_sites))})"
    )
    gid_filter = "" if not project_gids else f"AND p.gid IN ({','.join('?' * len(project_gids))})"

    params = jira_sites + (project_gids or [])

    query = f"""
    SELECT
        p.gid,
        p.name,
        p.team_gid,
        COALESCE(t.name, p.team_name) as team_name,
        COUNT(DISTINCT tp.task_gid) as total_tasks,
        pjd.decision,
        tpm.jira_key as atjm_ticket_key
    FROM projects p
    LEFT JOIN teams t ON t.gid = p.team_gid
    LEFT JOIN task_projects tp ON tp.project_gid = p.gid
    LEFT JOIN project_jira_decisions pjd ON pjd.project_gid = p.gid
    LEFT JOIN team_project_jira_mappings tpm ON tpm.asana_gid = p.gid AND tpm.kind = 'project'
    WHERE NOT EXISTS (
        SELECT 1
        FROM task_jira_mappings tjm
        JOIN tasks t2 ON t2.gid = tjm.task_gid
        JOIN task_projects tp2 ON tp2.task_gid = t2.gid
        WHERE tp2.project_gid = p.gid {jira_filter}
    )
    {gid_filter}
    GROUP BY p.gid
    ORDER BY COALESCE(team_name, ''), COALESCE(pjd.decision, '(no decision)'), p.name
    """

    rows = connection.execute(query, params).fetchall()
    if not rows:
        return [["(no data)"]]

    result = [
        ["Asana Project GID", "Asana Project Name", "Team GID", "Team Name", "Total Tasks", "Jira Migration Decision", "ATJM Ticket Key"]
    ]

    for proj_gid, proj_name, team_gid, team_name, total, decision, atjm_key in rows:
        result.append([
            proj_gid,
            proj_name,
            team_gid or "",
            team_name or "",
            str(total),
            decision or "(no decision)",
            atjm_key or "",
        ])

    return result


def export_project_import_status_workbook(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    jira_sites: Optional[list[str]] = None,
    project_gids: Optional[list[str]] = None,
) -> Path:
    """Generate multi-tab workbook for project import status."""
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"project_import_status_{timestamp}.xlsx"

    output_path = output_dir / filename
    logger.debug("Generating project import status workbook: %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)

        # Resolve site selection
        if not jira_sites:
            jira_sites = _get_all_jira_sites(connection)
            if not jira_sites:
                jira_sites = ["legacy_unknown"]

        logger.info("Generating workbook for sites: %s", ", ".join(jira_sites))

        # Build sheets
        overview_rows = _build_overview_tab(connection, jira_sites, project_gids)
        migrated_rows = _build_migrated_tab(connection, jira_sites, project_gids)
        summary_rows = _build_summary_tab(connection, jira_sites, project_gids)
        unmigrated_rows = _build_unmigrated_tab(connection, jira_sites, project_gids)

        sheets = [
            ("Overview", overview_rows),
            ("Migrated", migrated_rows),
            ("Summary", summary_rows),
            ("Unmigrated", unmigrated_rows),
        ]

        write_workbook(output_path, sheets)

    logger.info("Project import status workbook completed: %s", output_path)
    return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    project_gids = _load_project_gids(args.project_gids, args.project_gids_file)

    output_path = export_project_import_status_workbook(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        jira_sites=args.jira_sites,
        project_gids=project_gids,
    )
    logger.info("Export completed: %s", output_path)


if __name__ == "__main__":
    main()
