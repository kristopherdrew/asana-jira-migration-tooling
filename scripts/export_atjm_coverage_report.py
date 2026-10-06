#!/usr/bin/env python3
"""Export ATJM (Asana to Jira Migration) project planning ticket coverage report."""

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
        help="Optional filename for the workbook. Defaults to atjm_coverage_<timestamp>.xlsx.",
    )
    parser.add_argument(
        "--jira-site",
        action="append",
        dest="jira_sites",
        help="Jira site key to include (can be repeated for multi-select). Default: all sites.",
    )
    parser.add_argument(
        "--team-gid",
        help="Optional team GID to limit to one team's projects.",
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
    """Get all distinct jira_site_key values from team_project_jira_mappings."""
    query = "SELECT DISTINCT jira_site_key FROM team_project_jira_mappings ORDER BY jira_site_key"
    return [row[0] for row in connection.execute(query).fetchall()]


def _build_overview_tab(
    connection: sqlite3.Connection, jira_sites: list[str], team_gid: Optional[str], project_gids: Optional[list[str]]
) -> list[list]:
    """Build Overview tab with workspace-wide ATJM coverage stats."""
    team_filter = "" if not team_gid else " AND p.team_gid = ?"
    team_params = [team_gid] if team_gid else []
    gid_filter = "" if not project_gids else f" AND p.gid IN ({','.join('?' * len(project_gids))})"
    gid_params = project_gids or []

    # Total projects in scope
    workspace_query = f"SELECT COUNT(DISTINCT gid) FROM projects WHERE is_personal_project = 0{team_filter}{gid_filter}"
    total_projects = connection.execute(workspace_query, team_params + gid_params).fetchone()[0]

    # Projects with ATJM tickets
    atjm_filter = "" if not jira_sites else f"AND tpm.jira_site_key IN ({','.join('?' * len(jira_sites))})"
    atjm_query = f"""
    SELECT COUNT(DISTINCT p.gid)
    FROM projects p
    LEFT JOIN team_project_jira_mappings tpm ON tpm.asana_gid = p.gid AND tpm.kind = 'project'
    WHERE p.is_personal_project = 0 AND tpm.jira_key IS NOT NULL {atjm_filter} {team_filter} {gid_filter}
    """
    atjm_params = jira_sites + team_params + gid_params
    projects_with_atjm = connection.execute(atjm_query, atjm_params).fetchone()[0]

    rows = [
        ["ATJM Coverage Report"],
        ["Generated (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")],
        ["Total Projects (non-personal)", str(total_projects)],
        ["Projects with ATJM Tickets", str(projects_with_atjm)],
        ["Projects without ATJM Tickets", str(total_projects - projects_with_atjm)],
        [
            "ATJM Coverage %",
            f"{100.0 * projects_with_atjm / total_projects:.1f}%" if total_projects > 0 else "0%",
        ],
    ]
    return rows


def _build_atjm_coverage_tab(
    connection: sqlite3.Connection, jira_sites: list[str], team_gid: Optional[str], project_gids: Optional[list[str]]
) -> list[list]:
    """Build ATJM Coverage tab showing projects with ATJM tickets."""
    team_filter = "" if not team_gid else " AND p.team_gid = ?"
    team_params = [team_gid] if team_gid else []
    gid_filter = "" if not project_gids else f" AND p.gid IN ({','.join('?' * len(project_gids))})"
    gid_params = project_gids or []
    atjm_filter = "" if not jira_sites else f"AND tpm.jira_site_key IN ({','.join('?' * len(jira_sites))})"
    atjm_params = jira_sites

    query = f"""
    SELECT
        p.gid,
        p.name,
        COALESCE(t.name, p.team_name) as team_name,
        tpm.jira_key,
        pjd.decision,
        COUNT(DISTINCT tp.task_gid) as task_count
    FROM projects p
    LEFT JOIN teams t ON t.gid = p.team_gid
    LEFT JOIN team_project_jira_mappings tpm ON tpm.asana_gid = p.gid AND tpm.kind = 'project' {atjm_filter}
    LEFT JOIN project_jira_decisions pjd ON pjd.project_gid = p.gid
    LEFT JOIN task_projects tp ON tp.project_gid = p.gid
    WHERE p.is_personal_project = 0 AND tpm.jira_key IS NOT NULL {team_filter} {gid_filter}
    GROUP BY p.gid
    ORDER BY p.name
    """

    params = atjm_params + team_params + gid_params
    rows = connection.execute(query, params).fetchall()

    if not rows:
        return [["(no data)"]]

    result = [["Asana Project GID", "Project Name", "Team Name", "ATJM Ticket Key", "Migration Decision", "Task Count"]]
    for proj_gid, proj_name, team_name, atjm_key, decision, task_count in rows:
        result.append([
            proj_gid,
            proj_name,
            team_name or "",
            atjm_key or "",
            decision or "(no decision)",
            str(task_count),
        ])

    return result


