#!/usr/bin/env python3
"""Export Asana tasks/sections for Phase 2 Jira import with parent keys resolved.

Reuses the full export_project_to_jira_csv logic (custom fields, status, comments, etc.)
for each project, then combines results with parent key injection.
"""

from __future__ import annotations

import argparse
import csv
import logging
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.database import Database, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level

DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Asana tasks/sections for Phase 2 import with parent keys resolved."
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
        "--path",
        choices=["simple", "hierarchical"],
        default="hierarchical",
        help="Hierarchy path from Phase 1 (default: hierarchical).",
    )
    parser.add_argument(
        "--project-gids",
        help="Comma-separated project GIDs. If omitted, exports all projects.",
    )
    parser.add_argument(
        "--project-gids-file",
        help="File with newline-separated project GIDs.",
    )
    parser.add_argument(
        "--jira-site",
        help="Jira site key for parent key lookups.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (default: INFO).",
    )
    return parser.parse_args(argv)


def _load_project_gids(
    gids_str: Optional[str],
    gids_file: Optional[str],
    database_path: str,
) -> Optional[list[str]]:
    """Load project GIDs from arg or file, or None to export all."""
    if gids_str:
        return [g.strip() for g in gids_str.split(",") if g.strip()]

    if gids_file:
        with open(gids_file) as f:
            return [line.strip() for line in f if line.strip()]

    return None  # Export all


def _call_existing_export_for_project(
    database_path: str,
    project_gid: str,
    jira_site_key: Optional[str],
    temp_dir: Path,
) -> Optional[Path]:
    """Call export_project_to_jira_csv for a single project and return CSV path."""
    from scripts.atlassian import export_project_to_jira_csv

    # Build argv for the existing export script
    argv = [
        "--database", database_path,
        "--project-gid", project_gid,
        "--output-dir", str(temp_dir),
    ]
    if jira_site_key:
        argv.extend(["--jira-site", jira_site_key])

    try:
        # Call the existing export script
        export_project_to_jira_csv.main(argv)

        # Find the generated CSV (must be the actual tasks CSV, not stats/field_counts/link_mods/etc.)
        csv_files = list(temp_dir.glob("**/*.csv"))
        # Filter to task data files: exclude consolidated files and special reports
        task_csvs = [
            f for f in csv_files
            if ("_field_counts" not in f.name and
                "_stats" not in f.name and
                "_link_modifications" not in f.name and
                "_user_associations" not in f.name and
                "_section_counts" not in f.name and
                "_jira_config" not in f.name and
                "_Consolidated" not in f.name)
        ]
        if task_csvs:
            # Return the CSV that has "Issue Type" column (actual tasks CSV)
            for csv_path in task_csvs:
                with csv_path.open("r", encoding="utf-8") as f:
                    reader = csv.reader(f)
                    header = next(reader, None)
                    if header and "Issue Type" in header:
                        return csv_path
            # If no Issue Type column found, return the first non-filtered CSV
            return task_csvs[0]
    except Exception as exc:
        logger.warning("Export failed for project %s: %s", project_gid, exc)

    return None


def _merge_csvs_and_inject_parents(
    project_csvs: dict[str, Path],
    database_path: str,
    path: str,
) -> list[dict]:
    """Merge CSVs from all projects and inject parent keys.

    Args:
        project_csvs: Dict mapping project_gid → CSV path
        database_path: SQLite database path
        path: "simple" or "hierarchical"

    Returns:
        Combined list of dicts ready for CSV output
    """
    from src.teams.epic_mappings import resolve_parent_epic_keys_batch

    database = Database(db_path=database_path)
    all_rows = []

    with database.connect() as connection:
        database.initialize(connection)

        # Load parent keys from Phase 1 captures
        project_gids = list(project_csvs.keys())
        parent_keys = resolve_parent_epic_keys_batch(connection, project_gids)

        # Read and combine all CSVs
        for project_gid, csv_path in project_csvs.items():
            parent_key = parent_keys.get(project_gid)
            if not parent_key:
                logger.warning("No Phase 1 parent key for project %s, skipping", project_gid)
                continue

            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Skip Epic rows (synthetic, created in Phase 1)
                    issue_type = row.get("Issue Type", "").strip()
                    if issue_type == "Epic":
                        continue

                    # Inject parent key
                    row["Parent Issue Key"] = parent_key
                    row["_project_gid"] = project_gid
                    all_rows.append(row)

    logger.info("Merged %d task row(s) from %d project CSV(s)", len(all_rows), len(project_csvs))
    return all_rows


def export_tasks_with_parent_keys(
    database_path: str,
    output_dir: Path,
    path: str = "hierarchical",
    project_gids: Optional[list[str]] = None,
    filename: Optional[str] = None,
    jira_site_key: Optional[str] = None,
) -> tuple[Path, int]:
    """Export Asana tasks/sections with parent keys resolved.

    Reuses the full export_project_to_jira_csv logic for each project, then
    merges results and injects parent keys from Phase 1.

    Returns: (output_path, task_count)
    """
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"phase2_import_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing Phase 2 export to %s (path=%s)", output_path, path)

    # Determine which projects to export
    db = Database(db_path=database_path)
    with db.connect() as connection:
        db.initialize(connection)

        if project_gids:
            gids_to_export = project_gids
        else:
            # Get all projects with Phase 1 mappings
            cursor = connection.execute("""
                SELECT DISTINCT project_gid FROM team_project_epic_keys
                ORDER BY project_gid
            """)
            gids_to_export = [row["project_gid"] for row in cursor.fetchall()]

    logger.info("Exporting %d project(s) using full export logic", len(gids_to_export))

    # Use temp dir to collect per-project CSVs
    with tempfile.TemporaryDirectory() as temp_dir_str:
        temp_dir = Path(temp_dir_str)

        # Call existing export for each project
        project_csvs = {}
        for project_gid in gids_to_export:
            logger.debug("Exporting project %s via full export logic", project_gid)
            csv_path = _call_existing_export_for_project(
                database_path=database_path,
                project_gid=project_gid,
                jira_site_key=jira_site_key,
                temp_dir=temp_dir,
            )
            if csv_path:
                project_csvs[project_gid] = csv_path

        # Merge CSVs and inject parent keys
        all_rows = _merge_csvs_and_inject_parents(
            project_csvs=project_csvs,
            database_path=database_path,
            path=path,
        )

    # Write combined CSV with all columns from all rows
    if all_rows:
        column_set = set()
        for row in all_rows:
            for key in row.keys():
                if not key.startswith("_"):
                    column_set.add(key)
        csv_columns = sorted(column_set)

        logger.debug("CSV columns: %s", csv_columns)

        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=csv_columns, restval="")
            writer.writeheader()
            for row in all_rows:
                filtered_row = {k: row.get(k, "") for k in csv_columns}
                writer.writerow(filtered_row)

    logger.info("✓ Phase 2 export complete: %s (%d task rows)", output_path, len(all_rows))
    return output_path, len(all_rows)


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)

    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    configure_logging(log_level)

    # Load project GIDs
    project_gids = _load_project_gids(args.project_gids, args.project_gids_file, args.database)

    try:
        output_path, task_count = export_tasks_with_parent_keys(
            database_path=args.database,
            output_dir=Path(args.output_dir),
            path=args.path,
            project_gids=project_gids,
            filename=args.filename,
            jira_site_key=args.jira_site,
        )
        logger.info("Export to: %s (%d rows)", output_path, task_count)

    except Exception as exc:
        logger.error("Export failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
