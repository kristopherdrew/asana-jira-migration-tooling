#!/usr/bin/env python3
"""Export Asana tasks/sections for Phase 2 Jira import with parent keys resolved."""

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

from src.lib.database import Database, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level
from src.tasks.export_with_parent_keys import build_phase_2_rows
from scripts.atlassian.export_project_to_jira_csv import build_project_export_data

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


def export_tasks_with_parent_keys(
    database_path: str,
    output_dir: Path,
    path: str = "hierarchical",
    project_gids: Optional[list[str]] = None,
    filename: Optional[str] = None,
    jira_site_key: Optional[str] = None,
) -> tuple[Path, int, int]:
    """Export Asana tasks/sections with parent keys resolved.

    Returns: (output_path, section_count, task_count)
    """
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"phase2_import_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing Phase 2 export to %s (path=%s)", output_path, path)

    with database.connect() as connection:
        database.initialize(connection)

        # Use existing export builder to get task rows
        task_rows, _ = build_project_export_data(
            connection=connection,
            project_gids=project_gids,
            add_epics=False,  # No synthetic epics for Phase 2
        )

        logger.info("Exporting %d task row(s)", len(task_rows))

        # Build Phase 2 rows with sections (if hierarchical) and parent keys
        section_rows, final_tasks = build_phase_2_rows(
            connection, task_rows, path, jira_site_key
        )

    # Prepare all rows: sections first (if any), then tasks
    all_rows = section_rows + final_tasks
    section_count = len(section_rows)
    task_count = len(final_tasks)

    logger.info("Phase 2 export: %d section(s), %d task(s)", section_count, task_count)

    # Determine CSV columns (filter internal fields)
    if all_rows:
        csv_columns = [k for k in all_rows[0].keys() if not k.startswith("_")]
    else:
        csv_columns = []

    logger.debug("CSV columns: %s", csv_columns)

    # Write CSV
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_columns, restval="")
        writer.writeheader()
        for row in all_rows:
            filtered_row = {k: row.get(k, "") for k in csv_columns}
            writer.writerow(filtered_row)

    logger.info("✓ Phase 2 export complete: %s", output_path)
    return output_path, section_count, task_count


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
        output_path, section_count, task_count = export_tasks_with_parent_keys(
            database_path=args.database,
            output_dir=Path(args.output_dir),
            path=args.path,
            project_gids=project_gids,
            filename=args.filename,
            jira_site_key=args.jira_site,
        )
        logger.info("Export to: %s", output_path)

    except Exception as exc:
        logger.error("Export failed: %s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
