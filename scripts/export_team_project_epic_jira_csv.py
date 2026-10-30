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
import re
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
from scripts.atlassian.export_project_to_jira_csv import (
    fetch_project_update_comment_rows,
    format_user_display_name,
    load_user_display_names,
    load_user_emails,
    resolve_atlassian_identity,
)

DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
logger = logging.getLogger(__name__)


def _discover_custom_field_names(
    connection: sqlite3.Connection,
    scope_gids: Optional[list[str]] = None,
) -> List[str]:
    """Discover custom field names that have at least one non-empty value.

    Only returns fields with actual populated values, filtering out empty fields.
    """
    if scope_gids:
        placeholders = ",".join("?" * len(scope_gids))
        fields = connection.execute(
            f"""SELECT DISTINCT name FROM project_custom_fields
               WHERE project_gid IN ({placeholders})
               AND (enum_value_name IS NOT NULL
                    OR number_value IS NOT NULL
                    OR text_value IS NOT NULL)
               ORDER BY name""",
            scope_gids,
        ).fetchall()
    else:
        fields = connection.execute(
            """SELECT DISTINCT name FROM project_custom_fields
               WHERE enum_value_name IS NOT NULL
                  OR number_value IS NOT NULL
                  OR text_value IS NOT NULL
               ORDER BY name"""
        ).fetchall()

    return [f["name"] for f in fields]


def _discover_repeated_field_names(
    connection: sqlite3.Connection,
    scope_gids: Optional[list[str]] = None,
) -> List[tuple[str, str]]:
    """Return (name, type) for people and multi-select fields with at least one selected value."""
    scope_filter = ""
    params: list[str] = []
    if scope_gids:
        placeholders = ",".join("?" * len(scope_gids))
        scope_filter = f"AND v.project_gid IN ({placeholders})"
        params = list(scope_gids)
    fields = connection.execute(
        f"""SELECT f.name AS name, MIN(f.type) AS type
            FROM project_custom_field_values v
            JOIN project_custom_fields f
              ON f.project_gid = v.project_gid AND f.custom_field_gid = v.custom_field_gid
            WHERE 1 = 1 {scope_filter}
            GROUP BY f.name
            ORDER BY f.name""",
        params,
    ).fetchall()
    return [(f["name"], f["type"]) for f in fields]


def _format_comment_for_jira(comment: Dict, connection: sqlite3.Connection) -> str:
    """Format a single project status update comment for Jira CSV import.

    Format: DD/MMM/YY HH:MM AM/PM;ATLASSIAN_ACCOUNT_ID; Comment text
    Example: 12/Mar/26 9:33 PM;712020:a1b2c3d4; Status Update text
    """
    from datetime import datetime, timezone

    created_at = comment.get("created_at", "")
    created_by_gid = comment.get("created_by_gid", "")
    text = comment.get("text", "").strip()

    # Format timestamp: DD/MMM/YY HH:MM AM/PM
    timestamp = ""
    if created_at:
        try:
            # Parse ISO format (2026-01-28T21:11:41.008Z)
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            # Convert to UTC
            utc_time = dt.astimezone(timezone.utc)
            # Format: 12/Mar/26 9:33 PM
            date_part = utc_time.strftime("%d/%b/%y")
            time_part = utc_time.strftime("%I:%M %p").lstrip("0")
            timestamp = f"{date_part} {time_part}"
        except Exception:
            timestamp = ""

    # Get Atlassian account ID from database
    account_id = ""
    if created_by_gid:
        row = connection.execute(
            "SELECT atlassian_account_id FROM user_atlassian_accounts WHERE user_gid = ?",
            [created_by_gid],
        ).fetchone()
        if row and row["atlassian_account_id"]:
            account_id = row["atlassian_account_id"]

    # Format: timestamp;account_id; text
    if timestamp and account_id:
        return f"{timestamp};{account_id}; {text}"
    elif timestamp:
        return f"{timestamp}; {text}"
    else:
        return text


def _format_comments_for_issue(comments: List[Dict], connection: sqlite3.Connection) -> str:
    """Format project status update comments into Jira CSV comment column format.

    Combines all comments for an issue with timestamps and Atlassian account IDs.
    Format: DD/MMM/YY HH:MM AM/PM;ATLASSIAN_ID; Comment text
    """
    if not comments:
        return ""

    return "\n".join(_format_comment_for_jira(c, connection) for c in comments)


