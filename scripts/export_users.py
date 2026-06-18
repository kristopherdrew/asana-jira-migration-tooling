#!/usr/bin/env python3
"""Export users stored in SQLite to a CSV file."""

from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.lib.config import load_credentials
from src.lib.database import Database
from src.lib.date_formatting import format_report_date
from src.lib.asana_urls import build_user_url
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
        help="Optional filename for the export. Defaults to users_<timestamp>.csv.",
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


def _workspace_labels(connection) -> dict[str, List[str]]:
    rows = connection.execute(
        """
        SELECT user_gid, workspace_gid, workspace_name
        FROM user_workspaces
        ORDER BY workspace_name COLLATE NOCASE, workspace_gid COLLATE NOCASE
        """
    ).fetchall()

    workspace_map: dict[str, List[str]] = {}
    for row in rows:
        label = row["workspace_name"] or row["workspace_gid"]
        if not label:
            continue
        workspace_map.setdefault(row["user_gid"], []).append(label)
    return workspace_map


def _task_activity_counts(connection) -> dict[str, dict[str, object]]:
    """Return per-user task activity keyed by user GID.

    Each value holds the number of tasks the user created and completed, the
    number of distinct tasks on which the user authored at least one (non-deleted)
    comment (not the number of comments), and ``last_active`` — the most recent
    timestamp across those three activities (raw ISO string, or "" when none).
    """
    activity: dict[str, dict[str, object]] = {}

    def _default() -> dict[str, object]:
        return {"created": 0, "completed": 0, "commented": 0, "last_active": ""}

    def _accumulate(query: str, metric: str) -> None:
        for row in connection.execute(query).fetchall():
            user_gid = row["user_gid"]
            if not user_gid:
                continue
            entry = activity.setdefault(user_gid, _default())
            entry[metric] = row["task_count"]
            last_at = row["last_at"] or ""
            # ISO 8601 timestamps compare correctly as strings.
            if last_at > entry["last_active"]:
                entry["last_active"] = last_at

    _accumulate(
        """
        SELECT created_by_gid AS user_gid,
            COUNT(DISTINCT gid) AS task_count,
            MAX(created_at) AS last_at
        FROM tasks
        WHERE TRIM(COALESCE(created_by_gid, '')) != ''
        GROUP BY created_by_gid
        """,
        "created",
    )
    _accumulate(
        """
        SELECT completed_by_gid AS user_gid,
            COUNT(DISTINCT gid) AS task_count,
            MAX(completed_at) AS last_at
        FROM tasks
        WHERE TRIM(COALESCE(completed_by_gid, '')) != ''
        GROUP BY completed_by_gid
        """,
        "completed",
    )
    _accumulate(
        """
        SELECT created_by_gid AS user_gid,
            COUNT(DISTINCT task_gid) AS task_count,
            MAX(created_at) AS last_at
        FROM task_comments
        WHERE is_deleted = 0
            AND TRIM(COALESCE(created_by_gid, '')) != ''
        GROUP BY created_by_gid
        """,
        "commented",
    )
    return activity


def export_users(
    database_path: str,
    output_dir: Path,
    filename: Optional[str] = None,
    workspace_gid: Optional[str] = None,
) -> Path:
    database = Database(db_path=database_path)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        filename = f"users_{timestamp}.csv"

    output_path = output_dir / filename
    logger.debug("Writing user export to %s", output_path)

    with database.connect() as connection:
        database.initialize(connection)
        resolved_workspace_gid = workspace_gid

        users = connection.execute(
            """
            SELECT gid, name, email, resource_type, is_active, synced_at
            FROM users
            ORDER BY name COLLATE NOCASE, gid
            """
        ).fetchall()

        workspace_map = _workspace_labels(connection)
        atlassian_map = database.load_atlassian_account_map(connection)
        activity_map = _task_activity_counts(connection)

    if not users:
        logger.info("No users found in %s; generated an empty export.", database_path)

    fieldnames = [
        "gid",
        "name",
        "email",
        "resource_type",
        "is_active",
        "atlassian_id",
        "atlassian_status",
        "tasks_created",
        "tasks_completed",
        "tasks_commented",
        "last_active_date",
        "user_url",
        "synced_at",
        "workspaces",
    ]

    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()

        for row in users:
            workspaces = workspace_map.get(row["gid"], [])
            distinct_workspaces = sorted(dict.fromkeys(workspaces))
            atlassian_account = atlassian_map.get(row["gid"], {})
            activity = activity_map.get(row["gid"], {})
            writer.writerow(
                {
                    "gid": row["gid"],
                    "name": row["name"],
                    "email": row["email"],
                    "resource_type": row["resource_type"],
                    "is_active": _bool_to_text(row["is_active"]),
                    "atlassian_id": atlassian_account.get("account_id", ""),
                    "atlassian_status": atlassian_account.get("status", ""),
                    "tasks_created": activity.get("created", 0),
                    "tasks_completed": activity.get("completed", 0),
                    "tasks_commented": activity.get("commented", 0),
                    "last_active_date": format_report_date(activity.get("last_active", "")),
                    "user_url": build_user_url(resolved_workspace_gid, row["gid"]),
                    "synced_at": format_report_date(row["synced_at"]),
                    "workspaces": "; ".join(distinct_workspaces),
                }
            )

    logger.info("Exported %s user(s) to %s.", len(users), output_path)
    return output_path


def _bool_to_text(value: Optional[int]) -> str:
    if value is None:
        return ""
    return "true" if bool(value) else "false"


def main(argv: Optional[Iterable[str]] = None) -> None:
    args = parse_args(argv)
    try:
        log_level = resolve_log_level(args.log_level)
    except ValueError as exc:  # noqa: BLE001
        raise SystemExit(str(exc)) from exc
    configure_logging(log_level)

    output_path = export_users(
        database_path=args.database,
        output_dir=Path(args.output_dir),
        filename=args.filename,
        workspace_gid=args.workspace_gid or load_credentials(DEFAULT_CONFIG_ROOT).workspace_gid,
    )
    logger.info("User export completed: %s", output_path)


if __name__ == "__main__":
    main()
