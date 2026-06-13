#!/usr/bin/env python3
"""Export team-level summary metrics from SQLite to CSV."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.database import Database
from src.lib.asana_urls import build_team_url
from src.lib.logging_utils import configure_logging, resolve_log_level


DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
logger = logging.getLogger(__name__)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        default=DEFAULT_DB_PATH,
        help="Path to the SQLite database (default: data/asana.sqlite).",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help="Directory to store the CSV export (default: exports/).",
    )
    parser.add_argument(
        "--filename",
        help="Optional filename for the export. Defaults to team_report_<timestamp>.csv.",
    )
    parser.add_argument(
        "--team-gid",
        help="Optional team GID to filter the export to one team.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Logging verbosity (default: INFO).",
    )
    return parser.parse_args(argv)


def _bool_to_text(value: Optional[int]) -> str:
    if value is None:
        return ""
    return "true" if bool(value) else "false"


def _format_admin_label(name: Optional[str], email: Optional[str], user_gid: Optional[str]) -> str:
    label = (name or "").strip()
    if not label and user_gid:
        label = f"unknown_user {user_gid}"
    elif not label:
        label = ""
    if email:
        return f"{label} <{email}>"
    return label


def export_team_report(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    team_gid: Optional[str] = None,
) -> Path:
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"team_report_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing team report export to %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)
        params: list[str] = []
        query = """
            WITH project_counts AS (
                SELECT
                    team_gid,
                    COUNT(*) AS total_projects,
                    SUM(CASE WHEN COALESCE(archived, 0) = 0 THEN 1 ELSE 0 END) AS active_projects,
                    SUM(CASE WHEN COALESCE(archived, 0) = 1 THEN 1 ELSE 0 END) AS archived_projects
                FROM projects
                WHERE team_gid IS NOT NULL
                GROUP BY team_gid
            )
            SELECT
                t.gid,
                t.name,
                t.is_public,
                t.synced_at,
                COALESCE(pc.total_projects, 0) AS total_projects,
                COALESCE(pc.active_projects, 0) AS active_projects,
                COALESCE(pc.archived_projects, 0) AS archived_projects
            FROM teams t
            LEFT JOIN project_counts pc ON pc.team_gid = t.gid
        """
        if team_gid:
            query += " WHERE t.gid = ?"
            params.append(team_gid)
        query += " ORDER BY t.name COLLATE NOCASE, t.gid"
        team_rows = connection.execute(query, params).fetchall()

        admin_rows = connection.execute(
            """
            SELECT
                tm.team_gid,
                u.name AS user_name,
                u.email AS user_email,
                tm.user_gid AS user_gid
            FROM team_members tm
            LEFT JOIN users u ON u.gid = tm.user_gid
            WHERE COALESCE(tm.is_admin, 0) = 1
            ORDER BY tm.team_gid, u.name COLLATE NOCASE, tm.user_gid
            """
        ).fetchall()

    if not team_rows:
        logger.info("No teams found in %s; generated an empty export.", database_path)

    admins_by_team = defaultdict(list)
    for row in admin_rows:
        admins_by_team[row["team_gid"]].append(
            _format_admin_label(row["user_name"], row["user_email"], row["user_gid"])
        )

    fieldnames = [
        "team_gid",
        "team_name",
        "team_url",
        "team_is_public",
        "project_count_total",
        "project_count_active",
        "project_count_archived",
        "known_team_admins",
        "team_synced_at",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in team_rows:
            writer.writerow(
                {
                    "team_gid": row["gid"],
                    "team_name": row["name"],
                    "team_url": build_team_url(row["gid"]),
                    "team_is_public": _bool_to_text(row["is_public"]),
                    "project_count_total": row["total_projects"] or 0,
                    "project_count_active": row["active_projects"] or 0,
                    "project_count_archived": row["archived_projects"] or 0,
                    "known_team_admins": "; ".join(admins_by_team.get(row["gid"], [])),
                    "team_synced_at": row["synced_at"],
                }
            )

    logger.info("Exported %s team row(s) to %s.", len(team_rows), output_path)
    return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    output_path = export_team_report(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        team_gid=args.team_gid,
    )
    logger.info("Team report export completed: %s", output_path)


if __name__ == "__main__":
    main()
