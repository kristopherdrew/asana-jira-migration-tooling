#!/usr/bin/env python3
"""Export team memberships from SQLite to CSV."""

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

from src.lib.config import load_credentials
from src.lib.database import Database
from src.lib.asana_urls import build_team_url, build_user_url, infer_workspace_gid
from src.lib.logging_utils import configure_logging, resolve_log_level


DEFAULT_DB_PATH = "data/asana.sqlite"
DEFAULT_OUTPUT_DIR = "exports"
DEFAULT_CONFIG_ROOT = "."
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
        help="Optional filename for the export. Defaults to team_memberships_<timestamp>.csv.",
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
    parser.add_argument(
        "--workspace-gid",
        help="Optional Asana workspace GID used to build user URLs.",
    )
    return parser.parse_args(argv)


def _bool_to_text(value: Optional[int]) -> str:
    if value is None:
        return ""
    return "true" if bool(value) else "false"


def export_team_memberships(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    team_gid: Optional[str] = None,
    workspace_gid: Optional[str] = None,
) -> Path:
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"team_memberships_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing team memberships export to %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)
        resolved_workspace_gid = workspace_gid or infer_workspace_gid(connection)
        params: list[str] = []
        query = """
            SELECT
                team_gid,
                team_name,
                team_is_public,
                user_gid,
                user_name,
                user_email,
                resource_type,
                user_is_active,
                role,
                is_admin,
                membership_is_active,
                membership_synced_at,
                user_synced_at,
                team_synced_at
            FROM view_team_memberships
        """
        if team_gid:
            query += " WHERE team_gid = ?"
            params.append(team_gid)
        query += " ORDER BY team_name COLLATE NOCASE, user_name COLLATE NOCASE, team_gid, user_gid"
        rows = connection.execute(query, params).fetchall()

    if not rows:
        logger.info("No team memberships found in %s; generated an empty export.", database_path)

    fieldnames = [
        "team_gid",
        "team_name",
        "team_url",
        "team_is_public",
        "user_gid",
        "user_name",
        "user_url",
        "user_email",
        "resource_type",
        "user_is_active",
        "role",
        "is_admin",
        "membership_is_active",
        "membership_synced_at",
        "user_synced_at",
        "team_synced_at",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "team_gid": row["team_gid"],
                    "team_name": row["team_name"],
                    "team_url": build_team_url(row["team_gid"]),
                    "team_is_public": _bool_to_text(row["team_is_public"]),
                    "user_gid": row["user_gid"],
                    "user_name": row["user_name"],
                    "user_url": build_user_url(resolved_workspace_gid, row["user_gid"]),
                    "user_email": row["user_email"],
                    "resource_type": row["resource_type"],
                    "user_is_active": _bool_to_text(row["user_is_active"]),
                    "role": row["role"],
                    "is_admin": _bool_to_text(row["is_admin"]),
                    "membership_is_active": _bool_to_text(row["membership_is_active"]),
                    "membership_synced_at": row["membership_synced_at"],
                    "user_synced_at": row["user_synced_at"],
                    "team_synced_at": row["team_synced_at"],
                }
            )

    logger.info("Exported %s team membership row(s) to %s.", len(rows), output_path)
    return output_path


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    output_path = export_team_memberships(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        team_gid=args.team_gid,
        workspace_gid=args.workspace_gid or load_credentials(DEFAULT_CONFIG_ROOT).workspace_gid,
    )
    logger.info("Team memberships export completed: %s", output_path)


if __name__ == "__main__":
    main()
