#!/usr/bin/env python3
"""Export Asana projects with Jira mappings and team context (includes unmapped projects)."""

import argparse
import csv
import logging
import sys
from pathlib import Path

from src.lib.database import Database, resolve_default_db_path
from src.lib.logging_utils import configure_logging, resolve_log_level

logger = logging.getLogger(__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Export all Asana projects with Jira mappings and team context (includes unmapped projects)."
    )
    parser.add_argument(
        "--database",
        type=str,
        default=None,
        help="Path to SQLite database (defaults to ASANA_JIRA_DB_PATH or data/asana.sqlite)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output CSV path (defaults to exports/jira_project_mappings.csv)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )

    args = parser.parse_args(argv)
    configure_logging(resolve_log_level(args.log_level))

    db_path = args.database or resolve_default_db_path()
    db = Database(db_path=db_path)

    output_path = args.output or "exports/jira_project_mappings.csv"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    query = """
    SELECT
        p.gid AS project_gid,
        p.name AS asana_project_name,
        p.team_gid,
        p.team_name,
        jpm.jira_project_name,
        jpm.jira_project_key,
        jpm.jira_site_key,
        tpjm.jira_key AS atjm_key,
        pjd.decision AS asana_migration_decision,
        jpm.synced_at,
        CASE WHEN jpm.project_gid IS NOT NULL THEN 'Mapped' ELSE 'Not Mapped' END AS mapping_status,
        COALESCE(COUNT(DISTINCT tp.task_gid), 0) AS asana_task_count,
        COALESCE(COUNT(DISTINCT CASE WHEN tjm.task_gid IS NOT NULL THEN tjm.task_gid END), 0) AS mapped_task_count
    FROM projects p
    LEFT JOIN jira_project_mappings jpm ON p.gid = jpm.project_gid
    LEFT JOIN team_project_jira_mappings tpjm ON p.gid = tpjm.asana_gid AND tpjm.kind = 'project'
    LEFT JOIN project_jira_decisions pjd ON p.gid = pjd.project_gid
    LEFT JOIN task_projects tp ON p.gid = tp.project_gid
    LEFT JOIN task_jira_mappings tjm ON tp.task_gid = tjm.task_gid
    WHERE p.is_personal_project = 0
    GROUP BY p.gid, p.name, p.team_gid, p.team_name, jpm.jira_project_name, jpm.jira_project_key, jpm.jira_site_key, tpjm.jira_key, pjd.decision, jpm.synced_at, jpm.project_gid
    ORDER BY CASE WHEN jpm.project_gid IS NOT NULL THEN 0 ELSE 1 END, p.team_name, p.name
    """

    with db.connect() as conn:
        cursor = conn.execute(query)
        rows = cursor.fetchall()

    if not rows:
        print("No mappings found.")
        return

    columns = [
        "project_gid",
        "asana_project_name",
        "team_gid",
        "team_name",
        "jira_project_name",
        "jira_project_key",
        "jira_site_key",
        "atjm_key",
        "asana_migration_decision",
        "synced_at",
        "mapping_status",
        "asana_task_count",
        "mapped_task_count",
    ]

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        writer.writerows(rows)

    print(f"Exported {len(rows)} mappings to {output_path}")


if __name__ == "__main__":
    sys.exit(main())
