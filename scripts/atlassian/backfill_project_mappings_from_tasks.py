#!/usr/bin/env python3
"""Backfill jira_project_mappings from existing task mappings.

For each task that has a jira_project_mapping, this script derives project
mappings for all Asana projects that contain that task. Ensures comprehensive
project coverage even if a project had only one task in the reverse lookup.

Usage:
    python3 -m scripts.atlassian.backfill_project_mappings_from_tasks \
        --jira-site archive_jira \
        [--dry-run]
"""

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.lib.config import load_credentials, resolve_jira_base_url
from src.lib.database import Database, resolve_default_db_path

logger = logging.getLogger(__name__)


def lookup_task_projects(connection, task_gid: str) -> list[str]:
    """Look up all projects for a task."""
    rows = connection.execute(
        "SELECT project_gid FROM task_projects WHERE task_gid = ?",
        (task_gid,),
    ).fetchall()
    return [row["project_gid"] for row in rows]


def enrich_project_mapping(connection, project_gid: str, jira_project_key: str, jira_project_name: str) -> dict:
    """Enrich project mapping with Asana project name."""
    project_row = connection.execute(
        "SELECT gid, name FROM projects WHERE gid = ?",
        (project_gid,),
    ).fetchone()
    return {
        "project_gid": project_gid,
        "project_name": project_row["name"] if project_row else "",
        "jira_project_key": jira_project_key,
        "jira_project_name": jira_project_name,
    }


def main(argv: Optional[list[str]] = None) -> int:
    """Backfill project mappings from task mappings."""
    parser = argparse.ArgumentParser(
        description="Backfill jira_project_mappings from existing task_jira_mappings"
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
        "--dry-run",
        action="store_true",
        help="Report findings without upserting to DB",
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
            logger.info(f"Dry run: {args.dry_run}")

            timestamp = datetime.now(timezone.utc)

            # Query all task mappings for this site
            task_mappings = connection.execute(
                """
                SELECT DISTINCT task_gid, jira_key
                FROM task_jira_mappings
                WHERE jira_site_key = ?
                ORDER BY task_gid
                """,
                (args.jira_site,),
            ).fetchall()

            if not task_mappings:
                logger.info(f"No task mappings found for site {args.jira_site}")
                return 0

            logger.info(f"Found {len(task_mappings)} unique task mappings")

            mapped_projects_new = []
            mapped_projects_updated = []
            conflicts = []
            processed = 0

            for task_row in task_mappings:
                processed += 1
                task_gid = task_row["task_gid"]
                jira_key = task_row["jira_key"]

                # Extract project key from jira_key (e.g., "PROJ-123" → "PROJ")
                project_key = jira_key.split("-")[0] if "-" in jira_key else jira_key

                # Get project name from a mapped task (if available)
                # We'll query jira_project_mappings to find if we already know this project
                existing_proj_by_key = connection.execute(
                    """
                    SELECT jira_project_name FROM jira_project_mappings
                    WHERE jira_site_key = ? AND jira_project_key = ?
                    LIMIT 1
                    """,
                    (args.jira_site, project_key),
                ).fetchone()
                project_name = existing_proj_by_key["jira_project_name"] if existing_proj_by_key else ""

                # Find all Asana projects for this task
                project_gids = lookup_task_projects(connection, task_gid)
                if not project_gids:
                    logger.debug(f"Task {task_gid} has no projects")
                    continue

                for project_gid in project_gids:
                    try:
                        existing_proj = connection.execute(
                            "SELECT jira_project_key FROM jira_project_mappings WHERE jira_site_key = ? AND project_gid = ?",
                            (args.jira_site, project_gid),
                        ).fetchone()

                        if existing_proj and existing_proj["jira_project_key"] != project_key:
                            conflicts.append({
                                "conflict_type": "jira_project_mappings",
                                "project_gid": project_gid,
                                "jira_project_key": project_key,
                                "existing_value": existing_proj["jira_project_key"],
                                "attempted_value": project_key,
                                "task_gid": task_gid,
                            })
                        else:
                            is_new_proj_mapping = not existing_proj
                            if not args.dry_run:
                                connection.execute(
                                    """
                                    INSERT INTO jira_project_mappings
                                    (jira_site_key, project_gid, jira_project_name, jira_project_key, synced_at)
                                    VALUES (?, ?, ?, ?, ?)
                                    ON CONFLICT(jira_site_key, project_gid) DO UPDATE SET
                                        jira_project_name=excluded.jira_project_name,
                                        jira_project_key=excluded.jira_project_key,
                                        synced_at=excluded.synced_at
                                    """,
                                    (args.jira_site, project_gid, project_name, project_key, timestamp.isoformat()),
                                )
                            enriched = enrich_project_mapping(
                                connection, project_gid, project_key, project_name
                            )
                            if is_new_proj_mapping:
                                mapped_projects_new.append(enriched)
                            else:
                                mapped_projects_updated.append(enriched)
                    except Exception as exc:
                        logger.error(f"Error mapping project {project_gid} from task {task_gid}: {exc}")
                        conflicts.append({
                            "conflict_type": "project_mapping_error",
                            "project_gid": project_gid,
                            "task_gid": task_gid,
                            "error": str(exc),
                        })

            if not args.dry_run:
                connection.commit()

            logger.info(f"Processed {processed} task mappings")
            logger.info(f"Projects newly mapped: {len(mapped_projects_new)}")
            logger.info(f"Projects updated: {len(mapped_projects_updated)}")
            logger.info(f"Found {len(conflicts)} conflicts")

            # Export results
            now = datetime.now(timezone.utc)
            timestamp_str = now.strftime("%Y-%m-%dT%H-%M-%S")
            exports_dir = Path("exports")
            exports_dir.mkdir(exist_ok=True)

            if mapped_projects_new:
                projects_path = exports_dir / f"backfill_project_mappings_new_{timestamp_str}.csv"
                with open(projects_path, "w", newline="") as f:
                    fieldnames = ["project_gid", "project_name", "jira_project_key", "jira_project_name"]
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()
                    writer.writerows(mapped_projects_new)
                logger.info(f"Wrote {len(mapped_projects_new)} newly mapped projects to {projects_path}")

            if conflicts:
                conflicts_path = exports_dir / f"backfill_project_mappings_conflicts_{timestamp_str}.csv"
                with open(conflicts_path, "w", newline="") as f:
                    if conflicts:
                        fieldnames = set()
                        for c in conflicts:
                            fieldnames.update(c.keys())
                        fieldnames = sorted(fieldnames)
                        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="fill")
                        writer.writeheader()
                        writer.writerows(conflicts)
                logger.info(f"Wrote {len(conflicts)} conflicts to {conflicts_path}")

            # Summary
            summary_path = exports_dir / f"backfill_project_mappings_summary_{timestamp_str}.csv"
            with open(summary_path, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["metric", "count"])
                writer.writerow(["Task mappings processed", processed])
                writer.writerow(["Projects newly mapped", len(mapped_projects_new)])
                writer.writerow(["Projects updated", len(mapped_projects_updated)])
                writer.writerow(["Conflicts", len(conflicts)])
                writer.writerow(["Dry run", "Yes" if args.dry_run else "No"])
            logger.info(f"Wrote summary to {summary_path}")

            return 0

    except Exception as exc:
        logger.error(f"Error: {exc}")
        if args.log_level == "DEBUG":
            raise
        return 1


if __name__ == "__main__":
    sys.exit(main())
