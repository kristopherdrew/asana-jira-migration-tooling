#!/usr/bin/env python3
"""Export project import coverage: which Asana projects are mapped to Jira.

Compares all Asana projects against jira_project_mappings to determine import
coverage. Shows:
  - Imported projects: have mappings for the specified Jira site
  - Unmapped projects: no mappings (not yet imported)

Usage:
    python3 -m scripts.atlassian.export_project_import_coverage \
        --jira-site archive_jira \
        [--team-gid <gid>] \
        [--project-gids <gid1> <gid2>] \
        [--project-gids-file path/to/file.txt]
"""

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.lib.database import Database, resolve_default_db_path

logger = logging.getLogger(__name__)


def main(argv: Optional[list[str]] = None) -> int:
    """Export project import coverage."""
    parser = argparse.ArgumentParser(
        description="Export project import coverage by Jira site"
    )
    parser.add_argument(
        "--jira-site",
        required=True,
        help="Jira site key (e.g. production_jira, archive_jira)",
    )
    parser.add_argument(
        "--database",
        default=resolve_default_db_path(),
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--team-gid",
        help="Scope to projects in a specific team",
    )
    parser.add_argument(
        "--project-gids",
        nargs="+",
        help="Specific project GIDs to check",
    )
    parser.add_argument(
        "--project-gids-file",
        type=Path,
        help="File with one project GID per line",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )

    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level))

    try:
        db = Database(db_path=args.database)
        with db.connect() as connection:
            logger.info(f"Jira site: {args.jira_site}")
            logger.info(f"Database: {args.database}")

            # Build project scope
            project_gids_set = set()
            if args.project_gids:
                project_gids_set.update(args.project_gids)
            if args.project_gids_file:
                with open(args.project_gids_file) as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            project_gids_set.add(line)

            # Query all projects (with optional scope)
            if project_gids_set:
                placeholders = ",".join("?" * len(project_gids_set))
                query = f"SELECT gid, name FROM projects WHERE gid IN ({placeholders}) ORDER BY name"
                params = list(project_gids_set)
            elif args.team_gid:
                query = """
                    SELECT DISTINCT p.gid, p.name
                    FROM projects p
                    WHERE p.team_gid = ?
                    ORDER BY p.name
                """
                params = [args.team_gid]
            else:
                query = "SELECT gid, name FROM projects ORDER BY name"
                params = []

            projects = connection.execute(query, params).fetchall()
            logger.info(f"Found {len(projects)} project(s)")

            # Check mapping status for each project
            imported = []
            unmapped = []

            for project_row in projects:
                project_gid = project_row["gid"]
                project_name = project_row["name"]

                mapping = connection.execute(
                    """
                    SELECT jira_project_key, jira_project_name FROM jira_project_mappings
                    WHERE jira_site_key = ? AND project_gid = ?
                    """,
                    (args.jira_site, project_gid),
                ).fetchone()

                if mapping:
                    imported.append({
                        "project_gid": project_gid,
                        "project_name": project_name,
                        "jira_project_key": mapping["jira_project_key"],
                        "jira_project_name": mapping["jira_project_name"],
                        "status": "imported",
                    })
                else:
                    unmapped.append({
                        "project_gid": project_gid,
                        "project_name": project_name,
                        "jira_project_key": "",
                        "jira_project_name": "",
                        "status": "unmapped",
                    })

            logger.info(f"Imported: {len(imported)}")
            logger.info(f"Unmapped: {len(unmapped)}")

            # Export results
            now = datetime.now(timezone.utc)
            timestamp_str = now.strftime("%Y-%m-%dT%H-%M-%S")
            exports_dir = Path("exports")
            exports_dir.mkdir(exist_ok=True)

            # All projects combined
            all_projects_path = exports_dir / f"project_import_coverage_{args.jira_site}_{timestamp_str}.csv"
            with open(all_projects_path, "w", newline="") as f:
                fieldnames = ["project_gid", "project_name", "jira_project_key", "jira_project_name", "status"]
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(imported + unmapped)
            logger.info(f"Wrote all projects to {all_projects_path}")

            # Imported projects
            if imported:
                imported_path = exports_dir / f"project_import_coverage_imported_{args.jira_site}_{timestamp_str}.csv"
                with open(imported_path, "w", newline="") as f:
                    fieldnames = ["project_gid", "project_name", "jira_project_key", "jira_project_name"]
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(imported)
                logger.info(f"Wrote {len(imported)} imported projects to {imported_path}")

            # Unmapped projects
            if unmapped:
                unmapped_path = exports_dir / f"project_import_coverage_unmapped_{args.jira_site}_{timestamp_str}.csv"
                with open(unmapped_path, "w", newline="") as f:
                    fieldnames = ["project_gid", "project_name"]
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    for row in unmapped:
                        writer.writerow({
                            "project_gid": row["project_gid"],
                            "project_name": row["project_name"],
                        })
                logger.info(f"Wrote {len(unmapped)} unmapped projects to {unmapped_path}")

            # Summary
            summary_path = exports_dir / f"project_import_coverage_summary_{args.jira_site}_{timestamp_str}.csv"
            with open(summary_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["metric", "count", "percentage"])
                total = len(imported) + len(unmapped)
                imported_pct = (len(imported) / total * 100) if total > 0 else 0
                unmapped_pct = (len(unmapped) / total * 100) if total > 0 else 0
                writer.writerow(["Total projects", total, "100%"])
                writer.writerow(["Imported", len(imported), f"{imported_pct:.1f}%"])
                writer.writerow(["Unmapped", len(unmapped), f"{unmapped_pct:.1f}%"])
            logger.info(f"Wrote summary to {summary_path}")

            return 0

    except Exception as exc:
        logger.error(f"Error: {exc}")
        if args.log_level == "DEBUG":
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