def _build_missing_atjm_tab(
    connection: sqlite3.Connection, jira_sites: list[str], team_gid: Optional[str], project_gids: Optional[list[str]]
) -> list[list]:
    """Build Missing ATJM tab showing projects without ATJM tickets."""
    team_filter = "" if not team_gid else " AND p.team_gid = ?"
    team_params = [team_gid] if team_gid else []
    gid_filter = "" if not project_gids else f" AND p.gid IN ({','.join('?' * len(project_gids))})"
    gid_params = project_gids or []
    atjm_filter = "" if not jira_sites else f"AND tpm.jira_site_key IN ({','.join('?' * len(jira_sites))})"
    atjm_params = jira_sites

    query = f"""
    SELECT
        p.gid,
        p.name,
        COALESCE(t.name, p.team_name) as team_name,
        COUNT(DISTINCT tp.task_gid) as task_count,
        p.is_personal_project
    FROM projects p
    LEFT JOIN teams t ON t.gid = p.team_gid
    LEFT JOIN team_project_jira_mappings tpm ON tpm.asana_gid = p.gid AND tpm.kind = 'project' {atjm_filter}
    LEFT JOIN task_projects tp ON tp.project_gid = p.gid
    WHERE p.is_personal_project = 0 AND tpm.jira_key IS NULL {team_filter} {gid_filter}
    GROUP BY p.gid
    ORDER BY p.name
    """

    params = atjm_params + team_params + gid_params
    rows = connection.execute(query, params).fetchall()

    if not rows:
        return [["(no missing ATJM tickets)"]]

    result = [["Asana Project GID", "Project Name", "Team Name", "Task Count", "Reason"]]
    for proj_gid, proj_name, team_name, task_count, is_personal in rows:
        reason = "Personal project (no ATJM)" if is_personal else "No ATJM ticket"
        result.append([
            proj_gid,
            proj_name,
            team_name or "",
            str(task_count),
            reason,
        ])

    return result


def _build_decision_summary_tab(
    connection: sqlite3.Connection, jira_sites: list[str], team_gid: Optional[str], project_gids: Optional[list[str]]
) -> list[list]:
    """Build Decision Summary tab showing count by decision type for projects with ATJM."""
    team_filter = "" if not team_gid else " AND p.team_gid = ?"
    team_params = [team_gid] if team_gid else []
    gid_filter = "" if not project_gids else f" AND p.gid IN ({','.join('?' * len(project_gids))})"
    gid_params = project_gids or []
    atjm_filter = "" if not jira_sites else f"AND tpm.jira_site_key IN ({','.join('?' * len(jira_sites))})"
    atjm_params = jira_sites

    query = f"""
    SELECT
        COALESCE(pjd.decision, '(no decision)') as decision,
        COUNT(DISTINCT p.gid) as count
    FROM projects p
    LEFT JOIN team_project_jira_mappings tpm ON tpm.asana_gid = p.gid AND tpm.kind = 'project' {atjm_filter}
    LEFT JOIN project_jira_decisions pjd ON pjd.project_gid = p.gid
    WHERE p.is_personal_project = 0 AND tpm.jira_key IS NOT NULL {team_filter} {gid_filter}
    GROUP BY pjd.decision
    ORDER BY count DESC
    """

    params = atjm_params + team_params + gid_params
    rows = connection.execute(query, params).fetchall()

    if not rows:
        return [["(no data)"]]

    result = [["Migration Decision", "Project Count"]]
    total = 0
    for decision, count in rows:
        result.append([decision, str(count)])
        total += count

    result.append([None, None])
    result.append(["Total with ATJM", str(total)])

    return result


def export_atjm_coverage_workbook(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    jira_sites: Optional[list[str]] = None,
    team_gid: Optional[str] = None,
    project_gids: Optional[list[str]] = None,
) -> Path:
    """Generate multi-tab workbook for ATJM coverage validation."""
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"atjm_coverage_{timestamp}.xlsx"

    output_path = output_dir / filename
    logger.debug("Generating ATJM coverage workbook: %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)

        # Resolve site selection
        if not jira_sites:
            jira_sites = _get_all_jira_sites(connection)
            if not jira_sites:
                jira_sites = ["legacy_unknown"]

        logger.info("Generating workbook for sites: %s", ", ".join(jira_sites))

        # Build sheets
        overview_rows = _build_overview_tab(connection, jira_sites, team_gid, project_gids)
        coverage_rows = _build_atjm_coverage_tab(connection, jira_sites, team_gid, project_gids)
        missing_rows = _build_missing_atjm_tab(connection, jira_sites, team_gid, project_gids)
        decision_rows = _build_decision_summary_tab(connection, jira_sites, team_gid, project_gids)

        sheets = [
            ("Overview", overview_rows),
            ("ATJM Coverage", coverage_rows),
            ("Missing ATJM", missing_rows),
            ("Decision Summary", decision_rows),
        ]

        write_workbook(output_path, sheets)

    logger.info("ATJM coverage workbook completed: %s", output_path)
    return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    project_gids = _load_project_gids(args.project_gids, args.project_gids_file)

    output_path = export_atjm_coverage_workbook(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        jira_sites=args.jira_sites,
        team_gid=args.team_gid,
        project_gids=project_gids,
    )
    logger.info("Export completed: %s", output_path)


if __name__ == "__main__":
    main()