def _build_phase1_epic_rows(
    connection: sqlite3.Connection,
    user_map: Dict[str, Dict[str, Optional[str]]],
    user_display_names: Dict[str, str],
    user_emails: Dict[str, str],
    scope_gids: Optional[list[str]] = None,
) -> tuple[List[Dict], List[str]]:
    """Build epic rows from projects table with custom fields for Phase 1 export.

    Returns the project rows and the ordered repeated-column keys for people and
    multi-select fields (people fields expand to name/email/Atlassian ID triples).
    """
    if scope_gids:
        placeholders = ",".join("?" * len(scope_gids))
        projects = connection.execute(
            f"SELECT gid, name, notes, archived FROM projects WHERE gid IN ({placeholders}) ORDER BY name",
            scope_gids,
        ).fetchall()
    else:
        projects = connection.execute(
            "SELECT gid, name, notes, archived FROM projects ORDER BY name"
        ).fetchall()

    custom_field_names = _discover_custom_field_names(connection, scope_gids)
    repeated_fields = _discover_repeated_field_names(connection, scope_gids)
    max_values: Dict[str, int] = {name: 0 for name, _ in repeated_fields}

    rows = []
    for project in projects:
        row = {
            "Issue Type": "Deliverable",  # Phase 1 creates Deliverables (parent-level issues)
            "Summary": project["name"],
            "Description": project["notes"] or "",
            "_kind": "project",
            "_asana_gid": project["gid"],
        }

        for field_name in custom_field_names:
            field_row = connection.execute(
                """SELECT type, enum_value_name, number_value, text_value
                   FROM project_custom_fields
                   WHERE project_gid = ? AND name = ?""",
                [project["gid"], field_name],
            ).fetchone()

            field_value = ""
            if field_row:
                if field_row["type"] == "enum":
                    field_value = field_row["enum_value_name"] or ""
                elif field_row["type"] == "number":
                    field_value = field_row["number_value"] or ""
                elif field_row["type"] == "text":
                    field_value = field_row["text_value"] or ""

            row[field_name] = field_value

        for field_name, field_type in repeated_fields:
            values = connection.execute(
                """SELECT v.value_gid, v.value_name
                   FROM project_custom_field_values v
                   JOIN project_custom_fields f
                     ON f.project_gid = v.project_gid AND f.custom_field_gid = v.custom_field_gid
                   WHERE v.project_gid = ? AND f.name = ?
                   ORDER BY v.position""",
                [project["gid"], field_name],
            ).fetchall()
            max_values[field_name] = max(max_values[field_name], len(values))
            for index, value in enumerate(values, start=1):
                gid = value["value_gid"]
                if field_type == "people":
                    account_id, _ = resolve_atlassian_identity(user_map, gid)
                    row[f"{field_name}__{index}"] = format_user_display_name(
                        gid, user_display_names.get(gid) or value["value_name"]
                    )
                    row[f"{field_name}_email__{index}"] = user_emails.get(gid, "")
                    row[f"{field_name}_atlassian_id__{index}"] = account_id
                else:
                    row[f"{field_name}__{index}"] = value["value_name"] or ""

        rows.append(row)

    repeated_columns: List[str] = []
    for field_name, field_type in repeated_fields:
        for index in range(1, max_values[field_name] + 1):
            if field_type == "people":
                repeated_columns.extend(
                    [
                        f"{field_name}__{index}",
                        f"{field_name}_email__{index}",
                        f"{field_name}_atlassian_id__{index}",
                    ]
                )
            else:
                repeated_columns.append(f"{field_name}__{index}")

    logger.info(
        "Built %d epic rows from projects table with %d custom fields and %d repeated columns",
        len(rows),
        len(custom_field_names),
        len(repeated_columns),
    )
    return rows, repeated_columns


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

        # Fetch comments (track for multi-comment support)
        comments = fetch_project_update_comment_rows(connection, project_gid)
        if comments:
            # Store comments as a list on the row (will be expanded to multiple columns later)
            epic_row["_comments"] = comments
            comment_count += len(comments)

        output_rows.append(epic_row)

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

        user_map = database.load_atlassian_account_map(connection)
        user_display_names = load_user_display_names(connection)
        user_emails = load_user_emails(connection)
        epic_rows, repeated_columns = _build_phase1_epic_rows(
            connection, user_map, user_display_names, user_emails, project_gids
        )

        # Prepare output rows with proper External IDs and Jira keys
        output_rows, comment_count = _build_export_rows(
            connection, epic_rows, path, existing_mappings, project_gids
        )

    max_comments = max(
        (len(row.get("_comments", [])) for row in output_rows),
        default=0
    )

    for row in output_rows:
        comments = row.pop("_comments", [])
        for i, comment in enumerate(comments):
            row[f"Comment__{i + 1}"] = _format_comment_for_jira(comment, connection)

    all_keys = set()
    for row in output_rows:
        all_keys.update(k for k in row.keys() if not k.startswith("_"))

    standard_cols = ["Issue Type", "Summary", "Description"]
    special_cols = ["task_gid", "External issue ID"]
    repeated_set = set(repeated_columns)
    reserved = set(standard_cols + special_cols) | repeated_set
    custom_cols = sorted(
        k for k in all_keys if k not in reserved and not k.startswith("Comment__")
    )
    repeated_cols = [c for c in repeated_columns if c in all_keys]
    comment_cols = [f"Comment__{i + 1}" for i in range(max_comments)] if max_comments > 0 else []

    csv_columns = standard_cols + custom_cols + repeated_cols + comment_cols + special_cols
    csv_columns = [c for c in csv_columns if c in all_keys]

    logger.debug("CSV columns: %s", csv_columns)

    csv_header = [re.sub(r"__\d+$", "", col) for col in csv_columns]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(csv_header)

        for row in output_rows:
            writer.writerow([row.get(col, "") or "" for col in csv_columns])

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
