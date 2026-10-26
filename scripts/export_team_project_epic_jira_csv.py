#!/usr/bin/env python3
"""Export Asana projects/sections as Jira Epics/Issues with status updates as comments.

Supports two hierarchical paths:
  PATH 1: Project → Epic (simple)
  PATH 2: Project → Issue (parent), Section → Epic (hierarchical)

For new projects/sections, includes External Issue ID for Jira mapping.
For existing projects/sections (already in Jira), includes Issue Key and Summary.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional

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
        description="Export Asana projects/sections for Jira import (two hierarchy paths)."
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
        "--path",
        choices=["simple", "hierarchical"],
        default="hierarchical",
        help="Hierarchy path: 'simple' (Project→Epic) or 'hierarchical' (Project→Issue, Section→Epic). Default: hierarchical.",
    )
    parser.add_argument(
        "--jira-site",
        help="Jira site key for querying existing mappings (for upsert).",
    )
    parser.add_argument(
        "--project-gids",
        help="Comma-separated list of project GIDs to export. If omitted, exports all projects.",
    )
    parser.add_argument(
        "--project-gids-file",
        help="Path to file with newline-separated project GIDs.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO).",
    )
    return parser.parse_args(argv)


def _get_existing_mappings(
    connection: sqlite3.Connection,
    jira_site_key: Optional[str] = None,
    scope_gids: Optional[list[str]] = None,
) -> Dict[str, tuple[str, str]]:
    """Query existing project → Jira key mappings.

    Args:
        scope_gids: Optional list of project GIDs to filter. If None, queries all.

    Returns: {project_gid: (jira_key, summary)}
    """
    from src.teams.epic_mappings import resolve_parent_epic_keys_batch

    # Get project GIDs from the database (all or filtered by scope)
    if scope_gids:
        placeholders = ",".join("?" * len(scope_gids))
        projects = connection.execute(
            f"SELECT gid, name FROM projects WHERE gid IN ({placeholders})",
            scope_gids
        ).fetchall()
    else:
        projects = connection.execute("SELECT gid, name FROM projects").fetchall()

    project_gids = [p["gid"] for p in projects]

    # Query stored mappings
    mapped_keys = resolve_parent_epic_keys_batch(connection, project_gids, jira_site_key)

    # Build result dict: {project_gid: (jira_key, project_name)}
    result = {}
    for project_gid, jira_key in mapped_keys.items():
        project_name = next((p["name"] for p in projects if p["gid"] == project_gid), "")
        result[project_gid] = (jira_key, project_name)

    return result


def _build_export_rows(
    connection: sqlite3.Connection,
    epic_rows: List[Dict],
    path: str,
    existing_mappings: Dict[str, tuple[str, str]],
    scope_gids: Optional[list[str]] = None,
) -> tuple[List[Dict], int]:
    """Build export rows with existing issue keys and External IDs as appropriate.

    Args:
        scope_gids: Optional list of project GIDs to include. If None, includes all.

    Returns: (output_rows, comment_count)
    """
    output_rows = []
    comment_count = 0

    for epic_row in epic_rows:
        project_gid = epic_row.get("_asana_gid")
        if not project_gid:
            continue

        # Filter by scope if provided
        if scope_gids and project_gid not in scope_gids:
            continue

        # Check if this project already has a Jira mapping
        if project_gid in existing_mappings:
            jira_key, summary = existing_mappings[project_gid]
            # Existing issue: include key and summary, skip External ID
            epic_row["Issue Key"] = jira_key
            epic_row["Summary"] = summary
            # Keep task_gid for reference
            epic_row["task_gid"] = project_gid
            # Remove External Issue ID (already mapped)
            epic_row.pop("External issue ID", None)
        else:
            # New issue: include External ID for Jira mapping
            if path == "hierarchical":
                # Use synthetic GID for above-epic level
                external_id = f"{project_gid}::above_epic"
                epic_row["task_gid"] = external_id
            else:
                # PATH 1: simple, just use project GID
                external_id = project_gid
                epic_row["task_gid"] = project_gid

            epic_row["External issue ID"] = external_id
            # Clear Issue Key for new items
            epic_row.pop("Issue Key", None)

        output_rows.append(epic_row)

        # Fetch and add comments (project status updates)
        comments = fetch_project_update_comment_rows(connection, project_gid)
        if comments:
            output_rows.extend(comments)
            comment_count += len(comments)

    return output_rows, comment_count


def export_team_project_epic_jira_csv(
    database_path: str,
    output_dir: Path,
    path: str = "hierarchical",
    filename: Optional[str] = None,
    workspace_gid: Optional[str] = None,
    jira_site_key: Optional[str] = None,
    project_gids: Optional[list[str]] = None,
) -> tuple[Path, int, int]:
    """Export Asana projects as Epics/Issues with comments, ready for Jira import.

    Args:
        path: "simple" (Project→Epic) or "hierarchical" (Project→Issue, Section→Epic)
        project_gids: Optional list of project GIDs to export. If None, exports all.

    Returns: (output_path, project_count, comment_count)
    """
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"project_epic_import_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing Jira project/epic import export to %s (path=%s)", output_path, path)

    with database.connect() as connection:
        database.initialize(connection)

        # Get existing mappings for upsert capability
        existing_mappings = _get_existing_mappings(connection, jira_site_key, project_gids)
        logger.info("Found %d existing project→Jira mappings", len(existing_mappings))

        # Build rows (reuse existing export logic)
        epic_rows, _ = build_team_project_rows(
            connection, workspace_gid, include_empty_projects=True
        )

        # Prepare output rows with proper External IDs and Jira keys
        output_rows, comment_count = _build_export_rows(
            connection, epic_rows, path, existing_mappings, project_gids
        )

    # Determine CSV columns (filter out internal fields)
    if output_rows:
        csv_columns = [k for k in output_rows[0].keys() if not k.startswith("_")]
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

    project_count = len([r for r in output_rows if r.get("Issue Type") in ("Epic", "Deliverable")])
    logger.info(
        "Exported %s project row(s) and %s comment row(s) to %s (path=%s).",
        project_count,
        comment_count,
        output_path,
        path,
    )
    return output_path, project_count, comment_count


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    # Load project GIDs from argument or file
    project_gids = None
    if args.project_gids:
        project_gids = [g.strip() for g in args.project_gids.split(",") if g.strip()]
    elif args.project_gids_file:
        with open(args.project_gids_file) as f:
            project_gids = [line.strip() for line in f if line.strip()]

    output_path, project_count, comment_count = export_team_project_epic_jira_csv(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        path=args.path,
        filename=args.filename,
        workspace_gid=args.workspace_gid,
        jira_site_key=args.jira_site,
        project_gids=project_gids,
    )
    logger.info("Export completed: %s", output_path)


if __name__ == "__main__":
    main()
